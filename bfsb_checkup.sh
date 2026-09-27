#!/bin/bash
set -u

BFSB_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BFSB_RUNTIME_DIR="${BFSB_RUNTIME_DIR:-$HOME/.bfsb}"
SEARXNG_NAME="${BFSB_SEARXNG_NAME:-bfsb-searxng}"
BFSB_LAUNCHER_PID_FILE="$BFSB_RUNTIME_DIR/launcher.pid"
BFSB_BROWSER_PID_FILE="$BFSB_RUNTIME_DIR/browser.pid"
BFSB_LAUNCHER_LOCK_FILE="$BFSB_RUNTIME_DIR/launcher.lock"
BFSB_SEARXNG_OWNER_FILE="$BFSB_RUNTIME_DIR/searxng.owner"
REPLACE_EXISTING=0

if [ "${1:-}" = "--replace" ]; then
    REPLACE_EXISTING=1
fi

mkdir -p "$BFSB_RUNTIME_DIR" || exit 0

terminate_tree() {
    local pid="$1"
    local signal_name="$2"
    local child
    [ -n "$pid" ] || return 0
    for child in $(pgrep -P "$pid" 2>/dev/null); do
        terminate_tree "$child" "$signal_name"
    done
    kill -"$signal_name" "$pid" 2>/dev/null || true
}

is_launcher_process() {
    local pid="$1"
    local command_line=""
    [ -r "/proc/$pid/cmdline" ] || return 1
    command_line="$(tr '\000' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
    case "$command_line" in
        *bfsb_launcher.sh*|*bfsb_launcher.podman.sh*) return 0 ;;
    esac
    return 1
}

terminate_previous_launcher() {
    local old_pid=""
    [ "$REPLACE_EXISTING" -eq 1 ] || return 0
    [ -f "$BFSB_LAUNCHER_PID_FILE" ] || return 0
    old_pid="$(tr -d '[:space:]' < "$BFSB_LAUNCHER_PID_FILE" 2>/dev/null || true)"
    case "$old_pid" in
        ''|*[!0-9]*) rm -f "$BFSB_LAUNCHER_PID_FILE"; return 0 ;;
    esac
    if ! is_launcher_process "$old_pid"; then
        rm -f "$BFSB_LAUNCHER_PID_FILE"
        return 0
    fi
    terminate_tree "$old_pid" TERM
    for _ in $(seq 1 30); do
        if ! kill -0 "$old_pid" 2>/dev/null; then
            rm -f "$BFSB_LAUNCHER_PID_FILE"
            return 0
        fi
        sleep 0.1
    done
    if is_launcher_process "$old_pid"; then
        terminate_tree "$old_pid" KILL
    fi
    rm -f "$BFSB_LAUNCHER_PID_FILE"
}

owner_is_active() {
    local owner_id="$1"
    local owner_pid=""
    owner_pid="${owner_id%%-*}"
    case "$owner_pid" in
        ''|*[!0-9]*) return 1 ;;
    esac
    is_launcher_process "$owner_pid"
}

cleanup_stale_searxng() {
    local owner_id=""
    [ -f "$BFSB_SEARXNG_OWNER_FILE" ] || return 0
    owner_id="$(tr -d '[:space:]' < "$BFSB_SEARXNG_OWNER_FILE" 2>/dev/null || true)"
    [ -n "$owner_id" ] || return 0
    if owner_is_active "$owner_id"; then
        return 0
    fi
    if command -v podman >/dev/null 2>&1 && podman container exists "$SEARXNG_NAME" >/dev/null 2>&1; then
        if ! podman rm -f "$SEARXNG_NAME" >/dev/null 2>&1; then
            podman stop -t 3 "$SEARXNG_NAME" >/dev/null 2>&1 || true
        fi
    fi
    rm -f "$BFSB_SEARXNG_OWNER_FILE"
}

is_process_matching() {
    local pid="$1"
    local needle="$2"
    local command_line=""
    [ -r "/proc/$pid/cmdline" ] || return 1
    command_line="$(tr '\000' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
    case "$command_line" in
        *"$needle"*) return 0 ;;
    esac
    return 1
}

stop_recorded_process() {
    local pid="$1"
    local needle="$2"
    [ -n "$pid" ] || return 0
    is_process_matching "$pid" "$needle" || return 0
    terminate_tree "$pid" TERM
    for _ in $(seq 1 15); do
        if ! kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        sleep 0.1
    done
    if is_process_matching "$pid" "$needle"; then
        terminate_tree "$pid" KILL
    fi
}

cleanup_bfsb_processes() {
    local browser_pid=""
    local proxy_pid=""
    [ "$REPLACE_EXISTING" -eq 1 ] || return 0
    if [ -f "$BFSB_BROWSER_PID_FILE" ]; then
        browser_pid="$(tr -d '[:space:]' < "$BFSB_BROWSER_PID_FILE" 2>/dev/null || true)"
        case "$browser_pid" in
            ''|*[!0-9]*) browser_pid="" ;;
        esac
        stop_recorded_process "$browser_pid" "bfsb.main"
        rm -f "$BFSB_BROWSER_PID_FILE"
    fi
    if [ -f "$BFSB_RUNTIME_DIR/proxy.pid" ]; then
        proxy_pid="$(tr -d '[:space:]' < "$BFSB_RUNTIME_DIR/proxy.pid" 2>/dev/null || true)"
        case "$proxy_pid" in
            ''|*[!0-9]*) proxy_pid="" ;;
        esac
        stop_recorded_process "$proxy_pid" "proxy_addon.py"
        rm -f "$BFSB_RUNTIME_DIR/proxy.pid"
    fi
}

if command -v flock >/dev/null 2>&1; then
    exec 9>"$BFSB_LAUNCHER_LOCK_FILE"
    flock -w 10 9 || exit 0
fi

terminate_previous_launcher
cleanup_stale_searxng
cleanup_bfsb_processes
exit 0
