#!/bin/bash
# BFSB Launcher - wrapper script for desktop entry

# Platform: prefer Wayland (we're on Hyprland), fall back to X11 via XWayland.
# On pure X11 the env vars are already set correctly; on Wayland we want
# the Wayland platform plugin to avoid the silent-fail when XCB has no DISPLAY.
if [ -n "${WAYLAND_DISPLAY:-}" ] && [ -z "${BFSB_FORCE_XCB:-}" ]; then
    unset QT_QPA_PLATFORM
    unset QT_WEBENGINE_DISABLE_WAYLAND
else
    export QT_QPA_PLATFORM=xcb
    export QT_WEBENGINE_DISABLE_WAYLAND=1
fi

# Use software rendering backend to avoid GPU/compositor black screen
export QT_QUICK_BACKEND=software
export QT_WEBENGINE_DISABLE_GPU=1

# Chromium flags: disable GPU compositing, sandbox, dev-shm
# MUST be a single space-separated string — Qt reads only the first array element otherwise.
export QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-dev-shm-usage --disable-gpu --disable-gpu-compositing --disable-webgl --disable-webgl2 --disable-3d-apis --disable-breakpad --disable-extensions --disable-plugins --disable-default-apps --disable-sync --disable-background-networking --disable-background-timer-throttling --disable-renderer-backgrounding --disable-features=VizDisplayCompositor,UseSkiaRenderer,CanvasOopRasterization --num-raster-threads=1"

# ---------------------------------------------------------------------------
# QtWebEngineProcess / resources location.
# Do NOT hardcode system paths: this machine uses pip-bundled PyQt6 (which
# ships its own Qt under ~/.local/lib/python*/site-packages/PyQt6/Qt6),
# and there is no /usr/lib/qt6 or /usr/share/qt6/resources. Pointing
# QTWEBENGINEPROCESS_PATH at a nonexistent file makes Qt abort at startup.
# Auto-detect: pip bundle first, then Fedora/Debian system candidates.
# If nothing is found, leave unset so Qt auto-locates relative to the libs.
# ---------------------------------------------------------------------------
unset QTWEBENGINEPROCESS_PATH
unset QTWEBENGINE_RESOURCES_PATH
_PIP_WEBENGINE_PROC="$(python3 -c "import os, PyQt6; print(os.path.join(os.path.dirname(PyQt6.__file__), 'Qt6', 'libexec', 'QtWebEngineProcess'))" 2>/dev/null)"
_PIP_WEBENGINE_RES="$(python3 -c "import os, PyQt6; print(os.path.join(os.path.dirname(PyQt6.__file__), 'Qt6', 'resources'))" 2>/dev/null)"
for _cand in "$_PIP_WEBENGINE_PROC" \
    /usr/libexec/qt6/QtWebEngineProcess \
    /usr/lib64/qt6/libexec/QtWebEngineProcess \
    /usr/lib/qt6/QtWebEngineProcess; do
    if [ -n "$_cand" ] && [ -x "$_cand" ]; then
        export QTWEBENGINEPROCESS_PATH="$_cand"
        break
    fi
done
for _cand in "$_PIP_WEBENGINE_RES" \
    /usr/share/qt6/resources \
    /usr/lib64/qt6/resources; do
    if [ -n "$_cand" ] && [ -d "$_cand" ]; then
        export QTWEBENGINE_RESOURCES_PATH="$_cand"
        break
    fi
done
unset _PIP_WEBENGINE_PROC _PIP_WEBENGINE_RES _cand
if [ -n "${QTWEBENGINEPROCESS_PATH:-}" ]; then
    echo "[BFSB] QtWebEngineProcess: $QTWEBENGINEPROCESS_PATH"
else
    echo "[BFSB] QtWebEngineProcess: <auto (bundled Qt)>"
fi

# ---------------------------------------------------------------------------
# SearXNG lifecycle (Podman rootless) — start on BFSB launch, stop on exit.
# Bound to 127.0.0.1:8888, only reachable from this machine.
# BFSB's own UI server lives on 127.0.0.1:8889 (see bfsb/core/server.py).
# ---------------------------------------------------------------------------
BFSB_DIR="/home/binwalk/Downloads/bfsb"
BFSB_RUNTIME_DIR="$HOME/.bfsb"
mkdir -p "$BFSB_RUNTIME_DIR"

