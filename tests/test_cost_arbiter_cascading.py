"""
Tests for Smart Model Cascading & Automated Cost Arbiter (v2.9.8).
Validates Shannon token entropy, complexity classification, execution safety invariants,
vendor affinity routing, gateway header disclosures, and Prometheus metrics.
"""

import unittest
from unittest.mock import patch, AsyncMock
from starlette.testclient import TestClient

from core.config import config
from server.cascade_router import cascade_router, compute_shannon_entropy
from server.gateway import app, parse_cascade_opt_in


class TestCostArbiterCascading(unittest.TestCase):

    def setUp(self):
        cascade_router.reset_stats()
        self.client = TestClient(app)

    def tearDown(self):
        cascade_router.reset_stats()

    def test_shannon_entropy_computation(self):
        """Verify Shannon token entropy accurately separates repetitive boilerplate from diverse reasoning."""
        # Repetitive boilerplate tokens
        repetitive = "test " * 50
        h_low = compute_shannon_entropy(repetitive)
        self.assertLess(h_low, 0.20)

        # High-entropy technical vocabulary
        diverse = "quantum formal verification differential equations deadlock kernel concurrency ast bytecode optimization"
        h_high = compute_shannon_entropy(diverse)
        self.assertGreater(h_high, 0.90)

        # Edge cases
        self.assertEqual(compute_shannon_entropy(""), 0.5)
        self.assertEqual(compute_shannon_entropy("singleword"), 0.5)

    def test_complexity_classification(self):
        """Verify complexity classifier scores trivial queries < 0.35 and complex reasoning >= 0.60."""
        trivial_payload = {
            "messages": [{"role": "user", "content": "json format uppercase this word list: apple, orange, banana"}]
        }
        score_trivial = cascade_router.classify_complexity(trivial_payload)
        self.assertLess(score_trivial, 0.35)

        deep_payload = {
            "messages": [{"role": "user", "content": "prove and architect a formal verification algorithm for distributed deadlock detection under concurrency"}]
        }
        score_deep = cascade_router.classify_complexity(deep_payload)
        self.assertGreaterEqual(score_deep, 0.60)

        # Code detection bumps score
        code_payload = {
            "messages": [{"role": "user", "content": "def optimize_tree(node):\n    pass"}]
        }
        score_code = cascade_router.classify_complexity(code_payload)
        self.assertGreaterEqual(score_code, 0.25)

    def test_opt_in_guardrail_enforcement(self):
        """Verify cascade router refuses to substitute models unless allow_cascade=True."""
        payload = {
            "messages": [{"role": "user", "content": "uppercase this text"}]
        }

        # Default allow_cascade=False -> MUST preserve requested model
        model, tier, comp, cascaded, reason = cascade_router.evaluate_route(
            "gpt-4o", payload, allow_cascade=False
        )
        self.assertEqual(model, "gpt-4o")
        self.assertFalse(cascaded)
        self.assertEqual(reason, "cascade_opt_in_disabled")

        # allow_cascade=True -> down-routes trivial query
        model_opt, tier_opt, comp_opt, cascaded_opt, reason_opt = cascade_router.evaluate_route(
            "gpt-4o", payload, allow_cascade=True
        )
        self.assertTrue(cascaded_opt)
        self.assertIn(model_opt, ["gemini-2.5-flash", "gpt-4o-mini"])

    def test_execution_safety_invariants(self):
        """Verify queries with tools, schemas, or multi-turn context are NEVER cascaded."""
        # 1. Tools safety invariant
        payload_tools = {
            "messages": [{"role": "user", "content": "uppercase this"}],
            "tools": [{"name": "bash", "description": "run bash"}]
        }
        model, tier, comp, cascaded, reason = cascade_router.evaluate_route(
            "gpt-4o", payload_tools, allow_cascade=True
        )
        self.assertFalse(cascaded)
        self.assertEqual(reason, "preserved_for_agent_tools")
        self.assertEqual(model, "gpt-4o")

        # 2. Structured schema invariant
        payload_schema = {
            "messages": [{"role": "user", "content": "uppercase this"}],
            "response_format": {"type": "json_object"}
        }
        model, tier, comp, cascaded, reason = cascade_router.evaluate_route(
            "gpt-4o", payload_schema, allow_cascade=True
        )
        self.assertFalse(cascaded)
        self.assertEqual(reason, "preserved_for_structured_schema")
        self.assertEqual(model, "gpt-4o")

        # 3. Multi-turn context invariant
        payload_multiturn = {
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
                {"role": "user", "content": "uppercase this"}
            ]
        }
        model, tier, comp, cascaded, reason = cascade_router.evaluate_route(
            "gpt-4o", payload_multiturn, allow_cascade=True
        )
        self.assertFalse(cascaded)
        self.assertEqual(reason, "preserved_for_multiturn_context")
        self.assertEqual(model, "gpt-4o")

    def test_vendor_affinity_routing(self):
        """Verify same-vendor routing preserves provider family (Anthropic stays Anthropic)."""
        claude_payload = {
            "messages": [{"role": "user", "content": "fix spelling in this single sentence"}]
        }
        model, tier, comp, cascaded, reason = cascade_router.evaluate_route(
            "claude-3-7-sonnet", claude_payload, allow_cascade=True, vendor_affinity="same-vendor"
        )
        self.assertTrue(cascaded)
        self.assertEqual(model, "claude-3-5-haiku-20241022")
        self.assertEqual(tier, "tier_2_balanced")

        # OpenAI same-vendor routing
        gpt_payload = {
            "messages": [{"role": "user", "content": "strip whitespace"}]
        }
        model_gpt, tier_gpt, _, cascaded_gpt, _ = cascade_router.evaluate_route(
            "gpt-4o", gpt_payload, allow_cascade=True, vendor_affinity="same-vendor"
        )
        self.assertTrue(cascaded_gpt)
        self.assertEqual(model_gpt, "gpt-4o-mini")

    def test_arbitrage_savings_calculation(self):
        """Verify dynamic arbitrage savings calculation and counters."""
        payload = {
            "messages": [{"role": "user", "content": "format text " * 100}]
        }
        self.assertEqual(cascade_router.downgraded_count, 0)
        self.assertEqual(cascade_router.arbitrage_savings_usd, 0.0)

        cascade_router.evaluate_route("gpt-4o", payload, allow_cascade=True)
        self.assertEqual(cascade_router.downgraded_count, 1)
        self.assertGreater(cascade_router.arbitrage_savings_usd, 0.0)
        self.assertGreater(cascade_router.tokens_diverted_to_economy, 0)

        stats = cascade_router.get_stats()
        self.assertEqual(stats["total_routed"], 1)
        self.assertEqual(stats["downgraded_count"], 1)
        self.assertEqual(stats["downgrade_ratio"], 1.0)

    def test_gateway_parse_cascade_opt_in(self):
        """Verify parse_cascade_opt_in handles explicit headers and CASCADE_POLICY."""
        # Explicit headers
        self.assertTrue(parse_cascade_opt_in({"x-omnicache-model-cascade": "allow"}))
        self.assertTrue(parse_cascade_opt_in({"x-omnicache-model-cascade": "true"}))
        self.assertTrue(parse_cascade_opt_in({"x-allow-cascade": "1"}))
        self.assertFalse(parse_cascade_opt_in({"x-omnicache-model-cascade": "deny"}))
        self.assertFalse(parse_cascade_opt_in({"x-omnicache-model-cascade": "false"}))

        # Policy fallback
        with patch.object(config, "CASCADE_POLICY", "off"):
            self.assertFalse(parse_cascade_opt_in({}))

        with patch.object(config, "CASCADE_POLICY", "auto"):
            self.assertTrue(parse_cascade_opt_in({}))
            # Explicit denial overrides policy
            self.assertFalse(parse_cascade_opt_in({"x-omnicache-model-cascade": "deny"}))

    def test_gateway_anthropic_cascading_integration(self):
        """Verify Anthropic /v1/messages integrates model cascading on cache miss."""
        payload = {
            "model": "claude-3-7-sonnet",
            "messages": [{"role": "user", "content": "capitalize this word: test"}],
            "max_tokens": 50
        }
        mock_reply = {
            "id": "msg_cascade_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-3-5-haiku-20241022",
            "content": [{"type": "text", "text": "TEST"}],
            "usage": {"input_tokens": 10, "output_tokens": 5}
        }

        with patch("server.upstream.upstream_client.forward_anthropic_messages", new=AsyncMock(return_value=(200, mock_reply, {}))):
            # 1. Without cascade header (policy=off) -> Not cascaded
            res_no_cascade = self.client.post("/v1/messages", json=payload, headers={"x-api-key": "test"})
            self.assertEqual(res_no_cascade.status_code, 200)
            self.assertEqual(res_no_cascade.headers.get("X-Cascade-Applied"), "false")
            self.assertEqual(res_no_cascade.headers.get("X-Served-Model"), "claude-3-7-sonnet")

            # 2. With cascade header -> Cascaded to claude-3-5-haiku
            res_cascade = self.client.post("/v1/messages", json=payload, headers={
                "x-api-key": "test",
                "x-omnicache-model-cascade": "allow",
                "x-cache-bypass": "true"
            })
            self.assertEqual(res_cascade.status_code, 200)
            self.assertEqual(res_cascade.headers.get("X-Cascade-Applied"), "true")
            self.assertEqual(res_cascade.headers.get("X-Served-Model"), "claude-3-5-haiku-20241022")

    def test_gateway_openai_cascading_integration(self):
        """Verify OpenAI /v1/chat/completions cascades when opted in."""
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "lowercase: HELLO WORLD"}],
            "max_tokens": 50
        }
        mock_reply = {
            "id": "chatcmpl_cascade_test",
            "choices": [{"message": {"role": "assistant", "content": "hello world"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        }

        with patch("server.upstream.upstream_client.forward_non_stream", new=AsyncMock(return_value=(200, mock_reply, {}))):
            res = self.client.post("/v1/chat/completions", json=payload, headers={
                "Authorization": "Bearer test-key",
                "x-omnicache-model-cascade": "allow",
                "x-cache-bypass": "true"
            })
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.headers.get("X-Cascade-Applied"), "true")
            self.assertIn(res.headers.get("X-Served-Model"), ["gemini-2.5-flash", "gpt-4o-mini"])

    def test_stats_and_metrics_exposure(self):
        """Verify /v1/cache/stats and /metrics expose model cascade telemetry."""
        # 1. /v1/cache/stats
        res_stats = self.client.get("/v1/cache/stats")
        self.assertEqual(res_stats.status_code, 200)
        data = res_stats.json()
        ee = data.get("enterprise_engine", {})
        self.assertIn("cascade_stats", ee)
        self.assertIn("cascade_routes_total", ee)
        self.assertIn("cascade_policy", ee)
        self.assertEqual(data.get("system_info", {}).get("version"), config.VERSION)

        # 2. /metrics (Prometheus)
        res_metrics = self.client.get("/metrics")
        self.assertEqual(res_metrics.status_code, 200)
        metrics_text = res_metrics.text
        self.assertIn("omnicache_cascade_routes_total", metrics_text)
        self.assertIn("omnicache_cascade_downgrades_total", metrics_text)
        self.assertIn("omnicache_cascade_arbitrage_savings_usd", metrics_text)


if __name__ == "__main__":
    unittest.main()
