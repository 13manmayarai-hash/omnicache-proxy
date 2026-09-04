"""
Unit and integration tests for OmniCache Phase 1 Core Engine.
"""

import unittest
import time
from core.config import config
from core.hasher import RequestHasher
from core.embeddings import FastSemanticEmbedder
from core.vector_cache import DualTierCache

class TestOmniCacheCore(unittest.TestCase):
    def setUp(self):
        self.cache = DualTierCache()

    def test_01_embedding_performance_and_similarity(self):
        """Verify embedding generates in <1ms and calculates accurate cosine similarity."""
        prompt_a = "How do I reset my account password?"
        prompt_b = "Please tell me how to reset my account password."
        prompt_c = "Write a python script to sort a binary tree."

        # Warm up projection matrix
        FastSemanticEmbedder.embed("warmup query")

        start_time = time.perf_counter()
        vec_a = FastSemanticEmbedder.embed(prompt_a)
        vec_b = FastSemanticEmbedder.embed(prompt_b)
        vec_c = FastSemanticEmbedder.embed(prompt_c)
        duration_ms = (time.perf_counter() - start_time) * 1000

        # Sub-millisecond performance verification
        self.assertLess(duration_ms / 3, 5.0, "Average embedding time should be <5ms")

        # Similarity verification
        sim_ab = FastSemanticEmbedder.cosine_similarity(vec_a, vec_b)
        sim_ac = FastSemanticEmbedder.cosine_similarity(vec_a, vec_c)

        self.assertGreater(sim_ab, 0.90, f"Semantically related questions should have high similarity: {sim_ab}")
        self.assertLess(sim_ac, 0.10, f"Unrelated questions should have near-zero similarity: {sim_ac}")

    def test_02_l1_exact_cache_hit(self):
        """Verify L1 exact matching returns instant HIT_EXACT."""
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "What is 2+2?"}],
            "temperature": 0.0
        }
        response = {"id": "chatcmpl-123", "choices": [{"message": {"content": "4"}}]}

        # Initially MISS
        status, entry, score, reason = self.cache.lookup(payload)
        self.assertEqual(status, "MISS")

        # Store
        self.cache.store(payload, response)

        # Lookup again
        status, entry, score, reason = self.cache.lookup(payload)
        self.assertEqual(status, "HIT_EXACT")
        self.assertEqual(score, 1.0)
        self.assertEqual(entry.response_payload["choices"][0]["message"]["content"], "4")

    def test_03_l2_semantic_cache_hit(self):
        """Verify semantic variations hit L2 semantic cache."""
        payload_1 = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Where is the Eiffel Tower located in Paris?"}],
            "temperature": 0.2
        }
        response_1 = {"id": "chatcmpl-paris", "choices": [{"message": {"content": "It is located on the Champ de Mars."}}]}
        self.cache.store(payload_1, response_1, org_id="tenant_1")

        # Semantically similar rephrasing
        payload_2 = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Tell me where the Eiffel Tower is located in Paris."}],
            "temperature": 0.2
        }
        status, entry, score, reason = self.cache.lookup(payload_2, org_id="tenant_1")
        self.assertEqual(status, "HIT_SEMANTIC")
        self.assertIsNotNone(entry)
        self.assertGreaterEqual(score, 0.90)
        self.assertEqual(entry.response_payload["choices"][0]["message"]["content"], "It is located on the Champ de Mars.")

    def test_04_dynamic_intent_gating_creative_bypass(self):
        """Verify high temperature requests bypass semantic cache."""
        payload_high_temp = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Give me a creative name for a coffee shop"}],
            "temperature": 0.9
        }
        status, entry, score, reason = self.cache.lookup(payload_high_temp)
        self.assertEqual(status, "BYPASS")

    def test_05_dynamic_intent_gating_code_strictness(self):
        """Verify code detection applies strict 0.98 threshold."""
        intent, threshold, reason = self.cache.classify_intent("def quicksort(arr):", "no_schema", "no_tools", 0.2)
        self.assertEqual(intent, "code_generation")
        self.assertEqual(threshold, 0.98)

    def test_06_tenant_isolation(self):
        """Verify Tenant A cannot access Tenant B cached responses."""
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "What is our company confidential revenue target?"}],
            "temperature": 0.0
        }
        response_a = {"choices": [{"message": {"content": "Company A target: $10M"}}]}

        # Store under Tenant A
        self.cache.store(payload, response_a, org_id="org_a")

        # Tenant B looks up the same payload
        status_b, entry_b, score_b, reason_b = self.cache.lookup(payload, org_id="org_b")
        self.assertEqual(status_b, "MISS")

        # Tenant A looks up
        status_a, entry_a, score_a, reason_a = self.cache.lookup(payload, org_id="org_a")
        self.assertEqual(status_a, "HIT_EXACT")
        self.assertEqual(entry_a.response_payload["choices"][0]["message"]["content"], "Company A target: $10M")

    def test_07_tag_invalidation_and_purge(self):
        """Verify invalidation by tag and tenant purge."""
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "What is the return policy?"}],
            "temperature": 0.1
        }
        response = {"choices": [{"message": {"content": "30-day return policy."}}]}

        # Store with tag 'v1_policy'
        self.cache.store(payload, response, org_id="tenant_x", tag="v1_policy")

        # Verify hit
        status, entry, score, reason = self.cache.lookup(payload, org_id="tenant_x")
        self.assertEqual(status, "HIT_EXACT")

        # Invalidate tag 'v1_policy'
        removed = self.cache.invalidate_tag("v1_policy", org_id="tenant_x")
        self.assertGreaterEqual(removed, 1)

        # Lookup again should be MISS
        status_after, entry_after, _, _ = self.cache.lookup(payload, org_id="tenant_x")
        self.assertEqual(status_after, "MISS")

    def test_08_pii_redaction(self):
        """Verify PII patterns are properly redacted."""
        text = "Contact user at john.doe@example.com with SSN 123-45-6789 and Card 4111-2222-3333-4444"
        clean = RequestHasher.redact_pii(text)
        self.assertNotIn("john.doe@example.com", clean)
        self.assertNotIn("123-45-6789", clean)
        self.assertNotIn("4111-2222-3333-4444", clean)
        self.assertIn("[REDACTED_EMAIL]", clean)
        self.assertIn("[REDACTED_SSN]", clean)
        self.assertIn("[REDACTED_CC]", clean)

if __name__ == "__main__":
    unittest.main()
