# The published image is this repo's own tag over the upstream CPU server, with
# the speech service in proxy/ in front of it.
FROM ghcr.io/remsky/kokoro-fastapi-cpu:latest
LABEL org.opencontainers.image.title="kokoro-voice-server"
LABEL org.opencontainers.image.description="Kokoro TTS voice server for the OpenCode Android app (OpenAI-compatible /v1/audio/speech, optional model rewrite)"
LABEL org.opencontainers.image.source="https://github.com/TechnicalHeist/kokoro-voice-server"
LABEL org.opencontainers.image.licenses="Apache-2.0"

ENV KOKORO_PORT=8880 \
    KOKORO_INTERNAL_PORT=8881 \
    KOKORO_UPSTREAM=http://127.0.0.1:8881

COPY --chown=appuser:appuser start-voice-server.sh /app/start-voice-server.sh
COPY --chown=appuser:appuser proxy /app/proxy

EXPOSE 8880
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8880/health || exit 1

ENTRYPOINT ["/app/start-voice-server.sh"]
