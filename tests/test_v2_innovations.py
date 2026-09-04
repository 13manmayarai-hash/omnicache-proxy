"""
Test Suite for OmniCache 2.0 Innovations:
1. Radix Prefix Tree Multi-Turn Engine
2. Agent Tool-Call Output Replayer
3. Adaptive Cost Arbitrage & Complexity Classifier
4. Multi-Modal Vision Perceptual Hashing
5. Zero-Knowledge Privacy Shield (Reversible PII Tokenizer)
6. Virtual Key Quotas & Budget Enforcement
"""

import unittest
import base64
import os
import time
from core.radix_tree import radix_tree
from core.vision_cache import vision_cache, VisionPerceptualHasher
from core.privacy_shield import privacy_shield
from server.tool_replayer import tool_cache
from server.cascade_router import cascade_router
from server.quotas import quota_manager
from starlette.testclient import TestClient
from server.gateway import app

class TestV2Innovations(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    # 1. Radix Prefix Tree Test
    def test_radix_prefix_tree(self):
        msgs_1 = [
            {"role": "system", "content": "You are a Python expert"},
            {"role": "user", "content": "How do I reverse a list?"}
        ]
        completion_1 = {"id": "c1", "choices": [{"message": {"role": "assistant", "content": "list.reverse()"}}]}
        radix_tree.insert_conversation(msgs_1, completion_1)

        # Longer branch with same prefix
        msgs_2 = [
            {"role": "system", "content": "You are a Python expert"},
            {"role": "user", "content": "How do I reverse a list?"},
            {"role": "assistant", "content": "list.reverse()"},
            {"role": "user", "content": "What about slicing?"}
        ]
        matched_turns, matched_node = radix_tree.match_prefix(msgs_2)
        self.assertGreaterEqual(matched_turns, 2)
        self.assertIsNotNone(matched_node)

        # Ephemeral cache alignment test
        long_msgs = [{"role": "user", "content": "word " * 1000}]
        aligned = radix_tree.align_ephemeral_cache_blocks(long_msgs, block_size_tokens=500)
        self.assertIn("cache_control", aligned[0])

    # 2. Agent Tool-Call Replay Test
    def test_agent_tool_replay(self):
        tool_name = "read_file"
        args = {"filepath": "config.json"}
        file_content = '{"env": "production", "debug": false}'

        # Lookup before store -> Miss
        is_hit, out, _ = tool_cache.lookup_tool_call(tool_name, args, workspace_state="head1:clean")
        self.assertFalse(is_hit)

        # Store tool execution with explicit workspace state
        tool_cache.store_tool_call(tool_name, args, file_content, workspace_state="head1:clean")

        # Lookup after store with same workspace state -> Instant Hit
        is_hit, out, _ = tool_cache.lookup_tool_call(tool_name, args, workspace_state="head1:clean")
        self.assertTrue(is_hit)
        self.assertEqual(out, file_content)

        # Lookup with altered workspace state (e.g. git commit changed or dirty status) -> Miss (invalidated!)
        is_hit_dirty, out_dirty, _ = tool_cache.lookup_tool_call(tool_name, args, workspace_state="head1:dirty_abc")
        self.assertFalse(is_hit_dirty)
        self.assertIsNone(out_dirty)

        # Non-whitelisted/unsafe tools are rejected
        self.assertFalse(tool_cache.is_eligible("delete_database"))
        is_hit_unsafe, _, _ = tool_cache.lookup_tool_call("delete_database", {"db": "prod"})
        self.assertFalse(is_hit_unsafe)

        # Test Gateway Tool Replay API with workspace_state
        resp = self.client.post("/v1/agent/tool_replay", json={
            "tool_name": "read_file",
            "arguments": {"filepath": "config.json"},
            "workspace_state": "head1:clean"
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("status"), "HIT")
        self.assertEqual(resp.json().get("output"), file_content)

        # Gateway Tool Replay with mismatched workspace state -> MISS
        resp_mismatch = self.client.post("/v1/agent/tool_replay", json={
            "tool_name": "read_file",
            "arguments": {"filepath": "config.json"},
            "workspace_state": "head2:clean"
        })
        self.assertEqual(resp_mismatch.status_code, 200)
        self.assertEqual(resp_mismatch.json().get("status"), "MISS")

    def test_non_git_workspace_file_mtime_invalidation(self):
        """Verify non-git workspace tools track file mtime/size changes dynamically."""
        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_file = os.path.join(temp_dir, "sample.py")
            with open(temp_file, "w") as f:
                f.write("def foo(): return 1")

            tool_name = "read_file"
            args = {"file_path": temp_file}

            # First execution store
            tool_cache.store_tool_call(tool_name, args, "def foo(): return 1")

            # Lookup should HIT
            is_hit, output, _ = tool_cache.lookup_tool_call(tool_name, args)
            self.assertTrue(is_hit)
            self.assertEqual(output, "def foo(): return 1")

            # Modify file content / mtime
            time.sleep(0.01)
            with open(temp_file, "w") as f:
                f.write("def foo(): return 2 (modified)")

            # Lookup must MISS because mtime/size changed
            is_hit_mod, output_mod, _ = tool_cache.lookup_tool_call(tool_name, args)
            self.assertFalse(is_hit_mod)
            self.assertIsNone(output_mod)

    # 3. Adaptive Cost Arbitrage & Complexity Classifier Test
    def test_cost_cascade_router(self):
        # Trivial prompt -> Low complexity
        trivial_payload = {
            "messages": [{"role": "user", "content": "Capitalize this word: hello"}]
        }
        complexity_trivial = cascade_router.classify_complexity(trivial_payload)
        self.assertLess(complexity_trivial, 0.40)

        # Deep reasoning prompt -> High complexity
        deep_payload = {
            "messages": [{"role": "user", "content": "Prove and derive the mathematical formal verification of this distributed consensus algorithm with differential equations"}]
        }
        complexity_deep = cascade_router.classify_complexity(deep_payload)
        self.assertGreater(complexity_deep, 0.60)

        # Test automatic routing downgrade for trivial request to gpt-4o when opt-in enabled
        routed_model, tier, comp, was_cascaded, reason = cascade_router.evaluate_route("gpt-4o", trivial_payload, allow_cascade=True)
        self.assertEqual(routed_model, "gemini-2.5-flash")
        self.assertEqual(tier, "tier_1_economy")
        self.assertTrue(was_cascaded)

        # Test preservation when opt-in is disabled (default)
        routed_model_default, _, _, was_cascaded_default, _ = cascade_router.evaluate_route("gpt-4o", trivial_payload, allow_cascade=False)
        self.assertEqual(routed_model_default, "gpt-4o")
        self.assertFalse(was_cascaded_default)

    # 4. Multi-Modal Vision Perception Cache Test
    def test_vision_perceptual_cache(self):
        # Create synthetic test image byte buffers
        img_bytes_1 = b"IMAGE_DATA_HEADER_A" * 10
        img_bytes_2 = b"IMAGE_DATA_HEADER_A" * 10  # identical

        dhash_1 = VisionPerceptualHasher.compute_dhash64(img_bytes_1)
        dhash_2 = VisionPerceptualHasher.compute_dhash64(img_bytes_2)
        dist = VisionPerceptualHasher.hamming_distance(dhash_1, dhash_2)
        self.assertEqual(dist, 0)

        # Store in vision cache
        mock_ocr_response = {"choices": [{"message": {"role": "assistant", "content": "Receipt total: $42.50"}}]}
        vision_cache.store_image(dhash_1, "OCR this receipt", mock_ocr_response)

        # Lookup -> Hit
        v_hit, v_res, v_dist = vision_cache.lookup_image(dhash_2, "OCR this receipt")
        self.assertTrue(v_hit)
        self.assertEqual(v_dist, 0)
        self.assertEqual(v_res["choices"][0]["message"]["content"], "Receipt total: $42.50")

    # 5. Zero-Knowledge Privacy Shield Test (Reversible Tokenization)
    def test_privacy_shield_reversible_tokenization(self):
        raw_text = "Patient Alice SSN is 123-45-6789, email is alice@hospital.org, card is 4111111111111111"
        sanitized_text, token_map, count = privacy_shield.sanitize_text(raw_text)

        self.assertEqual(count, 3)
        self.assertNotIn("123-45-6789", sanitized_text)
        self.assertNotIn("alice@hospital.org", sanitized_text)
        self.assertNotIn("4111111111111111", sanitized_text)
        self.assertIn("[REDACTED_SSN_", sanitized_text)
        self.assertIn("[REDACTED_EMAIL_", sanitized_text)

        # Test Rehydration
        mock_llm_response = {
            "choices": [{"message": {"role": "assistant", "content": f"Confirmed record for {sanitized_text}"}}]
        }
        rehydrated = privacy_shield.rehydrate_response(mock_llm_response, token_map)
        final_content = rehydrated["choices"][0]["message"]["content"]
        self.assertIn("123-45-6789", final_content)
        self.assertIn("alice@hospital.org", final_content)

    # 6. Virtual Key Quotas & Budget Enforcement Test
    def test_virtual_key_quotas(self):
        quota_manager.register_key("team_fintech", team_name="Fintech Team", monthly_budget_usd=0.05, rate_limit_rpm=10)

        # Request within budget
        allowed, reason, _ = quota_manager.check_authorization("team_fintech")
        self.assertTrue(allowed)

        # Record spend that exceeds budget
        quota_manager.record_spend("team_fintech", 0.06)

        # Next request must be rejected
        allowed, reason, _ = quota_manager.check_authorization("team_fintech")
        self.assertFalse(allowed)
        self.assertIn("budget cap exceeded", reason)

        # Verify Quota API with admin credentials
        quota_manager.register_key("admin_quota_key", team_name="Admin Team", org_id="admin", role="admin")
        resp = self.client.get("/v1/enterprise/quotas", headers={"x-api-key": "admin_quota_key"})
        self.assertEqual(resp.status_code, 200)
        quotas_dict = resp.json().get("quotas", {})
        self.assertTrue(len(quotas_dict) > 0)

if __name__ == "__main__":
    unittest.main()
