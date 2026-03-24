import asyncio, time, uuid, base64, tempfile, os, logging, sys
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import StreamingResponse, JSONResponse
from models import (
    ChatCompletionRequest, ChatCompletionResponse, ChatCompletionChoice,
    ChatCompletionChunk, ChatCompletionChunkChoice, ChatCompletionChunkDelta,
    ChatMessage, UsageInfo, ModelList, ModelInfo,
    AnthropicRequest, AnthropicResponse, AnthropicContentBlock, AnthropicUsage,
    AgentStartRequest, AgentStartResponse, AgentStopResponse, AgentPauseResponse,
    AgentResumeResponse, AgentTaskRequest, AgentTaskResponse,
    AgentStatusResponse, AgentHistoryResponse, AgentClearHistoryResponse,
)
from copilot_backend import CopilotBackend, get_connection_pool, get_cache_stats
from circuit_breaker import get_circuit_breaker
from agent_manager import (
    get_agent_manager,
    list_agent_api_sessions,
    agent_registry_reaper_loop,
)
from token_counting import count_tokens, truncate_by_approx_tokens
import config

logger = logging.getLogger(__name__)

# ── Per-agent session registry ────────────────────────────────────────────────
# Containers send X-Agent-ID header; each ID gets its own dedicated
# CopilotBackend so conversations stay isolated across C2/C4/C5/C6.
_agent_sessions: dict[str, CopilotBackend] = {}          # type: ignore[name-defined]
_agent_session_last_used: dict[str, float] = {}
_agent_session_lock: asyncio.Lock | None = None
AGENT_SESSION_TTL: int = int(os.getenv("AGENT_SESSION_TTL", "1800"))   # 30 min idle expiry


def _get_session_lock() -> asyncio.Lock:
    global _agent_session_lock
    if _agent_session_lock is None:
        _agent_session_lock = asyncio.Lock()
    return _agent_session_lock


async def _get_or_create_agent_session(agent_id: str) -> "CopilotBackend":
    """Return the dedicated backend for agent_id, creating it on first call."""
    lock = _get_session_lock()
    async with lock:
        if agent_id not in _agent_sessions:
            backend = CopilotBackend()       # type: ignore[name-defined]
            _agent_sessions[agent_id] = backend
            logger.info("Agent session created: %s (total active: %d)", agent_id, len(_agent_sessions))
        _agent_session_last_used[agent_id] = time.time()
    return _agent_sessions[agent_id]


async def _noop_release(backend: "CopilotBackend") -> None:
    """Agent session backends are persistent — never returned to the pool."""
    pass


async def _session_reaper() -> None:
    """Background task: removes agent sessions idle longer than AGENT_SESSION_TTL."""
    while True:
        await asyncio.sleep(300)   # check every 5 minutes
        now = time.time()
        lock = _get_session_lock()
        async with lock:
            stale = [
                aid for aid, last in _agent_session_last_used.items()
                if now - last > AGENT_SESSION_TTL
            ]
            for aid in stale:
                backend = _agent_sessions.pop(aid, None)
                _agent_session_last_used.pop(aid, None)
                if backend:
                    try:
                        await backend.close()
                    except Exception:
                        pass
                logger.info("Agent session expired (idle): %s", aid)
# Imports moved to top

# ── Fast JSON serialization ───────────────────────────────────────────────────
# orjson is 2-3× faster than stdlib json for small SSE payloads.
# Falls back to stdlib so local dev without orjson still works.
try:
    import orjson as _orjson
    def _dumps(obj: dict) -> str:
        return _orjson.dumps(obj).decode()
except ImportError:
    import json as _json_fallback
    def _dumps(obj: dict) -> str:
        return _json_fallback.dumps(obj)

