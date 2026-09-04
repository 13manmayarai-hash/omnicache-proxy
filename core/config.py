"""
Configuration and pricing registry for OmniCache AI Proxy.
"""

import os
from typing import Dict, Any, List

def load_dotenv():
    paths = [
        os.path.join(os.getcwd(), ".env"),
        os.path.expanduser("~/.omnicache/.env"),
        os.path.expanduser("~/.env")
    ]
    for env_file in paths:
        if os.path.exists(env_file):
            try:
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip("'").strip('"')
                            if k not in os.environ:
                                os.environ[k] = v
                break
            except Exception:
                pass

load_dotenv()

MODEL_PRICING: Dict[str, Dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "output": 10.00, "cached_input": 1.25},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cached_input": 0.075},
    "o1": {"input": 15.00, "output": 60.00, "cached_input": 7.50},
    "o3-mini": {"input": 1.10, "output": 4.40, "cached_input": 0.55},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00, "cached_input": 5.00},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50, "cached_input": 0.25},
    "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00, "cached_input": 0.30},
    "claude-3-5-haiku-20241022": {"input": 0.80, "output": 4.00, "cached_input": 0.08},
    "claude-3-7-sonnet": {"input": 3.00, "output": 15.00, "cached_input": 0.30},
    "claude-sonnet-4-5-20250929": {"input": 3.00, "output": 15.00, "cached_input": 0.30},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00, "cached_input": 0.08},
    "gemini-2.5-flash": {"input": 0.10, "output": 0.40, "cached_input": 0.025},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00, "cached_input": 0.3125},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30, "cached_input": 0.01875},
    "default": {"input": 2.00, "output": 8.00, "cached_input": 1.00},
}

class ProxyConfig:
    PORT: int = int(os.getenv("PORT", os.getenv("OMNICACHE_PORT", "8000")))
    # Safe default binding to localhost (127.0.0.1)
    HOST: str = os.getenv("HOST", os.getenv("OMNICACHE_HOST", "127.0.0.1"))
    
    # Master Admin Key and Authentication Controls
    ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", os.getenv("OMNICACHE_ADMIN_KEY", ""))
    REQUIRE_AUTH: bool = os.getenv("REQUIRE_AUTH", "false").lower() in ("true", "1")
    
    # Cryptographic Salt for PII Tokenization (prevents cross-user collisions)
    PRIVACY_SALT: str = os.getenv("PRIVACY_SALT", os.getenv("OMNICACHE_PRIVACY_SALT", "omnicache_salt_v2"))
    
    # Restricted CORS Configuration
    CORS_ALLOWED_ORIGINS: List[str] = [
        origin.strip()
        for origin in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000").split(",")
        if origin.strip()
    ]
    CORS_ALLOW_ALL: bool = os.getenv("CORS_ALLOW_ALL", "true").lower() in ("true", "1")

    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    ANTHROPIC_BASE_URL: str = "https://api.anthropic.com/v1"
    GEMINI_BASE_URL: str = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
    
    DEFAULT_SIMILARITY_THRESHOLD: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.92"))
    EXACT_CACHE_TTL_SECONDS: int = int(os.getenv("EXACT_CACHE_TTL", "604800"))
    SEMANTIC_CACHE_TTL_SECONDS: int = int(os.getenv("SEMANTIC_CACHE_TTL", "604800"))
    MAX_CACHE_ENTRIES_PER_TENANT: int = int(os.getenv("MAX_CACHE_ENTRIES", "10000"))
    
    TEMPERATURE_BYPASS_THRESHOLD: float = 0.85
    STREAM_REPLAY_TOKENS_PER_SEC: float = 65.0
    SINGLEFLIGHT_TIMEOUT_SECONDS: float = 30.0
    
    HTTP_POOL_MAX_CONNECTIONS: int = 100
    HTTP_POOL_MAX_KEEPALIVE: int = 20
    HTTP_TIMEOUT_SECONDS: float = 60.0

config = ProxyConfig()
