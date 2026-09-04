"""
Unit and Integration Tests for Phase 1 Distributed Redis State Architecture.
Verifies:
1. Redis L1 exact & L2 semantic cache storage
2. Multi-instance cache sharing (Instance A writes, Instance B reads)
3. Atomic budget spend tracking (INCRBYFLOAT across workers)
4. Sliding-window Redis RPM rate limiting via atomic Lua
5. Synchronized multi-instance Circuit Breaker & Failover
6. Hard atomic budget check-and-reserve (TOCTOU race protection)
7. Tenant-scoped secondary index sets for O(tenant) purge & O(1) stats
8. Redis true LRU L2 cache eviction
9. ANN multi-replica version signal & cache invalidation resync
"""

import unittest
import time
import fakeredis
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.storage import RedisCacheStorage, InMemoryCacheStorage
from core.vector_cache import CacheEntry, DualTierCache
from server.quotas import RedisQuotaStorage, VirtualKeyManager
from server.failover import RedisCircuitBreakerStorage, CircuitBreaker


class TestDistributedRedisArchitecture(unittest.TestCase):
    def setUp(self):
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
        """Verify sliding-window rate limit enforcement using atomic Redis Lua scripts."""
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

    def test_06_atomic_budget_check_and_reserve_toctou(self):
        """Verify atomic check-and-reserve prevents TOCTOU budget overshoot under concurrent requests."""
        storage = RedisQuotaStorage(redis_client=self.redis_client)
        mgr = VirtualKeyManager(storage=storage)

        # Budget of $1.00, each request reserves $0.20
        mgr.register_key("team_toctou_key", team_name="Concurrent Team", monthly_budget_usd=1.00, rate_limit_rpm=100)

        success_count = 0
        rejected_count = 0

        def reserve_call():
            allowed, _, _ = mgr.check_authorization("team_toctou_key", reserve_amount_usd=0.20)
            return allowed

        with ThreadPoolExecutor(max_workers=8) as executor:
            futs = [executor.submit(reserve_call) for _ in range(10)]
            for fut in as_completed(futs):
                if fut.result():
                    success_count += 1
                else:
                    rejected_count += 1

        # Exactly 5 requests must pass ($0.20 * 5 = $1.00), remaining 5 must be rejected
        self.assertEqual(success_count, 5)
        self.assertEqual(rejected_count, 5)
        self.assertAlmostEqual(storage.get_spend("team_toctou_key"), 1.00, places=2)

    def test_07_tenant_secondary_index_purge_and_stats(self):
        """Verify secondary index set allows O(tenant) purge and O(1) stats without global SCAN."""
        storage = RedisCacheStorage(redis_client=self.redis_client, entry_cls=CacheEntry)

        # Store 5 entries for Tenant A and 3 entries for Tenant B
        for i in range(5):
            e = CacheEntry(key=f"k_a_{i}", org_id="org_a", model="gpt-4o", user_prompt=f"p_{i}", system_prompt="",
                           schema_hash="no_schema", tools_hash="no_tools", vector=[], response_payload={"text": f"a_{i}"})
            storage.set_exact(f"k_a_{i}", e, ttl_seconds=300)

        for i in range(3):
            e = CacheEntry(key=f"k_b_{i}", org_id="org_b", model="gpt-4o", user_prompt=f"p_{i}", system_prompt="",
                           schema_hash="no_schema", tools_hash="no_tools", vector=[], response_payload={"text": f"b_{i}"})
            storage.set_exact(f"k_b_{i}", e, ttl_seconds=300)

        # Stats check
        active_a_l1, _ = storage.get_stats_counts(org_id="org_a")
        active_b_l1, _ = storage.get_stats_counts(org_id="org_b")
        self.assertEqual(active_a_l1, 5)
        self.assertEqual(active_b_l1, 3)

        # Purge only org_a
        removed = storage.purge(org_id="org_a")
        self.assertEqual(removed, 5)

        # Verify org_b is intact
        self.assertEqual(storage.get_stats_counts(org_id="org_a")[0], 0)
        self.assertEqual(storage.get_stats_counts(org_id="org_b")[0], 3)
        self.assertIsNotNone(storage.get_exact("k_b_0"))

    def test_08_redis_true_lru_eviction(self):
        """Verify Redis backend evicts least recently accessed entries first based on timestamp score."""
        storage = RedisCacheStorage(redis_client=self.redis_client, entry_cls=CacheEntry)

        now = time.time()
        # Add 3 entries with capacity 2
        entry1 = CacheEntry(key="lru_1", org_id="org_lru", model="gpt-4o", user_prompt="q1", system_prompt="",
                            schema_hash="no_schema", tools_hash="no_tools", vector=[0.1], response_payload={"text": "1"})
        entry1.last_accessed_at = now - 100

        entry2 = CacheEntry(key="lru_2", org_id="org_lru", model="gpt-4o", user_prompt="q2", system_prompt="",
                            schema_hash="no_schema", tools_hash="no_tools", vector=[0.2], response_payload={"text": "2"})
        entry2.last_accessed_at = now - 50

        entry3 = CacheEntry(key="lru_3", org_id="org_lru", model="gpt-4o", user_prompt="q3", system_prompt="",
                            schema_hash="no_schema", tools_hash="no_tools", vector=[0.3], response_payload={"text": "3"})
        entry3.last_accessed_at = now

        storage.add_semantic_entry("org_lru", entry1, ttl_seconds=300, max_entries=2)
        storage.add_semantic_entry("org_lru", entry2, ttl_seconds=300, max_entries=2)
        # Adding 3rd should evict entry1 (oldest timestamp)
        storage.add_semantic_entry("org_lru", entry3, ttl_seconds=300, max_entries=2)

        entries = storage.get_semantic_entries("org_lru")
        entry_keys = [getattr(e, "key", "") for e in entries]
        self.assertNotIn("lru_1", entry_keys)
        self.assertIn("lru_2", entry_keys)
        self.assertIn("lru_3", entry_keys)

    def test_09_ann_replica_invalidation_sync(self):
        """Verify that when Replica A invalidates a tag in Redis, Replica B rebuilds/syncs its ANN index."""
        storage_a = RedisCacheStorage(redis_client=fakeredis.FakeRedis(server=self.fake_server, decode_responses=True), entry_cls=CacheEntry)
        storage_b = RedisCacheStorage(redis_client=fakeredis.FakeRedis(server=self.fake_server, decode_responses=True), entry_cls=CacheEntry)

        cache_a = DualTierCache(storage=storage_a)
        cache_b = DualTierCache(storage=storage_b)

        # Seed 55 entries to enable ANN indexing on both replicas
        for i in range(55):
            p = {"model": "gpt-4o", "messages": [{"role": "user", "content": f"Vector search prompt number {i}"}]}
            r = {"choices": [{"message": {"content": f"Answer {i}"}}]}
            cache_a.store(p, r, org_id="org_sync", tag="finance" if i < 10 else "general")

        # Replica B looks up to build local ANN index
        status_b, _, _, _ = cache_b.lookup({"model": "gpt-4o", "messages": [{"role": "user", "content": "Vector search prompt number 0"}]}, org_id="org_sync")
        self.assertEqual(status_b, "HIT_EXACT")

        # Replica A invalidates tag "finance"
        removed = cache_a.invalidate_tag("finance", org_id="org_sync")
        self.assertGreaterEqual(removed, 10)

        # Replica B lookup must detect storage version mismatch and resync ANN index
        status_b2, entry_b2, _, _ = cache_b.lookup({"model": "gpt-4o", "messages": [{"role": "user", "content": "Vector search prompt number 0"}]}, org_id="org_sync")
        # Since prompt 0 had tag "finance", it was deleted and should now be a MISS
        self.assertEqual(status_b2, "MISS")


if __name__ == "__main__":
    unittest.main()
