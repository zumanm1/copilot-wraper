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


_cached_cookie: str | None = None
_M365_REQUIRED_COOKIE_HINTS = ("MSFPC", "OH.SID", "OH.FLID", "OH.DCAffinity")
_COPILOT_REQUIRED_COOKIE_HINTS = ("MUID", "__Host-copilot-anon", "_C_ETH")


def _extract_conversation_id(data: object) -> str | None:
    """
    Extract conversation ID from both consumer and M365-shaped payloads.
    """
    if isinstance(data, dict):
        direct = data.get("id")
        if isinstance(direct, str) and direct:
            return direct
        for key in ("conversations", "items", "value"):
            items = data.get(key)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        cid = item.get("id") or item.get("conversationId")
                        if isinstance(cid, str) and cid:
                            return cid
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                cid = item.get("id") or item.get("conversationId")
                if isinstance(cid, str) and cid:
                    return cid
    return None

def _make_cookie_header() -> str:
    """Return cached Cookie header; refreshed on reload_cookies()."""
    global _cached_cookie
    if _cached_cookie is None:
        _cached_cookie = os.getenv("COPILOT_COOKIES") or os.getenv("BING_COOKIES") or ""
    return _cached_cookie


def _cookie_names_from_header(cookie_header: str) -> set[str]:
    names: set[str] = set()
    for pair in (cookie_header or "").split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, _, _val = pair.partition("=")
        name = name.strip()
        if name:
            names.add(name)
    return names


def _validate_provider_cookie_compatibility(cookie_header: str) -> None:
    """
    Fail fast with actionable guidance when cookie set does not match provider.
    """
    provider = config.copilot_provider()
    profile = getattr(config, "COPILOT_PORTAL_PROFILE", "consumer")
    cookie_names = _cookie_names_from_header(cookie_header)

    if not cookie_names:
        raise RuntimeError(
            "No Copilot cookies loaded. Run C3 extract and reload C1 cookies."
        )

    if provider == "m365":
        if not any(name in cookie_names for name in _M365_REQUIRED_COOKIE_HINTS):
            raise RuntimeError(
                "M365 provider selected but M365 session cookies not found "
                f"(expected one of: {', '.join(_M365_REQUIRED_COOKIE_HINTS)}). "
                "Sign in on m365.cloud.microsoft in noVNC, extract cookies, then retry."
            )
        return

    if provider == "copilot":
        if not any(name in cookie_names for name in _COPILOT_REQUIRED_COOKIE_HINTS):
            raise RuntimeError(
                "Copilot provider selected but Copilot/Bing cookies not found "
                f"(expected one of: {', '.join(_COPILOT_REQUIRED_COOKIE_HINTS)}). "
                "Sign in on copilot.microsoft.com, extract cookies, then retry."
            )
        if profile == "m365_hub":
            logger.warning(
                "Provider/profile mismatch: COPILOT_PROVIDER=copilot with "
                "COPILOT_PORTAL_PROFILE=m365_hub. Using explicit provider override."
            )

def reload_cookies() -> None:
    """Invalidate the cached cookie so the next call re-reads the env."""
    global _cached_cookie
    _cached_cookie = None


