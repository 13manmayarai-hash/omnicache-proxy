"""
Unit and Integration Tests for Radix Tree Multi-Turn Engine & Gateway Activation.
Tests:
1. Multi-turn conversation storage, prefix matching, and exact lookup
2. Tenant and model isolation within the Radix Tree
3. Ephemeral prompt cache block alignment (>1024 tokens)
4. Gateway OpenAI /v1/chat/completions multi-turn Radix Tree hit (HIT_RADIX_TREE)
5. Gateway Anthropic /v1/messages multi-turn Radix Tree hit (HIT_RADIX_TREE)
6. Anthropic non-streaming SingleFlight deduplication
"""

import unittest
import asyncio
from unittest.mock import patch, AsyncMock
from starlette.testclient import TestClient

from core.radix_tree import RadixPrefixTree, radix_tree
from server.gateway import app, METRICS_LEDGER, cache_instance

class TestRadixTreeScalability(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        radix_tree.root.children.clear()
        radix_tree.total_nodes = 1
        radix_tree.prefix_hits = 0
        radix_tree.exact_hits = 0
        cache_instance.clear()
        METRICS_LEDGER["radix_tree_hits"] = 0
        METRICS_LEDGER["singleflight_coalesced_count"] = 0

    def test_01_radix_conversation_exact_and_prefix_lookup(self):
        """Verify Radix tree exact conversation hit and prefix sub-path matching."""
        tree = RadixPrefixTree()
        
        turn1 = {"role": "user", "content": "How do I reverse a list in Python?"}
        turn2 = {"role": "assistant", "content": "Use list.reverse() or slicing [::-1]."}
        turn3 = {"role": "user", "content": "Which one is faster?"}
        
        comp_turn1 = {"id": "c1", "choices": [{"message": turn2}]}
        comp_turn3 = {"id": "c3", "choices": [{"message": {"role": "assistant", "content": "Slicing is generally faster in Python."}}]}

        # Insert 1-turn conversation
        tree.insert_conversation([turn1], comp_turn1, model="gpt-4o", org_id="tenant_1")
        
        # Exact lookup for turn 1
        is_hit, cached, matched, node = tree.lookup_conversation([turn1], model="gpt-4o", org_id="tenant_1")
        self.assertTrue(is_hit)
        self.assertEqual(matched, 1)
        self.assertEqual(cached["id"], "c1")

        # Lookup for 3-turn conversation before inserting turn 3 -> Prefix match (matched 1 turn), not exact hit
        is_hit, cached, matched, node = tree.lookup_conversation([turn1, turn2, turn3], model="gpt-4o", org_id="tenant_1")
        self.assertFalse(is_hit)
        self.assertEqual(matched, 1)

        # Now insert the 3-turn conversation
        tree.insert_conversation([turn1, turn2, turn3], comp_turn3, model="gpt-4o", org_id="tenant_1")

        # Exact lookup for 3-turn conversation -> HIT
        is_hit, cached, matched, node = tree.lookup_conversation([turn1, turn2, turn3], model="gpt-4o", org_id="tenant_1")
        self.assertTrue(is_hit)
        self.assertEqual(matched, 3)
        self.assertEqual(cached["id"], "c3")

    def test_02_radix_model_and_tenant_isolation(self):
        """Verify that conversation turns cannot be served across differing tenants or incompatible models."""
        tree = RadixPrefixTree()
        turn1 = {"role": "user", "content": "Explain quantum computing in one sentence."}
        comp = {"id": "q1", "choices": [{"message": {"role": "assistant", "content": "Quantum computers use qubits."}}]}

        tree.insert_conversation([turn1], comp, model="gpt-4o", org_id="tenant_alpha")

        # Same conversation, wrong tenant -> MISS
        is_hit, cached, matched, node = tree.lookup_conversation([turn1], model="gpt-4o", org_id="tenant_beta")
        self.assertFalse(is_hit)
        self.assertIsNone(cached)

        # Same conversation, wrong model -> MISS
        is_hit, cached, matched, node = tree.lookup_conversation([turn1], model="claude-3-5-sonnet-20241022", org_id="tenant_alpha")
        self.assertFalse(is_hit)
        self.assertIsNone(cached)

        # Same conversation, matching tenant and model -> HIT
        is_hit, cached, matched, node = tree.lookup_conversation([turn1], model="gpt-4o", org_id="tenant_alpha")
        self.assertTrue(is_hit)
        self.assertEqual(cached["id"], "q1")

    def test_03_gateway_openai_multi_turn_radix_hit(self):
        """Verify that /v1/chat/completions serves HIT_RADIX_TREE when L1 exact misses on multi-turn conversations."""
        msgs = [
            {"role": "system", "content": "You are a helpful coding assistant."},
            {"role": "user", "content": "What is 2 + 2?"},
            {"role": "assistant", "content": "2 + 2 is 4."},
            {"role": "user", "content": "What is 4 + 4?"}
        ]
        payload1 = {
            "model": "gpt-4o",
            "messages": msgs,
            "temperature": 0.0
        }
        mock_res = {
            "id": "chatcmpl-math-8",
            "object": "chat.completion",
            "model": "gpt-4o",
            "choices": [{"message": {"role": "assistant", "content": "4 + 4 is 8."}}],
            "usage": {"prompt_tokens": 25, "completion_tokens": 6, "total_tokens": 31}
        }

        with patch("server.upstream.upstream_client.forward_non_stream", new=AsyncMock(return_value=(200, mock_res, {}))):
            # Turn 1: Upstream Miss
            resp1 = self.client.post("/v1/chat/completions", json=payload1, headers={"x-api-key": "test_gw_key"})
            self.assertEqual(resp1.status_code, 200)
            self.assertEqual(resp1.headers.get("X-Cache-Status"), "MISS")

        # Turn 2: Exact same conversation turns, slightly different temperature (0.1) -> L1 misses, Radix Tree HITS!
        payload2 = {
            "model": "gpt-4o",
            "messages": msgs,
            "temperature": 0.1
        }
        resp2 = self.client.post("/v1/chat/completions", json=payload2, headers={"x-api-key": "test_gw_key"})
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.headers.get("X-Cache-Status"), "HIT_RADIX_TREE")
        self.assertEqual(resp2.headers.get("X-OmniCache-Decision"), "HIT")
        self.assertIn("Radix trie", resp2.headers.get("X-Cache-Decision-Reason", ""))
        self.assertEqual(resp2.json()["choices"][0]["message"]["content"], "4 + 4 is 8.")
        self.assertGreaterEqual(METRICS_LEDGER["radix_tree_hits"], 1)

    def test_04_gateway_anthropic_multi_turn_radix_hit(self):
        """Verify that /v1/messages serves HIT_RADIX_TREE when L1 misses on multi-turn Anthropic messages."""
        msgs = [
            {"role": "user", "content": "Hello Claude"},
            {"role": "assistant", "content": "Hello! How can I assist you today?"},
            {"role": "user", "content": "What is the square root of 64?"}
        ]
        payload1 = {
            "model": "claude-3-5-sonnet-20241022",
            "system": "You are a helpful math tutor.",
            "messages": msgs,
            "temperature": 0.0,
            "max_tokens": 100
        }
        mock_anthropic_res = {
            "id": "msg_sqrt_64",
            "type": "message",
            "role": "assistant",
            "model": "claude-3-5-sonnet-20241022",
            "content": [{"type": "text", "text": "The square root of 64 is 8."}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 30, "output_tokens": 10}
        }

        with patch("server.upstream.upstream_client.forward_anthropic_messages", new=AsyncMock(return_value=(200, mock_anthropic_res, {}))):
            # Request 1: MISS from upstream
            resp1 = self.client.post("/v1/messages", json=payload1, headers={"x-api-key": "test_gw_key"})
            self.assertEqual(resp1.status_code, 200)
            self.assertEqual(resp1.headers.get("X-Cache-Status"), "MISS")

        # Request 2: Multi-turn Radix Tree hit with temperature 0.2
        payload2 = {
            "model": "claude-3-5-sonnet-20241022",
            "system": "You are a helpful math tutor.",
            "messages": msgs,
            "temperature": 0.2,
            "max_tokens": 100
        }
        resp2 = self.client.post("/v1/messages", json=payload2, headers={"x-api-key": "test_gw_key"})
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.headers.get("X-Cache-Status"), "HIT_RADIX_TREE")
        self.assertEqual(resp2.headers.get("X-OmniCache-Decision"), "HIT")
        self.assertEqual(resp2.json()["content"][0]["text"], "The square root of 64 is 8.")

    def test_05_gateway_anthropic_singleflight_coalescing(self):
        """Verify Anthropic /v1/messages concurrent non-streaming requests coalesce into a single upstream request."""
        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "Concurrent identical singleflight test prompt"}],
            "max_tokens": 50
        }
        mock_reply = {
            "id": "msg_sf_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-3-5-sonnet-20241022",
            "content": [{"type": "text", "text": "SingleFlight Response"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 15, "output_tokens": 5}
        }

        upstream_calls = 0

        async def slow_upstream(*args, **kwargs):
            nonlocal upstream_calls
            upstream_calls += 1
            await asyncio.sleep(0.1)
            return (200, mock_reply, {})

        # Clear cache to ensure MISS
        cache_instance.clear()

        # Run concurrent requests
        async def run_concurrent():
            from httpx import AsyncClient, ASGITransport
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                t1 = ac.post("/v1/messages", json=payload, headers={"x-api-key": "test_key"})
                t2 = ac.post("/v1/messages", json=payload, headers={"x-api-key": "test_key"})
                return await asyncio.gather(t1, t2)

        with patch("server.upstream.upstream_client.forward_anthropic_messages", side_effect=slow_upstream):
            r1, r2 = asyncio.run(run_concurrent())

        self.assertEqual(upstream_calls, 1)
        statuses = [r1.headers.get("X-Cache-Status"), r2.headers.get("X-Cache-Status")]
        self.assertIn("MISS", statuses)
        self.assertIn("HIT_SINGLEFLIGHT", statuses)
        self.assertEqual(METRICS_LEDGER["singleflight_coalesced_count"], 1)


if __name__ == "__main__":
    unittest.main()
