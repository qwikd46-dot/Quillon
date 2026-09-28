# BFSB — single-container build.
#
# Everything the browser needs runs in here: the Qt/WebEngine UI, the
# mitmdump ad-block proxy, the Node Ghostery engine and SearXNG. One
# process (bfsb_supervisor) owns all of them, so the container starts and
# stops as one unit.
#
# Build:  podman build -t bfsb .
# Run:    podman run --rm -it --network slirp4networks \
#             -e DISPLAY=:99 -v /tmp/.X11-unix:/tmp/.X11-unix:ro \
#             -v bfsb-data:/home/binwalk/.bfsb bfsb

FROM docker.io/library/python:3.14-slim

# SearXNG and the UI server both need a CA bundle and a sane locale for
# UTF-8 search terms.
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8 \
    HOME=/home/binwalk \
    XDG_RUNTIME_DIR=/tmp/runtime

# Qt WebEngine is Chromium: it needs the usual X11/GL/font/dbus shared
# libraries even when running against Xvfb, plus the fonts a page would
# otherwise render as boxes. Xvfb and xauth are what let the GUI run
# headless. nodejs is the Ghostery ad-block engine's runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
    # Qt / Chromium runtime
        libglib2.0-0 \
        libnss3 \
        libnspr4 \
    # X11 core + the full xcb plugin set. The xcb platform plugin dlopens
    # each of these by name and only says "Could not load the Qt platform
    # plugin xcb" when one is missing, so the list is deliberately whole.
        libx11-6 \
        libx11-xcb1 \
        libxext6 \
        libxrender1 \
        libxfixes3 \
        libxi6 \
        libxrandr2 \
        libxss1 \
        libxtst6 \
        libxcomposite1 \
        libxcursor1 \
        libxdamage1 \
        libsm6 \
        libice6 \
        libxcb1 \
        libxcb-cursor0 \
        libxcb-glx0 \
        libxcb-icccm4 \
        libxcb-image0 \
        libxcb-keysyms1 \
        libxcb-randr0 \
        libxcb-render-util0 \
        libxcb-render0 \
        libxcb-shape0 \
        libxcb-shm0 \
        libxcb-sync1 \
        libxcb-util1 \
        libxcb-xfixes0 \
        libxcb-xinerama0 \
        libxcb-xinput0 \
        libxcb-xkb1 \
        libxkbcommon0 \
        libxkbcommon-x11-0 \
        libasound2 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libatspi2.0-0 \
        libcups2 \
        libdrm2 \
        libgbm1 \
        libegl1 \
        libgl1 \
        libpango-1.0-0 \
        libcairo2 \
        libdbus-1-3 \
        libfontconfig1 \
        libfreetype6 \
        libvulkan1 \
        fonts-dejavu-core \
        fonts-liberation \
    # Headless X for the GUI
        xvfb \
        xauth \
        procps \
    # Optional: forward a published port to the app's loopback listener.
        socat \
    # SearXNG's own runtime
        valkey-server \
    && rm -rf /var/lib/apt/lists/*

# SearXNG is not meaningfully packaged on PyPI (the 0.1.2 wheel there is
# a 26 kB placeholder), so it is installed from source the same way the
# upstream image does it.
ARG SEARXNG_REF=master
RUN git clone --depth 1 --branch "${SEARXNG_REF}" \
        https://github.com/searxng/searxng.git /usr/local/searxng \
    # searx's setup.py imports the package itself (searx/__init__.py ->
    # msgspec, setup.py -> yaml), so its own runtime requirements have to
    # be importable before pip can even work out its metadata.
    # --no-build-isolation stops pip from hiding them in an overlay that
    # does not have them.
    && pip install --no-cache-dir -r /usr/local/searxng/requirements.txt \
    && pip install --no-cache-dir setuptools wheel \
    && pip install --no-cache-dir --no-build-isolation /usr/local/searxng \
    && rm -rf /usr/local/searxng/.git

# Node runtime for the ad-block engine. Debian's nodejs is new enough for
# the engine (>= 18); the engine itself lives in the repo and installs its
# own npm dependencies at build time.
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/bfsb

# Dependencies first so application edits do not invalidate the wheel layer.
# PyQt6 and PyQt6-WebEngine are abi3 wheels, so they resolve for 3.14.
COPY pyproject.toml ./
COPY bfsb/__init__.py ./bfsb/__init__.py
RUN pip install --no-cache-dir \
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
        gunicorn

# The application itself.
COPY bfsb/ ./bfsb/
COPY ghostery-adblocker/ ./ghostery-adblocker/
COPY package.json package-lock.json ./
COPY bfsb_supervisor.py bfsb_launcher.sh ./

# The ad-block engine's node dependencies. node_modules is not committed,
# so this is the step a fresh clone used to be missing.
RUN npm install --no-audit --no-fund --omit=dev \
    && npm cache clean --force

# Unprivileged runtime user. The vault writes its key file under $HOME,
# so that directory has to exist and be owned before the drop.
RUN useradd --create-home --home-dir /home/binwalk --shell /bin/bash binwalk \
    && mkdir -p /home/binwalk/.bfsb /tmp/runtime \
    && chown -R binwalk:binwalk /home/binwalk /opt/bfsb /tmp/runtime
USER binwalk

ENV BFSB_APP_DIR=/opt/bfsb \
    BFSB_SEARXNG_URL=http://127.0.0.1:8888 \
    BFSB_HEADLESS=1

EXPOSE 8889 8228 8888

# The supervisor is PID 1 and reaps every child it starts.
ENTRYPOINT ["python3", "/opt/bfsb/bfsb_supervisor.py"]
