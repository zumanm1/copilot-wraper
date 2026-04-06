"""
Shared Copilot / M365 portal URL normalization for C1 (config) and C3 (cookie extractor).

Microsoft 365 web chat uses the registrable domain m365.cloud.microsoft (not m365.cloud.microsoft.com).
Users sometimes enter the .com typo; normalize to the canonical host while preserving path/query.
"""
from __future__ import annotations

from urllib.parse import urlparse, urlunparse

# Common typo: extra .com (DNS may fail or differ from prod)
_M365_HOST_ALIASES_TO_CANONICAL = frozenset(
    {
        "m365.cloud.microsoft.com",
        "www.m365.cloud.microsoft.com",
        "www.m365.cloud.microsoft",
    }
)


def normalize_copilot_portal_url(url: str) -> str:
    """
    Ensure https scheme and canonical hosts for Copilot/M365 portal URLs.
    Empty string is returned unchanged.
    """
    u = (url or "").strip()
    if not u:
        return u
    if not u.startswith(("http://", "https://")):
        u = "https://" + u.lstrip("/")
    parsed = urlparse(u)
    scheme = (parsed.scheme or "https").lower()
    if scheme not in ("http", "https"):
        scheme = "https"
    host = (parsed.netloc or "").lower()
    if host in _M365_HOST_ALIASES_TO_CANONICAL:
        host = "m365.cloud.microsoft"
    elif host == "www.copilot.microsoft.com":
        host = "copilot.microsoft.com"
    return urlunparse((scheme, host, parsed.path, parsed.params, parsed.query, parsed.fragment))


def m365_hub_default_landing() -> str:
    """Signed-in chat shell (path may gain ?auth=1 in browser)."""
    return "https://m365.cloud.microsoft/chat/"
