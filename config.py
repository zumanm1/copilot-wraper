"""
Configuration management for Copilot OpenAI Wrapper.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from dotenv import load_dotenv

from portal_urls import m365_hub_default_landing, normalize_copilot_portal_url

load_dotenv()

# ── Copilot portal profile (consumer vs M365 web hub) ─────────────────────────
# Phase A: both profiles default to consumer API host; Origin/Referer follow portal.
_VALID_PORTAL_PROFILES = frozenset({"consumer", "m365_hub"})
_DEFAULT_PORTAL_BASES = {
    "consumer": "https://copilot.microsoft.com/",
    "m365_hub": m365_hub_default_landing(),
}
_DEFAULT_API_BASE = "https://copilot.microsoft.com"
_DEFAULT_M365_API_BASE = "https://m365.cloud.microsoft"

# Server configuration
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
RELOAD = os.getenv("RELOAD", "true").lower() == "true"

# Microsoft Copilot authentication
BING_COOKIES = os.getenv("BING_COOKIES", "")
# copilot.microsoft.com domain cookies (new WebSocket API)
COPILOT_COOKIES = os.getenv("COPILOT_COOKIES", "") or os.getenv("BING_COOKIES", "")

# Portal profile: m365_hub (default) or consumer (copilot.microsoft.com)
COPILOT_PORTAL_PROFILE = os.getenv("COPILOT_PORTAL_PROFILE", "m365_hub").strip().lower()
if COPILOT_PORTAL_PROFILE not in _VALID_PORTAL_PROFILES:
    COPILOT_PORTAL_PROFILE = "consumer"
COPILOT_PORTAL_BASE_URL = os.getenv("COPILOT_PORTAL_BASE_URL", "").strip()
COPILOT_PORTAL_API_BASE_URL = os.getenv("COPILOT_PORTAL_API_BASE_URL", "").strip()
# Provider routing:
# - auto    : infer from portal profile (m365_hub -> m365, consumer -> copilot)
# - copilot : force copilot.microsoft.com provider path
# - m365    : force M365-compatible provider path
COPILOT_PROVIDER = os.getenv("COPILOT_PROVIDER", "auto").strip().lower()
if COPILOT_PROVIDER not in {"auto", "copilot", "m365"}:
    COPILOT_PROVIDER = "auto"
M365_API_BASE_URL = os.getenv("M365_API_BASE_URL", "").strip()
M365_PROVIDER_FALLBACK_TO_COPILOT = os.getenv(
    "M365_PROVIDER_FALLBACK_TO_COPILOT", "false"
).strip().lower() == "true"
# Auto cookie-refresh from C1 can destabilize a good M365 cookie set during
# challenge windows; keep it disabled for m365_hub unless explicitly enabled.
AUTO_COOKIE_REFRESH = os.getenv("AUTO_COOKIE_REFRESH", "true").strip().lower() == "true"
AUTO_COOKIE_REFRESH_M365 = os.getenv("AUTO_COOKIE_REFRESH_M365", "false").strip().lower() == "true"

# Copilot settings
COPILOT_STYLE = os.getenv("COPILOT_STYLE", "smart")
COPILOT_PERSONA = os.getenv("COPILOT_PERSONA", "copilot")

# API settings
API_KEY = os.getenv("API_KEY", "")
USE_PROXY = os.getenv("USE_PROXY", "false").lower() == "true"

# Timeout settings (seconds)
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "60"))
CONNECT_TIMEOUT = int(os.getenv("CONNECT_TIMEOUT", "15"))

# Connection pool settings
POOL_WARM_COUNT = int(os.getenv("POOL_WARM_COUNT", "2"))

# Agent settings
AGENT_MAX_HISTORY = int(os.getenv("AGENT_MAX_HISTORY", "1000"))

# Circuit breaker settings
CIRCUIT_BREAKER_THRESHOLD = int(os.getenv("CIRCUIT_BREAKER_THRESHOLD", "5"))
CIRCUIT_BREAKER_TIMEOUT   = int(os.getenv("CIRCUIT_BREAKER_TIMEOUT", "60"))

# Rate limiting (slowapi format, e.g. "20/minute", "100/hour", "" to disable)
RATE_LIMIT = os.getenv("RATE_LIMIT", "20/minute")

# Model ID → CopilotBackend.style (see copilot_backend._MODE_MAP for style→WS mode)
MODEL_MAP = {
    "copilot": "smart",
    "gpt-4": "smart",
    "gpt-4o": "smart",
    "gpt-4-turbo": "smart",
    "gpt-3.5-turbo": "smart",
    "copilot-balanced": "balanced",
    "copilot-creative": "creative",
    "copilot-precise": "precise",
    "o1": "reasoning",
    "o1-mini": "reasoning",
}

# Stateless chat: max characters sent to Copilot (system prefix preserved when truncating)
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "32000"))

# Multimodal: reject attachments larger than this (bytes)
MAX_IMAGE_BYTES = int(os.getenv("MAX_IMAGE_BYTES", str(4 * 1024 * 1024)))

# Named agent API sessions: idle TTL for stopped sessions (seconds)
AGENT_API_SESSION_TTL = int(os.getenv("AGENT_API_SESSION_TTL", "1800"))

def portal_base_url_resolved() -> str:
    """HTTPS portal URL with trailing slash (for Referer)."""
    if COPILOT_PORTAL_BASE_URL:
        raw = normalize_copilot_portal_url(COPILOT_PORTAL_BASE_URL.strip())
        if not raw.startswith("http://") and not raw.startswith("https://"):
            raw = "https://" + raw.lstrip("/")
        return raw.rstrip("/") + "/"
    return _DEFAULT_PORTAL_BASES.get(
        COPILOT_PORTAL_PROFILE, _DEFAULT_PORTAL_BASES["consumer"]
    )


def copilot_api_base_url() -> str:
    """HTTPS origin for REST + WSS (no path), profile/provider aware."""
    if COPILOT_PORTAL_API_BASE_URL:
        u = normalize_copilot_portal_url(COPILOT_PORTAL_API_BASE_URL.strip())
        if not u.startswith("http://") and not u.startswith("https://"):
            u = "https://" + u.lstrip("/")
        parsed = urlparse(u)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        return u.rstrip("/")
    if resolved_provider() == "m365":
        return m365_api_base_url()
    return _DEFAULT_API_BASE


def m365_api_base_url() -> str:
    """HTTPS origin for M365 provider REST + WSS (no path)."""
    if M365_API_BASE_URL:
        u = normalize_copilot_portal_url(M365_API_BASE_URL.strip())
        if not u.startswith("http://") and not u.startswith("https://"):
            u = "https://" + u.lstrip("/")
        parsed = urlparse(u)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        return u.rstrip("/")
    return _DEFAULT_M365_API_BASE


def resolved_provider() -> str:
    """Resolve active provider after applying auto/profile rules."""
    if COPILOT_PROVIDER in {"copilot", "m365"}:
        return COPILOT_PROVIDER
    return "m365" if COPILOT_PORTAL_PROFILE == "m365_hub" else "copilot"


def copilot_browser_origin() -> str:
    """Value for the Origin header (scheme + host, no path)."""
    parsed = urlparse(portal_base_url_resolved())
    if not parsed.scheme or not parsed.netloc:
        return "https://copilot.microsoft.com"
    return f"{parsed.scheme}://{parsed.netloc}"


def copilot_browser_referer() -> str:
    return portal_base_url_resolved()


def copilot_conversations_url() -> str:
    return f"{copilot_api_base_url()}/c/api/conversations"


def copilot_ws_chat_url() -> str:
    """Base wss URL without query string."""
    api = copilot_api_base_url()
    if api.startswith("https://"):
        host = api[len("https://") :]
    elif api.startswith("http://"):
        host = api[len("http://") :]
    else:
        host = api
    host = host.rstrip("/")
    return f"wss://{host}/c/api/chat"


def m365_conversations_url() -> str:
    return f"{m365_api_base_url()}/c/api/conversations"


def m365_ws_chat_url() -> str:
    """Base M365 wss URL without query string."""
    api = m365_api_base_url()
    if api.startswith("https://"):
        host = api[len("https://") :]
    elif api.startswith("http://"):
        host = api[len("http://") :]
    else:
        host = api
    host = host.rstrip("/")
    return f"wss://{host}/c/api/chat"


def validate_config():
    """Validate that required configuration is present."""
    if not COPILOT_COOKIES:
        raise ValueError(
            "COPILOT_COOKIES (or BING_COOKIES) environment variable is required. "
            "Please set it to your copilot.microsoft.com browser cookies. "
            "See README.md for instructions."
        )
    return True