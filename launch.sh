#!/bin/bash
# BFSB Launcher
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

export QTWEBENGINEPROCESS_PATH=/usr/lib/qt6/QtWebEngineProcess
export QTWEBENGINE_RESOURCES_PATH=/usr/share/qt6/resources
export QT_PLUGIN_PATH=/usr/lib/qt6/plugins
export XDG_RUNTIME_DIR=/run/user/$(id -u)
cd /home/binwalk/Downloads/bfsb
exec /usr/bin/python3 -m bfsb.main "$@"
