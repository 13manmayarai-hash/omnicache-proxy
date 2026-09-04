"""
Upstream Provider Client with HTTP/2 Connection Pooling, Circuit Breaker, and Multi-Provider Failover.
Full Pass-Through Header & Query Parameter Preservation.
"""

import httpx
import json
import time
from typing import Dict, Any, Tuple, Optional, List
from core.config import config, MODEL_PRICING
from server.failover import failover_engine
from server.translator import ProtocolTranslator

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
        """
        Forwards non-streaming OpenAI-format request with automated CircuitBreaker checking and model failover.
        """
        client = self.get_client()
        original_model = payload.get("model", "gpt-4o")
        candidate_models = [original_model] + failover_engine.get_fallback_chain(original_model)

        last_status = 500
        last_res_data: Dict[str, Any] = {"error": {"message": "All upstream providers failed."}}
        last_headers: Dict[str, str] = {}

        for model_candidate in candidate_models:
            provider = failover_engine.identify_provider(model_candidate)
            if not failover_engine.circuit_breaker.is_available(provider):
                continue

            current_payload = dict(payload)
            current_payload["model"] = model_candidate

            try:
                # If failover selected an Anthropic model from an OpenAI request:
                if provider == "anthropic":
                    anthropic_payload = ProtocolTranslator.openai_to_anthropic_payload(current_payload)
                    status_code, res_data, headers = await self.forward_anthropic_messages(anthropic_payload)
                    if status_code == 200:
                        failover_engine.circuit_breaker.record_success(provider)
                        translated_openai = ProtocolTranslator.anthropic_to_openai_response(res_data, original_model=model_candidate)
                        return status_code, translated_openai, headers
                    elif status_code in (429, 500, 502, 503, 504):
                        failover_engine.circuit_breaker.record_failure(provider)
                        last_status, last_res_data, last_headers = status_code, res_data, headers
                        continue
                    else:
                        return status_code, res_data, headers

                # Standard OpenAI / Gemini REST endpoint
                url = self.get_endpoint_for_model(model_candidate)
                headers = {"Content-Type": "application/json"}
                if auth_header:
                    headers["Authorization"] = auth_header
                elif "gemini" in model_candidate.lower() and config.GEMINI_API_KEY:
                    headers["Authorization"] = f"Bearer {config.GEMINI_API_KEY}"
                elif config.OPENAI_API_KEY:
                    headers["Authorization"] = f"Bearer {config.OPENAI_API_KEY}"

                clean_payload = {k: v for k, v in current_payload.items() if not k.startswith("_")}
                response = await client.post(url, json=clean_payload, headers=headers)

                try:
                    res_data = response.json()
                except Exception:
                    res_data = {"error": {"message": response.text, "code": response.status_code}}

                if response.status_code == 200:
                    failover_engine.circuit_breaker.record_success(provider)
                    return response.status_code, res_data, dict(response.headers)
                elif response.status_code in (429, 500, 502, 503, 504):
                    failover_engine.circuit_breaker.record_failure(provider)
                    last_status, last_res_data, last_headers = response.status_code, res_data, dict(response.headers)
                    continue
                else:
                    return response.status_code, res_data, dict(response.headers)

            except (httpx.RequestError, httpx.TimeoutException, Exception) as exc:
                failover_engine.circuit_breaker.record_failure(provider)
                last_res_data = {"error": {"message": str(exc), "type": "upstream_connection_error"}}
                continue

        return last_status, last_res_data, last_headers

    async def forward_stream(
        self,
        payload: Dict[str, Any],
        auth_header: Optional[str] = None
    ) -> Tuple[int, Optional[httpx.Response], Dict[str, Any], List[Dict[str, Any]]]:
        client = self.get_client()
        model = payload.get("model", "")
        provider = failover_engine.identify_provider(model)

        url = self.get_endpoint_for_model(model)
        headers = {"Content-Type": "application/json"}
        if auth_header:
            headers["Authorization"] = auth_header
        elif "gemini" in model.lower() and config.GEMINI_API_KEY:
            headers["Authorization"] = f"Bearer {config.GEMINI_API_KEY}"
        elif config.OPENAI_API_KEY:
            headers["Authorization"] = f"Bearer {config.OPENAI_API_KEY}"

        clean_payload = {k: v for k, v in payload.items() if not k.startswith("_")}
        clean_payload["stream"] = True

        try:
            req = client.build_request("POST", url, json=clean_payload, headers=headers)
            response = await client.send(req, stream=True)

            if response.status_code != 200:
                content = await response.aread()
                try:
                    err_json = json.loads(content.decode("utf-8"))
                except Exception:
                    err_json = {"error": {"message": content.decode("utf-8"), "code": response.status_code}}
                await response.aclose()
                if response.status_code in (429, 500, 502, 503, 504):
                    failover_engine.circuit_breaker.record_failure(provider)
                return response.status_code, None, err_json, []

            failover_engine.circuit_breaker.record_success(provider)
            return response.status_code, response, {}, []
        except Exception as exc:
            failover_engine.circuit_breaker.record_failure(provider)
            return 503, None, {"error": {"message": str(exc)}}, []

    def _build_anthropic_headers(self, incoming_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }
        if not incoming_headers:
            if config.ANTHROPIC_API_KEY:
                headers["x-api-key"] = config.ANTHROPIC_API_KEY
            return headers

        for k, v in incoming_headers.items():
            k_lower = k.lower()
            if k_lower in ("authorization", "x-api-key", "cookie", "anthropic-version", "anthropic-beta", "user-agent", "x-anthropic-client"):
                headers[k_lower] = v

        if "x-api-key" not in headers and "authorization" not in headers:
            if config.ANTHROPIC_API_KEY:
                headers["x-api-key"] = config.ANTHROPIC_API_KEY

        return headers

    async def forward_anthropic_messages(
        self,
        payload: Dict[str, Any],
        incoming_headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None
    ) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        client = self.get_client()
        url = "https://api.anthropic.com/v1/messages"
        headers = self._build_anthropic_headers(incoming_headers)

        clean_payload = {k: v for k, v in payload.items() if not k.startswith("_")}
        if "max_tokens" not in clean_payload:
            clean_payload["max_tokens"] = 1024

        try:
            response = await client.post(url, json=clean_payload, headers=headers, params=params)
            try:
                res_data = response.json()
            except Exception:
                res_data = {"type": "error", "error": {"message": response.text, "type": "upstream_error"}}

            if response.status_code == 200:
                failover_engine.circuit_breaker.record_success("anthropic")
            elif response.status_code in (429, 500, 502, 503, 504):
                failover_engine.circuit_breaker.record_failure("anthropic")

            return response.status_code, res_data, dict(response.headers)
        except Exception as exc:
            failover_engine.circuit_breaker.record_failure("anthropic")
            return 503, {"type": "error", "error": {"message": str(exc), "type": "upstream_error"}}, {}

    async def forward_anthropic_stream(
        self,
        payload: Dict[str, Any],
        incoming_headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None
    ) -> Tuple[int, Optional[httpx.Response], Dict[str, Any]]:
        client = self.get_client()
        url = "https://api.anthropic.com/v1/messages"
        headers = self._build_anthropic_headers(incoming_headers)

        clean_payload = {k: v for k, v in payload.items() if not k.startswith("_")}
        clean_payload["stream"] = True
        if "max_tokens" not in clean_payload:
            clean_payload["max_tokens"] = 1024

        try:
            req = client.build_request("POST", url, json=clean_payload, headers=headers, params=params)
            response = await client.send(req, stream=True)

            if response.status_code != 200:
                content = await response.aread()
                try:
                    err_json = json.loads(content.decode("utf-8"))
                except Exception:
                    err_json = {"type": "error", "error": {"message": content.decode("utf-8"), "type": "upstream_error"}}
                await response.aclose()
                if response.status_code in (429, 500, 502, 503, 504):
                    failover_engine.circuit_breaker.record_failure("anthropic")
                return response.status_code, None, err_json

            failover_engine.circuit_breaker.record_success("anthropic")
            return response.status_code, response, {}
        except Exception as exc:
            failover_engine.circuit_breaker.record_failure("anthropic")
            return 503, None, {"type": "error", "error": {"message": str(exc)}}, {}

upstream_client = UpstreamClient()
