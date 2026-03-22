"""
Configuration management for Copilot OpenAI Wrapper.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Server configuration
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
RELOAD = os.getenv("RELOAD", "true").lower() == "true"

# Microsoft Copilot authentication
BING_COOKIES = os.getenv("BING_COOKIES", "")
# copilot.microsoft.com domain cookies (new WebSocket API)
COPILOT_COOKIES = os.getenv("COPILOT_COOKIES", "") or os.getenv("BING_COOKIES", "")

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

def validate_config():
    """Validate that required configuration is present."""
    if not COPILOT_COOKIES:
        raise ValueError(
            "COPILOT_COOKIES (or BING_COOKIES) environment variable is required. "
            "Please set it to your copilot.microsoft.com browser cookies. "
            "See README.md for instructions."
        )
    return True