"""
Virtual Key Management, Budget Quotas & Rate Limiting Engine.
Allows engineering leaders to issue per-team virtual keys with hard monthly spend caps ($ USD),
per-minute rate limits, role-based access control, and tenant isolation.
"""

import time
import hmac
from typing import Dict, Any, Optional, Tuple
from core.config import config

class VirtualKeyManager:
    """Manages team keys, budget tracking, rate limiting, and RBAC."""
    def __init__(self):
        self._keys: Dict[str, Dict[str, Any]] = {
            "default": {
                "team_name": "Default Workspace",
                "org_id": "default",
                "role": "admin",
                "monthly_budget_usd": 1000.0,
                "current_spend_usd": 0.0,
                "rate_limit_rpm": 300,
                "request_timestamps": [],
                "created_at": time.time()
            }
        }
        # Pre-register master admin key from configuration if specified
        admin_key = getattr(config, "ADMIN_API_KEY", "").strip()
        if admin_key:
            self._keys[admin_key] = {
                "team_name": "System Administrator",
                "org_id": "admin",
                "role": "admin",
                "monthly_budget_usd": 1000000.0,
                "current_spend_usd": 0.0,
                "rate_limit_rpm": 10000,
                "request_timestamps": [],
                "created_at": time.time()
            }

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
            if admin_key not in self._keys:
                self.register_key(admin_key, team_name="System Administrator", org_id="admin", role="admin", monthly_budget_usd=1000000.0, rate_limit_rpm=10000)
            return True, "authorized", self._keys[admin_key]

        # For development / single-user convenience when REQUIRE_AUTH is false and default key matches:
        if not getattr(config, "REQUIRE_AUTH", False) and key_id in ("default", "dev", "test_key_123"):
            if key_id not in self._keys:
                self.register_key(key_id, team_name="Development Workspace", org_id="default", role="admin")
            return True, "authorized", self._keys[key_id]

        # Reject unrecognized keys - STRICTLY NO auto-registration
        if key_id not in self._keys:
            return False, "Unauthorized: Invalid or unrecognized virtual API key", None

        info = self._keys[key_id]
        now = time.time()

        # Admin role bypasses tenant rate limit and budget caps
        if info.get("role") == "admin":
            return True, "authorized", info

        # 1. Check Rate Limit (Sliding Window 60s)
        window_start = now - 60.0
        info["request_timestamps"] = [ts for ts in info["request_timestamps"] if ts > window_start]
        if len(info["request_timestamps"]) >= info["rate_limit_rpm"]:
            return False, f"Rate limit exceeded ({info['rate_limit_rpm']} RPM)", info

        # 2. Check Monthly Budget Cap
        if info["current_spend_usd"] >= info["monthly_budget_usd"]:
            return False, f"Monthly budget cap exceeded (${info['monthly_budget_usd']:.2f})", info

        info["request_timestamps"].append(now)
        return True, "authorized", info

    def is_admin(self, key_id: str) -> bool:
        """Checks if a key has administrator privileges."""
        if not key_id:
            return False
        admin_key = getattr(config, "ADMIN_API_KEY", "").strip()
        if admin_key and hmac.compare_digest(key_id, admin_key):
            return True
        key_info = self._keys.get(key_id)
        return key_info is not None and key_info.get("role") == "admin"

    def record_spend(self, key_id: str, spend_usd: float):
        """Records token cost against a key's monthly budget."""
        if key_id in self._keys:
            self._keys[key_id]["current_spend_usd"] += spend_usd

    def get_all_quotas(self) -> Dict[str, Any]:
        """Returns summary of all virtual keys and current spend."""
        summary = {}
        for k, v in self._keys.items():
            # Mask sensitive key string in quota dumps
            masked_key = f"{k[:4]}...{k[-4:]}" if len(k) > 10 else k
            summary[masked_key] = {
                "team_name": v["team_name"],
                "org_id": v.get("org_id", v["team_name"]),
                "role": v.get("role", "tenant"),
                "monthly_budget_usd": v["monthly_budget_usd"],
                "current_spend_usd": round(v["current_spend_usd"], 4),
                "budget_used_pct": round((v["current_spend_usd"] / max(0.01, v["monthly_budget_usd"])) * 100, 2),
                "active_rpm": len(v["request_timestamps"])
            }
        return summary

# Global Virtual Key Manager instance
quota_manager = VirtualKeyManager()
