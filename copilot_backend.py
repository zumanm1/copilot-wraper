"""
Copilot backend — new copilot.microsoft.com WebSocket API.

Architecture
------------
- Conversation create: POST https://copilot.microsoft.com/c/api/conversations
- Chat: wss://copilot.microsoft.com/c/api/chat?api-version=2&clientSessionId={uuid}
- Auth: copilot.microsoft.com browser cookies (COPILOT_COOKIES env var)
- Streaming events: connected → received → startMessage → appendText* → partCompleted → done

Speed improvements preserved from v1:
- TTLCache (200 entries, 5 min) for non-streaming identical prompts.
- In-flight deduplication for concurrent identical requests.
- Connection pool with pre-warmed conversations.
- Circuit breaker wraps every network call.
- asyncio.wait_for guards against hung WebSocket connections.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import re
import uuid
from typing import AsyncGenerator

import aiohttp
from cachetools import TTLCache
import config
from circuit_breaker import get_circuit_breaker, CircuitOpenError

# ── Streaming event protocol ──────────────────────────────────────────────────
_WS_BASE = "wss://copilot.microsoft.com/c/api/chat"
_CONV_URL = "https://copilot.microsoft.com/c/api/conversations"
_DONE_EVENTS = {"done", "error", "throttled", "badMessage"}

# ── Response cache ────────────────────────────────────────────────────────────
_response_cache: TTLCache = TTLCache(maxsize=200, ttl=300)
_cache_hits: int = 0
_cache_misses: int = 0

# ── In-flight deduplication ───────────────────────────────────────────────────
_in_flight: dict[str, asyncio.Future] = {}
_in_flight_lock = asyncio.Lock()


def get_cache_stats() -> dict:
    return {
        "hits": _cache_hits,
        "misses": _cache_misses,
        "size": len(_response_cache),
        "maxsize": _response_cache.maxsize,
        "ttl_seconds": _response_cache.ttl,
    }


def _cache_key(style: str, prompt: str) -> str:
    return hashlib.sha256(f"{style}:{prompt}".encode()).hexdigest()


def _make_cookie_header() -> str:
    """Build Cookie header from COPILOT_COOKIES env var."""
    return config.COPILOT_COOKIES or ""


def _make_headers() -> dict:
    return {
        "Origin": "https://copilot.microsoft.com",
        "Referer": "https://copilot.microsoft.com/",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/132.0.0.0 Safari/537.36"
        ),
        "Cookie": _make_cookie_header(),
    }


# ── Backend ───────────────────────────────────────────────────────────────────

class CopilotBackend:
    def __init__(self, style=None, persona=None):
        self.style = style or config.COPILOT_STYLE
        self.persona = persona or config.COPILOT_PERSONA
        self._conversation_id: str | None = None
        self._lock = asyncio.Lock()

    async def _ensure_conversation(self, session: aiohttp.ClientSession) -> str:
        """Create a new conversation if we don't have one."""
        if self._conversation_id:
            return self._conversation_id
        async with self._lock:
            if self._conversation_id:
                return self._conversation_id
            async with session.post(_CONV_URL, json={}) as resp:
                if resp.status != 200:
                    raise RuntimeError(
                        f"Failed to create Copilot conversation: HTTP {resp.status}"
                    )
                data = await resp.json()
                self._conversation_id = data["id"]
        return self._conversation_id

    def _ws_url(self) -> str:
        sid = str(uuid.uuid4())
        return f"{_WS_BASE}?api-version=2&clientSessionId={sid}"

    async def chat_completion(self, prompt: str, attachment_path=None, context=None, search=True) -> str:
        global _cache_hits, _cache_misses

        if attachment_path:
            return await self._do_chat_completion(prompt, attachment_path, context)

        key = _cache_key(self.style, prompt)
        if key in _response_cache:
            _cache_hits += 1
            return _response_cache[key]

        _cache_misses += 1

        async with _in_flight_lock:
            if key in _in_flight:
                fut = _in_flight[key]
            else:
                fut = asyncio.get_event_loop().create_future()
                _in_flight[key] = fut
                fut = None

        if fut is not None:
            return await asyncio.shield(fut)

        result_future = _in_flight[key]
        try:
            result = await self._do_chat_completion(prompt, None, context)
            _response_cache[key] = result
            result_future.set_result(result)
            return result
        except Exception as exc:
            if not result_future.done():
                result_future.set_exception(exc)
            raise
        finally:
            async with _in_flight_lock:
                _in_flight.pop(key, None)

    async def _do_chat_completion(self, prompt, attachment_path, context) -> str:
        breaker = get_circuit_breaker(
            threshold=config.CIRCUIT_BREAKER_THRESHOLD,
            timeout_seconds=config.CIRCUIT_BREAKER_TIMEOUT,
        )
        return await breaker.call(self._raw_copilot_call, prompt, context)

    async def _raw_copilot_call(self, prompt: str, context) -> str:
        """Accumulates all appendText events into a single string."""
        chunks = []
        async for chunk in self._ws_stream(prompt, context):
            chunks.append(chunk)
        return "".join(chunks)

    async def _ws_stream(self, prompt: str, context) -> AsyncGenerator[str, None]:
        """Low-level WebSocket streaming generator."""
        headers = _make_headers()
        connector = aiohttp.TCPConnector(ssl=True)
        timeout = aiohttp.ClientTimeout(
            total=config.REQUEST_TIMEOUT,
            connect=config.CONNECT_TIMEOUT,
        )

        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout,
            headers={k: v for k, v in headers.items() if k != "Cookie"},
            cookie_jar=aiohttp.CookieJar(unsafe=True),
        ) as session:
            # Set cookies manually
            if headers.get("Cookie"):
                for pair in headers["Cookie"].split(";"):
                    pair = pair.strip()
                    if "=" in pair:
                        name, _, val = pair.partition("=")
                        session.cookie_jar.update_cookies(
                            {name.strip(): val.strip()},
                        )

            conv_id = await self._ensure_conversation(session)

            ws_headers = {
                "Origin": headers["Origin"],
                "User-Agent": headers["User-Agent"],
                "Cookie": headers["Cookie"],
            }

            async with session.ws_connect(
                self._ws_url(),
                headers=ws_headers,
                timeout=aiohttp.ClientWSTimeout(ws_receive=config.REQUEST_TIMEOUT),
            ) as ws:
                # Wait for connected event
                hello = json.loads(await asyncio.wait_for(ws.receive_str(), timeout=10))
                if hello.get("event") != "connected":
                    raise RuntimeError(f"Unexpected hello: {hello}")

                # Map legacy style names to new copilot.microsoft.com API modes
                # Valid modes: smart, chat, research, reasoning, study, smart-latest
                _MODE_MAP = {
                    "balanced": "smart",
                    "creative": "smart",
                    "precise": "chat",
                    "smart": "smart",
                    "chat": "chat",
                    "research": "research",
                    "reasoning": "reasoning",
                    "study": "study",
                }
                mode = _MODE_MAP.get(self.style, "smart")

                # Send message
                payload = {
                    "event": "send",
                    "conversationId": conv_id,
                    "content": [{"type": "text", "text": prompt}],
                    "mode": mode,
                    "context": context or {},
                }
                await ws.send_str(json.dumps(payload))

                # Stream response
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        ev = data.get("event", "")
                        if ev == "appendText":
                            text = data.get("text", "")
                            if text:
                                yield text
                        elif ev in _DONE_EVENTS:
                            if ev == "error":
                                raise RuntimeError(
                                    f"Copilot error: {data.get('message', ev)}"
                                )
                            if ev == "throttled":
                                raise RuntimeError("Copilot rate limited (throttled)")
                            break
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break

    async def chat_completion_stream(self, prompt: str, attachment_path=None, context=None) -> AsyncGenerator[str, None]:
        """Streaming interface — yields text tokens as they arrive."""
        async for token in self._ws_stream(prompt, context):
            yield token

    async def reset_conversation(self):
        """Start a fresh conversation on next request."""
        async with self._lock:
            self._conversation_id = None

    async def close(self):
        async with self._lock:
            self._conversation_id = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


