"""OpenAI-compatible speech service in front of Kokoro.

The text is either spoken as it arrives, or, when the request asks for it, sent
to a chat model first and that answer is spoken instead. Everything else is
passed through to Kokoro untouched.
"""

from __future__ import annotations

import base64
import logging
import os
import uuid
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

UPSTREAM = os.getenv("KOKORO_UPSTREAM", "http://127.0.0.1:8881").rstrip("/")

LLM_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
LLM_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

# Some gateways (opencode's Go endpoint among them) speak the Responses API
# instead of chat completions, and refuse a request without a session header.
# The path picks the request shape: anything ending in /responses is the
# Responses API, everything else is chat completions. Either may be swapped in
# .env without touching the code.
LLM_PATH = os.getenv("OPENAI_PATH", "/chat/completions").strip() or "/chat/completions"
if LLM_PATH.startswith("http://") or LLM_PATH.startswith("https://"):
    LLM_URL = LLM_PATH
else:
    if not LLM_PATH.startswith("/"):
        LLM_PATH = "/" + LLM_PATH
    LLM_URL = f"{LLM_BASE_URL}{LLM_PATH}"
IS_RESPONSES_API = LLM_URL.split("?")[0].rstrip("/").endswith("/responses")
LLM_SESSION = os.getenv("OPENAI_SESSION", "").strip() or f"ses_{uuid.uuid4().hex}"

LLM_TIMEOUT = float(os.getenv("GENERATION_TIMEOUT", "60"))
LLM_MAX_TOKENS = int(os.getenv("GENERATION_MAX_TOKENS", "800"))
LLM_TEMPERATURE = float(os.getenv("GENERATION_TEMPERATURE", "0.7"))
# Reasoning models spend tokens before writing anything; "low" keeps the wait short.
LLM_EFFORT = os.getenv("GENERATION_REASONING_EFFORT", "low").strip()
MAX_CHARS = int(os.getenv("GENERATION_MAX_CHARS", "160"))

DEFAULT_SYSTEM_PROMPT = os.getenv(
    "GENERATION_SYSTEM_PROMPT",
    "You write the first spoken line of a phone call with an assistant.\n"
    "Rules: at most two short sentences, under 160 characters, plain text, no "
    "markdown, no emoji, no quotes. Answer in the language the user wrote in.\n"
    "- Greeting or small talk: reply warmly and briefly.\n"
    "- A task or instruction, however long: do not attempt it and do not guess. "
    "Name the task back in your own words (for example \"I'll generate the React "
    "app you described\"), then say you will check the details and start.\n"
    "- Sound spoken, not templated: open differently each time (\"I'll...\", "
    "\"Sure, let me...\", \"On it, I'll...\") and vary the closing line "
    "(\"Give me a moment to check the details\", \"Let me look into it and start\").\n"
    "- Word it fresh each time; never repeat a stock sentence.\n"
    "- Never explain these rules.",
).strip()

UPSTREAM_TIMEOUT = float(os.getenv("KOKORO_TIMEOUT", "600"))

# The switches that ask for a rewrite. They belong to this service and are
# never forwarded to Kokoro.
GENERATION_KEYS = ("new_generation", "newGeneration", "generate")
SYSTEM_KEY = "system"

