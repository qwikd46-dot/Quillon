#!/bin/bash
# Quillon Launcher
export DISPLAY=:0
export QT_QPA_PLATFORM=xcb
export QT_WEBENGINE_DISABLE_WAYLAND=1

# Use software rendering backend to avoid GPU/compositor black screen
export QT_QUICK_BACKEND=software
export QT_WEBENGINE_DISABLE_GPU=1

# Chromium flags: disable GPU compositing, sandbox, dev-shm
# Note: appended after main.py's flags. Both must reach Qt as a single
# space-separated string (Qt reads only the first element of an array).
EXTRA_FLAGS="--no-sandbox"
export QTWEBENGINE_CHROMIUM_FLAGS="${QTWEBENGINE_CHROMIUM_FLAGS:-} ${EXTRA_FLAGS}"

if [ -d /usr/lib/qt6/plugins ]; then export QT_PLUGIN_PATH=/usr/lib/qt6/plugins; fi
export XDG_RUNTIME_DIR=/run/user/$(id -u)
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
if [ -x "$SCRIPT_DIR/quillon_checkup.sh" ]; then
    "$SCRIPT_DIR/quillon_checkup.sh" --replace || true
fi
exec python3 -m quillon.main "$@"