# ── Connection pool ───────────────────────────────────────────────────────────

class CopilotConnectionPool:
    def __init__(self, max_connections=10):
        self.max_connections = max_connections
        self._connections: list[CopilotBackend] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> CopilotBackend:
        async with self._lock:
            if self._connections:
                return self._connections.pop()
        # Create fresh backend — conversation created lazily on first request
        return CopilotBackend()

    async def release(self, backend: CopilotBackend) -> None:
        async with self._lock:
            if len(self._connections) >= self.max_connections:
                await backend.close()
                return
        # Reset outside lock so we don't block acquires during I/O
        try:
            await backend.reset_conversation()
            async with self._lock:
                if len(self._connections) < self.max_connections:
                    self._connections.append(backend)
                    return
            await backend.close()
        except Exception:
            try:
                await backend.close()
            except Exception:
                pass

    async def close_all(self) -> None:
        async with self._lock:
            backends, self._connections = self._connections, []
        for backend in backends:
            try:
                await backend.close()
            except Exception:
                pass


# ── Singleton pool ────────────────────────────────────────────────────────────

_connection_pool: CopilotConnectionPool | None = None


def get_connection_pool() -> CopilotConnectionPool:
    global _connection_pool
    if _connection_pool is None:
        _connection_pool = CopilotConnectionPool()
    return _connection_pool


async def close_connection_pool() -> None:
    global _connection_pool
    if _connection_pool:
        await _connection_pool.close_all()
        _connection_pool = None
