import time
import json
import os
import tempfile
import sqlite3
import unittest
from server.audit import AuditLogger
from mcp.server import prune_jsonl_audit_logs, handle_tool_call
from core.vector_cache import cache_instance

class TestAuditPruningAndTTL(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_audit.db")
        self.jsonl_path = os.path.join(self.temp_dir.name, "mcp_audit.jsonl")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_audit_sqlite_and_memory_pruning_90_days(self):
        logger = AuditLogger(max_memory_records=100, db_path=self.db_path)
        now = time.time()
        day = 86400

        # Log event from 95 days ago (should be pruned)
        old_epoch = now - (95 * day)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO audit_events 
                (event_id, timestamp, timestamp_epoch, event_type, tenant_id, severity, actor, description, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("aud-old-95d", "old-ts", old_epoch, "OLD_EVENT", "tenant_a", "INFO", "tester", "Old 95d event", "{}")
            )
            conn.commit()

        # Add to memory ledger as well
        logger._memory_ledger.append({
            "event_id": "aud-old-95d",
            "timestamp": "old-ts",
            "timestamp_epoch": old_epoch,
            "event_type": "OLD_EVENT",
            "tenant_id": "tenant_a",
            "severity": "INFO",
            "actor": "tester",
            "description": "Old 95d event",
            "details": {}
        })

        # Log event from 10 days ago (should be kept)
        recent_ev = logger.log_event("RECENT_EVENT", tenant_id="tenant_a", description="Recent 10d event")

        # Prune with 90-day retention
        deleted = logger.prune_expired_logs(retention_days=90)
        self.assertEqual(deleted, 1)

        # Check SQLite
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT event_id FROM audit_events")
            rows = [r[0] for r in cursor.fetchall()]
            self.assertNotIn("aud-old-95d", rows)
            self.assertIn(recent_ev["event_id"], rows)

        # Check memory ledger
        memory_ids = [r["event_id"] for r in logger._memory_ledger]
        self.assertNotIn("aud-old-95d", memory_ids)
        self.assertIn(recent_ev["event_id"], memory_ids)

    def test_jsonl_audit_pruning_90_days(self):
        now = time.time()
        day = 86400
        old_time = now - (95 * day)
        recent_time = now - (5 * day)

        with open(self.jsonl_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": old_time, "tool": "omnicache_query", "org_id": "tenant_1", "duration_ms": 1.2, "status": "ok"}) + "\n")
            f.write(json.dumps({"timestamp": recent_time, "tool": "omnicache_store", "org_id": "tenant_1", "duration_ms": 2.5, "status": "ok"}) + "\n")

        os.environ["OMNICACHE_AUDIT_LOG_PATH"] = self.jsonl_path
        try:
            pruned = prune_jsonl_audit_logs(retention_days=90)
            self.assertEqual(pruned, 1)

            with open(self.jsonl_path, "r", encoding="utf-8") as f:
                lines = [json.loads(l) for l in f if l.strip()]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["tool"], "omnicache_store")
        finally:
            os.environ.pop("OMNICACHE_AUDIT_LOG_PATH", None)

    def test_custom_ttl_store_mcp(self):
        # Store with custom 30-day TTL (2592000 seconds)
        custom_ttl = 2592000
        prompt = f"Test TTL Prompt {time.time()}"
        answer = "Test TTL Answer"
        res = handle_tool_call(
            "omnicache_store",
            {
                "prompt": prompt,
                "answer": answer,
                "ttl_seconds": custom_ttl,
                "org_id": "test_ttl_org"
            },
            default_org_id="test_ttl_org"
        )
        self.assertIn("Successfully stored entry into OmniCache", res["content"][0]["text"])

        # Check entry in cache
        payload = {"messages": [{"role": "user", "content": prompt}], "model": "gpt-4o", "temperature": 0.0}
        status, entry, sim, reason = cache_instance.lookup(payload, org_id="test_ttl_org")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.ttl_seconds, custom_ttl)

if __name__ == "__main__":
    unittest.main()
