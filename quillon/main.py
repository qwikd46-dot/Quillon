"""Quillon Browser.

A privacy-focused browser built on Qt WebEngine with local metasearch.
"""

import os
import sys
from pathlib import Path


def _check_platform() -> None:
    """Refuse to start anywhere but Linux, with a reason.

    Quillon is Linux-only for now. The parts that would break elsewhere are
    small but load-bearing rather than incidental: the proxy and the
    ad-block engine are reaped with PR_SET_PDEATHSIG, the proxy's live-pid
    check reads /proc/<pid>/cmdline, and process groups are torn down with
    os.killpg. On Windows or macOS those degrade into a crash somewhere
    deep in proxy lifecycle handling, or -- worse -- into an orphaned
    proxy that holds port 8228 with no parent to clean it up.

    Failing here, with an explanation, beats either. The container image
    runs the same Linux code under any host, so anyone on another OS is
    better served by that; this message says so.

    QUILLON_ALLOW_UNSUPPORTED_PLATFORM=1 overrides the check, for anyone
    porting it who wants to see how far it gets.
    """
    if sys.platform.startswith("linux"):
        return
    if os.environ.get("QUILLON_ALLOW_UNSUPPORTED_PLATFORM") == "1":
        print(
            "[quillon] QUILLON_ALLOW_UNSUPPORTED_PLATFORM is set; continuing on "
            f"{sys.platform} at your own risk",
            file=sys.stderr,
        )
        return
    raise SystemExit(
        f"Quillon supports Linux only, and this is {sys.platform}.\n"
        "\n"
        "The browser, the ad-blocking proxy and the encrypted vault are\n"
        "Linux-only for now. Everything they need is in the container\n"
        "image, which runs on any host:\n"
        "\n"
        "    podman pull ghcr.io/qwikd46-dot/quillon:main\n"
        "\n"
        "To port it yourself, set QUILLON_ALLOW_UNSUPPORTED_PLATFORM=1 and "
        "expect\nquillon/core/proxy_bootstrap.py, proxy_manager.py and "
        "ghostery_engine.py to need work."
    )


# Checked here, at the top of the module, rather than inside main(). The
# proxy bootstrap below runs at import time -- importing this module
# starts mitmdump on 8228 -- so a check inside main() would let a
# non-Linux machine spawn a proxy that nothing can then reap.
_check_platform()

# Platform: prefer Wayland (we're on Hyprland), fall back to X11.
# On pure X11 the env is already correct; on Wayland we want the Wayland
# platform plugin to avoid the silent-fail when XCB has no DISPLAY.
# Override with QUILLON_FORCE_XCB=1 if you need to debug X11-specific issues.
if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("QUILLON_FORCE_XCB")):
    os.environ["QT_QPA_PLATFORM"] = "xcb"
    os.environ["QT_WEBENGINE_DISABLE_WAYLAND"] = "1"

# Use software rendering backend to avoid GPU/compositor black screen
os.environ["QT_QUICK_BACKEND"] = "software"
os.environ["QT_WEBENGINE_DISABLE_GPU"] = "1"

# Chromium flags: disable GPU compositing, sandbox, dev-shm, service workers, Mojo IPC
# Use ONLY software rendering - no SwiftShader conflicts
_chromium_flags = (
    "--disable-dev-shm-usage "
    "--disable-gpu "
    "--disable-gpu-compositing "
    "--disable-accelerated-2d-canvas "
    "--disable-accelerated-video-decode "
    "--disable-webgl "
    "--disable-webgl2 "
    "--disable-3d-apis "
    "--disable-breakpad "
    "--disable-component-extensions-with-background-pages "
    "--disable-extensions "
    "--disable-plugins "
    "--disable-default-apps "
    "--disable-sync "
    "--disable-background-networking "
    "--disable-background-timer-throttling "
    "--disable-renderer-backgrounding "
    "--disable-features=VizDisplayCompositor,UseSkiaRenderer,CanvasOopRasterization,ServiceWorker,NetworkService,OutOfProcessNetworkService "
    "--disable-service-worker "
    "--disable-mojo-broker "
    "--disable-mojo-internal "
    "--disable-ipc-flooding-protection "
    "--num-raster-threads=1 "
    "--renderer-process-limit=1"
)
if os.environ.get("QUILLON_TEST") == "1":
    _chromium_flags += " --remote-debugging-port=9222 --remote-allow-origins=*"

# Network-level ad blocking: route Chromium through the Quillon mitmproxy addon
# which rewrites /youtubei/v1/* responses and strips ad placements BEFORE the
# page sees them (undetectable by anti-adblock checks). Fail-safe: if the
# proxy cannot start, no proxy flags are set and browsing works as before.
#
# A read-only diagnostic must not spawn a proxy, so the bootstrap is skipped
# when one of those is asked for.
_DIAGNOSTIC_ONLY = any(
    flag in sys.argv for flag in ("--check-cookie-encryption", "--version")
)
if _DIAGNOSTIC_ONLY:
    _proxy = None
