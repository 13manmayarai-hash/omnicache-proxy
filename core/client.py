"""
OmniCache Client SDK for Python.
Provides high-performance drop-in client bindings with automatic upstream provider fallback,
semantic cache header injection, and cache metric introspection.
"""

import os
import time
from typing import Dict, Any, List, Optional, Union
import httpx


class CacheMetadata:
    """Represents cache execution telemetry extracted from gateway response headers."""
    def __init__(self, headers: httpx.Headers):
        self.status: str = headers.get("x-cache-status", "MISS").upper()
        self.similarity: float = float(headers.get("x-cache-similarity", "0.0"))
        self.latency_ms: float = float(headers.get("x-cache-latency-ms", "0.0"))
        self.tokens_saved: int = int(headers.get("x-tokens-saved", "0"))
        self.cost_saved_usd: float = float(headers.get("x-cost-saved-usd", "0.0"))
        self.served_model: str = headers.get("x-served-model", "")
        self.cascade_applied: bool = headers.get("x-cascade-applied", "false").lower() == "true"
        self.cascade_reason: str = headers.get("x-cascade-reason", "")
        self.swarm_hit: bool = headers.get("x-omnicache-swarm-hit", "false").lower() == "true"
        self.mesh_node: str = headers.get("x-omnicache-mesh-node", "")

    def is_hit(self) -> bool:
        return self.status.startswith("HIT")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "is_hit": self.is_hit(),
            "similarity": self.similarity,
            "latency_ms": self.latency_ms,
            "tokens_saved": self.tokens_saved,
            "cost_saved_usd": self.cost_saved_usd,
            "served_model": self.served_model,
            "cascade_applied": self.cascade_applied,
            "cascade_reason": self.cascade_reason,
            "swarm_hit": self.swarm_hit,
            "mesh_node": self.mesh_node
        }


class OmniCacheResponse(dict):
    """Augments dictionary API response with .cache metadata property."""
    def __init__(self, data: Dict[str, Any], metadata: CacheMetadata):
        super().__init__(data)
        self.cache: CacheMetadata = metadata


class _ChatCompletionsNamespace:
    """OpenAI-compatible client.chat.completions namespace."""
    def __init__(self, client: "OmniCacheClient"):
        self._client = client

    def create(self, **kwargs) -> OmniCacheResponse:
        return self._client.chat_completion(**kwargs)

    async def acreate(self, **kwargs) -> OmniCacheResponse:
        return await self._client.async_chat_completion(**kwargs)


class _ChatNamespace:
    def __init__(self, client: "OmniCacheClient"):
        self.completions = _ChatCompletionsNamespace(client)


class _MessagesNamespace:
    """Anthropic-compatible client.messages namespace."""
    def __init__(self, client: "OmniCacheClient"):
        self._client = client

    def create(self, **kwargs) -> OmniCacheResponse:
        return self._client.anthropic_message(**kwargs)

    async def acreate(self, **kwargs) -> OmniCacheResponse:
        return await self._client.async_anthropic_message(**kwargs)


