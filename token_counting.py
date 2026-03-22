"""Approximate token counts for usage fields (tiktoken when available)."""
from __future__ import annotations

_enc = None


def count_tokens(text: str) -> int:
    if not text:
        return 0
    global _enc
    if _enc is None:
        try:
            import tiktoken
            _enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _enc = False
    if _enc is False:
        return len(text.split())
    return len(_enc.encode(text))


def truncate_by_approx_tokens(text: str, max_tokens: int | None) -> tuple[str, bool]:
    """Truncate by word count as rough stand-in for tokens (max_tokens from OpenAI API)."""
    if max_tokens is None or max_tokens <= 0:
        return text, False
    words = text.split()
    if len(words) <= max_tokens:
        return text, False
    return " ".join(words[:max_tokens]) + "\n[truncated]", True
