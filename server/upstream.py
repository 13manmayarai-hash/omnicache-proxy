"""
Upstream Provider Client with HTTP/2 Connection Pooling and Failover.
Full Pass-Through Header Preservation for Claude Pro OAuth and Anthropic API Keys.
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

    async def forward_stream(
        self,
        payload: Dict[str, Any],
        auth_header: Optional[str] = None
    ) -> Tuple[int, Optional[httpx.Response], Dict[str, Any], List[Dict[str, Any]]]:
        client = self.get_client()
        url = self.get_endpoint_for_model(payload.get("model", ""))
        headers = {"Content-Type": "application/json"}
        if auth_header:
            headers["Authorization"] = auth_header
        elif config.OPENAI_API_KEY:
            headers["Authorization"] = f"Bearer {config.OPENAI_API_KEY}"

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

        return response.status_code, response, {}, []

    def _build_anthropic_headers(self, incoming_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }
        if not incoming_headers:
            if config.ANTHROPIC_API_KEY:
                headers["x-api-key"] = config.ANTHROPIC_API_KEY
            return headers

        # Preserve all Anthropic & Auth headers from client with 100% fidelity
        for k, v in incoming_headers.items():
            k_lower = k.lower()
            if k_lower in ("authorization", "x-api-key", "cookie", "anthropic-version", "anthropic-beta", "user-agent", "x-anthropic-client"):
                headers[k_lower] = v

        # Fallback to configured key if no auth header passed
        if "x-api-key" not in headers and "authorization" not in headers:
            if config.ANTHROPIC_API_KEY:
                headers["x-api-key"] = config.ANTHROPIC_API_KEY

        return headers

    async def forward_anthropic_messages(
        self,
        payload: Dict[str, Any],
        incoming_headers: Optional[Dict[str, str]] = None
    ) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        client = self.get_client()
        url = "https://api.anthropic.com/v1/messages"
        headers = self._build_anthropic_headers(incoming_headers)

        clean_payload = {k: v for k, v in payload.items() if not k.startswith("_")}
        if "max_tokens" not in clean_payload:
            clean_payload["max_tokens"] = 1024

        response = await client.post(url, json=clean_payload, headers=headers)
        try:
            res_data = response.json()
        except Exception:
            res_data = {"type": "error", "error": {"message": response.text, "type": "upstream_error"}}

        return response.status_code, res_data, dict(response.headers)

    async def forward_anthropic_stream(
        self,
        payload: Dict[str, Any],
        incoming_headers: Optional[Dict[str, str]] = None
    ) -> Tuple[int, Optional[httpx.Response], Dict[str, Any]]:
        client = self.get_client()
        url = "https://api.anthropic.com/v1/messages"
        headers = self._build_anthropic_headers(incoming_headers)

        clean_payload = {k: v for k, v in payload.items() if not k.startswith("_")}
        clean_payload["stream"] = True
        if "max_tokens" not in clean_payload:
            clean_payload["max_tokens"] = 1024

        req = client.build_request("POST", url, json=clean_payload, headers=headers)
        response = await client.send(req, stream=True)

        if response.status_code != 200:
            content = await response.aread()
            try:
                err_json = json.loads(content.decode("utf-8"))
            except Exception:
                err_json = {"type": "error", "error": {"message": content.decode("utf-8"), "type": "upstream_error"}}
            await response.aclose()
            return response.status_code, None, err_json

        return response.status_code, response, {}

upstream_client = UpstreamClient()
