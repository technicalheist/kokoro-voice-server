# The published image is this repo's own tag over the upstream CPU server, so the
# server can be pulled from our registry and the setup lives beside it.
FROM ghcr.io/remsky/kokoro-fastapi-cpu:latest
LABEL org.opencontainers.image.title="kokoro-voice-server"
LABEL org.opencontainers.image.description="Kokoro TTS voice server for the OpenCode Android app (OpenAI-compatible /v1/audio/speech)"
LABEL org.opencontainers.image.source="https://github.com/TechnicalHeist/kokoro-voice-server"
LABEL org.opencontainers.image.licenses="Apache-2.0"