SEARXNG_IMAGE="docker.io/searxng/searxng:latest"
SEARXNG_NAME="bfsb-searxng"
SEARXNG_LOG="$BFSB_RUNTIME_DIR/searxng.log"
SEARXNG_SETTINGS_DIR="$BFSB_RUNTIME_DIR/searxng"
SEARXNG_READY_TIMEOUT=60  # seconds (first pull can be slow)
mkdir -p "$SEARXNG_SETTINGS_DIR"

# Ensure settings.yml exists with json enabled + a local secret.
if [ ! -f "$SEARXNG_SETTINGS_DIR/settings.yml" ]; then
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32)
    cat > "$SEARXNG_SETTINGS_DIR/settings.yml" <<EOF
use_default_settings: true
server:
  secret_key: "$SECRET"
  bind_address: "0.0.0.0"
  port: 8888
  base_url: "http://127.0.0.1:8888/"
  limiter: false
  image_proxy: false
search:
  formats:
    - html
    - json
EOF
fi

start_searxng() {
    if podman container exists "$SEARXNG_NAME" >/dev/null 2>&1; then
        if [ "$(podman container inspect -f '{{.State.Running}}' "$SEARXNG_NAME" 2>/dev/null)" = "true" ]; then
            echo "[BFSB] searxng container already running"
            return 0
        fi
        echo "[BFSB] Starting existing searxng container..."
        podman start "$SEARXNG_NAME" >> "$SEARXNG_LOG" 2>&1 || {
            echo "[BFSB] 'podman start' failed, recreating container..."
            podman rm -f "$SEARXNG_NAME" >/dev/null 2>&1 || true
        }
        if [ "$(podman container inspect -f '{{.State.Running}}' "$SEARXNG_NAME" 2>/dev/null)" = "true" ]; then
            echo "[BFSB] searxng container started"
            return 0
        fi
    fi
    if ! podman container exists "$SEARXNG_NAME" >/dev/null 2>&1; then
        echo "[BFSB] Creating searxng container ($SEARXNG_IMAGE)..."
        podman run -d --replace --name "$SEARXNG_NAME" \
            -p 127.0.0.1:8888:8888 \
            -v "$SEARXNG_SETTINGS_DIR:/etc/searxng:Z" \
            -e "BASE_URL=http://127.0.0.1:8888/" \
            -e "SEARXNG_PORT=8888" \
            "$SEARXNG_IMAGE" >> "$SEARXNG_LOG" 2>&1 || return 1
    else
        podman start "$SEARXNG_NAME" >> "$SEARXNG_LOG" 2>&1 || return 1
    fi
    # Wait for /healthz
    for i in $(seq 1 $((SEARXNG_READY_TIMEOUT * 2))); do
        if curl -sf -m 1 http://127.0.0.1:8888/healthz >/dev/null 2>&1; then
            echo "[BFSB] SearXNG ready"
            return 0
        fi
        sleep 0.5
    done
    echo "[BFSB] WARNING: SearXNG not ready after ${SEARXNG_READY_TIMEOUT}s (continuing anyway)" >&2
    return 1
}

stop_searxng() {
    if podman container exists "$SEARXNG_NAME" >/dev/null 2>&1; then
        podman stop -t 10 "$SEARXNG_NAME" >/dev/null 2>&1 || true
        echo "[BFSB] SearXNG stopped"
    fi
}

cleanup() {
    # If BFSB is still running, terminate it so we don't leak its process.
    if [ -n "${BFSB_PID:-}" ] && kill -0 "$BFSB_PID" 2>/dev/null; then
        kill -TERM "$BFSB_PID" 2>/dev/null || true
        for i in $(seq 1 20); do
            kill -0 "$BFSB_PID" 2>/dev/null || break
            sleep 0.5
        done
        kill -KILL "$BFSB_PID" 2>/dev/null || true
        wait "$BFSB_PID" 2>/dev/null || true
    fi
    stop_searxng
}
trap cleanup EXIT INT TERM HUP

# Bring up the search backend (don't fail whole launch if slow)
start_searxng || true

cd "$BFSB_DIR"
# Run BFSB as a child (NOT exec) so the EXIT trap fires on clean exit.
# If the user closes the BFSB window, Qt exits the event loop, python returns,
# the child exits, and our trap stops the SearXNG container.
python3 -m bfsb.main "$@" &
BFSB_PID=$!
wait "$BFSB_PID"