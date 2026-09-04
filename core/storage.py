"""
Distributed Storage Adapters for OmniCache Dual-Tier Cache Engine.
Supports both zero-dependency InMemory mode and clustered Redis backend.
"""

import json
import time
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("omnicache.storage")


class BaseCacheStorage(ABC):
    """Abstract interface for L1 Exact and L2 Semantic Cache Storage."""

    @abstractmethod
    def get_exact(self, key: str) -> Optional[Any]:
        pass

    @abstractmethod
    def set_exact(self, key: str, entry: Any, ttl_seconds: int):
        pass

    @abstractmethod
    def delete_exact(self, key: str):
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
    def clear(self):
        pass


class InMemoryCacheStorage(BaseCacheStorage):
    """In-memory storage adapter for single-process local operation."""

    def __init__(self):
        self.l1_exact_cache: Dict[str, Any] = {}
        self.l2_semantic_cache: Dict[str, List[Any]] = {}

    def get_exact(self, key: str) -> Optional[Any]:
        return self.l1_exact_cache.get(key)

    def set_exact(self, key: str, entry: Any, ttl_seconds: int):
        self.l1_exact_cache[key] = entry

    def delete_exact(self, key: str):
        if key in self.l1_exact_cache:
            del self.l1_exact_cache[key]

    def get_semantic_entries(self, org_id: str) -> List[Any]:
        return self.l2_semantic_cache.get(org_id, [])

    def add_semantic_entry(self, org_id: str, entry: Any, ttl_seconds: int, max_entries: int):
        if org_id not in self.l2_semantic_cache:
            self.l2_semantic_cache[org_id] = []
        org_list = self.l2_semantic_cache[org_id]
        org_list.append(entry)

        # LRU eviction if tenant exceeds limit
        if len(org_list) > max_entries:
            org_list.sort(key=lambda x: getattr(x, "last_accessed_at", 0.0))
            evict_count = max(1, int(len(org_list) * 0.1))
            self.l2_semantic_cache[org_id] = org_list[evict_count:]

    def update_semantic_entries(self, org_id: str, entries: List[Any]):
        self.l2_semantic_cache[org_id] = entries

    def purge(self, org_id: Optional[str] = None) -> int:
        removed = 0
        if org_id:
            l1_keys = [k for k, v in self.l1_exact_cache.items() if getattr(v, "org_id", "") == org_id]
            for k in l1_keys:
                del self.l1_exact_cache[k]
                removed += 1
            if org_id in self.l2_semantic_cache:
                removed += len(self.l2_semantic_cache[org_id])
                del self.l2_semantic_cache[org_id]
        else:
            removed = len(self.l1_exact_cache) + sum(len(v) for v in self.l2_semantic_cache.values())
            self.clear()
        return removed

    def invalidate_tag(self, tag: str, org_id: Optional[str] = None) -> int:
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
            removed += (before_len - len(self.l2_semantic_cache[org]))
        return removed

    def get_stats_counts(self, org_id: Optional[str] = None) -> Tuple[int, int]:
        active_l1 = len(self.l1_exact_cache) if org_id is None else sum(1 for v in self.l1_exact_cache.values() if getattr(v, "org_id", "") == org_id)
        active_l2 = sum(len(v) for v in self.l2_semantic_cache.values()) if org_id is None else len(self.l2_semantic_cache.get(org_id, []))
        return active_l1, active_l2

    def clear(self):
        self.l1_exact_cache.clear()
        self.l2_semantic_cache.clear()


