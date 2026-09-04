"""
Provider Failover and Circuit Breaker Engine.
Supports in-memory and distributed Redis state for synchronized cluster-wide resilience.
"""

import time
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple, Optional, List
from core.config import config
from server.translator import ProtocolTranslator

logger = logging.getLogger("omnicache.failover")


class BaseCircuitBreakerStorage(ABC):
    @abstractmethod
    def is_available(self, provider: str, recovery_timeout: float) -> bool:
        pass

    @abstractmethod
    def record_success(self, provider: str):
        pass

    @abstractmethod
    def record_failure(self, provider: str, threshold: int, recovery_timeout: float):
        pass

    @abstractmethod
    def get_status(self, recovery_timeout: float) -> Dict[str, Any]:
        pass


class InMemoryCircuitBreakerStorage(BaseCircuitBreakerStorage):
    def __init__(self):
        self.consecutive_failures: Dict[str, int] = {}
        self.opened_at: Dict[str, float] = {}

    def is_available(self, provider: str, recovery_timeout: float) -> bool:
        if provider not in self.opened_at:
            return True
        if time.time() - self.opened_at[provider] > recovery_timeout:
            return True
        return False

    def record_success(self, provider: str):
        self.consecutive_failures[provider] = 0
        if provider in self.opened_at:
            del self.opened_at[provider]

    def record_failure(self, provider: str, threshold: int, recovery_timeout: float):
        self.consecutive_failures[provider] = self.consecutive_failures.get(provider, 0) + 1
        if self.consecutive_failures[provider] >= threshold:
            self.opened_at[provider] = time.time()

    def get_status(self, recovery_timeout: float) -> Dict[str, Any]:
        now = time.time()
        status = {}
        for p in ("openai", "anthropic", "google"):
            opened = p in self.opened_at
            failures = self.consecutive_failures.get(p, 0)
            if opened:
                if now - self.opened_at[p] > recovery_timeout:
                    state = "half-open"
                else:
                    state = "open"
            else:
                state = "closed"
            status[p] = {
                "state": state,
                "consecutive_failures": failures
            }
        return status


class RedisCircuitBreakerStorage(BaseCircuitBreakerStorage):
    """Cluster-wide distributed circuit breaker storage in Redis."""

    def __init__(self, redis_client=None, redis_url: str = "redis://127.0.0.1:6379/0", prefix: str = "omnicache"):
        self.prefix = prefix
        if redis_client is not None:
            self.client = redis_client
        else:
            import redis
            self.client = redis.Redis.from_url(redis_url, decode_responses=True)

    def _fail_key(self, provider: str) -> str:
        return f"{self.prefix}:circuit:fail:{provider}"

    def _open_key(self, provider: str) -> str:
        return f"{self.prefix}:circuit:open:{provider}"

    def is_available(self, provider: str, recovery_timeout: float) -> bool:
        try:
            val = self.client.get(self._open_key(provider))
            if not val:
                return True
            opened_ts = float(val)
            if time.time() - opened_ts > recovery_timeout:
                return True
            return False
        except Exception as exc:
            logger.warning("Redis circuit is_available check failed: %s", exc)
            return True

    def record_success(self, provider: str):
        try:
            pipe = self.client.pipeline()
            pipe.delete(self._fail_key(provider))
            pipe.delete(self._open_key(provider))
            pipe.execute()
        except Exception as exc:
            logger.warning("Redis circuit record_success failed: %s", exc)

    def record_failure(self, provider: str, threshold: int, recovery_timeout: float):
        try:
            fk = self._fail_key(provider)
            failures = self.client.incr(fk)
            self.client.expire(fk, 300)
            if failures >= threshold:
                ok = self._open_key(provider)
                self.client.set(ok, str(time.time()), ex=max(60, int(recovery_timeout * 4)))
        except Exception as exc:
            logger.warning("Redis circuit record_failure failed: %s", exc)

    def get_status(self, recovery_timeout: float) -> Dict[str, Any]:
        now = time.time()
        status = {}
        for p in ("openai", "anthropic", "google"):
            try:
                open_val = self.client.get(self._open_key(p))
                fail_val = self.client.get(self._fail_key(p))
                failures = int(fail_val) if fail_val else 0

                if open_val:
                    opened_ts = float(open_val)
                    if now - opened_ts > recovery_timeout:
                        state = "half-open"
                    else:
                        state = "open"
                else:
                    state = "closed"

                status[p] = {
                    "state": state,
                    "consecutive_failures": failures
                }
            except Exception as exc:
                logger.warning("Redis circuit get_status failed for %s: %s", p, exc)
                status[p] = {"state": "closed", "consecutive_failures": 0}
        return status


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
        storage: Optional[BaseCircuitBreakerStorage] = None
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        if storage is not None:
            self.storage = storage
        else:
            self.storage = self._init_storage()

    def _init_storage(self) -> BaseCircuitBreakerStorage:
        backend = getattr(config, "CACHE_STORAGE_BACKEND", "auto")
        redis_url = getattr(config, "REDIS_URL", "")
        prefix = getattr(config, "REDIS_KEY_PREFIX", "omnicache")
        if backend == "redis" or (backend == "auto" and redis_url):
            try:
                return RedisCircuitBreakerStorage(redis_url=redis_url, prefix=prefix)
            except Exception as exc:
                print(f"⚠️ [OmniCache] Failed to connect to Redis circuit breaker: {exc}. Using InMemory.")
                return InMemoryCircuitBreakerStorage()
        return InMemoryCircuitBreakerStorage()

    def is_available(self, provider: str) -> bool:
        """Checks if circuit is closed (healthy) or ready for test probe."""
        return self.storage.is_available(provider, self.recovery_timeout_seconds)

    def record_success(self, provider: str):
        self.storage.record_success(provider)

    def record_failure(self, provider: str):
        self.storage.record_failure(provider, self.failure_threshold, self.recovery_timeout_seconds)

    def get_status(self) -> Dict[str, Any]:
        """Returns the status of all tracked providers."""
        return self.storage.get_status(self.recovery_timeout_seconds)


class FailoverOrchestrator:
    def __init__(self, circuit_breaker: Optional[CircuitBreaker] = None):
        self.circuit_breaker = circuit_breaker or CircuitBreaker()

    @staticmethod
    def identify_provider(model: str) -> str:
        model_lower = model.lower()
        if "claude" in model_lower:
            return "anthropic"
        elif "gemini" in model_lower:
            return "google"
        return "openai"

    def get_fallback_chain(self, model: str) -> List[str]:
        """Returns list of fallback model names for the requested model."""
        return ProtocolTranslator.FALLBACK_MAP.get(model.lower(), ["gpt-4o-mini", "gemini-2.5-flash"])

failover_engine = FailoverOrchestrator()
