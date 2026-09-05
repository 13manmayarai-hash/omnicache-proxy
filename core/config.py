"""
Configuration and pricing registry for OmniCache AI Proxy.
"""

import os
import secrets
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

def get_or_generate_privacy_salt() -> str:
    """
    Returns configured PRIVACY_SALT or generates a cryptographically strong random salt
    unique to this deployment. Never uses a public hardcoded static string.
    """
    salt = os.getenv("PRIVACY_SALT", os.getenv("OMNICACHE_PRIVACY_SALT", "")).strip()
    if salt:
        return salt
    salt_file = os.path.expanduser("~/.omnicache/.privacy_salt")
    if os.path.exists(salt_file):
        try:
            with open(salt_file, "r", encoding="utf-8") as f:
                saved = f.read().strip()
                if saved:
                    return saved
        except Exception:
            pass
    # Generate unique 256-bit random salt
    new_salt = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(salt_file), exist_ok=True)
        with open(salt_file, "w", encoding="utf-8") as f:
            f.write(new_salt)
    except Exception:
        pass
    return new_salt


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
    VERSION: str = "2.6.2"
    PORT: int = int(os.getenv("PORT", os.getenv("OMNICACHE_PORT", "8000")))
    # Default host strictly bound to localhost
    HOST: str = os.getenv("HOST", os.getenv("OMNICACHE_HOST", "127.0.0.1"))
    
    # Master Admin Key and Authentication Controls
    ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", os.getenv("OMNICACHE_ADMIN_KEY", ""))
    REQUIRE_AUTH: bool = os.getenv("REQUIRE_AUTH", "false").lower() in ("true", "1")
    ALLOW_INSECURE_NETWORK_EXPOSURE: bool = os.getenv("OMNICACHE_ALLOW_INSECURE_NETWORK_EXPOSURE", "false").lower() in ("true", "1")
    
    # Cryptographically unique random PII salt (never static public string)
    PRIVACY_SALT: str = get_or_generate_privacy_salt()
    
    # Restricted CORS Configuration
    CORS_ALLOWED_ORIGINS: List[str] = [
        origin.strip()
        for origin in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000").split(",")
        if origin.strip()
    ]
    CORS_ALLOW_ALL: bool = os.getenv("CORS_ALLOW_ALL", "false").lower() in ("true", "1")

    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

    @staticmethod
    def _sanitize_upstream_url(url: str, default: str) -> str:
        url = (url or "").strip()
        if not url:
            return default
        url_lower = url.lower()
        if any(local in url_lower for local in ("127.0.0.1", "localhost", "0.0.0.0", "::1")):
            return default
        return url

    UPSTREAM_OPENAI_BASE_URL: str = os.getenv("UPSTREAM_OPENAI_BASE_URL", "").strip()
    OPENAI_BASE_URL: str = _sanitize_upstream_url.__func__(
        os.getenv("UPSTREAM_OPENAI_BASE_URL", "").strip() or os.getenv("OPENAI_BASE_URL", ""),
        "https://api.openai.com/v1"
    )

    UPSTREAM_ANTHROPIC_BASE_URL: str = os.getenv("UPSTREAM_ANTHROPIC_BASE_URL", "").strip()
    ANTHROPIC_BASE_URL: str = _sanitize_upstream_url.__func__(
        os.getenv("UPSTREAM_ANTHROPIC_BASE_URL", "").strip() or os.getenv("ANTHROPIC_BASE_URL", ""),
        "https://api.anthropic.com/v1"
    )

    UPSTREAM_GEMINI_BASE_URL: str = os.getenv("UPSTREAM_GEMINI_BASE_URL", "").strip()
    GEMINI_BASE_URL: str = _sanitize_upstream_url.__func__(
        os.getenv("UPSTREAM_GEMINI_BASE_URL", "").strip() or os.getenv("GEMINI_BASE_URL", ""),
        "https://generativelanguage.googleapis.com/v1beta/openai"
    )
    
    DEFAULT_SIMILARITY_THRESHOLD: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.92"))
    EXACT_CACHE_TTL_SECONDS: int = int(os.getenv("EXACT_CACHE_TTL", "604800"))
    SEMANTIC_CACHE_TTL_SECONDS: int = int(os.getenv("SEMANTIC_CACHE_TTL", "604800"))
    MAX_CACHE_ENTRIES_PER_TENANT: int = int(os.getenv("MAX_CACHE_ENTRIES", "10000"))
    
    # Distributed State & Redis Configuration
    REDIS_URL: str = os.getenv("REDIS_URL", os.getenv("OMNICACHE_REDIS_URL", "")).strip()
    CACHE_STORAGE_BACKEND: str = os.getenv("CACHE_STORAGE_BACKEND", "auto").strip().lower()
    REDIS_KEY_PREFIX: str = os.getenv("REDIS_KEY_PREFIX", "omnicache").strip()

    # Embedding & ANN Vector Search Configuration
    EMBEDDER_BACKEND: str = os.getenv("EMBEDDER_BACKEND", "auto").strip().lower()
    ANN_INDEX_ENABLED: bool = os.getenv("ANN_INDEX_ENABLED", "true").lower() in ("true", "1")
    ANN_TOP_K: int = int(os.getenv("ANN_TOP_K", "50"))

    TEMPERATURE_BYPASS_THRESHOLD: float = 0.85
    STREAM_REPLAY_TOKENS_PER_SEC: float = 65.0
    SINGLEFLIGHT_TIMEOUT_SECONDS: float = 30.0
    
    HTTP_POOL_MAX_CONNECTIONS: int = 100
    HTTP_POOL_MAX_KEEPALIVE: int = 20
    HTTP_TIMEOUT_SECONDS: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "120.0"))
    HTTP_STREAM_READ_TIMEOUT_SECONDS: float = float(os.getenv("HTTP_STREAM_READ_TIMEOUT_SECONDS", "90.0"))


def validate_startup_security_invariants(host: str = None):
    """
    Validates critical security invariants before proxy boot.
    Fails closed if bound to non-localhost (0.0.0.0 or public IP) without REQUIRE_AUTH=true,
    unless explicitly bypassed with OMNICACHE_ALLOW_INSECURE_NETWORK_EXPOSURE=true.
    """
    target_host = (host or config.HOST).strip().lower()
    is_localhost = target_host in ("127.0.0.1", "localhost", "::1")
    if not is_localhost and not config.REQUIRE_AUTH and not config.ALLOW_INSECURE_NETWORK_EXPOSURE:
        raise RuntimeError(
            f"SECURITY ERROR: Refusing to bind OmniCache to non-localhost interface '{target_host}' "
            f"with REQUIRE_AUTH=false. This would expose an unauthenticated proxy to the network. "
            f"To fix: Set REQUIRE_AUTH=true (with ADMIN_API_KEY) or set OMNICACHE_ALLOW_INSECURE_NETWORK_EXPOSURE=true."
        )


config = ProxyConfig()
