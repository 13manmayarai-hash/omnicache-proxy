"""
Pluggable Storage Engine Interface and Adapters.
Provides local in-memory and distributed Redis backend implementations for L1 and L2 caching
with atomic pipelines, secondary tenant index sets, true LRU eviction, and replica sync versions.
"""

import time
import json
import logging
import threading
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger("omnicache.storage")


class BaseCacheStorage(ABC):
    @abstractmethod
    def get_exact(self, key: str) -> Optional[Any]:
        pass

    @abstractmethod
    def set_exact(self, key: str, entry: Any, ttl_seconds: int):
        pass

    @abstractmethod
    def delete_exact(self, key: str, org_id: Optional[str] = None):
        pass

    @abstractmethod
    def get_semantic_entries(self, org_id: str) -> List[Any]:
        pass

    @abstractmethod
    def add_semantic_entry(self, org_id: str, entry: Any, ttl_seconds: int, max_entries: int):
        pass

    @abstractmethod
    def update_semantic_entries(self, org_id: str, entries: List[Any]):
        pass

    @abstractmethod
    def purge(self, org_id: Optional[str] = None) -> int:
        pass

    @abstractmethod
    def invalidate_tag(self, tag: str, org_id: Optional[str] = None) -> int:
        pass

    @abstractmethod
    def get_stats_counts(self, org_id: Optional[str] = None) -> Tuple[int, int]:
        pass

    @abstractmethod
    def get_l2_version(self, org_id: str) -> int:
        pass

    @abstractmethod
    def bump_l2_version(self, org_id: str) -> int:
        pass

    @abstractmethod
    def clear(self):
        pass