# ── Rate limiter (slowapi) ────────────────────────────────────────────────────
# Falls back gracefully if slowapi is not installed.
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded

    _limiter = Limiter(key_func=get_remote_address, default_limits=[config.RATE_LIMIT] if config.RATE_LIMIT else [])
    _rate_limiting_enabled = bool(config.RATE_LIMIT)
except ImportError:
    _limiter = None
    _rate_limiting_enabled = False


app = FastAPI(title="Copilot OpenAI-Compatible API", version="1.1.0")

# ── GZip (compress JSON/SSE bodies above threshold) ──────────────────────────
from starlette.middleware.gzip import GZipMiddleware

app.add_middleware(GZipMiddleware, minimum_size=500)

# ── CORS Middleware ──────────────────────────────────────────────────────────
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict this to internal container hostnames
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Agent-ID", "x-generation-params-note"],
)

if _limiter:
    app.state.limiter = _limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ══════════════════════════════════════════════════════════════════════
# Startup / Shutdown lifecycle
# ══════════════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup_event():
    """Pre-warm the connection pool and start the agent session reaper."""
    pool = get_connection_pool()
    warm_tasks = [_warm_one(pool) for _ in range(config.POOL_WARM_COUNT)]
    await asyncio.gather(*warm_tasks, return_exceptions=True)
    asyncio.create_task(_session_reaper())
    asyncio.create_task(agent_registry_reaper_loop())


async def _warm_one(pool):
    try:
        b = CopilotBackend()
        await pool.release(b)
    except Exception:
        pass


@app.on_event("shutdown")
async def shutdown_event():
    from copilot_backend import close_connection_pool
    await close_connection_pool()


# ══════════════════════════════════════════════════════════════════════
# Utility helpers
# ══════════════════════════════════════════════════════════════════════

def _truncate_context_chars(text: str) -> str:
    m = config.MAX_CONTEXT_CHARS
    if len(text) <= m:
        return text
    sys_prefix = ""
    rest = text
    if rest.startswith("[System]:"):
        br = rest.find("\n")
        if br != -1:
            sys_prefix = rest[: br + 1]
            rest = rest[br + 1 :]
    budget = m - len(sys_prefix) - 40
    if budget < 100:
        return text[:m]
    if len(rest) > budget:
        rest = "[...truncated...]\n" + rest[-budget:]
    return sys_prefix + rest


def extract_user_prompt(messages):
    parts = []
    for msg in messages:
        if msg.role == "system" and msg.content is not None:
            parts.append(f"[System]: {msg.content}")
        elif msg.role == "user":
            if isinstance(msg.content, str):
                parts.append(msg.content)
            elif isinstance(msg.content, list):
                for p in msg.content:
                    if p.type == "text" and p.text:
                        parts.append(p.text)
        elif msg.role == "assistant" and msg.content is not None:
            if isinstance(msg.content, str):
                parts.append(f"[Assistant]: {msg.content}")
        elif msg.role == "tool" and msg.content is not None:
            parts.append(f"[Tool]: {msg.content}")
    raw = "\n".join(parts) if parts else ""
    return _truncate_context_chars(raw)


def resolve_chat_style(model_id: str, temperature: float) -> str:
    """Map OpenAI model id + temperature → CopilotBackend.style."""
    if model_id in config.MODEL_MAP:
        return config.MODEL_MAP[model_id]
    if temperature <= 0.3:
        return "precise"
    if temperature <= 0.8:
        return "smart"
    return "creative"


def resolve_anthropic_style(model_id: str, temperature: float | None) -> str:
    t = 0.7 if temperature is None else float(temperature)
    if model_id in config.MODEL_MAP:
        return config.MODEL_MAP[model_id]
    if t <= 0.3:
        return "precise"
    if t <= 0.8:
        return "smart"
    return "creative"


