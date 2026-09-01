"""
Unit and integration tests for OmniCache Phase 3 & Phase 4 (Multi-Provider, Persistence, Dashboard).
"""

import unittest
import time
import os
import tempfile
from starlette.testclient import TestClient

from server.translator import ProtocolTranslator
from server.failover import CircuitBreaker, FailoverOrchestrator
from persistence.snapshot_store import SnapshotStore
from core.vector_cache import DualTierCache, CacheEntry
from server.gateway import app

class TestAdvancedOmniCache(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_01_openai_to_anthropic_translation(self):
        """Verify OpenAI payload translates accurately to Anthropic Messages API format."""
        openai_req = {
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "You are a coding assistant."},
                {"role": "user", "content": "Write hello world."}
            ],
            "temperature": 0.5,
            "max_tokens": 500,
            "tools": [{
                "type": "function",
                "function": {
                    "name": "get_time",
                    "description": "Returns current time",
                    "parameters": {"type": "object", "properties": {}}
                }
            }]
        }
        anthropic_req = ProtocolTranslator.openai_to_anthropic_payload(openai_req)
        self.assertEqual(anthropic_req["system"], "You are a coding assistant.")
        self.assertEqual(len(anthropic_req["messages"]), 1)
        self.assertEqual(anthropic_req["messages"][0]["role"], "user")
        self.assertEqual(anthropic_req["max_tokens"], 500)
        self.assertEqual(anthropic_req["tools"][0]["name"], "get_time")

    def test_02_anthropic_to_openai_translation(self):
        """Verify Anthropic response translates back into standard OpenAI format."""
        anthropic_res = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello, world!"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 15, "output_tokens": 5}
        }
        openai_res = ProtocolTranslator.anthropic_to_openai_response(anthropic_res, original_model="claude-3-5-sonnet")
        self.assertEqual(openai_res["object"], "chat.completion")
        self.assertEqual(openai_res["choices"][0]["message"]["content"], "Hello, world!")
        self.assertEqual(openai_res["usage"]["prompt_tokens"], 15)
        self.assertEqual(openai_res["usage"]["completion_tokens"], 5)

    def test_03_circuit_breaker_and_failover(self):
        """Verify circuit breaker trips after threshold failures."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout_seconds=0.1)
        self.assertTrue(cb.is_available("openai"))

        # Record failures
        cb.record_failure("openai")
        self.assertTrue(cb.is_available("openai"))
        cb.record_failure("openai")
        self.assertFalse(cb.is_available("openai"), "Circuit breaker should be open (unavailable)")

        # Wait for recovery timeout
        time.sleep(0.15)
        self.assertTrue(cb.is_available("openai"), "Circuit breaker should transition to half-open")

        # Verify fallback chain
        fo = FailoverOrchestrator()
        fallbacks = fo.get_fallback_chain("gpt-4o")
        self.assertIn("claude-3-5-sonnet-20241022", fallbacks)

    def test_04_persistence_snapshot_lifecycle(self):
        """Verify SQLite snapshot persists and reloads cached records across restarts."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_db = tmp.name

        try:
            store = SnapshotStore(db_path=tmp_db)
            cache_1 = DualTierCache()

            entry = CacheEntry(
                key="test_persist_key_1",
                org_id="tenant_p",
                model="gpt-4o",
                user_prompt="What is SQLite?",
                system_prompt="",
                schema_hash="no_schema",
                tools_hash="no_tools",
                vector=[0.1, 0.2, 0.3],
                response_payload={"choices": [{"message": {"content": "SQLite is a C-language library."}}]},
                tag="db_tag",
                ttl_seconds=3600
            )

            # Persist
            store.persist_entry(entry)

            # Cold-start simulate: create clean cache and reload
            cache_2 = DualTierCache()
            loaded = store.load_into_cache(cache_2)
            self.assertEqual(loaded, 1)
            self.assertIn("test_persist_key_1", cache_2.l1_exact_cache)
            self.assertEqual(cache_2.l1_exact_cache["test_persist_key_1"].response_payload["choices"][0]["message"]["content"], "SQLite is a C-language library.")

            # Invalidate tag
            store.remove_by_tag("db_tag")
            cache_3 = DualTierCache()
            loaded_after = store.load_into_cache(cache_3)
            self.assertEqual(loaded_after, 0)
        finally:
            if os.path.exists(tmp_db):
                os.remove(tmp_db)

    def test_05_dashboard_html_endpoint(self):
        """Verify dashboard UI endpoint returns 200 and HTML content."""
        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("OmniCache AI Proxy", resp.text)

if __name__ == "__main__":
    unittest.main()
