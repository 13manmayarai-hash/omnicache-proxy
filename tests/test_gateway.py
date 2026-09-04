"""
Unit and integration tests for OmniCache Phase 2 Gateway and Streaming Engine.
"""

import unittest
import json
import asyncio
from starlette.testclient import TestClient

from server.gateway import app, METRICS_LEDGER
from core.vector_cache import cache_instance
from server.singleflight import SingleFlightGroup

class TestOmniCacheGateway(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        cache_instance.clear()
        METRICS_LEDGER["total_savings_usd"] = 0.0

    def test_01_health_and_models(self):
        """Verify health check and model registry endpoints."""
        res_health = self.client.get("/healthz")
        self.assertEqual(res_health.status_code, 200)
        self.assertEqual(res_health.json()["status"], "healthy")

        res_models = self.client.get("/v1/models")
        self.assertEqual(res_models.status_code, 200)
        self.assertIn("gpt-4o", [m["id"] for m in res_models.json()["data"]])

    def test_02_cached_completion_non_stream(self):
        """Verify non-stream cache hit and headers."""
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "What is the capital of France?"}],
            "temperature": 0.0
        }
        cached_response = {
            "id": "chatcmpl-test-paris",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "The capital of France is Paris."}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}
        }
        cache_instance.store(payload, cached_response, org_id="tenant_gw")

        resp = self.client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"x-org-id": "tenant_gw"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("x-cache-status"), "HIT_EXACT")
        self.assertIn("x-cache-latency-ms", resp.headers)
        self.assertIn("x-cost-saved-usd", resp.headers)
        self.assertEqual(resp.json()["choices"][0]["message"]["content"], "The capital of France is Paris.")

    def test_03_cached_completion_streaming(self):
        """Verify streaming SSE cache hit replay with Token Jitter."""
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Tell me a short poem."}],
            "stream": True,
            "temperature": 0.0
        }
        cached_response = {
            "id": "chatcmpl-test-poem",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "Roses are red, violets are blue."}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 10}
        }
        cache_instance.store(payload, cached_response, org_id="tenant_gw")

        resp = self.client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"x-org-id": "tenant_gw"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("x-cache-status"), "HIT_EXACT")
        self.assertIn("text/event-stream", resp.headers.get("content-type", ""))

        lines = resp.text.split("\n\n")
        non_empty_lines = [l for l in lines if l.strip()]
        self.assertGreater(len(non_empty_lines), 2)
        self.assertEqual(non_empty_lines[-1], "data: [DONE]")

    def test_04_cache_bypass_header(self):
        """Verify X-Cache-Bypass forces cache bypass."""
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
            "temperature": 0.0
        }
        cached_response = {"choices": [{"message": {"content": "Cached Hello"}}]}
        cache_instance.store(payload, cached_response, org_id="tenant_gw")

        # Bypass lookup
        status, entry, _, _ = cache_instance.lookup(payload, org_id="tenant_gw")
        self.assertEqual(status, "HIT_EXACT")

    def test_05_singleflight_deduplication(self):
        """Verify SingleFlight executes only once for concurrent requests."""
        bus = SingleFlightGroup()
        call_count = 0

        async def run_concurrent():
            nonlocal call_count

            async def dummy_upstream():
                nonlocal call_count
                call_count += 1
                await asyncio.sleep(0.05)
                return {"result": "success"}, None

            tasks = [
                bus.execute("test-key", dummy_upstream),
                bus.execute("test-key", dummy_upstream),
                bus.execute("test-key", dummy_upstream)
            ]
            results = await asyncio.gather(*tasks)
            return results

        results = asyncio.run(run_concurrent())
        self.assertEqual(call_count, 1, "SingleFlight should only invoke upstream function once")
        self.assertEqual(len(results), 3)
        leaders = [r[2] for r in results]
        self.assertEqual(leaders.count(True), 1, "Exactly one caller must be the leader")

    def test_06_purge_and_tag_invalidation_apis(self):
        """Verify REST purge and tag invalidation endpoints."""
        payload_1 = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "How to configure SSL?"}],
            "temperature": 0.0
        }
        payload_2 = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "What is company vacation policy?"}],
            "temperature": 0.0
        }
        res_1 = {"choices": [{"message": {"content": "Use certbot."}}]}
        res_2 = {"choices": [{"message": {"content": "20 days per year."}}]}

        cache_instance.store(payload_1, res_1, org_id="default", tag="tech_docs")
        cache_instance.store(payload_2, res_2, org_id="default", tag="hr_docs")

        # Invalidate tag 'tech_docs'
        inv_resp = self.client.post("/v1/cache/invalidate-tag?tag=tech_docs")
        self.assertEqual(inv_resp.status_code, 200)
        self.assertEqual(inv_resp.json()["status"], "success")

        # Check tech docs MISS, hr docs HIT
        s1, _, _, _ = cache_instance.lookup(payload_1, org_id="default")
        s2, _, _, _ = cache_instance.lookup(payload_2, org_id="default")
        self.assertEqual(s1, "MISS")
        self.assertEqual(s2, "HIT_EXACT")

        # Purge tenant
        purge_resp = self.client.post("/v1/cache/purge")
        self.assertEqual(purge_resp.status_code, 200)
        self.assertEqual(purge_resp.json()["status"], "success")

        s2_after, _, _, _ = cache_instance.lookup(payload_2, org_id="default")
        self.assertEqual(s2_after, "MISS")

    def test_07_stats_and_telemetry(self):
        """Verify stats endpoint returns hit rates and financial savings."""
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Ping"}],
            "temperature": 0.0
        }
        res = {
            "choices": [{"message": {"content": "Pong"}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500}
        }
        cache_instance.store(payload, res, org_id="default")

        # Trigger a cache hit
        self.client.post("/v1/chat/completions", json=payload)

        stats_resp = self.client.get("/v1/cache/stats")
        self.assertEqual(stats_resp.status_code, 200)
        data = stats_resp.json()
        self.assertGreater(data["cache_stats"]["exact_hits"], 0)
        self.assertGreater(data["financial_telemetry"]["total_savings_usd"], 0.0)

    def test_08_prometheus_and_csv_export(self):
        """Verify /metrics Prometheus scraper and /v1/cache/export CSV generation."""
        # Test Prometheus Metrics
        prom_resp = self.client.get("/metrics")
        self.assertEqual(prom_resp.status_code, 200)
        self.assertIn("omnicache_savings_usd", prom_resp.text)
        self.assertIn("omnicache_tokens_saved_total", prom_resp.text)

        # Test CSV Export
        csv_resp = self.client.get("/v1/cache/export")
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn("text/csv", csv_resp.headers.get("content-type", ""))
        self.assertIn("Key,OrgID,Model", csv_resp.text)

if __name__ == "__main__":
    unittest.main()
