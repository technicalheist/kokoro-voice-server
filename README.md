# Kokoro voice server for the OpenCode Android app

A self-hosted, OpenAI-compatible text-to-speech server, plus how to point the
OpenCode Android app at it so voice previews and the key-free local call speak
with your own voice instead of a paid API.

Built on [remsky/Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) — this
repo is the deployment and the phone-side wiring, not a fork of that code.

Tested with: OnePlus API 36 phone, Ubuntu server, CPU-only Kokoro, Tailscale +
nginx + Let's Encrypt.

## 1. Run Kokoro (CPU)

```bash
docker compose up -d
curl -s http://127.0.0.1:8880/health      # {"status":"healthy"}
```

The server listens on port 8880. It speaks OpenAI's shape:

```bash
curl -s -X POST http://127.0.0.1:8880/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"kokoro","voice":"af_heart","input":"Hello.","response_format":"wav"}' \
  -o hello.wav
```

WAV answers are **mono, 24 kHz, 16-bit** — the format the app's call audio uses,
so nothing is resampled. MP3 is also available; the app asks for WAV.

Useful routes: `/v1/models`, `/v1/audio/voices`, `/v1/audio/speech`,
`/health`.

## 2. Put it behind HTTPS

Phones want a real certificate. `nginx.conf.example` is a working reverse proxy;
`proxy_buffering off` and a long read timeout matter for long replies.

```bash
sudo cp nginx.conf.example /etc/nginx/sites-available/kokoro.example.com
sudo ln -s /etc/nginx/sites-available/kokoro.example.com /etc/nginx/sites-enabled/
sudo certbot --nginx -d kokoro.example.com
sudo nginx -t && sudo systemctl reload nginx
```

If the phone and server are on a private network (Tailscale, WireGuard), plain
HTTP on the private address also works — the app accepts any base URL.

## 3. Point the app at it

Settings → Voice → Voice Provider → **Speech Server**, e.g.
`https://kokoro.example.com`. Leave it empty to use the provider's paid API.

What the app does with that address:

| Step | Value |
| --- | --- |
| URL | `<server>/v1/audio/speech` |
| model | `kokoro` |
| voice | the app's voice name mapped to a Kokoro id (`marin` → `af_heart`, `alloy` → `af_alloy`, …) |
| format | `wav` (mono 24 kHz PCM16) |
| auth | none, unless a key is also set |

The same setting switches on the **local call**: with no OpenAI/xAI key and a
speech server set, the call button runs listen → prompt the session → speak the
reply, sentence by sentence, two sentences ahead.

## 4. Voices

`GET /v1/audio/voices` lists 72 ids. American English is `af_*` (female) and
`am_*` (male); British is `bf_*`/`bm_*`. Good defaults: `af_heart` (warm),
`af_bella`, `am_michael`, `bm_george`.

## 5. Security

- The server has **no authentication**. Anyone who can reach the URL can use it.
  Keep it on a private network (Tailscale/WireGuard) or put auth in the proxy.
- It stores no data and keeps no logs of text by default; nginx logs only the
  request line. Do not log request bodies.
- Never commit `.env`, certificates or keys; `.gitignore` covers them.

## 6. Notes

- CPU-only synthesis on a small server runs roughly 0.3–1× real time for a
  sentence; long replies are the slow part, which is why the app chunks them.
- First request after a restart is slower (model load).
- GPU image: swap `docker-compose.yml`'s image for `kokoro-fastapi-gpu` and set
  `USE_GPU=true` (needs the NVIDIA container runtime).
