"""
Virtual Key Management, Budget Quotas & Rate Limiting Engine.
Supports local in-memory storage and distributed Redis backends with atomic spend and sliding-window rate limiting.
"""

import time
import hmac
import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Tuple, List
from core.config import config

logger = logging.getLogger("omnicache.quotas")


class BaseQuotaStorage(ABC):
    @abstractmethod
    def register_key(
        self,
        key_id: str,
        team_name: str,
        org_id: Optional[str] = None,
        monthly_budget_usd: float = 100.0,
        rate_limit_rpm: int = 120,
        role: str = "tenant"
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_key(self, key_id: str) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def check_and_record_rate_limit(self, key_id: str, limit_rpm: int) -> Tuple[bool, int]:
        pass

    @abstractmethod
    def get_spend(self, key_id: str) -> float:
        pass

    @abstractmethod
    def record_spend(self, key_id: str, spend_usd: float):
        pass

    @abstractmethod
    def get_all_keys(self) -> Dict[str, Dict[str, Any]]:
        pass


class InMemoryQuotaStorage(BaseQuotaStorage):
    def __init__(self):
        self._keys: Dict[str, Dict[str, Any]] = {}

    def register_key(
        self,
        key_id: str,
        team_name: str,
        org_id: Optional[str] = None,
        monthly_budget_usd: float = 100.0,
        rate_limit_rpm: int = 120,
        role: str = "tenant"
    ) -> Dict[str, Any]:
        self._keys[key_id] = {
            "team_name": team_name,
            "org_id": org_id or team_name,
            "role": role,
            "monthly_budget_usd": monthly_budget_usd,
            "current_spend_usd": 0.0,
            "rate_limit_rpm": rate_limit_rpm,
            "request_timestamps": [],
            "created_at": time.time()
        }
        return self._keys[key_id]

    def get_key(self, key_id: str) -> Optional[Dict[str, Any]]:
        return self._keys.get(key_id)

    def check_and_record_rate_limit(self, key_id: str, limit_rpm: int) -> Tuple[bool, int]:
        info = self._keys.get(key_id)
        if not info:
            return True, 0
        now = time.time()
        window_start = now - 60.0
        info["request_timestamps"] = [ts for ts in info["request_timestamps"] if ts > window_start]
        if len(info["request_timestamps"]) >= limit_rpm:
            return False, len(info["request_timestamps"])
        info["request_timestamps"].append(now)
        return True, len(info["request_timestamps"])

    def get_spend(self, key_id: str) -> float:
        info = self._keys.get(key_id)
        return float(info.get("current_spend_usd", 0.0)) if info else 0.0

    def record_spend(self, key_id: str, spend_usd: float):
        if key_id in self._keys:
            self._keys[key_id]["current_spend_usd"] += spend_usd

    def get_all_keys(self) -> Dict[str, Dict[str, Any]]:
        return self._keys


class RedisQuotaStorage(BaseQuotaStorage):
    """Distributed Redis quota and sliding-window rate limit manager."""

    def __init__(self, redis_client=None, redis_url: str = "redis://127.0.0.1:6379/0", prefix: str = "omnicache"):
        self.prefix = prefix
        if redis_client is not None:
            self.client = redis_client
        else:
            import redis
            self.client = redis.Redis.from_url(redis_url, decode_responses=True)

    def _meta_key(self, key_id: str) -> str:
        return f"{self.prefix}:quota:meta:{key_id}"

    def _spend_key(self, key_id: str) -> str:
        return f"{self.prefix}:quota:spend:{key_id}"

    def _rpm_key(self, key_id: str) -> str:
        return f"{self.prefix}:quota:rpm:{key_id}"

    def _reg_set_key(self) -> str:
        return f"{self.prefix}:quota:keys"

    def register_key(
        self,
        key_id: str,
        team_name: str,
        org_id: Optional[str] = None,
        monthly_budget_usd: float = 100.0,
        rate_limit_rpm: int = 120,
        role: str = "tenant"
    ) -> Dict[str, Any]:
        info = {
            "team_name": team_name,
            "org_id": org_id or team_name,
            "role": role,
            "monthly_budget_usd": monthly_budget_usd,
            "rate_limit_rpm": rate_limit_rpm,
            "created_at": time.time()
        }
        try:
            pipe = self.client.pipeline()
            pipe.hset(self._meta_key(key_id), mapping={k: str(v) for k, v in info.items()})
            pipe.sadd(self._reg_set_key(), key_id)
            pipe.execute()
        except Exception as exc:
            logger.warning("Redis register_key failed: %s", exc)
        info["current_spend_usd"] = self.get_spend(key_id)
        info["request_timestamps"] = []
        return info

    def get_key(self, key_id: str) -> Optional[Dict[str, Any]]:
        try:
            raw = self.client.hgetall(self._meta_key(key_id))
            if not raw:
                return None
            spend = self.get_spend(key_id)
            return {
                "team_name": raw.get("team_name", "Team"),
                "org_id": raw.get("org_id", "default"),
                "role": raw.get("role", "tenant"),
                "monthly_budget_usd": float(raw.get("monthly_budget_usd", 100.0)),
                "current_spend_usd": spend,
                "rate_limit_rpm": int(raw.get("rate_limit_rpm", 120)),
                "request_timestamps": [],
                "created_at": float(raw.get("created_at", 0.0))
            }
        except Exception as exc:
            logger.warning("Redis get_key failed: %s", exc)
            return None

    def check_and_record_rate_limit(self, key_id: str, limit_rpm: int) -> Tuple[bool, int]:
        try:
            now = time.time()
            window_start = now - 60.0
            rpm_k = self._rpm_key(key_id)
            
            pipe = self.client.pipeline()
            pipe.zremrangebyscore(rpm_k, 0, window_start)
            pipe.zcard(rpm_k)
            results = pipe.execute()
            current_count = results[1]

            if current_count >= limit_rpm:
                return False, current_count

            pipe = self.client.pipeline()
            pipe.zadd(rpm_k, {f"{now}:{time.time_ns()}": now})
            pipe.expire(rpm_k, 120)
            pipe.execute()
            return True, current_count + 1
        except Exception as exc:
            logger.warning("Redis rate limit check failed: %s", exc)
            return True, 0

    def get_spend(self, key_id: str) -> float:
        try:
            val = self.client.get(self._spend_key(key_id))
            return float(val) if val else 0.0
        except Exception as exc:
            logger.warning("Redis get_spend failed: %s", exc)
            return 0.0

    def record_spend(self, key_id: str, spend_usd: float):
        try:
            self.client.incrbyfloat(self._spend_key(key_id), spend_usd)
        except Exception as exc:
            logger.warning("Redis record_spend failed: %s", exc)

    def get_all_keys(self) -> Dict[str, Dict[str, Any]]:
        result = {}
        try:
            all_key_ids = self.client.smembers(self._reg_set_key())
            for kid in all_key_ids:
                info = self.get_key(kid)
                if info:
                    result[kid] = info
        except Exception as exc:
            logger.warning("Redis get_all_keys failed: %s", exc)
        return result


class VirtualKeyManager:
    """Manages team keys, budget tracking, rate limiting, and RBAC."""
    def __init__(self, storage: Optional[BaseQuotaStorage] = None):
        if storage is not None:
            self.storage = storage
        else:
            self.storage = self._init_storage()

        self._seed_default_keys()

    @property
    def _keys(self) -> Dict[str, Dict[str, Any]]:
        return self.storage.get_all_keys()

    def _init_storage(self) -> BaseQuotaStorage:
        backend = getattr(config, "CACHE_STORAGE_BACKEND", "auto")
        redis_url = getattr(config, "REDIS_URL", "")
        prefix = getattr(config, "REDIS_KEY_PREFIX", "omnicache")
        if backend == "redis" or (backend == "auto" and redis_url):
            try:
                return RedisQuotaStorage(redis_url=redis_url, prefix=prefix)
            except Exception as exc:
                print(f"⚠️ [OmniCache] Failed to connect to Redis quota storage: {exc}. Using InMemory.")
                return InMemoryQuotaStorage()
        return InMemoryQuotaStorage()

    def _seed_default_keys(self):
        # Default workspace tenant key
        if not self.storage.get_key("default"):
            self.storage.register_key("default", team_name="Default Workspace", org_id="default", role="tenant", monthly_budget_usd=1000.0, rate_limit_rpm=300)

        # Master admin key
        admin_key = getattr(config, "ADMIN_API_KEY", "").strip()
        if admin_key:
            self.storage.register_key(admin_key, team_name="System Administrator", org_id="admin", role="admin", monthly_budget_usd=1000000.0, rate_limit_rpm=10000)

    def register_key(
        self,
        key_id: str,
        team_name: str,
        org_id: Optional[str] = None,
        monthly_budget_usd: float = 100.0,
        rate_limit_rpm: int = 120,
        role: str = "tenant"
    ) -> Dict[str, Any]:
        """Registers a new virtual key with explicit budget, rate limits, and tenant org_id."""
        return self.storage.register_key(
            key_id,
            team_name=team_name,
            org_id=org_id,
            monthly_budget_usd=monthly_budget_usd,
            rate_limit_rpm=rate_limit_rpm,
            role=role
        )

    def check_authorization(self, key_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Validates whether an API key exists, and checks rate limits and budget caps.
        Returns (is_allowed, reason_or_error, key_info).
        """
        if not key_id:
            return False, "Missing API key in Authorization header or x-api-key", None

        # Check against configured ADMIN_API_KEY with constant-time comparison
        admin_key = getattr(config, "ADMIN_API_KEY", "").strip()
        if admin_key and hmac.compare_digest(key_id, admin_key):
            admin_info = self.storage.get_key(admin_key)
            if not admin_info:
                admin_info = self.register_key(admin_key, team_name="System Administrator", org_id="admin", role="admin", monthly_budget_usd=1000000.0, rate_limit_rpm=10000)
            return True, "authorized", admin_info

        info = self.storage.get_key(key_id)

        # In local developer mode (REQUIRE_AUTH=False), allow "default" or empty key
        if not info:
            if not getattr(config, "REQUIRE_AUTH", False) and key_id in ("default", ""):
                info = self.register_key("default", team_name="Default Workspace", org_id="default", role="tenant")
                key_id = "default"
            else:
                return False, "Unauthorized: Invalid or unrecognized virtual API key", None

        # Admin role bypasses tenant rate limit and budget caps
        if info.get("role") == "admin":
            return True, "authorized", info

        # 1. Check Rate Limit (Sliding Window 60s)
        rate_ok, current_rpm = self.storage.check_and_record_rate_limit(key_id, info["rate_limit_rpm"])
        if not rate_ok:
            return False, f"Rate limit exceeded ({info['rate_limit_rpm']} RPM)", info

        # 2. Check Monthly Budget Cap
        current_spend = self.storage.get_spend(key_id)
        info["current_spend_usd"] = current_spend
        if current_spend >= info["monthly_budget_usd"]:
            return False, f"Monthly budget cap exceeded (${info['monthly_budget_usd']:.2f})", info

        return True, "authorized", info

    def is_admin(self, key_id: str) -> bool:
        """
        Checks if a key has administrator privileges.
        Requires explicit ADMIN_API_KEY match or an explicit key registered with role='admin'.
        Zero hardcoded bypass strings.
        """
        if not key_id:
            return False
        admin_key = getattr(config, "ADMIN_API_KEY", "").strip()
        if admin_key and hmac.compare_digest(key_id, admin_key):
            return True
        key_info = self.storage.get_key(key_id)
        return key_info is not None and key_info.get("role") == "admin"

    def record_spend(self, key_id: str, spend_usd: float):
        """Records token cost against a key's monthly budget."""
        self.storage.record_spend(key_id, spend_usd)

    def get_all_quotas(self) -> Dict[str, Any]:
        """Returns summary of all virtual keys and current spend."""
        summary = {}
        all_keys = self.storage.get_all_keys()
        for k, v in all_keys.items():
            masked_key = f"{k[:4]}...{k[-4:]}" if len(k) > 10 else (k if k == "default" else "key_***")
            spend = self.storage.get_spend(k)
            summary[masked_key] = {
                "team_name": v["team_name"],
                "org_id": v.get("org_id", v["team_name"]),
                "role": v.get("role", "tenant"),
                "monthly_budget_usd": v["monthly_budget_usd"],
                "current_spend_usd": round(spend, 4),
                "budget_used_pct": round((spend / max(0.01, v["monthly_budget_usd"])) * 100, 2),
                "active_rpm": len(v.get("request_timestamps", []))
            }
        return summary

# Global Virtual Key Manager instance
quota_manager = VirtualKeyManager()
