"""
Unit tests for OmniCache Client SDK.
Verifies client instantiation, proxy routing, response metadata parsing,
and emergency fallback handlers.
"""

import pytest
from core.client import OmniCacheClient, CacheMetadata, OmniCacheResponse
from server.gateway import app


def test_cache_metadata_parsing():
    import httpx
    headers = httpx.Headers({
        "x-cache-status": "HIT_L1_RADIX",
        "x-cache-similarity": "1.0",
        "x-cache-latency-ms": "0.065",
        "x-tokens-saved": "350",
        "x-cost-saved-usd": "0.0035",
        "x-served-model": "gpt-4o",
        "x-cascade-applied": "true",
        "x-cascade-reason": "complexity_match",
        "x-omnicache-swarm-hit": "true",
        "x-omnicache-mesh-node": "node-alpha"
    })
    meta = CacheMetadata(headers)
    assert meta.status == "HIT_L1_RADIX"
    assert meta.is_hit() is True
    assert meta.similarity == 1.0
    assert meta.latency_ms == 0.065
    assert meta.tokens_saved == 350
    assert meta.cost_saved_usd == 0.0035
    assert meta.served_model == "gpt-4o"
    assert meta.cascade_applied is True
    assert meta.swarm_hit is True
    assert meta.mesh_node == "node-alpha"

    d = meta.to_dict()
    assert d["status"] == "HIT_L1_RADIX"
    assert d["is_hit"] is True


def test_client_is_healthy():
    client = OmniCacheClient(base_url="http://testserver", app=app)
    assert client.is_healthy() is True


def test_client_chat_completion_sync():
    import time
    client = OmniCacheClient(base_url="http://testserver", app=app, org_id="sdk_test_org")
    test_prompt = f"Write a python fibonacci generator {time.time()}"

    # First request: Cache MISS expected on unique prompt
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": test_prompt}],
        temperature=0.0,
        extra_headers={"x-dashboard-playground": "true"}
    )
    assert isinstance(resp, OmniCacheResponse)
    assert "choices" in resp
    assert hasattr(resp, "cache")
    assert resp.cache.status == "MISS"

    # Second request: Cache HIT expected
    resp2 = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": test_prompt}],
        temperature=0.0,
        extra_headers={"x-dashboard-playground": "true"}
    )
    assert resp2.cache.is_hit() is True
    assert resp2.cache.tokens_saved > 0


@pytest.mark.anyio
async def test_client_chat_completion_async():
    client = OmniCacheClient(base_url="http://testserver", app=app, org_id="sdk_test_org")
    
    resp = await client.chat.completions.acreate(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Explain quicksort in 1 sentence"}],
        temperature=0.0,
        extra_headers={"x-dashboard-playground": "true"}
    )
    assert isinstance(resp, OmniCacheResponse)
    assert "choices" in resp
    assert hasattr(resp, "cache")


def test_client_anthropic_messages_sync():
    client = OmniCacheClient(base_url="http://testserver", app=app, org_id="sdk_test_org")
    
    resp = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        messages=[{"role": "user", "content": "What is 2+2?"}],
        max_tokens=50,
        extra_headers={"x-dashboard-playground": "true"}
    )
    assert isinstance(resp, OmniCacheResponse)
    assert "content" in resp
    assert hasattr(resp, "cache")


@pytest.mark.anyio
async def test_client_anthropic_messages_async():
    client = OmniCacheClient(base_url="http://testserver", app=app, org_id="sdk_test_org")
    
    resp = await client.messages.acreate(
        model="claude-sonnet-4-5-20250929",
        messages=[{"role": "user", "content": "State Ohm's Law"}],
        max_tokens=50,
        extra_headers={"x-dashboard-playground": "true"}
    )
    assert isinstance(resp, OmniCacheResponse)
    assert "content" in resp
    assert hasattr(resp, "cache")


def test_client_header_injection():
    client = OmniCacheClient(base_url="http://testserver", api_key="test_key_123", org_id="org_cyber")
    headers = client._build_headers(cache_bypass=True, cache_ttl=3600, cascade_opt_in=True)

    assert headers["x-org-id"] == "org_cyber"
    assert headers["x-api-key"] == "test_key_123"
    assert headers["Authorization"] == "Bearer test_key_123"
    assert headers["x-cache-bypass"] == "true"
    assert headers["x-cache-ttl"] == "3600"
    assert headers["x-omnicache-model-cascade"] == "true"
