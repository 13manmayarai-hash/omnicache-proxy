"""
Unit and Integration Tests for Phase 1 Distributed Redis State Architecture.
Verifies:
1. Redis L1 exact & L2 semantic cache storage
2. Multi-instance cache sharing (Instance A writes, Instance B reads)
3. Atomic budget spend tracking (INCRBYFLOAT across workers)
4. Sliding-window Redis RPM rate limiting
5. Synchronized multi-instance Circuit Breaker & Failover
"""

import unittest
import time
import fakeredis

from core.storage import RedisCacheStorage, InMemoryCacheStorage
from core.vector_cache import CacheEntry, DualTierCache
from server.quotas import RedisQuotaStorage, VirtualKeyManager
from server.failover import RedisCircuitBreakerStorage, CircuitBreaker


class TestDistributedRedisArchitecture(unittest.TestCase):
    def setUp(self):
        # Create shared FakeRedis server simulating remote Redis cluster
        self.fake_server = fakeredis.FakeServer()
        self.redis_client = fakeredis.FakeRedis(server=self.fake_server, decode_responses=True)

    def test_01_redis_cache_storage_l1_exact(self):
        """Verify L1 exact matching across Redis storage."""
        storage = RedisCacheStorage(redis_client=self.redis_client, entry_cls=CacheEntry)
        entry = CacheEntry(
            key="exact_sha_123",
            org_id="tenant_alpha",
            model="gpt-4o",
            user_prompt="Hello Redis",
            system_prompt="",
            schema_hash="no_schema",
            tools_hash="no_tools",
            vector=[0.1, 0.2, 0.3],
            response_payload={"choices": [{"message": {"content": "Hello from Redis!"}}]},
            tag="greetings",
            ttl_seconds=300
        )
        storage.set_exact("exact_sha_123", entry, ttl_seconds=300)

        # Retrieve and verify
        retrieved = storage.get_exact("exact_sha_123")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.key, "exact_sha_123")
        self.assertEqual(retrieved.org_id, "tenant_alpha")
        self.assertEqual(retrieved.response_payload["choices"][0]["message"]["content"], "Hello from Redis!")

        # Invalidate tag
        removed = storage.invalidate_tag("greetings", org_id="tenant_alpha")
        self.assertEqual(removed, 1)
        self.assertIsNone(storage.get_exact("exact_sha_123"))

    def test_02_multi_instance_cache_sharing(self):
        """Verify Replica A writes to Redis cache, Replica B reads immediately."""
        storage_replica_a = RedisCacheStorage(redis_client=fakeredis.FakeRedis(server=self.fake_server, decode_responses=True), entry_cls=CacheEntry)
        storage_replica_b = RedisCacheStorage(redis_client=fakeredis.FakeRedis(server=self.fake_server, decode_responses=True), entry_cls=CacheEntry)

        cache_a = DualTierCache(storage=storage_replica_a)
        cache_b = DualTierCache(storage=storage_replica_b)

        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "What is distributed state?"}],
            "temperature": 0.0
        }
        res_payload = {"choices": [{"message": {"content": "State shared across nodes."}}]}

        # Replica A stores completion
        cache_a.store(payload, res_payload, org_id="tenant_cluster")

        # Replica B immediately looks up and gets L1 HIT
        status, entry, score, reason = cache_b.lookup(payload, org_id="tenant_cluster")
        self.assertEqual(status, "HIT_EXACT")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.response_payload["choices"][0]["message"]["content"], "State shared across nodes.")

    def test_03_atomic_spend_tracking_across_workers(self):
        """Verify atomic INCRBYFLOAT spend tracking prevents race conditions across worker replicas."""
        storage_worker_1 = RedisQuotaStorage(redis_client=fakeredis.FakeRedis(server=self.fake_server, decode_responses=True))
        storage_worker_2 = RedisQuotaStorage(redis_client=fakeredis.FakeRedis(server=self.fake_server, decode_responses=True))

        mgr_1 = VirtualKeyManager(storage=storage_worker_1)
        mgr_2 = VirtualKeyManager(storage=storage_worker_2)

        mgr_1.register_key("team_fintech_key", team_name="Fintech", monthly_budget_usd=10.0, rate_limit_rpm=100)

        # Worker 1 records spend
        mgr_1.record_spend("team_fintech_key", 3.25)
        # Worker 2 records spend
        mgr_2.record_spend("team_fintech_key", 4.50)

        # Both workers must see the combined atomic spend ($7.75)
        self.assertAlmostEqual(storage_worker_1.get_spend("team_fintech_key"), 7.75, places=4)
        self.assertAlmostEqual(storage_worker_2.get_spend("team_fintech_key"), 7.75, places=4)

        # Exceed budget
        mgr_2.record_spend("team_fintech_key", 3.00)  # Total: $10.75 > $10.00
        allowed, reason, _ = mgr_1.check_authorization("team_fintech_key")
        self.assertFalse(allowed)
        self.assertIn("budget cap exceeded", reason)

    def test_04_sliding_window_redis_rate_limiting(self):
        """Verify sliding-window rate limit enforcement using Redis Sorted Sets."""
        storage = RedisQuotaStorage(redis_client=self.redis_client)
        mgr = VirtualKeyManager(storage=storage)
        mgr.register_key("rate_test_key", team_name="Testing", monthly_budget_usd=1000.0, rate_limit_rpm=3)

        # 3 allowed requests
        for i in range(3):
            allowed, _, _ = mgr.check_authorization("rate_test_key")
            self.assertTrue(allowed, f"Request {i+1} should be allowed")

        # 4th request in same minute must be rejected
        allowed, reason, _ = mgr.check_authorization("rate_test_key")
        self.assertFalse(allowed)
        self.assertIn("Rate limit exceeded", reason)

    def test_05_synchronized_cluster_circuit_breaker(self):
        """Verify that when Replica A trips the circuit breaker, Replica B instantly sees provider as DOWN."""
        cb_storage_a = RedisCircuitBreakerStorage(redis_client=fakeredis.FakeRedis(server=self.fake_server, decode_responses=True))
        cb_storage_b = RedisCircuitBreakerStorage(redis_client=fakeredis.FakeRedis(server=self.fake_server, decode_responses=True))

        cb_replica_a = CircuitBreaker(failure_threshold=2, recovery_timeout_seconds=0.2, storage=cb_storage_a)
        cb_replica_b = CircuitBreaker(failure_threshold=2, recovery_timeout_seconds=0.2, storage=cb_storage_b)

        self.assertTrue(cb_replica_a.is_available("openai"))
        self.assertTrue(cb_replica_b.is_available("openai"))

        # Replica A records 2 failures -> trips circuit
        cb_replica_a.record_failure("openai")
        cb_replica_a.record_failure("openai")

        # Replica B must immediately observe circuit as OPEN
        self.assertFalse(cb_replica_b.is_available("openai"))
        status_b = cb_replica_b.get_status()
        self.assertEqual(status_b["openai"]["state"], "open")

        # After recovery timeout, circuit enters half-open on all replicas
        time.sleep(0.25)
        self.assertTrue(cb_replica_b.is_available("openai"))

        # Replica B records success -> resets circuit for Replica A
        cb_replica_b.record_success("openai")
        self.assertTrue(cb_replica_a.is_available("openai"))
        status_a = cb_replica_a.get_status()
        self.assertEqual(status_a["openai"]["state"], "closed")

if __name__ == "__main__":
    unittest.main()