def _make_headers() -> dict:
    """Browser-like headers; Origin/Referer follow portal profile (see config)."""
    return {
        "Origin": config.copilot_browser_origin(),
        "Referer": config.copilot_browser_referer(),
        "Accept": "application/json, text/plain, */*",
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
            self._conversation_id = await self._create_conversation(session)
        return self._conversation_id

    async def _create_conversation(self, session: aiohttp.ClientSession) -> str:
        """
        Provider-aware conversation bootstrap.
        - copilot: POST /c/api/conversations with empty JSON body.
        - m365:    try GET /c/api/conversations first, then fallback to POST for compatibility.
        """
        provider = config.copilot_provider()
        url = config.copilot_conversations_url()
        if provider == "copilot" and "m365.cloud.microsoft" in url:
            raise RuntimeError(
                "Config mismatch: copilot provider is targeting an M365 API base URL. "
                "Set COPILOT_PROVIDER=auto or m365 for m365_hub profile, "
                "or clear COPILOT_PORTAL_API_BASE_URL override."
            )
        if provider == "m365":
            conv_id = await self._create_conversation_m365(session, url)
            if conv_id:
                return conv_id
            raise RuntimeError(
                "Failed to create M365 conversation session. "
                "Ensure M365 web session is valid and cookies are reloaded."
            )
        async with session.post(url, json={}) as resp:
            if resp.status != 200:
                raise RuntimeError(
                    f"Failed to create Copilot conversation: HTTP {resp.status}"
                )
            data = await resp.json()
            return data["id"]

    async def _create_conversation_m365(
        self, session: aiohttp.ClientSession, url: str
    ) -> str | None:
        # M365 hub may require GET for listing/bootstrapping conversations.
        async with session.get(url) as get_resp:
            if get_resp.status == 200:
                try:
                    data = await get_resp.json(content_type=None)
                except Exception as exc:
                    raise RuntimeError(
                        "M365 conversation bootstrap response-envelope mismatch "
                        "(GET /c/api/conversations returned non-JSON)."
                    ) from exc
                cid = _extract_conversation_id(data)
                if cid:
                    return cid
            elif get_resp.status not in (404, 405):
                raise RuntimeError(
                    f"Failed to bootstrap M365 conversation via GET: HTTP {get_resp.status}"
                )

        # Fallback for environments where POST still works.
        async with session.post(url, json={}) as post_resp:
            if post_resp.status == 200:
                try:
                    data = await post_resp.json(content_type=None)
                except Exception as exc:
                    raise RuntimeError(
                        "M365 conversation bootstrap response-envelope mismatch "
                        "(POST /c/api/conversations returned non-JSON)."
                    ) from exc
                cid = _extract_conversation_id(data)
                if cid:
                    return cid
            elif post_resp.status in (401, 403):
                raise RuntimeError(
                    "M365 conversation bootstrap unauthorized (401/403). "
                    "Complete sign-in in noVNC and refresh cookies."
                )
        return None

    def _ws_url(self) -> str:
        sid = str(uuid.uuid4())
        base = config.copilot_ws_chat_url()
        return f"{base}?api-version=2&clientSessionId={sid}"

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
        _validate_provider_cookie_compatibility(headers.get("Cookie", ""))

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

            try:
                ws_ctx = session.ws_connect(
                    self._ws_url(),
                    headers=ws_headers,
                    timeout=aiohttp.ClientWSTimeout(ws_receive=config.REQUEST_TIMEOUT),
                )
                async with ws_ctx as ws:
                # Wait for connected event
                    hello_raw = await asyncio.wait_for(ws.receive_str(), timeout=15)
                    try:
                        hello = json.loads(hello_raw)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(
                            "WebSocket response-envelope mismatch: expected JSON 'connected' "
                            "event from provider endpoint. Check provider/API base alignment."
                        ) from exc
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
                            try:
                                data = json.loads(msg.data)
                            except json.JSONDecodeError as exc:
                                raise RuntimeError(
                                    "WebSocket response-envelope mismatch while streaming. "
                                    "Provider endpoint returned non-JSON frame."
                                ) from exc
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
            except aiohttp.WSServerHandshakeError as exc:
                provider = config.copilot_provider()
                profile = getattr(config, "COPILOT_PORTAL_PROFILE", "consumer")
                if exc.status in (401, 403):
                    raise RuntimeError(
                        "WebSocket handshake unauthorized "
                        f"(HTTP {exc.status}, provider={provider}, profile={profile}). "
                        "Refresh cookies via C3 and ensure sign-in for the selected provider."
                    ) from exc
                raise

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
