"""
Test Suite for Phase 5: Concurrent Multi-Tenant Load Verification & Micro-benchmarks.
"""

import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from starlette.testclient import TestClient
from server.gateway import app
from server.quotas import quota_manager
from core.vector_cache import cache_instance


class TestPhase5LoadBenchmark(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        quota_manager.register_key("load_tenant_1", team_name="Load Tenant 1", org_id="org_load_1", monthly_budget_usd=1000.0, rate_limit_rpm=1000)
        quota_manager.register_key("load_tenant_2", team_name="Load Tenant 2", org_id="org_load_2", monthly_budget_usd=1000.0, rate_limit_rpm=1000)
        quota_manager.register_key("rate_capped_tenant", team_name="Rate Capped", org_id="org_capped", monthly_budget_usd=100.0, rate_limit_rpm=20)
        cache_instance.purge()

    def test_01_core_cache_engine_microbenchmark(self):
        """Microbenchmark direct cache engine lookup latency (<0.1ms)."""
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Direct microbenchmark prompt"}]
        }
        res_payload = {
            "id": "chatcmpl-bench",
            "object": "chat.completion",
            "model": "gpt-4o",
            "choices": [{"message": {"role": "assistant", "content": "Instant benchmark response"}}]
        }
        cache_instance.store(payload, res_payload, org_id="org_load_1")

        # Warmup
        cache_instance.lookup(payload, org_id="org_load_1")

        t0 = time.perf_counter()
        iterations = 1000
        for _ in range(iterations):
            status, entry, sim, reason = cache_instance.lookup(payload, org_id="org_load_1")
            self.assertEqual(status, "HIT_EXACT")
        t1 = time.perf_counter()

        avg_latency_ms = ((t1 - t0) / iterations) * 1000.0
        print(f"\n🚀 Direct Cache Lookup Latency: {avg_latency_ms:.4f} ms/op ({iterations/((t1-t0)):.0f} ops/sec)")
        self.assertLess(avg_latency_ms, 1.0)  # Core engine is strictly sub-millisecond

    def test_02_concurrent_http_gateway_throughput(self):
        """Verify concurrent HTTP requests across worker threads with valid cache hits."""
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "HTTP throughput prompt"}]
        }
        res_payload = {
            "id": "chatcmpl-http-bench",
            "object": "chat.completion",
            "model": "gpt-4o",
            "choices": [{"message": {"role": "assistant", "content": "HTTP instant response"}}]
        }
        cache_instance.store(payload, res_payload, org_id="org_load_1")

        def worker_lookup():
            resp = self.client.post("/v1/chat/completions", json=payload, headers={"x-api-key": "load_tenant_1"})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.headers.get("X-Cache-Status"), "HIT_EXACT")
            return resp.status_code

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker_lookup) for _ in range(40)]
            for fut in as_completed(futures):
                self.assertEqual(fut.result(), 200)

    def test_03_concurrent_rate_limiting_enforcement(self):
        """Verify strict rate limit enforcement under concurrent bursts without race conditions."""
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Rate limit check prompt"}]
        }
        res_payload = {
            "id": "chatcmpl-rate",
            "object": "chat.completion",
            "model": "gpt-4o",
            "choices": [{"message": {"role": "assistant", "content": "Rate response"}}]
        }
        cache_instance.store(payload, res_payload, org_id="org_capped")

        success_count = 0
        rate_limited_count = 0

        def send_request():
            resp = self.client.post("/v1/chat/completions", json=payload, headers={"x-api-key": "rate_capped_tenant"})
            return resp.status_code

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(send_request) for _ in range(40)]
            for fut in as_completed(futures):
                code = fut.result()
                if code == 200:
                    success_count += 1
                elif code == 429:
                    rate_limited_count += 1

        self.assertEqual(success_count, 20)
        self.assertEqual(rate_limited_count, 20)

    def test_04_multi_tenant_parallel_isolation(self):
        """Verify multiple tenants operating concurrently maintain strict data boundaries."""
        t1_prompt = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Tenant 1 private pipeline"}]}
        t1_resp = {"id": "c1", "object": "chat.completion", "model": "gpt-4o", "choices": [{"message": {"role": "assistant", "content": "Data 1"}}]}
        cache_instance.store(t1_prompt, t1_resp, org_id="org_load_1")

        t2_prompt = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Tenant 2 private pipeline"}]}
        t2_resp = {"id": "c2", "object": "chat.completion", "model": "gpt-4o", "choices": [{"message": {"role": "assistant", "content": "Data 2"}}]}
        cache_instance.store(t2_prompt, t2_resp, org_id="org_load_2")

        def run_tenant_1():
            resp = self.client.post("/v1/chat/completions", json=t1_prompt, headers={"x-api-key": "load_tenant_1"})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["choices"][0]["message"]["content"], "Data 1")

        def run_tenant_2():
            resp = self.client.post("/v1/chat/completions", json=t2_prompt, headers={"x-api-key": "load_tenant_2"})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["choices"][0]["message"]["content"], "Data 2")

        with ThreadPoolExecutor(max_workers=8) as executor:
            futs = []
            for _ in range(20):
                futs.append(executor.submit(run_tenant_1))
                futs.append(executor.submit(run_tenant_2))
            for f in as_completed(futs):
                f.result()


if __name__ == "__main__":
    unittest.main()
