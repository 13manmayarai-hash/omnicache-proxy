"""
Upstream Provider Client with HTTP/2 Connection Pooling and Failover.
Forwards requests to OpenAI/Anthropic/Gemini and intercepts completions for caching.
"""

import httpx
import json
import time
from typing import Dict, Any, Tuple, Optional, AsyncGenerator, List
from core.config import config, MODEL_PRICING

class UpstreamClient:
    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    def get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            limits = httpx.Limits(
                max_connections=config.HTTP_POOL_MAX_CONNECTIONS,
                max_keepalive_connections=config.HTTP_POOL_MAX_KEEPALIVE,
                keepalive_expiry=30.0
            )
            self._client = httpx.AsyncClient(
                limits=limits,
                timeout=httpx.Timeout(config.HTTP_TIMEOUT_SECONDS, connect=10.0)
            )
        return self._client

    @staticmethod
    def get_endpoint_for_model(model: str) -> str:
        model_lower = model.lower()
        if "gemini" in model_lower:
            return f"{config.GEMINI_BASE_URL}/chat/completions"
        elif "claude" in model_lower:
            return f"{config.OPENAI_BASE_URL}/chat/completions"
        return f"{config.OPENAI_BASE_URL}/chat/completions"

    @classmethod
    def calculate_savings(cls, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Calculates total dollar savings for a cache hit."""
        pricing = MODEL_PRICING.get(model.lower(), MODEL_PRICING["default"])
        in_cost = (prompt_tokens / 1_000_000.0) * pricing["input"]
        out_cost = (completion_tokens / 1_000_000.0) * pricing["output"]
        return in_cost + out_cost

    async def forward_non_stream(
        self,
        payload: Dict[str, Any],
        auth_header: Optional[str] = None
    ) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        client = self.get_client()
        url = self.get_endpoint_for_model(payload.get("model", ""))
        headers = {"Content-Type": "application/json"}
        if auth_header:
            headers["Authorization"] = auth_header
        elif config.OPENAI_API_KEY:
            headers["Authorization"] = f"Bearer {config.OPENAI_API_KEY}"

        clean_payload = {k: v for k, v in payload.items() if not k.startswith("_")}
        response = await client.post(url, json=clean_payload, headers=headers)
        try:
            res_data = response.json()
        except Exception:
            res_data = {"error": {"message": response.text, "code": response.status_code}}
            
        return response.status_code, res_data, dict(response.headers)

    async def forward_anthropic_messages(
        self,
        payload: Dict[str, Any],
        api_key_header: Optional[str] = None
    ) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        """
        Directly forwards an Anthropic Messages API payload to https://api.anthropic.com/v1/messages.
        """
        client = self.get_client()
        url = "https://api.anthropic.com/v1/messages"
        
        api_key = api_key_header or config.ANTHROPIC_API_KEY
        if api_key and api_key.startswith("Bearer "):
            api_key = api_key.replace("Bearer ", "").strip()

        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01"
        }

        clean_payload = {k: v for k, v in payload.items() if not k.startswith("_")}
        if "max_tokens" not in clean_payload:
            clean_payload["max_tokens"] = 1024

        response = await client.post(url, json=clean_payload, headers=headers)
        try:
            res_data = response.json()
        except Exception:
            res_data = {"type": "error", "error": {"message": response.text, "type": "upstream_error"}}

        return response.status_code, res_data, dict(response.headers)

upstream_client = UpstreamClient()