def extract_image(messages):
    """Return path to a temp file containing the first base64 image, or None."""
    for msg in messages:
        if msg.role == "user" and isinstance(msg.content, list):
            for p in msg.content:
                if p.type == "image_url" and p.image_url:
                    url = p.image_url.url
                    if url.startswith("data:"):
                        _, data = url.split(",", 1)
                        raw = base64.b64decode(data)
                        if len(raw) > config.MAX_IMAGE_BYTES:
                            raise HTTPException(
                                status_code=413,
                                detail=f"Image exceeds MAX_IMAGE_BYTES ({config.MAX_IMAGE_BYTES})",
                            )
                        fd, path = tempfile.mkstemp(suffix=".png", dir="/tmp")
                        with os.fdopen(fd, "wb") as f:
                            f.write(raw)
                        logger.info("Extracted image to: %s", path)
                        return path
    return None


def _cleanup_attachment(path):
    """Delete a temp attachment file, ignoring errors."""
    if path and os.path.exists(path):
        try:
            os.unlink(path)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════
# Core API Endpoints
# ══════════════════════════════════════════════════════════════════════

@app.get("/v1/models")
async def list_models():
    ts = int(time.time())
    models = [
        ModelInfo(id="copilot",          object="model", created=ts, owned_by="microsoft"),
        ModelInfo(id="gpt-4",            object="model", created=ts, owned_by="microsoft"),
        ModelInfo(id="gpt-4o",           object="model", created=ts, owned_by="microsoft"),
        ModelInfo(id="copilot-balanced", object="model", created=ts, owned_by="microsoft"),
        ModelInfo(id="copilot-creative", object="model", created=ts, owned_by="microsoft"),
        ModelInfo(id="copilot-precise",  object="model", created=ts, owned_by="microsoft"),
    ]
    return ModelList(object="list", data=models)


@app.post("/v1/chat/completions")
async def create_chat_completion(request: ChatCompletionRequest, raw_request: Request):
    # CRITICAL: Always print to stderr for Docker logs visibility
    print(f"Bounty Hunter Trace: API_ENTRY model={request.model} streaming={request.stream}", file=sys.stderr, flush=True)
    agent_id = raw_request.headers.get("X-Agent-ID")
    prompt = extract_user_prompt(request.messages)
    try:
        attachment = extract_image(request.messages)
    except HTTPException:
        raise
    logger.info("Chat request: prompt='%s', attachment=%s, stream=%s", prompt[:50], attachment, request.stream)
    if not prompt:
        raise HTTPException(status_code=400, detail="No prompt found in messages")
    pool = get_connection_pool()

    # Route to per-agent dedicated backend or shared pool
    if agent_id:
        backend = await _get_or_create_agent_session(agent_id)
        release_fn = _noop_release
    else:
        backend = await pool.acquire()
        backend.style = resolve_chat_style(request.model, request.temperature)
        release_fn = pool.release

    temp_map = "off" if request.model in config.MODEL_MAP else "on"
    gen_note = (
        f"copilot_style={backend.style};temperature_mapping={temp_map};"
        f"max_tokens={'word_truncation' if request.max_tokens else 'none'}"
    )

    # ── Streaming path ────────────────────────────────────────────────
    if request.stream:
        return StreamingResponse(
            stream_gen(
                release_fn, backend, prompt, attachment, request.model, request.max_tokens,
            ),
            media_type="text/event-stream",
            headers={"x-generation-params-note": gen_note},
        )

    # ── Non-streaming path ────────────────────────────────────────────
    try:
        response = await backend.chat_completion(prompt=prompt, attachment_path=attachment)
        sys.stderr.write(f"Bounty Hunter Trace: RESPONSE_CONTENT='{response[:100]}...'\n")
        sys.stderr.flush()
        response, truncated = truncate_by_approx_tokens(response, request.max_tokens)
        if truncated:
            gen_note = gen_note + ";truncated=1"
        img_extra = 85 if attachment else 0
        pt = count_tokens(prompt) + img_extra
        ct = count_tokens(response)
        body = ChatCompletionResponse(
            model=request.model,
            usage=UsageInfo(
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=pt + ct,
            ),
            choices=[ChatCompletionChoice(
                index=0,
                message=ChatMessage(role="assistant", content=response),
                finish_reason="length" if truncated else "stop",
                suggested_responses=backend._last_suggestions,
            )],
        ).model_dump()
        return JSONResponse(content=body, headers={"x-generation-params-note": gen_note})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await release_fn(backend)
        _cleanup_attachment(attachment)


