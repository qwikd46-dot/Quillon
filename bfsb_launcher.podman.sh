#!/bin/bash
# BFSB Launcher (Podman) - starts local SearXNG on 127.0.0.1:8888, runs BFSB UI on 127.0.0.1:8889, stops SearXNG on exit.
set -u
BFSB_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [ -n "${WAYLAND_DISPLAY:-}" ] && [ -z "${BFSB_FORCE_XCB:-}" ]; then
    unset QT_QPA_PLATFORM
    unset QT_WEBENGINE_DISABLE_WAYLAND
else
    export QT_QPA_PLATFORM=xcb
    export QT_WEBENGINE_DISABLE_WAYLAND=1
fi
export QT_QUICK_BACKEND=software
export QT_WEBENGINE_DISABLE_GPU=1
export QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-dev-shm-usage --disable-gpu --disable-gpu-compositing --disable-webgl --disable-webgl2 --disable-3d-apis --disable-breakpad --disable-extensions --disable-plugins --disable-default-apps --disable-sync --disable-background-networking --disable-background-timer-throttling --disable-renderer-backgrounding --disable-features=VizDisplayCompositor,UseSkiaRenderer,CanvasOopRasterization --num-raster-threads=1"
unset QTWEBENGINEPROCESS_PATH
unset QTWEBENGINE_RESOURCES_PATH
BFSB_RUNTIME_DIR="$HOME/.bfsb"
mkdir -p "$BFSB_RUNTIME_DIR"
BFSB_CHECKUP="$BFSB_DIR/bfsb_checkup.sh"
if [ -x "$BFSB_CHECKUP" ]; then
    "$BFSB_CHECKUP" --replace || true
fi
SEARXNG_IMAGE="docker.io/searxng/searxng:latest"
SEARXNG_NAME="bfsb-searxng"
SEARXNG_LOG="$BFSB_RUNTIME_DIR/searxng.log"
SEARXNG_SETTINGS_DIR="$BFSB_RUNTIME_DIR/searxng"
SEARXNG_READY_TIMEOUT=60
BFSB_LAUNCHER_ID="$$-$(date +%s)-$RANDOM"
BFSB_LAUNCHER_PID_FILE="$BFSB_RUNTIME_DIR/launcher.pid"
BFSB_BROWSER_PID_FILE="$BFSB_RUNTIME_DIR/browser.pid"
BFSB_LAUNCHER_LOCK_FILE="$BFSB_RUNTIME_DIR/launcher.lock"
BFSB_SEARXNG_OWNER_FILE="$BFSB_RUNTIME_DIR/searxng.owner"
BFSB_LAUNCHER_LOCKED=0
mkdir -p "$SEARXNG_SETTINGS_DIR"

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

write_searxng_owner() {
    local owner_tmp="${BFSB_SEARXNG_OWNER_FILE}.tmp.$$"
    printf '%s\n' "$BFSB_LAUNCHER_ID" > "$owner_tmp"
    mv -f "$owner_tmp" "$BFSB_SEARXNG_OWNER_FILE"
}

read_searxng_owner() {
    if [ -f "$BFSB_SEARXNG_OWNER_FILE" ]; then
        tr -d '[:space:]' < "$BFSB_SEARXNG_OWNER_FILE" 2>/dev/null || true
    fi
}

start_searxng() {
    local current_owner=""
    current_owner="$(read_searxng_owner)"
    if [ -n "$current_owner" ] && [ "$current_owner" != "$BFSB_LAUNCHER_ID" ]; then
        echo "[BFSB] searxng is owned by another launcher"
        return 1
    fi
    write_searxng_owner
    if podman container exists "$SEARXNG_NAME" >/dev/null 2>&1; then
        if [ "$(podman container inspect -f '{{.State.Running}}' "$SEARXNG_NAME" 2>/dev/null)" = "true" ]; then
            echo "[BFSB] searxng container already running"
            return 0
        fi
        echo "[BFSB] Starting existing searxng container..."
        podman start "$SEARXNG_NAME" >> "$SEARXNG_LOG" 2>&1 || {
            echo "[BFSB] podman start failed, recreating..."
            podman rm -f "$SEARXNG_NAME" >/dev/null 2>&1 || true
        }
        if [ "$(podman container inspect -f '{{.State.Running}}' "$SEARXNG_NAME" 2>/dev/null)" = "true" ]; then
            echo "[BFSB] searxng container started"
            return 0
        fi
    fi
    if ! podman container exists "$SEARXNG_NAME" >/dev/null 2>&1; then
        echo "[BFSB] Creating searxng container ($SEARXNG_IMAGE)..."
        podman run -d --replace --name "$SEARXNG_NAME" -p 127.0.0.1:8888:8888 -v "$SEARXNG_SETTINGS_DIR:/etc/searxng:Z" -e "BASE_URL=http://127.0.0.1:8888/" -e "SEARXNG_PORT=8888" "$SEARXNG_IMAGE" >> "$SEARXNG_LOG" 2>&1 || return 1
    else
        podman start "$SEARXNG_NAME" >> "$SEARXNG_LOG" 2>&1 || return 1
    fi
    for _ in $(seq 1 $((SEARXNG_READY_TIMEOUT * 2))); do
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
    local current_owner=""
    current_owner="$(read_searxng_owner)"
    [ "$current_owner" = "$BFSB_LAUNCHER_ID" ] || return 0
    if command -v podman >/dev/null 2>&1 && podman container exists "$SEARXNG_NAME" >/dev/null 2>&1; then
        if ! podman rm -f "$SEARXNG_NAME" >/dev/null 2>&1; then
            podman stop -t 3 "$SEARXNG_NAME" >/dev/null 2>&1 || true
        fi
    fi
    rm -f "$BFSB_SEARXNG_OWNER_FILE"
}

