"""
SQLite Persistence Tier for Zero Cold-Start Recovery.
Asynchronously records in-memory cache entries to an embedded SQLite store and reloads them on server startup.
"""

import sqlite3
import json
import os
import time
from typing import Optional, List, Dict, Any
from core.vector_cache import CacheEntry, DualTierCache

DB_PATH = os.getenv("OMNICACHE_DB_PATH", "/root/omnicache_proxy/omnicache.db")

class SnapshotStore:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS cache_records (
                        key TEXT PRIMARY KEY,
                        org_id TEXT NOT NULL,
                        model TEXT NOT NULL,
                        user_prompt TEXT,
                        system_prompt TEXT,
                        schema_hash TEXT,
                        tools_hash TEXT,
                        vector_json TEXT,
                        response_json TEXT NOT NULL,
                        tag TEXT,
                        is_stream INTEGER DEFAULT 0,
                        stream_chunks_json TEXT,
                        created_at REAL NOT NULL,
                        last_accessed_at REAL NOT NULL,
                        ttl_seconds INTEGER NOT NULL,
                        hit_count INTEGER DEFAULT 0
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_org_id ON cache_records(org_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_tag ON cache_records(tag)")
        finally:
            conn.close()

    def persist_entry(self, entry: CacheEntry):
        """Persists a CacheEntry to SQLite."""
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                with conn:
                    conn.execute("""
                        INSERT OR REPLACE INTO cache_records (
                            key, org_id, model, user_prompt, system_prompt, schema_hash, tools_hash,
                            vector_json, response_json, tag, is_stream, stream_chunks_json,
                            created_at, last_accessed_at, ttl_seconds, hit_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        entry.key,
                        entry.org_id,
                        entry.model,
                        entry.user_prompt,
                        entry.system_prompt,
                        entry.schema_hash,
                        entry.tools_hash,
                        json.dumps(entry.vector),
                        json.dumps(entry.response_payload),
                        entry.tag,
                        1 if entry.is_stream else 0,
                        json.dumps(entry.stream_chunks),
                        entry.created_at,
                        entry.last_accessed_at,
                        entry.ttl_seconds,
                        entry.hit_count
                    ))
            finally:
                conn.close()
        except Exception:
            pass

    def load_into_cache(self, cache: DualTierCache) -> int:
        """Loads all non-expired records from SQLite into the hot DualTierCache."""
        now = time.time()
        loaded_count = 0
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.execute("SELECT * FROM cache_records")
                rows = cursor.fetchall()
                for row in rows:
                    created_at = row["created_at"]
                    ttl_seconds = row["ttl_seconds"]
                    if (now - created_at) > ttl_seconds:
                        continue

                    entry = CacheEntry(
                        key=row["key"],
                        org_id=row["org_id"],
                        model=row["model"],
                        user_prompt=row["user_prompt"] or "",
                        system_prompt=row["system_prompt"] or "",
                        schema_hash=row["schema_hash"] or "no_schema",
                        tools_hash=row["tools_hash"] or "no_tools",
                        vector=json.loads(row["vector_json"]) if row["vector_json"] else [],
                        response_payload=json.loads(row["response_json"]),
                        tag=row["tag"],
                        is_stream=bool(row["is_stream"]),
                        stream_chunks=json.loads(row["stream_chunks_json"]) if row["stream_chunks_json"] else [],
                        ttl_seconds=ttl_seconds
                    )
                    entry.created_at = created_at
                    entry.last_accessed_at = row["last_accessed_at"]
                    entry.hit_count = row["hit_count"]

                    cache.l1_exact_cache[entry.key] = entry
                    if entry.vector:
                        if entry.org_id not in cache.l2_semantic_cache:
                            cache.l2_semantic_cache[entry.org_id] = []
                        cache.l2_semantic_cache[entry.org_id].append(entry)

                    loaded_count += 1
            finally:
                conn.close()
        except Exception:
            pass

        return loaded_count

    def remove_by_key(self, key: str):
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                conn.execute("DELETE FROM cache_records WHERE key = ?", (key,))
        finally:
            conn.close()

    def remove_by_org(self, org_id: str):
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                conn.execute("DELETE FROM cache_records WHERE org_id = ?", (org_id,))
        finally:
            conn.close()

    def remove_by_tag(self, tag: str, org_id: Optional[str] = None):
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                if org_id:
                    conn.execute("DELETE FROM cache_records WHERE tag = ? AND org_id = ?", (tag, org_id))
                else:
                    conn.execute("DELETE FROM cache_records WHERE tag = ?", (tag,))
        finally:
            conn.close()

snapshot_store = SnapshotStore()
