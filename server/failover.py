"""
Provider Failover and Circuit Breaker Engine.
Automatically routes requests to secondary providers when primary models suffer rate-limits (429) or outages (5xx).
"""

import time
import asyncio
from typing import Dict, Any, Tuple, Optional, List
from core.config import config
from server.translator import ProtocolTranslator

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_timeout_seconds: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.consecutive_failures: Dict[str, int] = {}
        self.opened_at: Dict[str, float] = {}

    def is_available(self, provider: str) -> bool:
        """Checks if circuit is closed (healthy) or ready for test probe."""
        if provider not in self.opened_at:
            return True
        # If open, check if recovery timeout expired
        if time.time() - self.opened_at[provider] > self.recovery_timeout_seconds:
            # Half-open: allow a trial request
            return True
        return False

    def record_success(self, provider: str):
        self.consecutive_failures[provider] = 0
        if provider in self.opened_at:
            del self.opened_at[provider]

    def record_failure(self, provider: str):
        self.consecutive_failures[provider] = self.consecutive_failures.get(provider, 0) + 1
        if self.consecutive_failures[provider] >= self.failure_threshold:
            self.opened_at[provider] = time.time()

class FailoverOrchestrator:
    def __init__(self):
        self.circuit_breaker = CircuitBreaker()

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