claim_launcher_lock() {
    if command -v flock >/dev/null 2>&1; then
        exec 9>"$BFSB_LAUNCHER_LOCK_FILE"
        if ! flock -w 15 9; then
            echo "[BFSB] another launcher is still stopping"
            exit 1
        fi
        BFSB_LAUNCHER_LOCKED=1
    fi
    printf '%s\n' "$$" > "$BFSB_LAUNCHER_PID_FILE"
}

SEARXNG_START_PID=""
BFSB_CLEANUP_SERVICES=0

start_searxng_async() {
    if [ "${BFSB_LAUNCHER_LOCKED:-0}" = "1" ]; then
        (start_searxng >> "$BFSB_RUNTIME_DIR/searxng-start.log" 2>&1 9>&-) &
    else
        (start_searxng >> "$BFSB_RUNTIME_DIR/searxng-start.log" 2>&1) &
    fi
    SEARXNG_START_PID=$!
}

terminate_tree() {
    local pid="$1"
    local signal_name="$2"
    local child
    for child in $(pgrep -P "$pid" 2>/dev/null); do
        terminate_tree "$child" "$signal_name"
    done
    kill -"$signal_name" "$pid" 2>/dev/null || true
}

is_recorded_proxy() {
    local pid="$1"
    local command_line=""
    [ -r "/proc/$pid/cmdline" ] || return 1
    command_line="$(tr '\000' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
    case "$command_line" in
        *proxy_addon.py*8228*) return 0 ;;
    esac
    return 1
}

stop_recorded_proxy() {
    local proxy_pid=""
    if [ -f "$BFSB_RUNTIME_DIR/proxy.pid" ]; then
        proxy_pid="$(tr -d '[:space:]' < "$BFSB_RUNTIME_DIR/proxy.pid" 2>/dev/null || true)"
        case "$proxy_pid" in
            ''|*[!0-9]*) proxy_pid="" ;;
        esac
        if is_recorded_proxy "$proxy_pid"; then
            terminate_tree "$proxy_pid" TERM
            for _ in $(seq 1 15); do
                if ! kill -0 "$proxy_pid" 2>/dev/null; then
                    break
                fi
                sleep 0.1
            done
            if is_recorded_proxy "$proxy_pid"; then
                terminate_tree "$proxy_pid" KILL
            fi
        fi
        rm -f "$BFSB_RUNTIME_DIR/proxy.pid"
    fi
}

cleanup() {
    if [ -n "${SEARXNG_START_PID:-}" ] && kill -0 "$SEARXNG_START_PID" 2>/dev/null; then
        terminate_tree "$SEARXNG_START_PID" TERM
        wait "$SEARXNG_START_PID" 2>/dev/null || true
    fi
    if [ -n "${BFSB_PID:-}" ] && kill -0 "$BFSB_PID" 2>/dev/null; then
        terminate_tree "$BFSB_PID" TERM
        for _ in $(seq 1 20); do
            kill -0 "$BFSB_PID" 2>/dev/null || break
            sleep 0.1
        done
        if kill -0 "$BFSB_PID" 2>/dev/null; then
            terminate_tree "$BFSB_PID" KILL
        fi
        wait "$BFSB_PID" 2>/dev/null || true
    fi
    stop_recorded_proxy
    if [ "${BFSB_CLEANUP_SERVICES:-0}" = "1" ]; then
        stop_searxng
    else
        echo "[BFSB] browser did not exit cleanly; leaving SearXNG running"
    fi
    if [ -f "$BFSB_BROWSER_PID_FILE" ] && [ "$(tr -d '[:space:]' < "$BFSB_BROWSER_PID_FILE" 2>/dev/null || true)" = "${BFSB_PID:-}" ]; then
        rm -f "$BFSB_BROWSER_PID_FILE"
    fi
    if [ -f "$BFSB_LAUNCHER_PID_FILE" ] && [ "$(tr -d '[:space:]' < "$BFSB_LAUNCHER_PID_FILE" 2>/dev/null || true)" = "$$" ]; then
        rm -f "$BFSB_LAUNCHER_PID_FILE"
    fi
}

shutdown_handler() {
    BFSB_CLEANUP_SERVICES=1
    cleanup
    exit 143
}

trap cleanup EXIT
trap shutdown_handler INT TERM HUP

claim_launcher_lock
write_searxng_owner

for _ in $(seq 1 20); do
    if ! curl -sf -m 1 http://127.0.0.1:8889/health >/dev/null 2>&1 && ! curl -sf -m 1 http://127.0.0.1:9222/json/version >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done
start_searxng_async
cd "$BFSB_DIR"
if command -v setsid >/dev/null 2>&1; then
    if [ "${BFSB_LAUNCHER_LOCKED:-0}" = "1" ]; then
        setsid python3 -m bfsb.main "$@" 9>&- &
    else
        setsid python3 -m bfsb.main "$@" &
    fi
else
    if [ "${BFSB_LAUNCHER_LOCKED:-0}" = "1" ]; then
        python3 -m bfsb.main "$@" 9>&- &
    else
        python3 -m bfsb.main "$@" &
    fi
fi
BFSB_PID=$!
printf '%s\n' "$BFSB_PID" > "$BFSB_BROWSER_PID_FILE"
if wait "$BFSB_PID"; then
    BFSB_CLEANUP_SERVICES=1
else
    BFSB_EXIT_CODE=$?
    exit "$BFSB_EXIT_CODE"
fi
