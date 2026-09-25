"""
Enterprise AI Safety & Semantic Logic Regression Test Suite.
Codifies empirical findings from the Enterprise AI Safety Audit:
- AST Validator edge cases & structural parity
- Intent classification boundary conditions & bypass verification
- Shannon entropy token complexity scoring & model cascade routing
- StreamReplayer SSE jitter, TTFT, and SDK frame compliance
"""

import unittest
import asyncio
import time
import json
from core.ast_validator import ASTValidator, ASTStructureVisitor
from core.vector_cache import DualTierCache
from server.cascade_router import cascade_router, compute_shannon_entropy
from server.stream_replayer import StreamReplayer


class TestEnterpriseAISafetyAudit(unittest.TestCase):
    def setUp(self):
        self.cache = DualTierCache()

    # -------------------------------------------------------------------------
    # 1. AST Validator Structural Parity & Language Constructs
    # -------------------------------------------------------------------------
    def test_01_ast_walrus_operator_boundary(self):
        """Verifies walrus operator boundary condition differentiation."""
        code_gt = "def check(d):\n    if (n := len(d)) > 10:\n        return n\n    return 0"
        code_lte = "def check(d):\n    if (n := len(d)) <= 10:\n        return n\n    return 0"
        is_valid, reason = ASTValidator.validate_structural_parity(code_gt, code_lte)
        self.assertFalse(is_valid)
        self.assertIn("AST_BOUNDARY_DIVERGENCE", reason)

    def test_02_ast_lambda_boundary(self):
        """Verifies lambda boundary condition differentiation."""
        lam_gt = "f = lambda x: x > 0"
        lam_lte = "f = lambda x: x <= 0"
        is_valid, reason = ASTValidator.validate_structural_parity(lam_gt, lam_lte)
        self.assertFalse(is_valid)
        self.assertIn("AST_BOUNDARY_DIVERGENCE", reason)

    def test_03_ast_comprehension_boundary(self):
        """Verifies comprehension filter boundary condition differentiation."""
        comp_gt = "res = [x * 2 for x in data if x > 0]"
        comp_lte = "res = [x * 2 for x in data if x <= 0]"
        is_valid, reason = ASTValidator.validate_structural_parity(comp_gt, comp_lte)
        self.assertFalse(is_valid)
        self.assertIn("AST_BOUNDARY_DIVERGENCE", reason)

    def test_04_ast_match_case_inverted_returns(self):
        """Verifies match/case return inversion detection."""
        mc_1 = "def f(x):\n    match x:\n        case 1:\n            return True\n        case _:\n            return False"
        mc_2 = "def f(x):\n    match x:\n        case 1:\n            return False\n        case _:\n            return True"
        is_valid, reason = ASTValidator.validate_structural_parity(mc_1, mc_2)
        self.assertFalse(is_valid)

    def test_05_ast_multilang_js_strict_vs_loose(self):
        """Verifies JavaScript === vs == detection."""
        js_strict = "function c(a, b) { return a === b; }"
        js_loose = "function c(a, b) { return a == b; }"
        is_valid, reason = ASTValidator.validate_structural_parity(js_strict, js_loose)
        self.assertFalse(is_valid)
        self.assertIn("AST_BOUNDARY_DIVERGENCE", reason)

    def test_06_ast_multilang_go_channel_direction(self):
        """Verifies Go channel send vs receive structural differentiation."""
        go_send = "func w(c chan int) { c <- 1 }"
        go_recv = "func w(c chan int) { val := <-c }"
        is_valid, reason = ASTValidator.validate_structural_parity(go_send, go_recv)
        self.assertFalse(is_valid)

    def test_07_ast_multilang_rust_arithmetic(self):
        """Verifies Rust arithmetic operator shift."""
        r_add = "fn h(x: i32) -> i32 { x + 1 }"
        r_sub = "fn h(x: i32) -> i32 { x - 1 }"
        is_valid, reason = ASTValidator.validate_structural_parity(r_add, r_sub)
        self.assertFalse(is_valid)
        self.assertIn("AST_ARITHMETIC_DIVERGENCE", reason)

    def test_08_ast_false_positive_resistance(self):
        """Ensures variable renaming and comments continue to hit the cache."""
        py_1 = "def calc(total, rate):\n    # Subtotal\n    return total * rate"
        py_2 = "def calc(amount, tax_pct):\n    # Different comment\n    return amount * tax_pct"
        is_valid, reason = ASTValidator.validate_structural_parity(py_1, py_2)
        self.assertTrue(is_valid)
        self.assertIn("AST_STRUCTURAL_MATCH", reason)

    # -------------------------------------------------------------------------
    # 2. Vector Cache Safety Bypasses (Tools, Schema, Multi-Turn)
    # -------------------------------------------------------------------------
    def test_09_safety_bypass_agent_tools(self):
        """Verifies agent tools 100% bypass fuzzy semantic matching."""
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Execute database check"}],
            "tools": [{"type": "function", "function": {"name": "query_db"}}]
        }
        status, entry, sim, reason = self.cache.lookup(payload)
        self.assertEqual(status, "BYPASS")
        self.assertIn("BYPASS_AGENT_TOOLS", reason)

    def test_10_safety_bypass_structured_schema(self):
        """Verifies structured JSON schema 100% bypasses fuzzy semantic matching."""
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Extract customer data"}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "Customer"}}
        }
        status, entry, sim, reason = self.cache.lookup(payload)
        self.assertEqual(status, "BYPASS")
        self.assertIn("BYPASS_STRUCTURED_SCHEMA", reason)

    def test_11_safety_bypass_multiturn(self):
        """Verifies multi-turn conversations 100% bypass fuzzy semantic matching."""
        payload = {
            "model": "gpt-4o",
            "messages": [
                {"role": "user", "content": "Initial prompt"},
                {"role": "assistant", "content": "Response"},
                {"role": "user", "content": "Follow up question"}
            ]
        }
        status, entry, sim, reason = self.cache.lookup(payload)
        self.assertEqual(status, "BYPASS")
        self.assertIn("BYPASS_MULTITURN_CONVERSATION", reason)

    # -------------------------------------------------------------------------
    # 3. Cascade Router Complexity & Routing Boundaries
    # -------------------------------------------------------------------------
    def test_12_cascade_dense_systems_code_retains_frontier(self):
        """Verifies dense systems code with low entropy is not down-routed."""
        c_code = """
        typedef struct {
            uint32_t magic;
            uint32_t flags;
            uint32_t size;
            uint32_t checksum;
        } packet_t;
        """
        payload = {"messages": [{"role": "user", "content": c_code}]}
        complexity = cascade_router.classify_complexity(payload)
        self.assertGreaterEqual(complexity, 0.65)

        model, tier, comp, was_casc, reason = cascade_router.evaluate_route(
            "claude-3-7-sonnet", payload, allow_cascade=True
        )
        self.assertFalse(was_casc)
        self.assertEqual(model, "claude-3-7-sonnet")

    def test_13_cascade_opt_in_governance(self):
        """Verifies that cascading is strictly disabled without explicit opt-in."""
        payload = {"messages": [{"role": "user", "content": "summarize this word: hi"}]}
        model, tier, comp, was_casc, reason = cascade_router.evaluate_route(
            "claude-3-7-sonnet", payload, allow_cascade=False
        )
        self.assertFalse(was_casc)
        self.assertEqual(model, "claude-3-7-sonnet")
        self.assertEqual(reason, "cascade_opt_in_disabled")

    # -------------------------------------------------------------------------
    # 4. Stream Replayer Protocol Fidelity
    # -------------------------------------------------------------------------
    def test_14_openai_stream_fidelity(self):
        """Verifies OpenAI SSE stream formatting and completion signal."""
        async def run_test():
            payload = {
                "id": "chatcmpl-test",
                "model": "gpt-4o",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "Testing streaming replay."},
                    "finish_reason": "stop"
                }]
            }
            chunks = []
            async for c in StreamReplayer.replay_cached_stream(payload, tokens_per_sec=200.0):
                chunks.append(c)
            self.assertTrue(chunks[0].startswith("data: "))
            self.assertEqual(chunks[-1], "data: [DONE]\n\n")

        asyncio.run(run_test())

    def test_15_anthropic_stream_fidelity(self):
        """Verifies Anthropic SSE event frame sequence compliance."""
        async def run_test():
            payload = {
                "id": "msg_test",
                "model": "claude-3-5-sonnet-20241022",
                "content": [{"type": "text", "text": "Anthropic protocol test."}],
                "usage": {"input_tokens": 5, "output_tokens": 5}
            }
            events = []
            async for ev in StreamReplayer.replay_cached_anthropic_stream(payload, tokens_per_sec=200.0):
                events.append(ev)
            self.assertTrue(events[0].startswith("event: message_start"))
            self.assertTrue(events[-1].startswith("event: message_stop"))

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