async def stream_gen(release_fn, backend, prompt, attachment, model, max_tokens):
    """SSE generator — releases backend (or no-ops for agent sessions) after streaming."""
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())   # computed once, not per-token
    try:
        init = {
            "id": chat_id, "object": "chat.completion.chunk",
            "created": created, "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
        }
        yield f"data: {_dumps(init)}\n\n"
        # Immediate heartbeat (empty content) to satisfy Playwright/client timeouts
        yield f"data: {_dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {'content': ''}, 'finish_reason': None}]})}\n\n"

        sys.stderr.write(f"Starting stream for prompt='{prompt[:50]}', attachment={attachment}\n")
        sys.stderr.flush()
        end_fr = "stop"
        char_budget = max_tokens * 4 if max_tokens else 0
        chars_sent = 0
        async for token in backend.chat_completion_stream(prompt=prompt, attachment_path=attachment):
            if char_budget:
                chars_sent += len(token)
                if chars_sent > char_budget:
                    overflow = chars_sent - char_budget
                    suffix = token[: len(token) - overflow] if overflow < len(token) else ""
                    if suffix:
                        chunk = {
                            "id": chat_id, "object": "chat.completion.chunk",
                            "created": created, "model": model,
                            "choices": [{"index": 0, "delta": {"content": suffix}, "finish_reason": None}],
                        }
                        yield f"data: {_dumps(chunk)}\n\n"
                    end_fr = "length"
                    break
            chunk = {
                "id": chat_id, "object": "chat.completion.chunk",
                "created": created, "model": model,
                "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}],
            }
            yield f"data: {_dumps(chunk)}\n\n"

        # Final chunk with finish_reason and suggestions
        final = {
            "id": chat_id, "object": "chat.completion.chunk",
            "created": created, "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": end_fr,
                "suggested_responses": backend._last_suggestions,
            }]
        }
        yield f"data: {_dumps(final)}\n\n"
        yield "data: [DONE]\n\n"

    except Exception as e:
        yield f"data: {_dumps({'error': {'message': str(e), 'type': 'server_error'}})}\n\n"
        yield "data: [DONE]\n\n"
    finally:
        await release_fn(backend)
        _cleanup_attachment(attachment)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "copilot-openai-wrapper"}

@app.get("/v1/debug/log-test")
async def log_test():
    print("DEBUG_LOG_TEST_PRINT", flush=True)
    sys.stdout.write("DEBUG_LOG_TEST_STDOUT\n")
    sys.stdout.flush()
    return {"message": "logs sent"}


@app.get("/v1/cache/stats")
async def cache_stats():
    """Returns response-cache hit/miss counters."""
    return get_cache_stats()


@app.get("/v1/sessions", tags=["Agent"])
async def list_agent_sessions():
    """Returns all active per-agent backend sessions (created via X-Agent-ID header)."""
    now = time.time()
    return {
        "sessions": {
            aid: {
                "connected": True,
                "idle_seconds": round(now - _agent_session_last_used.get(aid, now)),
            }
            for aid in _agent_sessions
        },
        "total": len(_agent_sessions),
        "ttl_seconds": AGENT_SESSION_TTL,
    }


# ══════════════════════════════════════════════════════════════════════
# Anthropic-compatible Endpoint  (POST /v1/messages)
# ══════════════════════════════════════════════════════════════════════

