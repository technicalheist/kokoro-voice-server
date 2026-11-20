#!/usr/bin/env bash
# Replaces the running Kokoro container with the published image, on the same port.
set -euo pipefail
IMAGE="${1:-ghcr.io/technicalheist/kokoro-voice-server:latest}"
docker pull "$IMAGE"
docker tag "$IMAGE" ghcr.io/remsky/kokoro-fastapi-cpu:latest
docker rm -f kokoro-fastapi || true
docker run -d --name kokoro-fastapi --restart unless-stopped --network host \
  -e USE_GPU=false -e DEVICE=cpu "$IMAGE"
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8880/health >/dev/null; then echo "healthy"; exit 0; fi
  sleep 2
done
echo "not healthy" >&2
docker logs --tail 40 kokoro-fastapi >&2
exit 1
