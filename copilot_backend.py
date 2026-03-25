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


def _cache_key(style: str, prompt: str, agent_id: str = "") -> str:
    return hashlib.sha256(f"{style}:{agent_id}:{prompt}".encode()).hexdigest()


_cached_cookie: str | None = None

def _make_cookie_header() -> str:
    """Return cached Cookie header; refreshed on reload_cookies()."""
    global _cached_cookie
    if _cached_cookie is None:
        _cached_cookie = os.getenv("COPILOT_COOKIES") or os.getenv("BING_COOKIES") or ""
    return _cached_cookie

def reload_cookies() -> None:
    """Invalidate the cached cookie so the next call re-reads the env."""
    global _cached_cookie
    _cached_cookie = None


def _make_headers(provider_name: str = "copilot") -> dict:
    """Browser-like headers; Origin/Referer always follow config (profile-aware)."""
    origin = config.copilot_browser_origin()
    referer = config.copilot_browser_referer()
    return {
        "Origin": origin,
        "Referer": referer,
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/132.0.0.0 Safari/537.36"
        ),
        "Cookie": _make_cookie_header(),
    }


# ── Provider strategy ──────────────────────────────────────────────────────────
class _ProviderBase:
    name = "base"

    def conversations_url(self) -> str:
        raise NotImplementedError

    def ws_chat_url(self) -> str:
        raise NotImplementedError

    def validate_session(self, cookie_header: str) -> None:
        # Base provider: no-op.
        return


class CopilotPublicProvider(_ProviderBase):
    name = "copilot"

    def conversations_url(self) -> str:
        return config.copilot_conversations_url()

    def ws_chat_url(self) -> str:
        return config.copilot_ws_chat_url()


class M365Provider(_ProviderBase):
    name = "m365"

    def conversations_url(self) -> str:
        return config.m365_conversations_url()

    def ws_chat_url(self) -> str:
        return config.m365_ws_chat_url()

    def validate_session(self, cookie_header: str) -> None:
        """
        m365_hub cookies differ from consumer Copilot cookies.
        Fail fast with a clear operator error if M365 session material is absent.
        """
        cookie_header = cookie_header or ""
        has_m365_cookie = ("OH.SID=" in cookie_header) or ("MSFPC=" in cookie_header)
        if has_m365_cookie:
            return
        raise RuntimeError(
            "M365 provider requires an active M365 web session cookie "
            "(expected one of: OH.SID, MSFPC). "
            "Refresh cookies in C3 with COPILOT_PORTAL_PROFILE=m365_hub, then retry."
        )


def _should_fallback_to_copilot(provider: _ProviderBase, cookie_header: str) -> bool:
    # Strict provider isolation mode:
    # - m365 provider must remain m365
    # - copilot provider must remain copilot
    # Cross-provider automatic fallback is intentionally disabled.
    return False


def _build_provider() -> _ProviderBase:
    provider_name = config.resolved_provider()
    if provider_name == "m365":
        return M365Provider()
    return CopilotPublicProvider()


def _auto_refresh_allowed() -> bool:
    if not config.AUTO_COOKIE_REFRESH:
        return False
    if config.COPILOT_PORTAL_PROFILE == "m365_hub" and not config.AUTO_COOKIE_REFRESH_M365:
        return False
    return True


# ── Backend ───────────────────────────────────────────────────────────────────

