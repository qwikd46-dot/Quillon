#!/usr/bin/env bash
# Populate the Quillon dependency cache.
#
# Everything the image needs is downloaded once, here, into vendor/ -- pip
# wheels, the npm cache, and (optionally) the SearXNG source tree. The
# Dockerfile installs from vendor/ when it is present and falls back to the
# network when it is not, so:
#
#   * a first build on a fresh clone works with no cache at all;
#   * a first build on a machine that has run this script is fully offline
#     and much faster;
#   * a CI build can cache vendor/ between runs.
#
# The cache location defaults to ./vendor next to the repository. Point it
# somewhere persistent -- a shared location, or a CI cache key -- with:
#
#   QUILLON_CACHE_DIR=~/.cache/quillon ./scripts/cache_deps.sh
#
# If QUILLON_CACHE_DIR is outside the repository, the directory is symlinked to
# vendor/ so the build context can still reach it.

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE_DIR="${QUILLON_CACHE_DIR:-$REPO_ROOT/vendor}"
VENDOR_DIR="$REPO_ROOT/vendor"
PIP_ARGS=(--only-binary=:all: --prefer-binary)

log() { printf '[cache] %s\n' "$*"; }

# The interpreter that actually has PyQt6, if it is not plain python3.
detect_python() {
    if python3 -c 'import PyQt6' 2>/dev/null; then
        echo python3
    elif python3.14 -c 'import PyQt6' 2>/dev/null; then
        echo python3.14
    else
        echo python3
    fi
}

link_cache() {
    if [ "$CACHE_DIR" = "$VENDOR_DIR" ]; then
        return
    fi
    mkdir -p "$(dirname "$CACHE_DIR")"
    mkdir -p "$CACHE_DIR"
    if [ -e "$VENDOR_DIR" ] && [ ! -L "$VENDOR_DIR" ]; then
        log "vendor/ exists and is not a symlink; leaving it alone"
        return
    fi
    ln -sfn "$CACHE_DIR" "$VENDOR_DIR"
    log "vendor/ -> $CACHE_DIR"
}

fetch_wheels() {
    local python; python="$(detect_python)"
    local out="$CACHE_DIR/wheels"
    mkdir -p "$out"
    log "fetching pip wheels into $out (using $python)"

    # Mirrors the Dockerfile's install list, plus gunicorn for SearXNG and
    # the build-time requirements searx's setup.py needs importable.
    "$python" -m pip download "${PIP_ARGS[@]}" \
        -d "$out" \
        "PyQt6>=6.6.0" \
        "PyQt6-WebEngine>=6.6.0" \
        "aiohttp>=3.9.0" \
        "jinja2>=3.1.0" \
        "requests>=2.31.0" \
        "python-dotenv>=1.0.0" \
        "httpx>=0.27.0" \
        "lxml>=5.0.0" \
        "cssselect>=1.2.0" \
        "cryptography>=41.0.0" \
        "keyring>=25.0" \
        "adblock>=0.6.0" \
        "mitmproxy>=12.2.3" \
        gunicorn \
        setuptools \
        wheel

    # SearXNG installs from git and imports msgspec at build time. Cache
    # that wheel too so the in-image build is offline as well.
    "$python" -m pip download "${PIP_ARGS[@]}" -d "$out" msgspec || \
        log "warning: msgspec wheel unavailable; the image build will fetch it"

    log "wheels: $(find "$out" -name '*.whl' | wc -l) files, $(du -sh "$out" | cut -f1)"
}

fetch_npm() {
    command -v npm >/dev/null 2>&1 || {
        log "npm not found; skipping the Node cache"
        return 0
    }
    local out="$CACHE_DIR/npm"
    local work
    work="$(mktemp -d)"
    mkdir -p "$out"
    log "populating the npm cache at $out"

    # `npm install` in the repository is a no-op when node_modules already
    # exists, which would leave the cache empty on any working machine --
    # exactly the machines that would benefit from it. Installing into a
    # throwaway directory forces the real download while leaving the
    # working tree alone.
    #
    # Cleanup is explicit rather than a `trap ... RETURN`: that fires when
    # the variable is already out of scope, which under `set -u` is a fatal
    # "work: unbound variable" after the cache has already been written.
    cp "$REPO_ROOT/package.json" "$REPO_ROOT/package-lock.json" "$work/" 2>/dev/null \
        || cp "$REPO_ROOT/package.json" "$work/"
    ( cd "$work" && npm ci --cache "$out/npm-cache" --no-audit --no-fund --omit=dev ) \
        || ( cd "$work" && npm install --cache "$out/npm-cache" --no-audit --no-fund --omit=dev )
    rm -rf "$work"

    local size entries
    size="$(du -sh "$out" 2>/dev/null | cut -f1)"
    entries="$(find "$out/npm-cache/_cacache" -type f 2>/dev/null | wc -l)"
    if [ "$entries" -gt 0 ]; then
        log "npm cache: $size ($entries entries)"
    else
        log "npm cache: $size (warning: _cacache is empty; the image build will fetch from the network)"
    fi
}

fetch_searxng() {
    if [ "${QUILLON_CACHE_SEARXNG:-0}" != "1" ]; then
        log "skipping the SearXNG source cache (set QUILLON_CACHE_SEARXNG=1 to include it)"
        return 0
    fi
    local out="$CACHE_DIR/searxng"
    if [ -d "$out/.git" ]; then
        log "SearXNG source already cached at $out"
        return 0
    fi
    local ref="${SEARXNG_REF:-master}"
    log "cloning SearXNG ($ref) into $out"
    rm -rf "$out"
    git clone --depth 1 --branch "$ref" \
        https://github.com/searxng/searxng.git "$out"
}

main() {
    mkdir -p "$CACHE_DIR"
    link_cache
    fetch_wheels
    fetch_npm
    fetch_searxng
    log "cache ready at $CACHE_DIR"
    log "build with: podman build -t quillon ."
}

main "$@"