class OmniCacheClient:
    """
    Zero-config client for OmniCache AI Proxy.
    Supports OpenAI (/v1/chat/completions) and Anthropic (/v1/messages) standards.
    """
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        api_key: Optional[str] = None,
        org_id: str = "default",
        fallback_to_upstream: bool = True,
        openai_api_key: Optional[str] = None,
        anthropic_api_key: Optional[str] = None,
        timeout: float = 30.0,
        app: Optional[Any] = None
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.getenv("OMNICACHE_API_KEY", "")
        self.org_id = org_id
        self.fallback_to_upstream = fallback_to_upstream
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY", "")
        self.anthropic_api_key = anthropic_api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.timeout = timeout
        self._app = app

        # Namespaced helpers
        self.chat = _ChatNamespace(self)
        self.messages = _MessagesNamespace(self)

    def _build_transport(self) -> Optional[httpx.BaseTransport]:
        if self._app is not None:
            return httpx.ASGITransport(app=self._app)
        return None

    def _build_headers(
        self,
        cache_bypass: bool = False,
        cache_ttl: Optional[int] = None,
        cascade_opt_in: Optional[bool] = None,
        extra_headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "x-org-id": self.org_id
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["x-api-key"] = self.api_key

        if cache_bypass:
            headers["x-cache-bypass"] = "true"

        if cache_ttl is not None:
            headers["x-cache-ttl"] = str(cache_ttl)

        if cascade_opt_in is not None:
            headers["x-omnicache-model-cascade"] = "true" if cascade_opt_in else "false"

        if extra_headers:
            headers.update(extra_headers)

        return headers

    def is_healthy(self) -> bool:
        """Verifies if OmniCache proxy is online."""
        try:
            if self._app is not None:
                from starlette.testclient import TestClient
                with TestClient(self._app, base_url=self.base_url) as client:
                    res = client.get(f"{self.base_url}/healthz")
                    return res.status_code == 200
            with httpx.Client(timeout=3.0) as client:
                res = client.get(f"{self.base_url}/healthz")
                return res.status_code == 200
        except Exception:
            return False

    def chat_completion(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        cache_bypass: bool = False,
        cache_ttl: Optional[int] = None,
        cascade_opt_in: Optional[bool] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> OmniCacheResponse:
        """Executes an OpenAI-compatible chat completion request."""
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            **kwargs
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        headers = self._build_headers(cache_bypass, cache_ttl, cascade_opt_in, extra_headers)
        endpoint = f"{self.base_url}/v1/chat/completions"

        try:
            if self._app is not None:
                from starlette.testclient import TestClient
                with TestClient(self._app, base_url=self.base_url) as client:
                    resp = client.post(endpoint, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    meta = CacheMetadata(resp.headers)
                    return OmniCacheResponse(data, meta)
            else:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(endpoint, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    meta = CacheMetadata(resp.headers)
                    return OmniCacheResponse(data, meta)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            if self.fallback_to_upstream and self.openai_api_key:
                return self._fallback_openai(payload)
            raise exc

    async def async_chat_completion(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        cache_bypass: bool = False,
        cache_ttl: Optional[int] = None,
        cascade_opt_in: Optional[bool] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> OmniCacheResponse:
        """Asynchronously executes an OpenAI-compatible chat completion request."""
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            **kwargs
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        headers = self._build_headers(cache_bypass, cache_ttl, cascade_opt_in, extra_headers)
        endpoint = f"{self.base_url}/v1/chat/completions"

        try:
            transport = httpx.ASGITransport(app=self._app) if self._app is not None else None
            async with httpx.AsyncClient(transport=transport, timeout=self.timeout) as client:
                resp = await client.post(endpoint, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                meta = CacheMetadata(resp.headers)
                return OmniCacheResponse(data, meta)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            if self.fallback_to_upstream and self.openai_api_key:
                return self._fallback_openai(payload)
            raise exc

    def anthropic_message(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        system: Optional[str] = None,
        max_tokens: int = 1024,
        cache_bypass: bool = False,
        cache_ttl: Optional[int] = None,
        cascade_opt_in: Optional[bool] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> OmniCacheResponse:
        """Executes an Anthropic-compatible message creation request."""
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            **kwargs
        }
        if system:
            payload["system"] = system

        headers = self._build_headers(cache_bypass, cache_ttl, cascade_opt_in, extra_headers)
        endpoint = f"{self.base_url}/v1/messages"

        try:
            if self._app is not None:
                from starlette.testclient import TestClient
                with TestClient(self._app, base_url=self.base_url) as client:
                    resp = client.post(endpoint, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    meta = CacheMetadata(resp.headers)
                    return OmniCacheResponse(data, meta)
            else:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(endpoint, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    meta = CacheMetadata(resp.headers)
                    return OmniCacheResponse(data, meta)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            if self.fallback_to_upstream and self.anthropic_api_key:
                return self._fallback_anthropic(payload)
            raise exc

    async def async_anthropic_message(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        system: Optional[str] = None,
        max_tokens: int = 1024,
        cache_bypass: bool = False,
        cache_ttl: Optional[int] = None,
        cascade_opt_in: Optional[bool] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> OmniCacheResponse:
        """Asynchronously executes an Anthropic-compatible message creation request."""
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            **kwargs
        }
        if system:
            payload["system"] = system

        headers = self._build_headers(cache_bypass, cache_ttl, cascade_opt_in, extra_headers)
        endpoint = f"{self.base_url}/v1/messages"

        try:
            transport = httpx.ASGITransport(app=self._app) if self._app is not None else None
            async with httpx.AsyncClient(transport=transport, timeout=self.timeout) as client:
                resp = await client.post(endpoint, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                meta = CacheMetadata(resp.headers)
                return OmniCacheResponse(data, meta)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            if self.fallback_to_upstream and self.anthropic_api_key:
                return self._fallback_anthropic(payload)
            raise exc

    def _fallback_openai(self, payload: Dict[str, Any]) -> OmniCacheResponse:
        """Emergency direct fallback to api.openai.com when proxy is down."""
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.openai_api_key}"}
            )
            resp.raise_for_status()
            data = resp.json()
            mock_headers = httpx.Headers({"x-cache-status": "FALLBACK_DIRECT_UPSTREAM"})
            return OmniCacheResponse(data, CacheMetadata(mock_headers))

    def _fallback_anthropic(self, payload: Dict[str, Any]) -> OmniCacheResponse:
        """Emergency direct fallback to api.anthropic.com when proxy is down."""
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers={
                    "x-api-key": self.anthropic_api_key,
                    "anthropic-version": "2023-06-01"
                }
            )
            resp.raise_for_status()
            data = resp.json()
            mock_headers = httpx.Headers({"x-cache-status": "FALLBACK_DIRECT_UPSTREAM"})
            return OmniCacheResponse(data, CacheMetadata(mock_headers))
