# Use the official Invidious image as base
FROM quay.io/invidious/invidious:latest

# Expose the default port
EXPOSE 3000

# Run the Invidious server with config mounted at /config.yml
ENTRYPOINT ["/invidious/invidious", "--config", "/config.yml"]