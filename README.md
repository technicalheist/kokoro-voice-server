# Kokoro voice server for the OpenCode Android app

A self-hosted, OpenAI-compatible text-to-speech server, plus how to point the
OpenCode Android app at it so voice previews and the key-free local call speak
with your own voice instead of a paid API.

It can also rewrite the text first: ask for a new generation and the request goes
to a chat model, and the model's answer is spoken instead of the text you sent.

Built on [remsky/Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) — this
repo adds the speech service in `proxy/`, the container, and the phone-side
wiring. Kokoro's own code is upstream's.

Tested with: OnePlus API 36 phone, Ubuntu server, CPU-only Kokoro, Tailscale +
nginx + Let's Encrypt.

## 1. Run it

```bash
cp .env.example .env        # then fill in the model settings (optional)
docker compose up -d
curl -s http://127.0.0.1:8880/health      # {"status":"healthy"}
```

The published image is `ghcr.io/technicalheist/kokoro-voice-server:latest`;
`docker compose build` builds the same thing locally.

Two processes run inside the container:

| Port | What | Reachable |
| --- | --- | --- |
| 8880 | this repo's speech service (`proxy/main.py`) | from the phone |
| 8881 | Kokoro itself, unchanged | loopback only |

Everything that is not a speech request is passed straight through, so
`/v1/models`, `/v1/audio/voices`, `/docs` and the web player behave as before.

It speaks OpenAI's shape:

```bash
curl -s -X POST http://127.0.0.1:8880/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"kokoro","voice":"af_heart","input":"Hello.","response_format":"wav"}' \
  -o hello.wav
```

WAV answers are **mono, 24 kHz, 16-bit** — the format the app's call audio uses,
so nothing is resampled. MP3 is also available; the app asks for WAV.

## 2. Rewrite the text before speaking (optional)

Add `new_generation` to the same request and the text goes to a chat model first;
the answer is what gets spoken:

```bash
curl -s -X POST http://127.0.0.1:8880/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"kokoro","voice":"af_heart","input":"hi","response_format":"wav","new_generation":1}' \
  -D headers.txt -o reply.wav
```

- `new_generation: 1|true|"true"|"yes"` → rewrite, then speak the answer.
- `new_generation: 0|false` or the key absent → speak the text exactly as sent.
- The accepted spellings are `new_generation`, `newGeneration` and `generate`.
- The spoken answer comes back in the `X-Generated-Text` header, base64 encoded
  (`X-Generated-Text-Encoding: base64`), so a client can show what was said.
- A per-request system prompt may be set in the `X-Generation-System` header.

The default prompt makes the model a first-responder, not an answerer: a
greeting gets a warm one-liner, and a task or long instruction gets a short
"let me check and come back to you" instead of a guess. Every answer is trimmed
to `GENERATION_MAX_CHARS` (160 by default), so it stays one quick spoken line.

Settings live in `.env`:

| Key | Meaning |
| --- | --- |
| `OPENAI_API_KEY` | key for the chat model; stays on the server |
| `OPENAI_BASE_URL` | any OpenAI-compatible base URL, e.g. `https://api.openai.com/v1` |
| `OPENAI_MODEL` | model name, e.g. `gpt-4o-mini` |
| `OPENAI_PATH` | appended to the base URL: `/chat/completions` (default) or `/responses`, e.g. `https://opencode.ai/inference/go` + `/openai/v1/chat/completions` or `/openai/v1/responses`; a full URL also works |
| `OPENAI_SESSION` | optional session header some gateways demand; generated when empty |
| `GENERATION_SYSTEM_PROMPT` | optional; defaults to the first-responder prompt above |
| `GENERATION_MAX_CHARS` | spoken answer cap, default 160 |
| `GENERATION_MAX_TOKENS` | model answer budget, default 800: reasoning models spend most of it thinking |
| `GENERATION_REASONING_EFFORT` | `minimal` (default, fastest), `low`, `medium`, `high` or empty; Responses API only |
| `GENERATION_TEMPERATURE` | chat completions only, default 0.7 |
| `GENERATION_TIMEOUT` | seconds, default 60 |

Both request shapes are supported, so a provider that answers chat completions
and one that answers the Responses API both work; pick the path and it follows.
A reasoning model that says it ran out of tokens needs a bigger
`GENERATION_MAX_TOKENS`; the error names this.

With no key set, plain speech still works and a rewrite answers `502` with
`{"detail":"OPENAI_API_KEY is empty"}` — the failure never blocks normal use.
If the model errors, the request also fails with `502` rather than speaking the
wrong thing.

## 3. Put it behind HTTPS

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

## 4. Point the app at it

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

The app does not send `new_generation` yet: today it speaks the text it is given.

## 5. Voices

`GET /v1/audio/voices` lists 72 ids. American English is `af_*` (female) and
`am_*` (male); British is `bf_*`/`bm_*`. Good defaults: `af_heart` (warm),
`af_bella`, `am_michael`, `bm_george`.

## 6. Security

- The server has **no authentication**. Anyone who can reach the URL can use it.
  Keep it on a private network (Tailscale/WireGuard) or put auth in the proxy.
- The model key lives in `.env` on the server and is only sent to the base URL
  you configure; it is never returned to a client.
- It stores no data and keeps no logs of text by default; nginx logs only the
  request line. Do not log request bodies.
- Never commit `.env`, certificates or keys; `.gitignore` covers them.

## 7. Notes

- CPU-only synthesis on a small server runs roughly 0.3–1× real time for a
  sentence; long replies are the slow part, which is why the app chunks them.
- A rewrite adds one model round trip before the first audio byte.
- First request after a restart is slower (model load).
- GPU image: swap `docker-compose.yml`'s image for `kokoro-fastapi-gpu` and set
  `USE_GPU=true` (needs the NVIDIA container runtime).
