#!/usr/bin/env bash
# Replaces the running Kokoro container with the published image, on the same port.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IMAGE="${1:-ghcr.io/technicalheist/kokoro-voice-server:latest}"
ENV_FILE="${ENV_FILE:-$HERE/.env}"

if [ ! -f "$ENV_FILE" ]; then
  echo "missing $ENV_FILE: copy .env.example to .env and fill it in" >&2
  exit 1
fi

docker pull "$IMAGE"
docker rm -f kokoro-fastapi || true
docker run -d --name kokoro-fastapi --restart unless-stopped --network host \
  --env-file "$ENV_FILE" \
  -e USE_GPU=false -e DEVICE=cpu "$IMAGE"
for _ in $(seq 1 150); do
  if curl -fsS http://127.0.0.1:8880/health >/dev/null; then echo "healthy"; exit 0; fi
  sleep 2
done
echo "not healthy" >&2
docker logs --tail 40 kokoro-fastapi >&2
exit 1
