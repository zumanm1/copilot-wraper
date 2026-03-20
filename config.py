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

# Copilot settings
COPILOT_STYLE = os.getenv("COPILOT_STYLE", "balanced")
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

# Model mapping
MODEL_MAP = {
    "copilot": "copilot",
    "gpt-4": "copilot",
    "gpt-4o": "copilot",
    "gpt-4-turbo": "copilot",
    "gpt-3.5-turbo": "copilot",
    "copilot-balanced": "copilot",
    "copilot-creative": "copilot",
    "copilot-precise": "copilot",
}

def validate_config():
    """Validate that required configuration is present."""
    if not BING_COOKIES:
        raise ValueError(
            "BING_COOKIES environment variable is required. "
            "Please set it to your Bing.com cookies (especially the _U cookie). "
            "See README.md for instructions on how to obtain cookies."
        )
    return True