class RedisCacheStorage(BaseCacheStorage):
    """Distributed Redis cache adapter for multi-worker and multi-replica clusters."""

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

    def _l2_hash_key(self, org_id: str) -> str:
        return f"{self.prefix}:l2:{org_id}"

    def _tag_key(self, tag: str) -> str:
        return f"{self.prefix}:tag:{tag}"

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
            r_key = self._l1_key(key)
            pipe = self.client.pipeline()
            pipe.set(r_key, payload, ex=ttl_seconds)
            tag = getattr(entry, "tag", None)
            if tag:
                pipe.sadd(self._tag_key(tag), key)
                pipe.expire(self._tag_key(tag), ttl_seconds)
            pipe.execute()
        except Exception as exc:
            logger.warning("Redis set_exact failed: %s", exc)

    def delete_exact(self, key: str):
        try:
            self.client.delete(self._l1_key(key))
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
            hkey = self._l2_hash_key(org_id)
            pipe = self.client.pipeline()
            pipe.hset(hkey, key, payload)
            pipe.expire(hkey, ttl_seconds)
            pipe.execute()

            # Trim if tenant exceeds max entries
            size = self.client.hlen(hkey)
            if size > max_entries:
                keys_to_remove = list(self.client.hkeys(hkey))[: max(1, int(size * 0.1))]
                if keys_to_remove:
                    self.client.hdel(hkey, *keys_to_remove)
        except Exception as exc:
            logger.warning("Redis add_semantic_entry failed: %s", exc)

    def update_semantic_entries(self, org_id: str, entries: List[Any]):
        try:
            hkey = self._l2_hash_key(org_id)
            mapping = {getattr(e, "key", f"entry_{idx}"): self._serialize_entry(e) for idx, e in enumerate(entries)}
            pipe = self.client.pipeline()
            pipe.delete(hkey)
            if mapping:
                pipe.hset(hkey, mapping=mapping)
            pipe.execute()
        except Exception as exc:
            logger.warning("Redis update_semantic_entries failed: %s", exc)

    def purge(self, org_id: Optional[str] = None) -> int:
        removed = 0
        try:
            if org_id:
                # Remove L2 entries for org
                hkey = self._l2_hash_key(org_id)
                removed += self.client.hlen(hkey)
                self.client.delete(hkey)

                # Scan and remove L1 keys matching org
                for l1_k in self.client.scan_iter(f"{self.prefix}:l1:*"):
                    val = self.client.get(l1_k)
                    if val and f'"org_id": "{org_id}"' in val:
                        self.client.delete(l1_k)
                        removed += 1
            else:
                for k in self.client.scan_iter(f"{self.prefix}:*"):
                    self.client.delete(k)
                    removed += 1
        except Exception as exc:
            logger.warning("Redis purge failed: %s", exc)
        return removed

    def invalidate_tag(self, tag: str, org_id: Optional[str] = None) -> int:
        removed = 0
        try:
            tag_k = self._tag_key(tag)
            keys = self.client.smembers(tag_k)
            pipe = self.client.pipeline()
            for k in keys:
                pipe.delete(self._l1_key(k))
                removed += 1
            pipe.delete(tag_k)
            pipe.execute()

            # Scan L2 hashes and remove tagged entries
            pattern = f"{self.prefix}:l2:*" if org_id is None else self._l2_hash_key(org_id)
            for hkey in self.client.scan_iter(pattern):
                items = self.client.hgetall(hkey)
                for item_k, item_val in items.items():
                    if f'"tag": "{tag}"' in item_val or f'"tag":"{tag}"' in item_val:
                        self.client.hdel(hkey, item_k)
                        removed += 1
        except Exception as exc:
            logger.warning("Redis invalidate_tag failed: %s", exc)
        return removed

    def get_stats_counts(self, org_id: Optional[str] = None) -> Tuple[int, int]:
        active_l1 = 0
        active_l2 = 0
        try:
            if org_id:
                active_l2 = self.client.hlen(self._l2_hash_key(org_id))
                for l1_k in self.client.scan_iter(f"{self.prefix}:l1:*"):
                    val = self.client.get(l1_k)
                    if val and f'"org_id": "{org_id}"' in val:
                        active_l1 += 1
            else:
                active_l1 = len(list(self.client.scan_iter(f"{self.prefix}:l1:*")))
                for hkey in self.client.scan_iter(f"{self.prefix}:l2:*"):
                    active_l2 += self.client.hlen(hkey)
        except Exception as exc:
            logger.warning("Redis get_stats_counts failed: %s", exc)
        return active_l1, active_l2

    def clear(self):
        try:
            for k in self.client.scan_iter(f"{self.prefix}:*"):
                self.client.delete(k)
        except Exception as exc:
            logger.warning("Redis clear failed: %s", exc)
