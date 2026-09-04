"""
SQLite Persistence Tier for Zero Cold-Start Recovery.
Asynchronously records in-memory cache entries to an embedded SQLite store with WAL mode,
thread-local connection reuse, and non-blocking background persistence.
"""

import sqlite3
import json
import os
import time
import asyncio
import threading
from typing import Optional, List, Dict, Any
from core.vector_cache import CacheEntry, DualTierCache

def get_default_db_path() -> str:
    base_dir = os.getenv("OMNICACHE_DATA_DIR", os.path.expanduser("~/.omnicache"))
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, "omnicache.db")

DB_PATH = os.getenv("OMNICACHE_DB_PATH", get_default_db_path())

class SnapshotStore:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Returns or creates a thread-local reused SQLite connection configured with WAL mode."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute("PRAGMA temp_store=MEMORY;")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self):
        conn = self._get_connection()
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_org_model ON cache_records(org_id, model)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tag ON cache_records(tag)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_expiry ON cache_records(created_at, ttl_seconds)")

    def persist_entry(self, entry: CacheEntry) -> bool:
        """Synchronously persists a cache entry."""
        try:
            conn = self._get_connection()
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO cache_records (
                        key, org_id, model, user_prompt, system_prompt, schema_hash,
                        tools_hash, vector_json, response_json, tag, is_stream,
                        stream_chunks_json, created_at, last_accessed_at, ttl_seconds, hit_count
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
                    json.dumps(entry.stream_chunks) if entry.stream_chunks else None,
                    entry.created_at,
                    entry.last_accessed_at,
                    entry.ttl_seconds,
                    entry.hit_count
                ))
            return True
        except Exception as e:
            print(f"⚠️ [SnapshotStore] Failed to persist entry {entry.key}: {e}")
            return False

    async def persist_entry_async(self, entry: CacheEntry) -> bool:
        """Asynchronously persists a cache entry without blocking the event loop."""
        return await asyncio.to_thread(self.persist_entry, entry)

    def load_into_cache(self, cache: DualTierCache) -> int:
        conn = self._get_connection()
        cursor = conn.cursor()
        now = time.time()
        loaded = 0
        try:
            cursor.execute("SELECT * FROM cache_records")
            rows = cursor.fetchall()
            for row in rows:
                key, org_id, model, user_prompt, system_prompt, schema_hash, \
                tools_hash, vector_json, response_json, tag, is_stream, \
                stream_chunks_json, created_at, last_accessed_at, ttl_seconds, hit_count = row

                # Skip expired entries
                if (now - created_at) > ttl_seconds:
                    continue

                vector = json.loads(vector_json) if vector_json else []
                response_payload = json.loads(response_json)
                stream_chunks = json.loads(stream_chunks_json) if stream_chunks_json else None

                entry = CacheEntry(
                    key=key,
                    org_id=org_id,
                    model=model,
                    user_prompt=user_prompt or "",
                    system_prompt=system_prompt or "",
                    schema_hash=schema_hash or "no_schema",
                    tools_hash=tools_hash or "no_tools",
                    vector=vector,
                    response_payload=response_payload,
                    tag=tag,
                    is_stream=bool(is_stream),
                    stream_chunks=stream_chunks,
                    ttl_seconds=ttl_seconds
                )
                entry.created_at = created_at
                entry.last_accessed_at = last_accessed_at
                entry.hit_count = hit_count

                # Restore into L1
                cache.l1_exact_cache[key] = entry

                # Restore into L2 if conversational
                if entry.vector and schema_hash == "no_schema" and tools_hash == "no_tools":
                    if org_id not in cache.l2_semantic_cache:
                        cache.l2_semantic_cache[org_id] = []
                    cache.l2_semantic_cache[org_id].append(entry)

                loaded += 1
            return loaded
        except Exception as e:
            print(f"⚠️ [SnapshotStore] Recovery failed: {e}")
            return 0

    def delete_by_tag(self, tag: str, org_id: Optional[str] = None) -> int:
        conn = self._get_connection()
        try:
            with conn:
                if org_id:
                    cursor = conn.execute("DELETE FROM cache_records WHERE tag = ? AND org_id = ?", (tag, org_id))
                else:
                    cursor = conn.execute("DELETE FROM cache_records WHERE tag = ?", (tag,))
                return cursor.rowcount
        except Exception as e:
            print(f"⚠️ [SnapshotStore] Delete by tag failed: {e}")
            return 0

    async def delete_by_tag_async(self, tag: str, org_id: Optional[str] = None) -> int:
        return await asyncio.to_thread(self.delete_by_tag, tag, org_id)

    def remove_by_tag(self, tag: str, org_id: Optional[str] = None) -> int:
        return self.delete_by_tag(tag, org_id)

    def purge_all(self, org_id: Optional[str] = None) -> int:
        conn = self._get_connection()
        try:
            with conn:
                if org_id:
                    cursor = conn.execute("DELETE FROM cache_records WHERE org_id = ?", (org_id,))
                else:
                    cursor = conn.execute("DELETE FROM cache_records")
                return cursor.rowcount
        except Exception as e:
            print(f"⚠️ [SnapshotStore] Purge failed: {e}")
            return 0

    async def purge_all_async(self, org_id: Optional[str] = None) -> int:
        return await asyncio.to_thread(self.purge_all, org_id)

    def close(self):
        """Closes thread-local connection."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None

snapshot_store = SnapshotStore()
