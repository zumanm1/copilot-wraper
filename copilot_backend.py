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
import uuid
import sys
from typing import AsyncGenerator

import aiohttp
import logging
from cachetools import TTLCache
import config
from circuit_breaker import get_circuit_breaker, CircuitOpenError

# ── Logging ──────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")

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

# ── Shared TLS connector (reuse TCP to copilot.microsoft.com; sessions stay per-call for cookie safety)
_shared_connector: aiohttp.TCPConnector | None = None
_connector_lock = asyncio.Lock()


async def _get_shared_connector() -> aiohttp.TCPConnector:
    global _shared_connector
    async with _connector_lock:
        if _shared_connector is None or _shared_connector.closed:
            kwargs: dict = {
                "ssl": True,
                "limit": 20,
                "enable_cleanup_closed": True,
            }
            try:
                _shared_connector = aiohttp.TCPConnector(**kwargs, tcp_nodelay=True)
            except TypeError:
                _shared_connector = aiohttp.TCPConnector(**kwargs)
        return _shared_connector


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
    """Build Cookie header from environment (allows real-time updates)."""
    return os.getenv("COPILOT_COOKIES") or os.getenv("BING_COOKIES") or ""


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
        self._last_suggestions: list[str] = []

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

        try:
            return await breaker.call(self._raw_copilot_call, prompt, context, attachment_path)
        except RuntimeError as exc:
            msg = str(exc).lower()
            # If we hit a challenge or auth failure, try to trigger a refresh
            if "verification required" in msg or "unauthorized" in msg or "handshake" in msg:
                logger.info("Auth failure/Challenge detected. Triggering automated cookie refresh...")
                try:
                    async with aiohttp.ClientSession() as session:
                        # Call the browser-auth extract endpoint (internal Docker network)
                        async with session.post("http://browser-auth:8001/extract", timeout=15) as resp:
                            if resp.status == 200:
                                logger.info("Cookie refresh successful. Waiting 5s for persistent sync...")
                                # Increased delay for file system / cluster sync
                                await asyncio.sleep(5)
                                # Start a fresh conversation after refresh
                                await self.reset_conversation()
                                logger.info("Retrying chat completion after refresh...")
                                # Retry once
                                return await breaker.call(self._raw_copilot_call, prompt, context, attachment_path)
                            else:
                                logger.error("Cookie refresh failed: HTTP %s", resp.status)
                except Exception as refresh_exc:
                    logger.error("Automated cookie refresh failed: %s", refresh_exc)
            
            # If not a challenge or refresh failed, re-raise
            raise

    async def _raw_copilot_call(self, prompt: str, context, attachment_path=None) -> str:
        """Accumulates all appendText events into a single string."""
        chunks = []
        async for chunk in self._ws_stream(prompt, context, attachment_path):
            chunks.append(chunk)
        return "".join(chunks)

    async def _ws_stream(self, prompt: str, context, attachment_path=None) -> AsyncGenerator[str, None]:
        """Low-level WebSocket streaming generator."""
        logger.info("WS_STREAM: prompt='%s', attachment=%s", prompt[:50], attachment_path)
        
        # Always re-read cookies from environment to ensure latest C3 sync
        headers = _make_headers()

        connector = await _get_shared_connector()
        timeout = aiohttp.ClientTimeout(
            total=config.REQUEST_TIMEOUT,
            connect=config.CONNECT_TIMEOUT,
        )

        async with aiohttp.ClientSession(
            connector=connector,
            connector_owner=False,
            timeout=timeout,
            headers={k: v for k, v in headers.items() if k != "Cookie"},
            cookie_jar=aiohttp.CookieJar(unsafe=True),
        ) as session:
            # Set cookies manually from the verified headers
            cookie_str = headers.get("Cookie", "")
            if cookie_str:
                for pair in cookie_str.split(";"):
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
                "Cookie": cookie_str,
            }

            async with session.ws_connect(
                self._ws_url(),
                headers=ws_headers,
                timeout=aiohttp.ClientWSTimeout(ws_receive=config.REQUEST_TIMEOUT),
            ) as ws:
                # Wait for connected event
                hello = json.loads(await asyncio.wait_for(ws.receive_str(), timeout=15))
                if hello.get("event") != "connected":
                    raise RuntimeError(f"Unexpected hello: {hello}")
                
                # BOUNTY HUNTER HEARTBEAT
                sys.stderr.write(f"Bounty Hunter: WS_LINK_ESTABLISHED conv={conv_id}\n")
                sys.stderr.flush()

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

                content_payload = [{"type": "text", "text": prompt}]
                if attachment_path:
                    abs_path = os.path.abspath(attachment_path)
                    if os.path.exists(abs_path):
                        import base64
                        sz = os.path.getsize(abs_path)
                        if sz > config.MAX_IMAGE_BYTES:
                            raise ValueError(
                                f"Image file exceeds MAX_IMAGE_BYTES ({config.MAX_IMAGE_BYTES})"
                            )
                        with open(abs_path, "rb") as f:
                            b64 = base64.b64encode(f.read()).decode("utf-8")
                        content_payload.append({
                            "type": "image",
                            "imageUrl": f"data:image/jpeg;base64,{b64}"
                        })
                        sys.stderr.write(f"Encoding image as JPEG (Base64 length: {len(b64)}) from: {abs_path}\n")
                        sys.stderr.flush()
                    else:
                        logger.warning("Attachment path not found: %s", abs_path)

                # Send message
                payload = {
                    "event": "send",
                    "conversationId": conv_id,
                    "content": content_payload,
                    "mode": mode,
                    "context": context or {},
                }
                sys.stderr.write(f"WS_SEND: {json.dumps(payload)[:200]}\n")
                sys.stderr.flush()
                await ws.send_str(json.dumps(payload))

                # Stream response
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        ev = data.get("event", "")
                        sys.stderr.write(f"WS_RECV_EVENT: {ev}\n")
                        sys.stderr.flush()
                        if ev == "appendText":
                            yield data.get("text", "")
                        elif ev == "challenge":
                            # Copilot bot-verification challenge.
                            # method="copilot": solve cubic formula mod 22 and reply.
                            method = data.get("method", "")
                            parameter = data.get("parameter", "")
                            if method == "copilot" and parameter:
                                try:
                                    a = float(parameter)
                                    token = str(round((a ** 3 / 100 + a * 25) % 22))
                                except (ValueError, ZeroDivisionError):
                                    token = "0"
                                await ws.send_str(json.dumps({
                                    "event": "challengeResponse",
                                    "method": "copilot",
                                    "token": token,
                                }))
                            else:
                                # Other methods (hashcash, cloudflare) or failed copilot challenge
                                raise RuntimeError(
                                    f"Copilot verification required (method: {method or ev}). "
                                    "Please refresh cookies via Container 3 (browser-auth) "
                                    "or solve the challenge in the noVNC browser."
                                )
                        elif ev in _DONE_EVENTS:
                            if ev == "error":
                                raise RuntimeError(
                                    f"Copilot error: {data.get('message', ev)}"
                                )
                            if ev == "throttled":
                                raise RuntimeError("Copilot rate limited (throttled)")
                            
                            # Capture suggested follow-up prompts
                            self._last_suggestions = data.get("suggestedResponses", [])
                            if self._last_suggestions:
                                sys.stderr.write(f"Bounty Hunter Trace: SUGGESTIONS_CAPTURED count={len(self._last_suggestions)}\n")
                                sys.stderr.flush()
                            break
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break

    async def chat_completion_stream(self, prompt: str, attachment_path=None, context=None) -> AsyncGenerator[str, None]:
        """Streaming interface — yields text tokens as they arrive."""
        try:
            async for token in self._ws_stream(prompt, context, attachment_path):
                yield token
        except RuntimeError as exc:
            msg = str(exc).lower()
            if "verification required" in msg or "unauthorized" in msg or "handshake" in msg:
                logger.info("Auth failure/Challenge detected in stream. Triggering automated cookie refresh...")
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post("http://browser-auth:8001/extract", timeout=15) as resp:
                            if resp.status == 200:
                                await asyncio.sleep(2)
                                await self.reset_conversation()
                                async for token in self._ws_stream(prompt, context, attachment_path):
                                    yield token
                                return
                except Exception as refresh_exc:
                    logger.error("Automated cookie refresh failed in stream: %s", refresh_exc)
            raise

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
    global _connection_pool, _shared_connector
    if _connection_pool:
        await _connection_pool.close_all()
        _connection_pool = None
    async with _connector_lock:
        if _shared_connector is not None and not _shared_connector.closed:
            await _shared_connector.close()
        _shared_connector = None
