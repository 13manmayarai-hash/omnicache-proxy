"""
Unit & Regression Tests for Failure Modes & Strategic Recommendations.
Verifies:
1. AST-Guided Invalidation: Prevents the Code-Logic Semantic Dilemma where high cosine
   vector similarity (>0.95) falsely matches code with inverted boolean logic (and vs or),
   boundary conditions (< vs <=), or reordered calls.
2. Shannon-Entropy Secret Detection: Upgrades PrivacyShield to detect unpatterned,
   high-randomness secrets and bearer tokens without false-positiving on code identifiers,
   file paths, or git commit SHAs.
"""

import unittest
import copy
from core.ast_validator import ASTValidator
from core.privacy_shield import PrivacyShield
from core.vector_cache import DualTierCache
from core.config import config


class TestASTGuidedInvalidation(unittest.TestCase):
    """Verifies AST-guided structural parity and semantic cache invalidation."""

    def test_01_boolean_inversion_detection_python(self):
        code_and = "def check_user(u):\n    return u.is_active and u.is_admin"
        code_or = "def check_user(u):\n    return u.is_active or u.is_admin"

        is_valid, reason = ASTValidator.validate_structural_parity(code_and, code_or)
        self.assertFalse(is_valid)
        self.assertIn("AST_LOGICAL_DIVERGENCE", reason)
        self.assertIn("Boolean inversion", reason)

    def test_02_boundary_condition_detection_python(self):
        code_lt = "def check_capacity(count):\n    if count < 100:\n        return True\n    return False"
        code_lte = "def check_capacity(count):\n    if count <= 100:\n        return True\n    return False"

        is_valid, reason = ASTValidator.validate_structural_parity(code_lt, code_lte)
        self.assertFalse(is_valid)
        self.assertIn("AST_BOUNDARY_DIVERGENCE", reason)
        self.assertIn("Comparison boundary condition mismatch", reason)

    def test_03_return_value_divergence(self):
        code_true = "def is_ready():\n    return True"
        code_false = "def is_ready():\n    return False"

        is_valid, reason = ASTValidator.validate_structural_parity(code_true, code_false)
        self.assertFalse(is_valid)
        self.assertIn("AST_RETURN_DIVERGENCE", reason)

    def test_04_call_order_permutation(self):
        code_1 = "init_db()\nrun_server()"
        code_2 = "run_server()\ninit_db()"

        is_valid, reason = ASTValidator.validate_structural_parity(code_1, code_2)
        self.assertFalse(is_valid)
        self.assertIn("AST_STRUCTURAL_DIVERGENCE", reason)

    def test_05_javascript_multilanguage_operator_stream(self):
        js_and = "function auth(u) { if (u.count < 10) return u.active && u.admin; }"
        js_or = "function auth(u) { if (u.count < 10) return u.active || u.admin; }"

        is_valid, reason = ASTValidator.validate_structural_parity(js_and, js_or)
        self.assertFalse(is_valid)
        self.assertIn("AST_LOGICAL_DIVERGENCE", reason)

        js_lt = "function limit(x) { return x < 50; }"
        js_lte = "function limit(x) { return x <= 50; }"
        is_valid_b, reason_b = ASTValidator.validate_structural_parity(js_lt, js_lte)
        self.assertFalse(is_valid_b)
        self.assertIn("AST_BOUNDARY_DIVERGENCE", reason_b)

    def test_06_identical_logic_with_comments_and_formatting(self):
        code_raw = "def compute(a, b):\n    return a + b"
        code_commented = "# Compute addition\ndef compute(a, b):\n\n    # inline note\n    return a + b\n"

        is_valid, reason = ASTValidator.validate_structural_parity(code_raw, code_commented)
        self.assertTrue(is_valid)
        self.assertIn("AST_STRUCTURAL_MATCH", reason)

    def test_07_code_presence_asymmetry(self):
        code_prompt = "Here is the implementation:\n```python\ndef run():\n    pass\n```"
        text_prompt = "Tell me how to implement run() without writing any code"

        is_valid, reason = ASTValidator.validate_structural_parity(code_prompt, text_prompt)
        self.assertFalse(is_valid)
        self.assertIn("CODE_PRESENCE_ASYMMETRY", reason)

    def test_08_dual_tier_cache_ast_bypass_integration(self):
        cache = DualTierCache()
        org_id = "test_tenant_ast"

        # 1. Warm cache with base logic
        payload_base = {
            "model": "claude-3-7-sonnet",
            "messages": [{"role": "user", "content": "def authenticate(user):\n    return user.is_active and user.is_staff"}]
        }
        cache.store(payload_base, {"choices": [{"message": {"content": "Base logic stored"}}]}, org_id=org_id)

        # 2. Query with inverted logic ('and' -> 'or')
        # Even with similarity > 0.98, AST check must reject and return BYPASS
        payload_inverted = {
            "model": "claude-3-7-sonnet",
            "messages": [{"role": "user", "content": "def authenticate(user):\n    return user.is_active or user.is_staff"}]
        }
        status, entry, score, reason = cache.lookup(payload_inverted, org_id=org_id, custom_threshold=0.90)
        self.assertEqual(status, "BYPASS")
        self.assertGreater(score, 0.95)
        self.assertIn("BYPASS_AST_DIVERGENCE", reason)
        self.assertIn("Boolean inversion", reason)

        # 3. Query with identical logic + comment
        payload_identical = {
            "model": "claude-3-7-sonnet",
            "messages": [{"role": "user", "content": "# Check authentication\ndef authenticate(user):\n    return user.is_active and user.is_staff"}]
        }
        status_hit, entry_hit, score_hit, reason_hit = cache.lookup(payload_identical, org_id=org_id, custom_threshold=0.90)
        self.assertEqual(status_hit, "HIT_SEMANTIC")
        self.assertIsNotNone(entry_hit)


