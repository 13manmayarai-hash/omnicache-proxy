"""
Test Suite for Phase 3: Write-Behind Durability Queue & Virtual Key SQLite Persistence.
"""

import os
import time
import tempfile
import unittest
from persistence.snapshot_store import SnapshotStore
from server.quotas import SQLiteQuotaStorage, VirtualKeyManager
from core.vector_cache import CacheEntry, DualTierCache


class TestPhase3WriteBehindDurability(unittest.TestCase):
    def setUp(self):
        self.tmp_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db = self.tmp_file.name
        self.tmp_file.close()

    def tearDown(self):
        if os.path.exists(self.tmp_db):
            try:
                os.remove(self.tmp_db)
            except Exception:
                pass

    def test_01_async_write_behind_burst(self):
        """Verify non-blocking write-behind queue drains and persists burst entries."""
        store = SnapshotStore(db_path=self.tmp_db, enable_write_behind=True)

        for i in range(50):
            entry = CacheEntry(
                key=f"burst_key_{i}",
                org_id="burst_tenant",
                model="gpt-4o",
                user_prompt=f"Burst prompt {i}",
                system_prompt="sys",
                schema_hash="no_schema",
                tools_hash="no_tools",
                vector=[float(i) * 0.01],
                response_payload={"choices": [{"message": {"content": f"Burst answer {i}"}}]},
                tag="burst_tag",
                ttl_seconds=3600
            )
            # Enqueue asynchronously
            store.persist_entry(entry, synchronous=False)

        # Flush write-behind queue
        store.flush()

        # Verify restoration in fresh cache
        cache = DualTierCache()
        loaded = store.load_into_cache(cache)
        self.assertEqual(loaded, 50)
        self.assertIn("burst_key_0", cache.l1_exact_cache)
        self.assertIn("burst_key_49", cache.l1_exact_cache)

        store.close()

    def test_02_durable_virtual_keys_restart_recovery(self):
        """Verify virtual keys and spend counters survive complete proxy restarts."""
        store_1 = SnapshotStore(db_path=self.tmp_db, enable_write_behind=False)
        storage_1 = SQLiteQuotaStorage(store=store_1)
        manager_1 = VirtualKeyManager(storage=storage_1)

        # Register key and accumulate spend
        manager_1.register_key(
            key_id="tenant_durability_key_1",
            team_name="Data Science",
            org_id="org_ds",
            monthly_budget_usd=10.0,
            rate_limit_rpm=60,
            role="tenant"
        )
        manager_1.record_spend("tenant_durability_key_1", 4.50)

        # Check authorization before restart
        allowed, reason, info = manager_1.check_authorization("tenant_durability_key_1")
        self.assertTrue(allowed)
        self.assertAlmostEqual(info["current_spend_usd"], 4.50, places=2)

        store_1.close()

        # ================= SIMULATE RESTART =================
        store_2 = SnapshotStore(db_path=self.tmp_db, enable_write_behind=False)
        storage_2 = SQLiteQuotaStorage(store=store_2)
        manager_2 = VirtualKeyManager(storage=storage_2)

        # Ensure key exists and spend is preserved
        key_info = manager_2.storage.get_key("tenant_durability_key_1")
        self.assertIsNotNone(key_info)
        self.assertEqual(key_info["team_name"], "Data Science")
        self.assertEqual(key_info["org_id"], "org_ds")
        self.assertEqual(key_info["monthly_budget_usd"], 10.0)
        self.assertAlmostEqual(manager_2.storage.get_spend("tenant_durability_key_1"), 4.50, places=2)

        # Add more spend exceeding budget
        manager_2.record_spend("tenant_durability_key_1", 6.00)
        allowed, reason, info = manager_2.check_authorization("tenant_durability_key_1")
        self.assertFalse(allowed)
        self.assertIn("budget cap exceeded", reason)

        store_2.close()

    def test_03_durability_tag_invalidation_and_purge(self):
        """Verify tag invalidation updates persistent store cleanly."""
        store = SnapshotStore(db_path=self.tmp_db, enable_write_behind=False)

        entry1 = CacheEntry(
            key="k1", org_id="t1", model="gpt-4o",
            user_prompt="q1", system_prompt="",
            schema_hash="no_schema", tools_hash="no_tools",
            vector=[0.1], response_payload={"text": "a1"},
            tag="tag_finance"
        )
        entry2 = CacheEntry(
            key="k2", org_id="t1", model="gpt-4o",
            user_prompt="q2", system_prompt="",
            schema_hash="no_schema", tools_hash="no_tools",
            vector=[0.2], response_payload={"text": "a2"},
            tag="tag_ops"
        )
        store.persist_entry(entry1)
        store.persist_entry(entry2)

        store.delete_by_tag("tag_finance", org_id="t1")

        cache = DualTierCache()
        loaded = store.load_into_cache(cache)
        self.assertEqual(loaded, 1)
        self.assertIn("k2", cache.l1_exact_cache)
        self.assertNotIn("k1", cache.l1_exact_cache)

        store.purge_all("t1")
        cache_empty = DualTierCache()
        loaded_empty = store.load_into_cache(cache_empty)
        self.assertEqual(loaded_empty, 0)

        store.close()


if __name__ == "__main__":
    unittest.main()
