#!/bin/bash
# Kokoro keeps its own port; this service owns the public one and decides
# whether the text is spoken as sent or rewritten by a model first.
set -euo pipefail

export PORT="${KOKORO_INTERNAL_PORT:-8881}"

/app/entrypoint.sh &
engine=$!

for _ in $(seq 1 150); do
    if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
        break
    fi
    if ! kill -0 "$engine" 2>/dev/null; then
        echo "kokoro exited before becoming healthy" >&2
        exit 1
    fi
    sleep 2
done

exec python -m uvicorn proxy.main:app --host 0.0.0.0 --port "${KOKORO_PORT:-8880}"