class TestEntropyBasedSecretDetection(unittest.TestCase):
    """Verifies Shannon-entropy scanning in PrivacyShield."""

    def test_01_high_entropy_token_detection_and_rehydration(self):
        raw_secret = "d7A9kL2pQ8xW4vM1zB6yR3tC5jN0hF8g"
        text = f"Configure secret key: {raw_secret} for deployment."

        sanitized, token_map, count = PrivacyShield.sanitize_text(text)
        self.assertNotIn(raw_secret, sanitized)
        self.assertIn("[REDACTED_", sanitized)
        self.assertEqual(count, 1)

        # Verify rehydration
        fake_response = {"choices": [{"message": {"content": f"Acknowledged key: {sanitized}"}}]}
        rehydrated = PrivacyShield.rehydrate_response(fake_response, token_map)
        content = rehydrated["choices"][0]["message"]["content"]
        self.assertIn(raw_secret, content)

    def test_02_bearer_token_keyword_detection(self):
        bearer_token = "ya29.a0AfH6SMAabc123XYZ456-789_TokenValueHereSecret"
        text = f"Authorization: Bearer {bearer_token}"

        sanitized, token_map, count = PrivacyShield.sanitize_text(text)
        self.assertNotIn(bearer_token, sanitized)
        self.assertIn("[REDACTED_BEARER_SECRET_", sanitized)
        self.assertEqual(count, 1)

    def test_03_false_positive_resistance(self):
        safe_strings = [
            "Please view file /root/omnicache_proxy/core/privacy_shield.py for details.",
            "Call compute_workspace_stat_signal with target_dir parameter.",
            "Normal english sentence with long words like supercalifragilisticexpialidocious.",
            "Verified on git commit SHA 2bafa7aef16cdc080383bd5b923e1e4f4081e036 in repository."
        ]

        for s in safe_strings:
            sanitized, token_map, count = PrivacyShield.sanitize_text(s)
            self.assertEqual(count, 0, f"False positive detected on: {s}")
            self.assertEqual(sanitized, s)

    def test_04_sanitize_payload_recursive_entropy(self):
        custom_secret = "sec_live_9f8a7b6c5d4e3f2a1b0c9d8e7f"
        payload = {
            "model": "gpt-4o",
            "messages": [
                {"role": "user", "content": f"Update the service with token: {custom_secret}"}
            ]
        }

        sanitized_payload, token_map, count = PrivacyShield.sanitize_payload(payload)
        user_content = sanitized_payload["messages"][0]["content"]
        self.assertNotIn(custom_secret, user_content)
        self.assertIn("[REDACTED_", user_content)
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
