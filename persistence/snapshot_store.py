"""
SQLite Persistence Tier for Zero Cold-Start Recovery & Write-Behind Durability.
Asynchronously records in-memory cache entries and virtual keys to an embedded SQLite store
with WAL mode, batching write-behind worker queue, and sub-millisecond hot-path latency.
"""

import sqlite3
import json
import os
import time
import queue
import threading
import asyncio
import logging
from typing import Optional, List, Dict, Any
from core.vector_cache import CacheEntry, DualTierCache

logger = logging.getLogger("omnicache.persistence")


def get_default_db_path() -> str:
    base_dir = os.getenv("OMNICACHE_DATA_DIR", os.path.expanduser("~/.omnicache"))
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, "omnicache.db")


DB_PATH = os.getenv("OMNICACHE_DB_PATH", get_default_db_path())


class SnapshotStore:
    def __init__(self, db_path: str = DB_PATH, enable_write_behind: bool = True):
        self.db_path = db_path
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._local = threading.local()
        self._init_db()

        self._enable_write_behind = enable_write_behind
        self._write_queue: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue(maxsize=50000)
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None

        if self._enable_write_behind:
            self._start_worker()

    def _get_connection(self) -> sqlite3.Connection:
        """Returns or creates a thread-local reused SQLite connection configured with WAL mode."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, timeout=15.0, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute("PRAGMA temp_store=MEMORY;")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self):
        conn = self._get_connection()
        with conn:
            # Cache records table
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

            # Durable virtual keys table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS virtual_keys (
                    key_id TEXT PRIMARY KEY,
                    team_name TEXT NOT NULL,
                    org_id TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'tenant',
                    monthly_budget_usd REAL NOT NULL DEFAULT 100.0,
                    rate_limit_rpm INTEGER NOT NULL DEFAULT 120,
                    created_at REAL NOT NULL,
                    current_spend_usd REAL NOT NULL DEFAULT 0.0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_vkey_org ON virtual_keys(org_id)")

    def _start_worker(self):
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="omnicache-write-behind-worker",
            daemon=True
        )
        self._worker_thread.start()

    def _worker_loop(self):
        """Drains the write-behind queue in micro-batches and commits with WAL."""
        batch: List[Dict[str, Any]] = []
        batch_max = 100
        timeout = 0.05

        while self._running:
            try:
                item = self._write_queue.get(timeout=timeout)
                if item is None:
                    self._write_queue.task_done()
                    break
                batch.append(item)
                while len(batch) < batch_max:
                    try:
                        extra = self._write_queue.get_nowait()
                        if extra is None:
                            self._write_queue.task_done()
                            break
                        batch.append(extra)
                    except queue.Empty:
                        break

                if batch:
                    self._process_batch(batch)
                    for _ in range(len(batch)):
                        self._write_queue.task_done()
                    batch.clear()

            except queue.Empty:
                if batch:
                    self._process_batch(batch)
                    for _ in range(len(batch)):
                        self._write_queue.task_done()
                    batch.clear()
            except Exception as exc:
                logger.error(f"Error in write-behind worker loop: {exc}")
                if batch:
                    for _ in range(len(batch)):
                        self._write_queue.task_done()
                    batch.clear()

        # Final drain on shutdown
        while not self._write_queue.empty():
            try:
                item = self._write_queue.get_nowait()
                if item is not None:
                    self._process_batch([item])
                self._write_queue.task_done()
            except Exception:
                break

    def _process_batch(self, items: List[Dict[str, Any]]):
        conn = self._get_connection()
        try:
            with conn:
                cache_inserts = []
                key_inserts = []
                spend_updates = []
                tag_deletes = []
                purge_orgs = []

                for it in items:
                    op = it.get("op")
                    if op == "insert_cache":
                        entry: CacheEntry = it["entry"]
                        cache_inserts.append((
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
                    elif op == "save_key":
                        key_inserts.append((
                            it["key_id"],
                            it["team_name"],
                            it["org_id"],
                            it["role"],
                            it["monthly_budget_usd"],
                            it["rate_limit_rpm"],
                            it["created_at"],
                            it["current_spend_usd"]
                        ))
                    elif op == "spend_key":
                        spend_updates.append((it["spend_usd"], it["key_id"]))
                    elif op == "delete_tag":
                        tag_deletes.append((it["tag"], it.get("org_id")))
                    elif op == "purge":
                        purge_orgs.append(it.get("org_id"))

                if cache_inserts:
                    conn.executemany("""
                        INSERT OR REPLACE INTO cache_records (
                            key, org_id, model, user_prompt, system_prompt, schema_hash,
                            tools_hash, vector_json, response_json, tag, is_stream,
                            stream_chunks_json, created_at, last_accessed_at, ttl_seconds, hit_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, cache_inserts)

                if key_inserts:
                    conn.executemany("""
                        INSERT OR REPLACE INTO virtual_keys (
                            key_id, team_name, org_id, role, monthly_budget_usd,
                            rate_limit_rpm, created_at, current_spend_usd
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, key_inserts)

                if spend_updates:
                    conn.executemany("""
                        UPDATE virtual_keys
                        SET current_spend_usd = current_spend_usd + ?
                        WHERE key_id = ?
                    """, spend_updates)

                for tag, org_id in tag_deletes:
                    if org_id:
                        conn.execute("DELETE FROM cache_records WHERE tag = ? AND org_id = ?", (tag, org_id))
                    else:
                        conn.execute("DELETE FROM cache_records WHERE tag = ?", (tag,))

                for org_id in purge_orgs:
                    if org_id:
                        conn.execute("DELETE FROM cache_records WHERE org_id = ?", (org_id,))
                    else:
                        conn.execute("DELETE FROM cache_records")

        except Exception as e:
            logger.warning(f"[SnapshotStore] Batch execution error: {e}")

    # =========================================================================
    # Cache Persistence API
    # =========================================================================

    def persist_entry(self, entry: CacheEntry, synchronous: bool = True) -> bool:
        """Persists a cache entry synchronously by default, or asynchronously via queue if False."""
        if synchronous or not self._enable_write_behind:
            try:
                self._process_batch([{"op": "insert_cache", "entry": entry}])
                return True
            except Exception as e:
                logger.warning(f"[SnapshotStore] Sync persist error: {e}")
                return False
        else:
            try:
                self._write_queue.put_nowait({"op": "insert_cache", "entry": entry})
                return True
            except queue.Full:
                logger.warning("[SnapshotStore] Write-behind queue is full, writing synchronously")
                return self.persist_entry(entry, synchronous=True)

    async def persist_entry_async(self, entry: CacheEntry) -> bool:
        """Non-blocking asynchronous persist without blocking request completion."""
        if self._enable_write_behind:
            try:
                self._write_queue.put_nowait({"op": "insert_cache", "entry": entry})
                return True
            except queue.Full:
                return await asyncio.to_thread(self.persist_entry, entry, True)
        return await asyncio.to_thread(self.persist_entry, entry, True)

    def load_into_cache(self, cache: DualTierCache) -> int:
        """Flushes any pending writes and loads non-expired records from SQLite into DualTierCache."""
        self.flush()
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
            logger.warning(f"[SnapshotStore] Recovery failed: {e}")
            return 0

    def delete_by_tag(self, tag: str, org_id: Optional[str] = None, synchronous: bool = True) -> int:
        """Deletes cache records by tag."""
        if synchronous or not self._enable_write_behind:
            self.flush()
            conn = self._get_connection()
            try:
                with conn:
                    if org_id:
                        cursor = conn.execute("DELETE FROM cache_records WHERE tag = ? AND org_id = ?", (tag, org_id))
                    else:
                        cursor = conn.execute("DELETE FROM cache_records WHERE tag = ?", (tag,))
                    return cursor.rowcount
            except Exception as e:
                logger.warning(f"[SnapshotStore] Delete by tag failed: {e}")
                return 0
        else:
            self._write_queue.put({"op": "delete_tag", "tag": tag, "org_id": org_id})
            return 1

    async def delete_by_tag_async(self, tag: str, org_id: Optional[str] = None) -> int:
        return await asyncio.to_thread(self.delete_by_tag, tag, org_id, True)

    def remove_by_tag(self, tag: str, org_id: Optional[str] = None) -> int:
        return self.delete_by_tag(tag, org_id, synchronous=True)

    def purge_all(self, org_id: Optional[str] = None, synchronous: bool = True) -> int:
        """Purges cache records."""
        if synchronous or not self._enable_write_behind:
            self.flush()
            conn = self._get_connection()
            try:
                with conn:
                    if org_id:
                        cursor = conn.execute("DELETE FROM cache_records WHERE org_id = ?", (org_id,))
                    else:
                        cursor = conn.execute("DELETE FROM cache_records")
                    return cursor.rowcount
            except Exception as e:
                logger.warning(f"[SnapshotStore] Purge failed: {e}")
                return 0
        else:
            self._write_queue.put({"op": "purge", "org_id": org_id})
            return 1

    async def purge_all_async(self, org_id: Optional[str] = None) -> int:
        return await asyncio.to_thread(self.purge_all, org_id, True)

    # =========================================================================
    # Virtual Key Persistence API
    # =========================================================================

    def save_virtual_key(
        self,
        key_id: str,
        team_name: str,
        org_id: str,
        role: str,
        monthly_budget_usd: float,
        rate_limit_rpm: int,
        created_at: float,
        current_spend_usd: float = 0.0,
        synchronous: bool = True
    ):
        """Saves or updates virtual key registration."""
        item = {
            "op": "save_key",
            "key_id": key_id,
            "team_name": team_name,
            "org_id": org_id,
            "role": role,
            "monthly_budget_usd": monthly_budget_usd,
            "rate_limit_rpm": rate_limit_rpm,
            "created_at": created_at,
            "current_spend_usd": current_spend_usd
        }
        if synchronous or not self._enable_write_behind:
            self._process_batch([item])
        else:
            self._write_queue.put(item)

    def record_virtual_key_spend(self, key_id: str, spend_usd: float, synchronous: bool = False):
        """Increments virtual key recorded spend."""
        item = {"op": "spend_key", "key_id": key_id, "spend_usd": spend_usd}
        if synchronous or not self._enable_write_behind:
            self._process_batch([item])
        else:
            self._write_queue.put(item)

    def load_virtual_keys(self) -> Dict[str, Dict[str, Any]]:
        """Loads all registered virtual keys and historical spend from SQLite."""
        self.flush()
        conn = self._get_connection()
        result = {}
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT key_id, team_name, org_id, role, monthly_budget_usd, rate_limit_rpm, created_at, current_spend_usd FROM virtual_keys")
            rows = cursor.fetchall()
            for row in rows:
                key_id, team_name, org_id, role, monthly_budget_usd, rate_limit_rpm, created_at, current_spend_usd = row
                result[key_id] = {
                    "team_name": team_name,
                    "org_id": org_id,
                    "role": role,
                    "monthly_budget_usd": float(monthly_budget_usd),
                    "rate_limit_rpm": int(rate_limit_rpm),
                    "created_at": float(created_at),
                    "current_spend_usd": float(current_spend_usd),
                    "request_timestamps": []
                }
        except Exception as e:
            logger.warning(f"[SnapshotStore] Failed to load virtual keys: {e}")
        return result

    # =========================================================================
    # Lifecycle & Cleanup
    # =========================================================================

    def flush(self, timeout: float = 3.0):
        """Blocks until the write-behind queue has fully drained and committed to SQLite."""
        if not self._enable_write_behind:
            return
        try:
            self._write_queue.join()
        except Exception:
            pass

    def close(self):
        """Flushes queue, shuts down worker, and closes thread-local SQLite connection."""
        if self._running:
            self._running = False
            if self._worker_thread and self._worker_thread.is_alive():
                self._write_queue.put(None)
                self._worker_thread.join(timeout=3.0)

        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None


snapshot_store = SnapshotStore()