class CopilotBackend:
    def __init__(self, style=None, persona=None):
        self.style = style or config.COPILOT_STYLE
        self.persona = persona or config.COPILOT_PERSONA
        self.provider = _build_provider()
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
            # M365 may expose a different bootstrap contract (GET list, or non-JSON redirect),
            # so use provider-aware probing before committing to a conversation id.
            if self.provider.name == "m365":
                self._conversation_id = await self._create_conversation_m365(session)
            else:
                self._conversation_id = await self._create_conversation_copilot(session)
        return self._conversation_id

    async def _create_conversation_copilot(self, session: aiohttp.ClientSession) -> str:
        url = self.provider.conversations_url()
        async with session.post(url, json={}) as resp:
            if resp.status != 200:
                raise RuntimeError(
                    f"Failed to create {self.provider.name} conversation: HTTP {resp.status}"
                )
            data = await resp.json()
            return data["id"]

    async def _create_conversation_m365(self, session: aiohttp.ClientSession) -> str:
        url = self.provider.conversations_url()
        # Probe GET first for M365 hubs that list conversations.
        # Keep redirects disabled so we can detect auth redirects explicitly.
        async with session.get(url, allow_redirects=False) as get_resp:
            if get_resp.status in (301, 302, 303, 307, 308):
                location = (get_resp.headers.get("Location") or "").lower()
                if "login.microsoftonline.com" in location or "oauth2" in location:
                    raise RuntimeError(
                        "M365 conversation bootstrap redirected to Microsoft login/OAuth. "
                        "Current session is missing required M365 auth context/token for API bootstrap."
                    )
                raise RuntimeError(
                    f"M365 conversation bootstrap redirected (HTTP {get_resp.status}) "
                    "to a non-chat endpoint."
                )
            if get_resp.status == 200:
                try:
                    data = await get_resp.json(content_type=None)
                    if isinstance(data, dict):
                        convs = data.get("conversations") or data.get("items") or data.get("value")
                        if isinstance(convs, list):
                            for item in convs:
                                if isinstance(item, dict):
                                    cid = item.get("id") or item.get("conversationId")
                                    if isinstance(cid, str) and cid:
                                        return cid
                        if isinstance(data.get("id"), str) and data.get("id"):
                            return data["id"]
                except Exception as exc:
                    # Some M365 sessions return HTML/redirect content for GET bootstrap.
                    # Do not fail fast here; attempt POST fallback before returning error.
                    logger.warning(
                        "M365 GET bootstrap returned non-JSON; trying POST fallback: %s",
                        exc,
                    )
            elif get_resp.status not in (404, 405):
                raise RuntimeError(
                    f"Failed to create m365 conversation via GET: HTTP {get_resp.status}"
                )

        # Fallback POST path for environments where m365 supports the consumer shape.
        async with session.post(url, json={}) as post_resp:
            if post_resp.status != 200:
                raise RuntimeError(
                    f"Failed to create {self.provider.name} conversation: HTTP {post_resp.status}"
                )
            try:
                data = await post_resp.json(content_type=None)
            except Exception as exc:
                raise RuntimeError(
                    "M365 conversation bootstrap response-envelope mismatch "
                    "(POST /c/api/conversations returned non-JSON)."
                ) from exc
            cid = data.get("id") if isinstance(data, dict) else None
            if isinstance(cid, str) and cid:
                return cid
            raise RuntimeError("M365 conversation bootstrap returned no conversation id.")

    def _ws_url(self) -> str:
        sid = str(uuid.uuid4())
        base = self.provider.ws_chat_url()
        return f"{base}?api-version=2&clientSessionId={sid}"

    async def chat_completion(self, prompt: str, attachment_path=None, context=None, search=True, agent_id: str = "") -> str:
        global _cache_hits, _cache_misses
        import time as _time
        _t0 = _time.monotonic()

        if attachment_path:
            return await self._do_chat_completion(prompt, attachment_path, context)

        key = _cache_key(self.style, prompt, agent_id)
        if key in _response_cache:
            _cache_hits += 1
            logger.info("PERF chat_completion: cache_hit agent=%s elapsed=%dms", agent_id, int((_time.monotonic()-_t0)*1000))
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
            logger.info("PERF chat_completion: cache_miss agent=%s total=%dms", agent_id, int((_time.monotonic()-_t0)*1000))
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
            if (
                _auto_refresh_allowed()
                and ("verification required" in msg or "unauthorized" in msg or "handshake" in msg)
            ):
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
            elif "verification required" in msg or "unauthorized" in msg or "handshake" in msg:
                logger.info(
                    "Auth failure detected, but auto cookie refresh is disabled "
                    "(profile=%s).",
                    config.COPILOT_PORTAL_PROFILE,
                )
            
            # If not a challenge or refresh failed, re-raise
            raise

    async def _raw_copilot_call(self, prompt: str, context, attachment_path=None) -> str:
        """Accumulates all appendText events into a single string.
        M365 provider proxies through C3 browser-auth /chat endpoint.
        Consumer Copilot uses direct WebSocket."""
        if self.provider.name == "m365":
            return await self._c3_proxy_call(prompt)

        chunks = []
        async for chunk in self._ws_stream(prompt, context, attachment_path):
            chunks.append(chunk)
        # Reset conversation so next request gets a fresh server-side context.
        # Reusing a conversation_id across separate WebSocket connections causes
        # Copilot to reject the 'send' event with errorCode: 'invalid-event'.
        self._conversation_id = None
        return "".join(chunks)

    _c3_session: aiohttp.ClientSession | None = None

    @classmethod
    def _get_c3_session(cls) -> aiohttp.ClientSession:
        if cls._c3_session is None or cls._c3_session.closed:
            cls._c3_session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=6, keepalive_timeout=120),
            )
        return cls._c3_session

    async def _c3_proxy_call(self, prompt: str) -> str:
        """Proxy chat through C3 browser-auth /chat endpoint (M365 SignalR)."""
        import time as _time
        _t0 = _time.monotonic()
        c3_url = os.getenv("C3_URL", "http://browser-auth:8001")
        logger.info("M365 proxy via C3: %s/chat prompt='%s'", c3_url, prompt[:60])
        try:
            session = self._get_c3_session()
            async with session.post(
                f"{c3_url}/chat",
                json={"prompt": prompt, "timeout": int(config.REQUEST_TIMEOUT * 1000)},
                timeout=aiohttp.ClientTimeout(total=config.REQUEST_TIMEOUT + 10),
            ) as resp:
                data = await resp.json()
                _c3_ms = int((_time.monotonic() - _t0) * 1000)
                _c3_perf = data.get("perf", {})
                logger.info("PERF _c3_proxy_call: total=%dms c3_internal=%s", _c3_ms, _c3_perf)
                if data.get("success") and data.get("text"):
                    return data["text"]
                    error = (
                        data.get("error")
                        or data.get("message")
                        or data.get("detail")
                        or "No response from M365 Copilot (empty reply)"
                    )
                    raise RuntimeError(f"C3 /chat failed: {error}")
        except aiohttp.ClientError as exc:
            raise RuntimeError(
                f"C3 browser-auth unreachable at {c3_url}/chat: {exc}"
            ) from exc

    async def _ws_stream(self, prompt: str, context, attachment_path=None) -> AsyncGenerator[str, None]:
        """Low-level WebSocket streaming generator."""
        logger.info("WS_STREAM: prompt='%s', attachment=%s", prompt[:50], attachment_path)
        
        # Always re-read cookies from environment to ensure latest C3 sync
        headers = _make_headers(self.provider.name)
        cookie_header = headers.get("Cookie", "")
        self.provider.validate_session(cookie_header)

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

            try:
                conv_id = await self._ensure_conversation(session)
            except RuntimeError as exc:
                msg = str(exc).lower()
                raise

            ws_headers = {
                "Origin": headers["Origin"],
                "Referer": headers.get("Referer", ""),
                "User-Agent": headers["User-Agent"],
                "Accept-Language": "en-US,en;q=0.9",
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
                    hello = json.loads(await asyncio.wait_for(ws.receive_str(), timeout=15))
                    if hello.get("event") != "connected":
                        raise RuntimeError(f"Unexpected hello: {hello}")
                
                    # BOUNTY HUNTER HEARTBEAT
                    sys.stderr.write(f"Bounty Hunter: WS_LINK_ESTABLISHED conv={conv_id}\n")
                    sys.stderr.flush()

                    # Pre-send challenge probe: Copilot may send a challenge event
                    # immediately after 'connected' and BEFORE it accepts a 'send'.
                    # Respond to it here so the 'send' is not rejected as invalid-event.
                    try:
                        pre_msg = await asyncio.wait_for(ws.receive_str(), timeout=2.0)
                        pre_data = json.loads(pre_msg)
                        pre_ev = pre_data.get("event", "")
                        sys.stderr.write(f"WS_PRE_SEND_EVENT: {pre_ev}\n")
                        sys.stderr.flush()
                        if pre_ev == "challenge":
                            pre_method = pre_data.get("method") or ""
                            pre_param = pre_data.get("parameter") or ""
                            pre_id = pre_data.get("id")
                            if pre_method == "copilot" and pre_param:
                                try:
                                    a = float(pre_param)
                                    token = str(round((a ** 3 / 100 + a * 25) % 22))
                                except (ValueError, ZeroDivisionError):
                                    token = "0"
                                await ws.send_str(json.dumps({
                                    "event": "challengeResponse",
                                    "method": "copilot",
                                    "token": token,
                                }))
                            else:
                                resp = {"event": "challengeResponse"}
                                if pre_id is not None:
                                    resp["id"] = pre_id
                                await ws.send_str(json.dumps(resp))
                            sys.stderr.write(f"WS_PRE_SEND_CHALLENGE_HANDLED: method={pre_method!r}\n")
                            sys.stderr.flush()
                        elif pre_ev:
                            # Unexpected non-challenge event before send — log and ignore
                            sys.stderr.write(f"WS_PRE_SEND_UNEXPECTED: {pre_data}\n")
                            sys.stderr.flush()
                    except asyncio.TimeoutError:
                        # No pre-send challenge — proceed normally
                        pass

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
                                sys.stderr.write(f"WS_CHALLENGE_DATA: {json.dumps(data)[:300]}\n")
                                sys.stderr.flush()
                                if method == "copilot" and parameter:
                                    sys.stderr.write("WS_CHALLENGE: Handling copilot method with parameter\n")
                                    sys.stderr.flush()
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
                                elif not method:
                                    sys.stderr.write("WS_CHALLENGE: Handling null method challenge\n")
                                    sys.stderr.flush()
                                    # Some sessions emit a challenge marker with null fields but a
                                    # challenge id. Echo an acknowledgement to unblock generation.
                                    challenge_id = data.get("id")
                                    payload = {"event": "challengeResponse", "id": challenge_id}
                                    await ws.send_str(json.dumps(payload))
                                    sys.stderr.write(f"WS_CHALLENGE_RESPONSE_SENT: {json.dumps(payload)}\n")
                                    sys.stderr.flush()
                                    # Small delay to allow server to process challenge response
                                    await asyncio.sleep(0.1)
                                    continue
                                else:
                                    # Other methods (hashcash, cloudflare) or failed copilot challenge
                                    raise RuntimeError(
                                        f"Copilot verification required (method: {method or ev}, data={data}). "
                                        "Please refresh cookies via Container 3 (browser-auth) "
                                        "or solve the challenge in the noVNC browser."
                                    )
                            elif ev in _DONE_EVENTS:
                                if ev == "error":
                                    raise RuntimeError(
                                        f"Copilot error: {data.get('message', ev)} (payload={data})"
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
                raise RuntimeError(
                    f"WebSocket handshake unauthorized (HTTP {exc.status}, "
                    f"provider={self.provider.name}, profile={config.COPILOT_PORTAL_PROFILE}). "
                    "Refresh cookies via C3 and ensure sign-in for the selected provider."
                ) from exc
            except RuntimeError as exc:
                raise

    async def chat_completion_stream(self, prompt: str, attachment_path=None, context=None) -> AsyncGenerator[str, None]:
        """Streaming interface — yields text tokens as they arrive.
        M365 provider returns full text in one chunk (C3 proxy is non-streaming)."""
        if self.provider.name == "m365":
            text = await self._c3_proxy_call(prompt)
            yield text
            return
        try:
            async for token in self._ws_stream(prompt, context, attachment_path):
                yield token
        except RuntimeError as exc:
            msg = str(exc).lower()
            if (
                _auto_refresh_allowed()
                and ("verification required" in msg or "unauthorized" in msg or "handshake" in msg)
            ):
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
            elif "verification required" in msg or "unauthorized" in msg or "handshake" in msg:
                logger.info(
                    "Auth failure detected in stream, but auto cookie refresh is disabled "
                    "(profile=%s).",
                    config.COPILOT_PORTAL_PROFILE,
                )
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
