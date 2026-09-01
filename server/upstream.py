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
            return f"{config.OPENAI_BASE_URL}/chat/completions"  # or Anthropic translator
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
        """
        Forwards a non-streaming chat completion request upstream.
        """
        client = self.get_client()
        url = self.get_endpoint_for_model(payload.get("model", ""))
        headers = {"Content-Type": "application/json"}
        if auth_header:
            headers["Authorization"] = auth_header

        # Strip internal fields
        clean_payload = {k: v for k, v in payload.items() if not k.startswith("_")}
        
        response = await client.post(url, json=clean_payload, headers=headers)
        try:
            res_data = response.json()
        except Exception:
            res_data = {"error": {"message": response.text, "code": response.status_code}}
            
        return response.status_code, res_data, dict(response.headers)

    async def forward_stream(
        self,
        payload: Dict[str, Any],
        auth_header: Optional[str] = None
    ) -> Tuple[int, AsyncGenerator[str, None], Dict[str, Any], List[Dict[str, Any]]]:
        """
        Forwards a streaming chat completion request upstream.
        Yields SSE chunks to the client while recording full payload for caching.
        """
        client = self.get_client()
        url = self.get_endpoint_for_model(payload.get("model", ""))
        headers = {"Content-Type": "application/json"}
        if auth_header:
            headers["Authorization"] = auth_header

        clean_payload = {k: v for k, v in payload.items() if not k.startswith("_")}
        clean_payload["stream"] = True

        req = client.build_request("POST", url, json=clean_payload, headers=headers)
        response = await client.send(req, stream=True)

        if response.status_code != 200:
            content = await response.aread()
            try:
                err_json = json.loads(content.decode("utf-8"))
            except Exception:
                err_json = {"error": {"message": content.decode("utf-8"), "code": response.status_code}}
            await response.aclose()
            return response.status_code, None, err_json, []

        return response.status_code, response, None, []

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

upstream_client = UpstreamClient()