def _anthropic_messages_to_prompt(request: AnthropicRequest) -> str:
    """
    Convert Anthropic messages[] + optional system prompt → flat prompt string
    suitable for Copilot's single-turn ask().

    Anthropic format:
        system: "You are a helpful assistant."
        messages: [{"role": "user", "content": "Hello"}, ...]

    We emit:
        [System]: You are a helpful assistant.
        [User]: Hello
        ...
        (last user message is the actual prompt)
    """
    parts: list[str] = []
    if request.system:
        parts.append(f"[System]: {request.system}")
    for msg in request.messages:
        prefix = "[User]" if msg.role == "user" else "[Assistant]"
        if isinstance(msg.content, str):
            parts.append(f"{prefix}: {msg.content}")
        else:
            for block in msg.content:
                if block.text:
                    parts.append(f"{prefix}: {block.text}")
    return "\n".join(parts) if parts else ""


@app.post(
    "/v1/messages",
    response_model=AnthropicResponse,
    tags=["Anthropic-Compatible"],
    summary="Anthropic Messages API (proxied to Copilot)",
    description=(
        "Drop-in replacement for Anthropic's POST /v1/messages endpoint. "
        "Translates Claude-format requests to Microsoft Copilot and returns "
        "responses in Anthropic content[] format. Compatible with the Anthropic "
        "Python SDK, Claude Code, and Cursor in Anthropic mode."
    ),
)
async def anthropic_messages(request: AnthropicRequest, raw_request: Request):
    agent_id = raw_request.headers.get("X-Agent-ID")
    prompt = _anthropic_messages_to_prompt(request)
    if not prompt:
        raise HTTPException(status_code=400, detail="No message content found")

    pool = get_connection_pool()

    # Route to per-agent dedicated backend or shared pool
    if agent_id:
        backend = await _get_or_create_agent_session(agent_id)
        release_fn = _noop_release
    else:
        backend = await pool.acquire()
        backend.style = resolve_anthropic_style(request.model, request.temperature)
        release_fn = pool.release

    # ── Streaming path ──────────────────────────────────────────────────
    if request.stream:
        return StreamingResponse(
            _anthropic_stream_gen(release_fn, backend, prompt, request.model),
            media_type="text/event-stream",
        )

    # ── Non-streaming path ──────────────────────────────────────────────
    try:
        response_text = await backend.chat_completion(prompt=prompt)
        token_in = count_tokens(prompt)
        token_out = count_tokens(response_text)
        sys.stderr.write(f"Bounty Hunter Trace: ANTHROPIC_RESPONSE_CONTENT='{response_text[:100]}...'\n")
        sys.stderr.flush()
        return AnthropicResponse(
            model=request.model,
            content=[AnthropicContentBlock(type="text", text=response_text)],
            usage=AnthropicUsage(input_tokens=token_in, output_tokens=token_out),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await release_fn(backend)


async def _anthropic_stream_gen(release_fn, backend, prompt: str, model: str):
    """SSE generator in Anthropic streaming format."""
    msg_id  = f"msg_{uuid.uuid4().hex[:20]}"

    # message_start
    yield f"data: {_dumps({'type': 'message_start', 'message': {'id': msg_id, 'type': 'message', 'role': 'assistant', 'content': [], 'model': model, 'stop_reason': None, 'usage': {'input_tokens': count_tokens(prompt), 'output_tokens': 0}}})}\n\n"
    # content_block_start
    yield f"data: {_dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
    yield f"data: {_dumps({'type': 'ping'})}\n\n"

    total_chars = 0
    stream_ok = False
    try:
        async for token in backend.chat_completion_stream(prompt=prompt):
            total_chars += len(token)
            yield f"data: {_dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': token}})}\n\n"
        stream_ok = True
    except Exception as exc:
        yield f"data: {_dumps({'type': 'error', 'error': {'type': 'server_error', 'message': str(exc)}})}\n\n"
    finally:
        await release_fn(backend)

    if stream_ok:
        total_tokens = max(1, total_chars // 4)
        yield f"data: {_dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
        yield f"data: {_dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': total_tokens}})}\n\n"
        yield f"data: {_dumps({'type': 'message_stop'})}\n\n"


# ══════════════════════════════════════════════════════════════════════
# Debug & Observability Endpoints
# ══════════════════════════════════════════════════════════════════════

@app.get("/v1/debug/cookie", tags=["Debug"])
async def debug_cookie():
    """
    Returns metadata about the current Bing cookie — age, presence of key
    cookies — WITHOUT exposing the actual cookie values.
    Useful for diagnosing auth issues.
    """
    cookie_str = config.BING_COOKIES or ""
    parts = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, _, v = item.partition("=")
            parts[k.strip()] = len(v.strip())  # value length, not value

    key_cookies = ["_U", "SRCHHPGUSR", "MUID", "MUIDB"]
    return {
        "cookie_present":      bool(cookie_str),
        "total_cookies":       len(parts),
        "key_cookies_present": {k: (k in parts) for k in key_cookies},
        "total_chars":         len(cookie_str),
        "rate_limit_config":   config.RATE_LIMIT or "disabled",
        "circuit_breaker":     get_circuit_breaker(
            config.CIRCUIT_BREAKER_THRESHOLD,
            config.CIRCUIT_BREAKER_TIMEOUT,
        ).get_status(),
    }


@app.get("/v1/debug/circuit-breaker", tags=["Debug"])
async def debug_circuit_breaker():
    """Returns the current circuit breaker state and failure counters."""
    return get_circuit_breaker(
        config.CIRCUIT_BREAKER_THRESHOLD,
        config.CIRCUIT_BREAKER_TIMEOUT,
    ).get_status()


@app.post("/v1/debug/circuit-breaker/reset", tags=["Debug"])
async def reset_circuit_breaker():
    """Manually resets the circuit breaker to CLOSED state."""
    cb = get_circuit_breaker(
        config.CIRCUIT_BREAKER_THRESHOLD,
        config.CIRCUIT_BREAKER_TIMEOUT,
    )
    await cb.reset()
    return {"status": "ok", "message": "Circuit breaker reset to CLOSED."}


@app.post("/v1/cookies/extract", tags=["Admin"])
async def extract_cookies_endpoint():
    """
    Container 1 extracts fresh cookies from the mounted Chrome data directory.

    Requires docker-compose volumes:
      - ~/Library/Application Support/Google/Chrome:/chrome-data:ro
    Requires env vars:
      - CHROME_KEY_PASSWORD  (Chrome Safe Storage Keychain password from host)
      - CHROME_DATA_PATH     (default: /chrome-data)

    After extraction:
      1. Updates BING_COOKIES in the live config
      2. Resets the connection pool so the new cookie is used immediately
      3. Returns extracted service names and cookie counts
    """
    chrome_data = os.getenv("CHROME_DATA_PATH", "/chrome-data")
    chrome_key  = os.getenv("CHROME_KEY_PASSWORD", "")

    if not chrome_key:
        raise HTTPException(
            status_code=503,
            detail="CHROME_KEY_PASSWORD env var not set. "
                   "Run: export CHROME_KEY_PASSWORD=$(security find-generic-password "
                   "-w -s 'Chrome Safe Storage' -a 'Chrome') on the host, "
                   "then restart the container.",
        )
    if not os.path.isdir(chrome_data):
        raise HTTPException(
            status_code=503,
            detail=f"Chrome data not mounted at {chrome_data}. "
                   "Check docker-compose volumes configuration.",
        )

    try:
        from cookie_extractor_linux import extract_cookies, patch_env_file
        import importlib
        import config as _config

        # Run extraction (CPU-bound but fast — do in thread to not block event loop)
        loop = asyncio.get_event_loop()
        cookies = await loop.run_in_executor(
            None, extract_cookies, chrome_data, chrome_key
        )

        found    = {k: v for k, v in cookies.items() if v}
        missing  = [k for k, v in cookies.items() if not v]

        # Update live env + .env file
        for env_key, cookie_str in found.items():
            os.environ[env_key] = cookie_str

        env_path = "/app/.env"
        if found:
            await loop.run_in_executor(None, patch_env_file, env_path, found)
            # Reload config module and reset pool
            from dotenv import load_dotenv
            load_dotenv(override=True)
            importlib.reload(_config)
            from copilot_backend import close_connection_pool
            await close_connection_pool()
            from circuit_breaker import get_circuit_breaker
            await get_circuit_breaker().reset()

        return {
            "status": "ok",
            "extracted": {k: f"{len(v.split(';'))} cookies" for k, v in found.items()},
            "missing":   missing,
            "pool_reset": bool(found),
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/v1/reload-config", tags=["Admin"])
async def reload_config():
    """
    Hot-reload BING_COOKIES (and other env vars) from .env without restarting
    the server.  Called automatically by cookie_manager/service.py after it
    updates the .env file.

    Steps:
      1. Re-read .env into the process environment (override=True).
      2. Reload the config module so BING_COOKIES etc. pick up new values.
      3. Reset the connection pool so the next acquire() uses the new cookie.
    """
    try:
        from dotenv import load_dotenv
        import importlib
        import config as _config

        load_dotenv(override=True)
        importlib.reload(_config)

        from copilot_backend import close_connection_pool, reload_cookies
        reload_cookies()
        await close_connection_pool()

        # Fresh cookies mean the backend should be reachable — reset circuit breaker
        from circuit_breaker import get_circuit_breaker
        await get_circuit_breaker().reset()

        return {"status": "ok", "message": "Config reloaded, pool reset, circuit breaker cleared."}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ══════════════════════════════════════════════════════════════════════
# Agent Management Endpoints
# ══════════════════════════════════════════════════════════════════════

@app.post(
    "/v1/agent/start",
    response_model=AgentStartResponse,
    summary="Start the AI agent",
    description=(
        "Initialises a new agent session backed by a persistent Microsoft Copilot "
        "WebSocket connection. The agent maintains full conversation context across "
        "all subsequent /v1/agent/task calls until stopped."
    ),
    tags=["Agent"],
)
async def agent_start(request: AgentStartRequest = AgentStartRequest()):
    manager = await get_agent_manager(request.session_name)
    try:
        result = await manager.start(system_prompt=request.system_prompt)
        return AgentStartResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/v1/agent/stop",
    response_model=AgentStopResponse,
    summary="Stop the AI agent",
    description=(
        "Gracefully stops the agent, closes the Copilot WebSocket connection, "
        "and returns a summary of tasks completed in the session."
    ),
    tags=["Agent"],
)
async def agent_stop(session_name: str = Query("default")):
    manager = await get_agent_manager(session_name)
    try:
        result = await manager.stop()
        return AgentStopResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/v1/agent/pause",
    response_model=AgentPauseResponse,
    summary="Pause the AI agent",
    description=(
        "Pauses the agent. While paused, task submissions are rejected with 409. "
        "The Copilot session remains open. Call /v1/agent/resume to continue."
    ),
    tags=["Agent"],
)
async def agent_pause(session_name: str = Query("default")):
    manager = await get_agent_manager(session_name)
    try:
        result = await manager.pause()
        return AgentPauseResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/v1/agent/resume",
    response_model=AgentResumeResponse,
    summary="Resume the AI agent",
    description="Resumes a paused agent so it can accept new tasks again.",
    tags=["Agent"],
)
async def agent_resume(session_name: str = Query("default")):
    manager = await get_agent_manager(session_name)
    try:
        result = await manager.resume()
        return AgentResumeResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/v1/agent/task",
    summary="Give the agent a task",
    description=(
        "Submit a task or question to the running agent. "
        "Set `stream: true` to receive a streaming SSE response. "
        "Example: `{\"task\": \"What is 1 + 1?\"}` "
        "The agent maintains full conversation history across tasks in the same session."
    ),
    tags=["Agent"],
)
async def agent_task(request: AgentTaskRequest):
    manager = await get_agent_manager(request.session_name)

    # ── Streaming response ────────────────────────────────────────────
    if request.stream:
        async def sse_stream():
            task_id  = f"task-{uuid.uuid4().hex[:12]}"
            chat_id  = f"agentcmpl-{uuid.uuid4().hex[:20]}"
            created  = int(time.time())
            session  = manager.session_id

            init = {
                "id": chat_id, "task_id": task_id,
                "session_id": session,
                "object": "agent.task.chunk", "created": created,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
            }
            yield f"data: {_dumps(init)}\n\n"

            try:
                async for token in manager.run_task_stream(request.task):
                    chunk = {
                        "id": chat_id, "task_id": task_id,
                        "object": "agent.task.chunk", "created": created,
                        "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}],
                    }
                    yield f"data: {_dumps(chunk)}\n\n"

                end = {
                    "id": chat_id, "task_id": task_id,
                    "object": "agent.task.chunk", "created": created,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {_dumps(end)}\n\n"
                yield "data: [DONE]\n\n"

            except ValueError as exc:
                yield f"data: {_dumps({'error': {'message': str(exc), 'type': 'agent_error'}})}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as exc:
                yield f"data: {_dumps({'error': {'message': str(exc), 'type': 'server_error'}})}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(sse_stream(), media_type="text/event-stream")

    # ── Non-streaming response ────────────────────────────────────────
    try:
        task = await manager.run_task(request.task)
        return AgentTaskResponse(
            task_id=task.task_id,
            session_id=manager.session_id,
            status=task.status.value,
            prompt=task.prompt,
            result=task.result,
            error=task.error,
            created_at=task.created_at.isoformat(),
            completed_at=task.completed_at.isoformat() if task.completed_at else None,
            suggested_responses=task.suggested_responses,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get(
    "/v1/agent/status",
    response_model=AgentStatusResponse,
    summary="Get agent status",
    description="Returns the current status of the agent and task statistics.",
    tags=["Agent"],
)
async def agent_status(session_name: str = Query("default")):
    manager = await get_agent_manager(session_name)
    return AgentStatusResponse(**manager.get_status())


@app.get(
    "/v1/agent/history",
    response_model=AgentHistoryResponse,
    summary="Get agent task history",
    description="Returns the full list of tasks submitted in the current session.",
    tags=["Agent"],
)
async def agent_history(session_name: str = Query("default")):
    manager = await get_agent_manager(session_name)
    tasks = manager.get_history()
    return AgentHistoryResponse(
        session_id=manager.session_id,
        tasks=tasks,
        total=len(tasks),
    )


@app.get(
    "/v1/agent/history/{task_id}",
    summary="Get a specific task by ID",
    description="Returns the details of a single task by its task_id.",
    tags=["Agent"],
)
async def agent_get_task(task_id: str, session_name: str = Query("default")):
    manager = await get_agent_manager(session_name)
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return task


@app.delete(
    "/v1/agent/history",
    response_model=AgentClearHistoryResponse,
    summary="Clear agent task history",
    description="Clears all task history for the current session (does not stop the agent).",
    tags=["Agent"],
)
async def agent_clear_history(session_name: str = Query("default")):
    manager = await get_agent_manager(session_name)
    result = manager.clear_history()
    return AgentClearHistoryResponse(**result)


@app.get(
    "/v1/agent/sessions",
    tags=["Agent"],
    summary="List named agent API sessions",
    description="Returns status for each session_name registry entry (management API).",
)
async def agent_named_sessions():
    return await list_agent_api_sessions()
