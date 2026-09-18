"""
Test Suite for OmniCache Proxy v3.2 Improvements:
1. Resilient Cloud Self-Keepalive Worker (Zero Cold Starts)
2. Visual Cache Fabric Explorer API & Management
3. Frontier AI Reasoning Tokens (DeepSeek R1 / o1) & Guaranteed Schema Validation
"""

import json
import pytest
from starlette.testclient import TestClient
from server.gateway import app
from core.vector_cache import cache_instance, get_model_family, CacheEntry
from core.config import config, MODEL_PRICING
from server.keepalive import keepalive_worker
from persistence.snapshot_store import snapshot_store


@pytest.fixture
def client():
    return TestClient(app)


class TestCloudResilienceKeepAlive:
    def test_01_healthz_includes_keepalive_telemetry(self, client):
        res = client.get("/healthz")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"
        assert data["service"] == "omnicache-proxy"
        assert "circuit_breaker" in data
        assert "keepalive" in data
        assert "enabled" in data["keepalive"]
        assert "target_url" in data["keepalive"]
        assert "pings_sent" in data["keepalive"]

    def test_02_keepalive_ping_endpoint(self, client):
        res = client.post("/v1/system/keepalive/ping")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] in ("success", "error")
        assert "target_url" in data


class TestVisualCacheExplorerAPI:
    def test_03_cache_entries_list_and_pagination(self, client):
        # Seed an entry into snapshot_store and cache
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Explain binary search in Rust"}]
        }
        resp_data = {
            "id": "chatcmpl_test_rust_bin_search",
            "object": "chat.completion",
            "model": "gpt-4o",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "fn binary_search() {}"},
                "finish_reason": "stop"
            }],
            "usage": {"prompt_tokens": 20, "completion_tokens": 40, "total_tokens": 60}
        }
        entry = cache_instance.store(
            payload=payload,
            response_payload=resp_data,
            org_id="default",
            tag="rust_algo"
        )
        snapshot_store.persist_entry(entry, synchronous=True)

        res = client.get("/v1/cache/entries?limit=10")
        assert res.status_code == 200
        data = res.json()
        assert "entries" in data
        assert "total" in data
        assert data["total"] >= 1
        found = any("binary search in Rust" in e.get("user_prompt", "") for e in data["entries"])
        assert found

    def test_04_cache_entry_deletion(self, client):
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Unique prompt to be evicted"}]
        }
        resp_data = {
            "id": "chatcmpl_delete_me",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "bye"}}]
        }
        entry = cache_instance.store(payload=payload, response_payload=resp_data, org_id="default")
        snapshot_store.persist_entry(entry, synchronous=True)

        # Confirm listed
        res_list = client.get(f"/v1/cache/entries?q=Unique+prompt+to+be+evicted")
        assert res_list.status_code == 200
        assert res_list.json()["total"] >= 1

        # Delete entry
        res_del = client.delete(f"/v1/cache/entries/{entry.key}")
        assert res_del.status_code == 200
        assert res_del.json()["status"] == "success"

        # Verify entry no longer in search
        res_after = client.get(f"/v1/cache/entries?q=Unique+prompt+to+be+evicted")
        assert res_after.json()["total"] == 0

    def test_05_test_similarity_sandbox_endpoint(self, client):
        # Store a target prompt in cache
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Calculate the surface area of a cylinder"}]
        }
        resp = {
            "id": "chatcmpl_cylinder",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "A = 2*pi*r*h + 2*pi*r^2"}}]
        }
        entry = cache_instance.store(payload=payload, response_payload=resp, org_id="default")
        snapshot_store.persist_entry(entry, synchronous=True)

        # Test exact match query
        test_body = {
            "prompt": "Calculate the surface area of a cylinder",
            "model": "gpt-4o",
            "threshold": 0.75
        }
        res = client.post("/v1/cache/test-similarity", json=test_body)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] in ("HIT_EXACT", "HIT_SEMANTIC")
        assert data["similarity_score"] >= 0.95
        assert data["matched"] is True
        assert data["matched_entry"]["key"] == entry.key