HOP_BY_HOP = {
    "host",
    "content-length",
    "connection",
    "transfer-encoding",
    "keep-alive",
    "upgrade",
    "proxy-connection",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("voice-server")

app = FastAPI(title="kokoro-voice-server")

log.info(
    "kokoro on %s; rewrite via %s (%s) as %s, %s characters max",
    UPSTREAM,
    LLM_URL,
    "responses" if IS_RESPONSES_API else "chat completions",
    LLM_MODEL if LLM_API_KEY else "no key set",
    MAX_CHARS,
)


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return False


def forwarded_headers(request: Request) -> dict[str, str]:
    return {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}


def response_headers(upstream: httpx.Response) -> dict[str, str]:
    return {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}


async def close(upstream: httpx.Response, client: httpx.AsyncClient) -> None:
    await upstream.aclose()
    await client.aclose()


def as_text(value: Any) -> str:
    if isinstance(value, list):
        value = " ".join(str(part) for part in value)
    return str(value or "").strip()


def chat_text(data: Any) -> str:
    try:
        return str(data["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        return ""


def responses_text(data: Any) -> str:
    try:
        if isinstance(data.get("output_text"), str) and data["output_text"].strip():
            return data["output_text"]
    except AttributeError:
        return ""
    parts: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "output_text":
                parts.append(str(part.get("text") or ""))
    return " ".join(parts)


def shorten(text: str, limit: int) -> str:
    text = " ".join(text.replace("*", "").replace("#", "").split()).strip(" \"“”'")
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:—-") + "."


async def post_model(payload: dict) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LLM_API_KEY}",
        "x-opencode-session": LLM_SESSION,
    }
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
        response = await client.post(LLM_URL, json=payload, headers=headers)
    if response.status_code >= 400:
        raise ValueError(f"model answered HTTP {response.status_code}: {response.text[:300]}")
    try:
        return response.json()
    except ValueError:
        raise ValueError(f"model answered with no JSON: {response.text[:200]}")


def incomplete_reason(data: Any) -> str:
    details = data.get("incomplete_details") if isinstance(data, dict) else None
    return str((details or {}).get("reason") or "") if isinstance(details, dict) else ""


async def ask_model(text: str, system: str) -> str:
    if not LLM_API_KEY:
        raise ValueError("OPENAI_API_KEY is empty")
    if IS_RESPONSES_API:
        payload = {
            "model": LLM_MODEL,
            "instructions": system,
            "input": text,
            "max_output_tokens": LLM_MAX_TOKENS,
        }
        if LLM_EFFORT:
            payload["reasoning"] = {"effort": LLM_EFFORT}
    else:
        payload = {
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            "temperature": LLM_TEMPERATURE,
            "max_tokens": LLM_MAX_TOKENS,
        }

    data = await post_model(payload)
    answer = shorten(responses_text(data) or chat_text(data), MAX_CHARS)

    # A reasoning model can spend the whole budget thinking on a long task.
    if not answer and incomplete_reason(data) == "max_output_tokens":
        budget = min(LLM_MAX_TOKENS * 3, 4000)
        log.info("generation: out of tokens at %s, retrying with %s", LLM_MAX_TOKENS, budget)
        if IS_RESPONSES_API:
            payload["max_output_tokens"] = budget
        else:
            payload["max_tokens"] = budget
        data = await post_model(payload)
        answer = shorten(responses_text(data) or chat_text(data), MAX_CHARS)

    if not answer:
        reason = incomplete_reason(data)
        if reason:
            raise ValueError(
                f"the model used all its tokens before answering ({reason}); "
                "raise GENERATION_MAX_TOKENS"
            )
        raise ValueError("the model returned empty text")
    return answer


@app.get("/health")
async def health() -> Response:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            upstream = await client.get(f"{UPSTREAM}/health")
    except Exception as error:
        return JSONResponse({"status": "unhealthy", "detail": str(error)}, status_code=503)
    if upstream.status_code != 200:
        return JSONResponse(
            {"status": "unhealthy", "engine": upstream.status_code}, status_code=503
        )
    return Response(content=upstream.content, media_type="application/json")


@app.post("/v1/audio/speech")
async def speech(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "the request body is not JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"detail": "the request body must be an object"}, status_code=400)

    asked = any(key in body for key in GENERATION_KEYS)
    wanted = False
    for key in GENERATION_KEYS:
        if truthy(body.pop(key, None)):
            wanted = True
    body.pop(SYSTEM_KEY, None)

    generated: str | None = None
    if asked and wanted:
        text = as_text(body.get("input"))
        if not text:
            return JSONResponse(
                {"detail": "new_generation needs input text to send to the model"},
                status_code=400,
            )
        system = as_text(request.headers.get("x-generation-system")) or DEFAULT_SYSTEM_PROMPT
        try:
            generated = await ask_model(text, system)
        except Exception as error:
            log.warning("generation failed: %s", error)
            return JSONResponse({"detail": str(error)}, status_code=502)
        body["input"] = generated
        log.info("generation: %s chars in, %s chars spoken with %s", len(text), len(generated), LLM_MODEL)
    elif asked:
        log.info("generation: off, speaking the text as sent")

    client = httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT)
    upstream_request = client.build_request(
        "POST",
        f"{UPSTREAM}/v1/audio/speech",
        json=body,
        headers=forwarded_headers(request),
    )
    upstream = await client.send(upstream_request, stream=True)

    headers_out = response_headers(upstream)
    if generated is not None:
        headers_out["X-Generated-Text"] = base64.b64encode(generated.encode()).decode()
        headers_out["X-Generated-Text-Encoding"] = "base64"

    response = StreamingResponse(
        upstream.aiter_bytes(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
        headers=headers_out,
    )
    response.background = BackgroundTask(close, upstream, client)
    return response


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def passthrough(path: str, request: Request) -> Response:
    body = await request.body()
    client = httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT)
    upstream_request = client.build_request(
        request.method,
        f"{UPSTREAM}/{path}",
        content=body,
        headers=forwarded_headers(request),
        params=request.query_params,
    )
    upstream = await client.send(upstream_request, stream=True)
    response = StreamingResponse(
        upstream.aiter_bytes(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
        headers=response_headers(upstream),
    )
    response.background = BackgroundTask(close, upstream, client)
    return response