else:
    try:
        from quillon.core.proxy_bootstrap import ensure_proxy_running
        _proxy = ensure_proxy_running()
        if _proxy:
            _port, _spki = _proxy
            _chromium_flags += (
                f" --proxy-server=http://127.0.0.1:{_port}"
                f" --ignore-certificate-errors-spki-list={_spki}"
            )
            print(f"[Quillon] Network ad-block proxy active on 127.0.0.1:{_port}")
        else:
            print("[Quillon] Proxy unavailable - running without network ad-block")
    except Exception as _e:
        print(f"[Quillon] Proxy bootstrap failed: {_e}")

_existing_chromium_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
if _existing_chromium_flags:
    _chromium_flags = f"{_existing_chromium_flags} {_chromium_flags}"
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = _chromium_flags

# Disable Qt logging spam
os.environ["QT_LOGGING_RULES"] = "qt.webenginecontext.debug=false;qt.webenginecontext.warning=false"

# Set Qt WebEngine paths from the active PyQt6 installation.
import PyQt6
_pyqt_root = Path(PyQt6.__file__).resolve().parent
os.environ.setdefault("QTWEBENGINEPROCESS_PATH", str(_pyqt_root / "Qt6" / "libexec" / "QtWebEngineProcess"))
os.environ.setdefault("QTWEBENGINE_RESOURCES_PATH", str(_pyqt_root / "Qt6" / "resources"))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineCore import QWebEngineUrlScheme

# Register quillon:// custom URL scheme BEFORE QApplication creation.
# Guarded: webengine.py also registers it, so skip if already done.
quillon_scheme = QWebEngineUrlScheme(b"quillon")
quillon_scheme.setSyntax(QWebEngineUrlScheme.Syntax.HostPortAndUserInformation)
quillon_scheme.setDefaultPort(8889)
quillon_scheme.setFlags(
    QWebEngineUrlScheme.Flag.SecureScheme |
    QWebEngineUrlScheme.Flag.LocalScheme |
    QWebEngineUrlScheme.Flag.LocalAccessAllowed |
    QWebEngineUrlScheme.Flag.CorsEnabled |
    QWebEngineUrlScheme.Flag.FetchApiAllowed
)
try:
    QWebEngineUrlScheme.registerScheme(quillon_scheme)
except RuntimeError:
    pass  # already registered

from quillon.core import PATHS, URLBlocker
from quillon.ui import QuillonWindow


def main() -> int:
    """Application entry point."""
    if "--check-cookie-encryption" in sys.argv:
        from quillon.core.chromium_crypt import format_report
        print(format_report())
        return 0

    PATHS.ensure_dirs()
    blocker = URLBlocker()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("Quillon")
    app.setApplicationDisplayName("Quillon")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("Binwalk")
    app.setOrganizationDomain("quillon.browser")
    # Tie the window to the installed quillon.desktop entry. On Wayland this
    # sets the shell app_id to "quillon" (instead of "python"), so the
    # taskbar/dock shows the name "Quillon" and the correct SVG/PNG icon
    # from the .desktop file instead of "quillon.python" + a generic icon.
    app.setDesktopFileName("quillon")

    from PyQt6.QtGui import QIcon
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "templates" / "static" / "quillon_icon.png")))

    from quillon.ui.styles import get_palette
    app.setPalette(get_palette())

    window = QuillonWindow(blocker)
    window.show()
    _install_shutdown_handler(blocker)
    print("Quillon started")
    return app.exec()


def _install_shutdown_handler(blocker) -> None:
    """Stop the Node adblocker backend on SIGTERM/SIGINT/SIGHUP.

    atexit only runs on a normal interpreter exit. Closing the browser
    from the launcher, a SIGTERM from the checkup script, or Ctrl-C all
    terminate the process without it, which is how seventy-one orphaned
    node processes accumulated. Signal handlers must be installed from the
    main thread, which is why this is here and not in the engine.
    """
    import signal

    def _handler(signum, _frame):
        try:
            ghostery = getattr(blocker, "_ghostery", None)
            if ghostery is not None:
                ghostery.stop()
        except Exception:
            pass
        try:
            # mitmdump runs in its own session, so it does not die with us
            # and nothing after this point runs -- including atexit. Left
            # alone it stays alive holding port 8228, and the browser shows
            # ERR_PROXY_CONNECTION_FAILED until the next launch reclaims it.
            from quillon.core import proxy_bootstrap as _proxy_bootstrap

            _proxy_bootstrap.shutdown_proxy()
        except Exception:
            pass
        finally:
            # Re-raise with the default disposition so the exit status and
            # any core dump behaviour stay normal.
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass  # not the main thread, or unsupported here


if __name__ == "__main__":
    sys.exit(main())