class TestFrontierAIReasoningAndSchemas:
    def test_06_model_family_and_pricing_for_frontier_models(self):
        assert get_model_family("deepseek-reasoner") == "deepseek-reasoner"
        assert get_model_family("deepseek-r1") == "deepseek-reasoner"
        assert get_model_family("deepseek-chat") == "deepseek-chat"
        assert get_model_family("o3-mini") == "openai-reasoning"
        assert get_model_family("gemini-2.0-flash-thinking-exp") == "google-gemini-thinking"

        assert "deepseek-reasoner" in MODEL_PRICING
        assert "deepseek-chat" in MODEL_PRICING
        assert "o1-mini" in MODEL_PRICING

    def test_07_intent_aware_dynamic_thresholding(self):
        # Code intent -> 0.98
        intent, thresh, _ = cache_instance.classify_intent("def quicksort(arr): return arr", "no_schema", "no_tools", 0.0)
        assert intent == "code_generation"
        assert thresh == 0.98

        # Math intent -> 0.98
        intent, thresh, _ = cache_instance.classify_intent("calculate 45 * 89 / 3", "no_schema", "no_tools", 0.0)
        assert intent == "math_calculation"
        assert thresh == 0.98

        # Reasoning intent -> 0.98
        intent, thresh, _ = cache_instance.classify_intent("think deeply and reason step by step why P != NP", "no_schema", "no_tools", 0.0)
        assert intent == "deep_reasoning"
        assert thresh == 0.98

        # Conversational QA -> standard threshold
        intent, thresh, _ = cache_instance.classify_intent("What is the capital of France?", "no_schema", "no_tools", 0.0)
        assert intent == "conversational_qa"
        assert thresh == config.DEFAULT_SIMILARITY_THRESHOLD

    def test_08_reasoning_tokens_stripping_on_demand(self, client):
        # Store a response containing reasoning_content and <think> tags
        payload = {
            "model": "deepseek-reasoner",
            "messages": [{"role": "user", "content": "Prove that the square root of 2 is irrational"}]
        }
        resp_data = {
            "id": "chatcmpl_r1_proof",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "deepseek-reasoner",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "reasoning_content": "Assume sqrt(2) = a/b where a and b are coprime...",
                    "content": "<think>Step 1: assume rational</think>Here is the proof by contradiction."
                },
                "finish_reason": "stop"
            }],
            "usage": {"prompt_tokens": 15, "completion_tokens": 80, "total_tokens": 95}
        }
        entry = cache_instance.store(payload=payload, response_payload=resp_data, org_id="default")
        snapshot_store.persist_entry(entry, synchronous=True)

        # Standard request: receives full response
        res_full = client.post("/v1/chat/completions", json=payload)
        assert res_full.status_code == 200
        assert res_full.headers["X-Cache-Status"] == "HIT_EXACT"
        body_full = res_full.json()
        assert "reasoning_content" in body_full["choices"][0]["message"]

        # Request with X-OmniCache-Strip-Thinking: true
        res_stripped = client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"X-OmniCache-Strip-Thinking": "true"}
        )
        assert res_stripped.status_code == 200
        assert res_stripped.headers["X-Cache-Status"] == "HIT_EXACT"
        assert res_stripped.headers.get("X-OmniCache-Thinking-Stripped") == "true"
        body_stripped = res_stripped.json()
        # reasoning_content is stripped
        assert "reasoning_content" not in body_stripped["choices"][0]["message"]
        # <think> tag is stripped from content
        assert "<think>" not in body_stripped["choices"][0]["message"]["content"]
        assert "Here is the proof by contradiction." in body_stripped["choices"][0]["message"]["content"]

    def test_09_guaranteed_structured_json_schema_validation(self, client):
        # Store an entry with valid structured JSON
        schema_payload = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Extract customer data for Alice"}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "CustomerSchema",
                    "schema": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}, "email": {"type": "string"}},
                        "required": ["name", "email"]
                    }
                }
            }
        }
        valid_resp = {
            "id": "chatcmpl_schema_valid",
            "object": "chat.completion",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps({"name": "Alice", "email": "alice@example.com"})},
                "finish_reason": "stop"
            }],
            "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50}
        }
        entry = cache_instance.store(payload=schema_payload, response_payload=valid_resp, org_id="default")
        snapshot_store.persist_entry(entry, synchronous=True)

        # Hit should pass schema validation
        res = client.post("/v1/chat/completions", json=schema_payload)
        assert res.status_code == 200
        assert res.headers["X-Cache-Status"] == "HIT_EXACT"
        assert res.headers.get("X-OmniCache-Schema-Validated") == "true"
