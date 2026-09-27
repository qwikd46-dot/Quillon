#!/bin/sh
# Copy config from mounted volume to working directory
cp /config/config.yml /config.yml
# Run invidious with the copied config
exec /invidious/invidious --config /config.yml "$@"