"""
Virtual Key Management, Budget Quotas & Rate Limiting Engine.
Allows engineering leaders to issue per-team virtual keys with hard monthly spend caps ($ USD),
per-minute rate limits, and alert webhooks.
"""

import time
from typing import Dict, Any, Optional, Tuple

class VirtualKeyManager:
    """Manages team keys, budget tracking, and rate limiting."""
    def __init__(self):
        self._keys: Dict[str, Dict[str, Any]] = {
            "default": {
                "team_name": "Default Workspace",
                "monthly_budget_usd": 1000.0,
                "current_spend_usd": 0.0,
                "rate_limit_rpm": 300,
                "request_timestamps": [],
                "created_at": time.time()
            }
        }

    def register_key(self, key_id: str, team_name: str, monthly_budget_usd: float = 100.0, rate_limit_rpm: int = 120) -> Dict[str, Any]:
        """Registers a new virtual key."""
        self._keys[key_id] = {
            "team_name": team_name,
            "monthly_budget_usd": monthly_budget_usd,
            "current_spend_usd": 0.0,
            "rate_limit_rpm": rate_limit_rpm,
            "request_timestamps": [],
            "created_at": time.time()
        }
        return self._keys[key_id]

    def check_authorization(self, key_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Validates rate limits and budget caps.
        Returns (is_allowed, error_reason, key_info).
        """
        if key_id not in self._keys:
            # Auto-register unconfigured keys with default tier
            self.register_key(key_id, team_name=f"Team-{key_id}")

        info = self._keys[key_id]
        now = time.time()

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

    def record_spend(self, key_id: str, spend_usd: float):
        """Records token cost against a key's monthly budget."""
        if key_id in self._keys:
            self._keys[key_id]["current_spend_usd"] += spend_usd

    def get_all_quotas(self) -> Dict[str, Any]:
        """Returns summary of all virtual keys and current spend."""
        summary = {}
        for k, v in self._keys.items():
            summary[k] = {
                "team_name": v["team_name"],
                "monthly_budget_usd": v["monthly_budget_usd"],
                "current_spend_usd": round(v["current_spend_usd"], 4),
                "budget_used_pct": round((v["current_spend_usd"] / max(0.01, v["monthly_budget_usd"])) * 100, 2),
                "active_rpm": len(v["request_timestamps"])
            }
        return summary

# Global Virtual Key Manager instance
quota_manager = VirtualKeyManager()