class InMemoryCacheStorage(BaseCacheStorage):
    """Local single-process memory cache adapter."""

    def __init__(self):
        self.l1_exact_cache: Dict[str, Any] = {}
        self.l2_semantic_cache: Dict[str, List[Any]] = {}
        self._l2_versions: Dict[str, int] = {}
        self._lock = threading.RLock()

    def get_exact(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self.l1_exact_cache.get(key)
            if entry and getattr(entry, "is_expired", lambda: False)():
                del self.l1_exact_cache[key]
                return None
            return entry

    def set_exact(self, key: str, entry: Any, ttl_seconds: int):
        with self._lock:
            self.l1_exact_cache[key] = entry

    def delete_exact(self, key: str, org_id: Optional[str] = None):
        with self._lock:
            if key in self.l1_exact_cache:
                del self.l1_exact_cache[key]

    def get_semantic_entries(self, org_id: str) -> List[Any]:
        with self._lock:
            return list(self.l2_semantic_cache.get(org_id, []))

    def add_semantic_entry(self, org_id: str, entry: Any, ttl_seconds: int, max_entries: int):
        with self._lock:
            if org_id not in self.l2_semantic_cache:
                self.l2_semantic_cache[org_id] = []

            # Replace existing entry if matching key
            entry_key = getattr(entry, "key", "")
            replaced = False
            for idx, existing in enumerate(self.l2_semantic_cache[org_id]):
                if getattr(existing, "key", "") == entry_key:
                    self.l2_semantic_cache[org_id][idx] = entry
                    replaced = True
                    break

            if not replaced:
                self.l2_semantic_cache[org_id].append(entry)

            # Evict LRU oldest 10% if exceeds max entries
            if len(self.l2_semantic_cache[org_id]) > max_entries:
                self.l2_semantic_cache[org_id].sort(key=lambda x: getattr(x, "last_accessed_at", 0.0))
                evict_count = max(1, int(len(self.l2_semantic_cache[org_id]) * 0.1))
                self.l2_semantic_cache[org_id] = self.l2_semantic_cache[org_id][evict_count:]

            self._l2_versions[org_id] = self._l2_versions.get(org_id, 0) + 1

    def update_semantic_entries(self, org_id: str, entries: List[Any]):
        with self._lock:
            self.l2_semantic_cache[org_id] = list(entries)
            self._l2_versions[org_id] = self._l2_versions.get(org_id, 0) + 1

    def purge(self, org_id: Optional[str] = None) -> int:
        with self._lock:
            removed = 0
            if org_id:
                l1_keys = [k for k, v in self.l1_exact_cache.items() if getattr(v, "org_id", "") == org_id]
                for k in l1_keys:
                    del self.l1_exact_cache[k]
                    removed += 1
                if org_id in self.l2_semantic_cache:
                    removed += len(self.l2_semantic_cache[org_id])
                    del self.l2_semantic_cache[org_id]
                self._l2_versions[org_id] = self._l2_versions.get(org_id, 0) + 1
            else:
                removed = len(self.l1_exact_cache) + sum(len(v) for v in self.l2_semantic_cache.values())
                self.l1_exact_cache.clear()
                self.l2_semantic_cache.clear()
                for org in list(self._l2_versions.keys()):
                    self._l2_versions[org] += 1
            return removed

    def invalidate_tag(self, tag: str, org_id: Optional[str] = None) -> int:
        with self._lock:
            removed = 0
            l1_keys = [k for k, v in self.l1_exact_cache.items() if getattr(v, "tag", None) == tag and (org_id is None or getattr(v, "org_id", "") == org_id)]
            for k in l1_keys:
                del self.l1_exact_cache[k]
                removed += 1

            for org, entries in list(self.l2_semantic_cache.items()):
                if org_id is not None and org != org_id:
                    continue
                before_len = len(entries)
                self.l2_semantic_cache[org] = [e for e in entries if getattr(e, "tag", None) != tag]
                delta = before_len - len(self.l2_semantic_cache[org])
                if delta > 0:
                    removed += delta
                    self._l2_versions[org] = self._l2_versions.get(org, 0) + 1
            return removed

    def get_stats_counts(self, org_id: Optional[str] = None) -> Tuple[int, int]:
        with self._lock:
            active_l1 = len(self.l1_exact_cache) if org_id is None else sum(1 for v in self.l1_exact_cache.values() if getattr(v, "org_id", "") == org_id)
            active_l2 = sum(len(v) for v in self.l2_semantic_cache.values()) if org_id is None else len(self.l2_semantic_cache.get(org_id, []))
            return active_l1, active_l2

    def get_l2_version(self, org_id: str) -> int:
        with self._lock:
            return self._l2_versions.get(org_id, 0)

    def bump_l2_version(self, org_id: str) -> int:
        with self._lock:
            new_v = self._l2_versions.get(org_id, 0) + 1
            self._l2_versions[org_id] = new_v
            return new_v

    def clear(self):
        with self._lock:
            self.l1_exact_cache.clear()
            self.l2_semantic_cache.clear()
            for org in list(self._l2_versions.keys()):
                self._l2_versions[org] += 1


class RedisCacheStorage(BaseCacheStorage):
    """
    Distributed Redis cache adapter for multi-worker and multi-replica clusters.
    Uses secondary tenant index sets, true LRU eviction, and synchronized replica versioning.
    """

    def __init__(self, redis_client=None, redis_url: str = "redis://127.0.0.1:6379/0", prefix: str = "omnicache", entry_cls: Any = None):
        self.prefix = prefix
        self.entry_cls = entry_cls
        if redis_client is not None:
            self.client = redis_client
        else:
            import redis
            self.client = redis.Redis.from_url(redis_url, decode_responses=True)

    def _l1_key(self, key: str) -> str:
        return f"{self.prefix}:l1:{key}"

    def _tenant_l1_key(self, org_id: str) -> str:
        return f"{self.prefix}:tenant_l1:{org_id}"

    def _l2_hash_key(self, org_id: str) -> str:
        return f"{self.prefix}:l2:{org_id}"

    def _l2_lru_key(self, org_id: str) -> str:
        return f"{self.prefix}:l2_lru:{org_id}"

    def _l2_ver_key(self, org_id: str) -> str:
        return f"{self.prefix}:l2_ver:{org_id}"

    def _tag_key(self, tag: str) -> str:
        return f"{self.prefix}:tag:{tag}"

    def _tenant_tag_key(self, org_id: str, tag: str) -> str:
        return f"{self.prefix}:tenant_tag:{org_id}:{tag}"

    def _deserialize_entry(self, data_str: str) -> Optional[Any]:
        if not data_str:
            return None
        try:
            d = json.loads(data_str)
            if self.entry_cls and hasattr(self.entry_cls, "from_dict"):
                return self.entry_cls.from_dict(d)
            return d
        except Exception as exc:
            logger.warning("Redis deserialization error: %s", exc)
            return None

    def _serialize_entry(self, entry: Any) -> str:
        if hasattr(entry, "to_dict"):
            d = entry.to_dict()
        elif isinstance(entry, dict):
            d = entry
        else:
            d = entry.__dict__
        return json.dumps(d)

    def get_exact(self, key: str) -> Optional[Any]:
        try:
            val = self.client.get(self._l1_key(key))
            return self._deserialize_entry(val) if val else None
        except Exception as exc:
            logger.warning("Redis get_exact failed: %s", exc)
            return None

    def set_exact(self, key: str, entry: Any, ttl_seconds: int):
        try:
            payload = self._serialize_entry(entry)
            org_id = getattr(entry, "org_id", "default")
            r_key = self._l1_key(key)
            t_l1_k = self._tenant_l1_key(org_id)

            pipe = self.client.pipeline()
            pipe.set(r_key, payload, ex=ttl_seconds)
            pipe.sadd(t_l1_k, key)
            pipe.expire(t_l1_k, max(86400, ttl_seconds))

            tag = getattr(entry, "tag", None)
            if tag:
                pipe.sadd(self._tag_key(tag), key)
                pipe.sadd(self._tenant_tag_key(org_id, tag), key)
                pipe.expire(self._tag_key(tag), ttl_seconds)
                pipe.expire(self._tenant_tag_key(org_id, tag), ttl_seconds)

            pipe.execute()
        except Exception as exc:
            logger.warning("Redis set_exact failed: %s", exc)

    def delete_exact(self, key: str, org_id: Optional[str] = None):
        try:
            pipe = self.client.pipeline()
            pipe.delete(self._l1_key(key))
            if org_id:
                pipe.srem(self._tenant_l1_key(org_id), key)
            pipe.execute()
        except Exception as exc:
            logger.warning("Redis delete_exact failed: %s", exc)

    def get_semantic_entries(self, org_id: str) -> List[Any]:
        try:
            raw_entries = self.client.hgetall(self._l2_hash_key(org_id))
            entries = []
            for k, val in raw_entries.items():
                e = self._deserialize_entry(val)
                if e:
                    entries.append(e)
            return entries
        except Exception as exc:
            logger.warning("Redis get_semantic_entries failed: %s", exc)
            return []

    def add_semantic_entry(self, org_id: str, entry: Any, ttl_seconds: int, max_entries: int):
        try:
            key = getattr(entry, "key", "") or str(time.time())
            payload = self._serialize_entry(entry)
            last_accessed = float(getattr(entry, "last_accessed_at", time.time()))
            hkey = self._l2_hash_key(org_id)
            lru_key = self._l2_lru_key(org_id)

            pipe = self.client.pipeline()
            pipe.hset(hkey, key, payload)
            pipe.expire(hkey, ttl_seconds)
            pipe.zadd(lru_key, {key: last_accessed})
            pipe.expire(lru_key, ttl_seconds)
            pipe.incr(self._l2_ver_key(org_id))
            pipe.execute()

            # True LRU Eviction when tenant exceeds capacity
            size = self.client.hlen(hkey)
            if size > max_entries:
                evict_count = max(1, int(size * 0.1))
                # True LRU: ZRANGE returns members scored by oldest last_accessed_at
                oldest_keys = self.client.zrange(lru_key, 0, evict_count - 1)
                if oldest_keys:
                    pipe = self.client.pipeline()
                    pipe.hdel(hkey, *oldest_keys)
                    pipe.zrem(lru_key, *oldest_keys)
                    pipe.incr(self._l2_ver_key(org_id))
                    pipe.execute()
        except Exception as exc:
            logger.warning("Redis add_semantic_entry failed: %s", exc)

    def update_semantic_entries(self, org_id: str, entries: List[Any]):
        try:
            hkey = self._l2_hash_key(org_id)
            lru_key = self._l2_lru_key(org_id)
            mapping = {getattr(e, "key", f"entry_{idx}"): self._serialize_entry(e) for idx, e in enumerate(entries)}
            lru_mapping = {getattr(e, "key", f"entry_{idx}"): float(getattr(e, "last_accessed_at", time.time())) for idx, e in enumerate(entries)}

            pipe = self.client.pipeline()
            pipe.delete(hkey)
            pipe.delete(lru_key)
            if mapping:
                pipe.hset(hkey, mapping=mapping)
                pipe.zadd(lru_key, mapping=lru_mapping)
            pipe.incr(self._l2_ver_key(org_id))
            pipe.execute()
        except Exception as exc:
            logger.warning("Redis update_semantic_entries failed: %s", exc)

    def purge(self, org_id: Optional[str] = None) -> int:
        removed = 0
        try:
            if org_id:
                # 1. Purge L1 via secondary index set (O(tenant_keys), zero global scan)
                t_l1_k = self._tenant_l1_key(org_id)
                l1_keys = self.client.smembers(t_l1_k)
                pipe = self.client.pipeline()
                for k in l1_keys:
                    pipe.delete(self._l1_key(k))
                    removed += 1
                pipe.delete(t_l1_k)

                # 2. Purge L2 for org
                hkey = self._l2_hash_key(org_id)
                lru_key = self._l2_lru_key(org_id)
                removed += self.client.hlen(hkey)
                pipe.delete(hkey)
                pipe.delete(lru_key)
                pipe.incr(self._l2_ver_key(org_id))
                pipe.execute()
            else:
                keys = list(self.client.keys(f"{self.prefix}:*"))
                if keys:
                    pipe = self.client.pipeline()
                    for k in keys:
                        pipe.delete(k)
                    pipe.execute()
                    removed = len(keys)
        except Exception as exc:
            logger.warning("Redis purge failed: %s", exc)
        return removed

    def invalidate_tag(self, tag: str, org_id: Optional[str] = None) -> int:
        removed = 0
        try:
            if org_id:
                tenant_tag_k = self._tenant_tag_key(org_id, tag)
                l1_keys = self.client.smembers(tenant_tag_k)
                t_l1_k = self._tenant_l1_key(org_id)
                pipe = self.client.pipeline()
                for k in l1_keys:
                    pipe.delete(self._l1_key(k))
                    pipe.srem(t_l1_k, k)
                    removed += 1
                pipe.delete(tenant_tag_k)
                pipe.execute()

                # Invalidate in L2 hash
                hkey = self._l2_hash_key(org_id)
                lru_key = self._l2_lru_key(org_id)
                items = self.client.hgetall(hkey)
                keys_to_delete = []
                for item_k, item_val in items.items():
                    entry = self._deserialize_entry(item_val)
                    if entry and getattr(entry, "tag", None) == tag:
                        keys_to_delete.append(item_k)

                if keys_to_delete:
                    pipe = self.client.pipeline()
                    pipe.hdel(hkey, *keys_to_delete)
                    pipe.zrem(lru_key, *keys_to_delete)
                    pipe.incr(self._l2_ver_key(org_id))
                    pipe.execute()
                    removed += len(keys_to_delete)
            else:
                tag_k = self._tag_key(tag)
                keys = self.client.smembers(tag_k)
                pipe = self.client.pipeline()
                for k in keys:
                    pipe.delete(self._l1_key(k))
                    removed += 1
                pipe.delete(tag_k)
                pipe.execute()

                for hkey in self.client.scan_iter(f"{self.prefix}:l2:*"):
                    items = self.client.hgetall(hkey)
                    keys_to_delete = []
                    for item_k, item_val in items.items():
                        entry = self._deserialize_entry(item_val)
                        if entry and getattr(entry, "tag", None) == tag:
                            keys_to_delete.append(item_k)
                    if keys_to_delete:
                        org_from_key = hkey.split(":")[-1]
                        pipe = self.client.pipeline()
                        pipe.hdel(hkey, *keys_to_delete)
                        pipe.zrem(self._l2_lru_key(org_from_key), *keys_to_delete)
                        pipe.incr(self._l2_ver_key(org_from_key))
                        pipe.execute()
                        removed += len(keys_to_delete)

        except Exception as exc:
            logger.warning("Redis invalidate_tag failed: %s", exc)
        return removed

    def get_stats_counts(self, org_id: Optional[str] = None) -> Tuple[int, int]:
        active_l1 = 0
        active_l2 = 0
        try:
            if org_id:
                active_l1 = self.client.scard(self._tenant_l1_key(org_id))
                active_l2 = self.client.hlen(self._l2_hash_key(org_id))
            else:
                active_l1 = len(list(self.client.scan_iter(f"{self.prefix}:l1:*")))
                for hkey in self.client.scan_iter(f"{self.prefix}:l2:*"):
                    active_l2 += self.client.hlen(hkey)
        except Exception as exc:
            logger.warning("Redis get_stats_counts failed: %s", exc)
        return active_l1, active_l2

    def get_l2_version(self, org_id: str) -> int:
        try:
            val = self.client.get(self._l2_ver_key(org_id))
            return int(val) if val else 0
        except Exception as exc:
            logger.warning("Redis get_l2_version failed: %s", exc)
            return 0

    def bump_l2_version(self, org_id: str) -> int:
        try:
            return self.client.incr(self._l2_ver_key(org_id))
        except Exception as exc:
            logger.warning("Redis bump_l2_version failed: %s", exc)
            return 0

    def clear(self):
        try:
            for k in self.client.scan_iter(f"{self.prefix}:*"):
                self.client.delete(k)
        except Exception as exc:
            logger.warning("Redis clear failed: %s", exc)
