#!/bin/bash
# BFSB Launcher
export DISPLAY=:0
export QT_QPA_PLATFORM=xcb
export QT_WEBENGINE_DISABLE_WAYLAND=1

# Use software rendering backend to avoid GPU/compositor black screen
export QT_QUICK_BACKEND=software
export QT_WEBENGINE_DISABLE_GPU=1

# Chromium flags: disable GPU compositing, sandbox, dev-shm
export QTWEBENGINE_CHROMIUM_FLAGS=(
    "--no-sandbox"
    "--disable-dev-shm-usage"
    "--disable-gpu"
    "--disable-gpu-compositing"
    "--disable-webgl"
    "--disable-webgl2"
    "--disable-3d-apis"
    "--disable-breakpad"
    "--disable-extensions"
    "--disable-plugins"
    "--disable-default-apps"
    "--disable-sync"
    "--disable-background-networking"
    "--disable-background-timer-throttling"
    "--disable-renderer-backgrounding"
    "--disable-features=VizDisplayCompositor,UseSkiaRenderer,CanvasOopRasterization"
    "--num-raster-threads=1"
    "--renderer-process-limit=1"
)

export QTWEBENGINEPROCESS_PATH=/usr/lib/qt6/QtWebEngineProcess
export QTWEBENGINE_RESOURCES_PATH=/usr/share/qt6/resources
export QT_PLUGIN_PATH=/usr/lib/qt6/plugins
export XDG_RUNTIME_DIR=/run/user/$(id -u)
cd /home/zon/bfsb
exec /usr/bin/python3 -m bfsb.main "$@"
