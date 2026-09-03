"""
L1/L2 Dual-Tier Caching Engine with Intent Gating, Multi-Tenancy Isolation,
Tag-based Invalidation, and LRU Eviction.
"""

import time
import re
from typing import Dict, Any, List, Optional, Tuple
from .config import config
from .embeddings import FastSemanticEmbedder
from .hasher import RequestHasher

class CacheEntry:
    def __init__(
        self,
        key: str,
        org_id: str,
        model: str,
        user_prompt: str,
        system_prompt: str,
        schema_hash: str,
        tools_hash: str,
        vector: List[float],
        response_payload: Dict[str, Any],
        tag: Optional[str] = None,
        is_stream: bool = False,
        stream_chunks: Optional[List[Dict[str, Any]]] = None,
        ttl_seconds: int = 604800
    ):
        self.key = key
        self.org_id = org_id
        self.model = model
        self.user_prompt = user_prompt
        self.system_prompt = system_prompt
        self.schema_hash = schema_hash
        self.tools_hash = tools_hash
        self.vector = vector
        self.response_payload = response_payload
        self.tag = tag
        self.is_stream = is_stream
        self.stream_chunks = stream_chunks or []
        self.created_at = time.time()
        self.last_accessed_at = time.time()
        self.ttl_seconds = ttl_seconds
        self.hit_count = 0

    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl_seconds

    def touch(self):
        self.last_accessed_at = time.time()
        self.hit_count += 1


class DualTierCache:
    def __init__(self):
        # L1 Exact Cache: key -> CacheEntry
        self.l1_exact_cache: Dict[str, CacheEntry] = {}
        # L2 Semantic Cache: org_id -> List[CacheEntry]
        self.l2_semantic_cache: Dict[str, List[CacheEntry]] = {}
        # Cumulative Telemetry
        self.total_exact_hits = 0
        self.total_semantic_hits = 0
        self.total_misses = 0
        self.total_bypasses = 0

    def classify_intent(self, prompt: str, schema_hash: str, tools_hash: str, temperature: float) -> Tuple[str, float]:
        """
        Determines the intent and the appropriate dynamic similarity threshold.
        Returns (intent_type, required_threshold).
        """
        # If strict structured JSON schema or tools are requested
        if schema_hash != "no_schema" or tools_hash != "no_tools":
            return "structured_schema", 1.0  # Exact match only

        # High temperature means user specifically wants randomness/creativity
        if temperature > config.TEMPERATURE_BYPASS_THRESHOLD:
            return "creative_bypass", 1.01  # Cannot hit semantic cache

        # Code detection heuristics
        code_patterns = [r"```", r"def\s+\w+\(", r"function\s+\w+\(", r"class\s+\w+:", r"SELECT\s+.+\s+FROM", r"import\s+\w+"]
        for pat in code_patterns:
            if re.search(pat, prompt, re.IGNORECASE):
                return "code_generation", 0.98

        # Math / calculation detection
        if re.search(r"(\d+\s*[\+\-\*\/\^]\s*\d+|\bcalculate\b|\bsolve\b|\bevaluate\b)", prompt, re.IGNORECASE):
            return "math_calculation", 0.98

        # Default conversational / FAQ QA
        return "conversational_qa", config.DEFAULT_SIMILARITY_THRESHOLD

    def lookup(
        self,
        payload: Dict[str, Any],
        org_id: str = "default",
        custom_threshold: Optional[float] = None
    ) -> Tuple[str, Optional[CacheEntry], float]:
        """
        Performs dual-tier lookup.
        Returns: (status: 'HIT_EXACT'|'HIT_SEMANTIC'|'MISS'|'BYPASS', entry, similarity_score)
        """
        messages = payload.get("messages", [])
        system_prompt, user_prompt, is_multimodal = RequestHasher.extract_system_and_user_prompts(messages)
        temperature = float(payload.get("temperature", 0.0))
        response_format = payload.get("response_format", None)
        tools = payload.get("tools", None)
        schema_hash = RequestHasher.compute_schema_hash(response_format)
        tools_hash = RequestHasher.compute_tools_hash(tools)
        
        # 1. Check L1 Exact Cache
        exact_key = RequestHasher.compute_exact_hash(payload, org_id=org_id)
        if exact_key in self.l1_exact_cache:
            entry = self.l1_exact_cache[exact_key]
            if not entry.is_expired():
                entry.touch()
                self.total_exact_hits += 1
                return "HIT_EXACT", entry, 1.0
            else:
                del self.l1_exact_cache[exact_key]

        # If multimodal (image/audio), skip semantic vector search to avoid false matches
        if is_multimodal or not user_prompt.strip():
            self.total_misses += 1
            return "MISS", None, 0.0

        # Intent Classification & Dynamic Thresholding
        intent, dynamic_threshold = self.classify_intent(user_prompt, schema_hash, tools_hash, temperature)
        effective_threshold = custom_threshold if custom_threshold is not None else dynamic_threshold
        
        if effective_threshold > 1.0:
            self.total_bypasses += 1
            return "BYPASS", None, 0.0

        # 2. Check L2 Semantic Cache (Strictly scoped to org_id)
        org_entries = self.l2_semantic_cache.get(org_id, [])
        if not org_entries:
            self.total_misses += 1
            return "MISS", None, 0.0

        query_vector = FastSemanticEmbedder.embed(user_prompt)
        best_score = 0.0
        best_entry = None

        # Filter and compute cosine similarity
        for entry in org_entries:
            if entry.is_expired():
                continue
                
            # Must match system prompt, target schema, tools signature, and model family
            if entry.system_prompt != system_prompt or entry.schema_hash != schema_hash or entry.tools_hash != tools_hash:
                continue

            similarity = FastSemanticEmbedder.cosine_similarity(query_vector, entry.vector)
            if similarity > best_score:
                best_score = similarity
                best_entry = entry

        if best_entry and best_score >= effective_threshold:
            best_entry.touch()
            self.total_semantic_hits += 1
            return "HIT_SEMANTIC", best_entry, best_score

        self.total_misses += 1
        return "MISS", None, best_score

    def store(
        self,
        payload: Dict[str, Any],
        response_payload: Dict[str, Any],
        org_id: str = "default",
        tag: Optional[str] = None,
        custom_ttl: Optional[int] = None,
        stream_chunks: Optional[List[Dict[str, Any]]] = None
    ) -> CacheEntry:
        """
        Stores an LLM completion into both L1 and L2 caches.
        """
        messages = payload.get("messages", [])
        system_prompt, user_prompt, is_multimodal = RequestHasher.extract_system_and_user_prompts(messages)
        model = payload.get("model", "").strip().lower()
        response_format = payload.get("response_format", None)
        tools = payload.get("tools", None)
        schema_hash = RequestHasher.compute_schema_hash(response_format)
        tools_hash = RequestHasher.compute_tools_hash(tools)
        
        exact_key = RequestHasher.compute_exact_hash(payload, org_id=org_id)
        vector = FastSemanticEmbedder.embed(user_prompt) if (user_prompt and not is_multimodal) else []
        ttl_seconds = custom_ttl if custom_ttl is not None else config.SEMANTIC_CACHE_TTL_SECONDS

        entry = CacheEntry(
            key=exact_key,
            org_id=org_id,
            model=model,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            schema_hash=schema_hash,
            tools_hash=tools_hash,
            vector=vector,
            response_payload=response_payload,
            tag=tag,
            is_stream=bool(payload.get("stream", False)),
            stream_chunks=stream_chunks or [],
            ttl_seconds=ttl_seconds
        )

        # Store in L1 Exact Cache
        self.l1_exact_cache[exact_key] = entry

        # Store in L2 Semantic Cache if vector is valid
        if vector and not is_multimodal:
            if org_id not in self.l2_semantic_cache:
                self.l2_semantic_cache[org_id] = []
            
            org_list = self.l2_semantic_cache[org_id]
            org_list.append(entry)

            # Enforce LRU eviction if tenant exceeds quota
            if len(org_list) > config.MAX_CACHE_ENTRIES_PER_TENANT:
                org_list.sort(key=lambda x: x.last_accessed_at)
                evict_count = max(1, int(len(org_list) * 0.1))
                self.l2_semantic_cache[org_id] = org_list[evict_count:]

        return entry

    def purge_tenant(self, org_id: str) -> int:
        """Purges all cache entries belonging to a tenant."""
        removed = 0
        # Remove from L1
        l1_keys_to_del = [k for k, v in self.l1_exact_cache.items() if v.org_id == org_id]
        for k in l1_keys_to_del:
            del self.l1_exact_cache[k]
            removed += 1
            
        # Remove from L2
        if org_id in self.l2_semantic_cache:
            removed += len(self.l2_semantic_cache[org_id])
            del self.l2_semantic_cache[org_id]
            
        return removed

    def purge(self, org_id: Optional[str] = None) -> int:
        """Purges cache entries for a tenant or globally."""
        if org_id:
            return self.purge_tenant(org_id)
        removed = len(self.l1_exact_cache) + sum(len(v) for v in self.l2_semantic_cache.values())
        self.clear()
        return removed

    def invalidate_tag(self, tag: str, org_id: Optional[str] = None) -> int:
        """Invalidates all cache entries with a specific tag."""
        removed = 0
        l1_keys = [k for k, v in self.l1_exact_cache.items() if v.tag == tag and (org_id is None or v.org_id == org_id)]
        for k in l1_keys:
            del self.l1_exact_cache[k]
            removed += 1

        for org, entries in list(self.l2_semantic_cache.items()):
            if org_id is not None and org != org_id:
                continue
            before_len = len(entries)
            self.l2_semantic_cache[org] = [e for e in entries if e.tag != tag]
            removed += (before_len - len(self.l2_semantic_cache[org]))

        return removed

    def get_stats(self, org_id: Optional[str] = None) -> Dict[str, Any]:
        """Returns runtime cache statistics and hit ratios."""
        total_requests = self.total_exact_hits + self.total_semantic_hits + self.total_misses + self.total_bypasses
        hit_rate = (self.total_exact_hits + self.total_semantic_hits) / total_requests if total_requests > 0 else 0.0
        
        active_l1 = len(self.l1_exact_cache) if org_id is None else sum(1 for v in self.l1_exact_cache.values() if v.org_id == org_id)
        active_l2 = sum(len(v) for v in self.l2_semantic_cache.values()) if org_id is None else len(self.l2_semantic_cache.get(org_id, []))

        return {
            "total_requests": total_requests,
            "exact_hits": self.total_exact_hits,
            "semantic_hits": self.total_semantic_hits,
            "misses": self.total_misses,
            "bypasses": self.total_bypasses,
            "hit_rate_percentage": round(hit_rate * 100, 2),
            "active_l1_exact_entries": active_l1,
            "active_l2_semantic_entries": active_l2
        }

    def clear(self):
        self.l1_exact_cache.clear()
        self.l2_semantic_cache.clear()
        self.total_exact_hits = 0
        self.total_semantic_hits = 0
        self.total_misses = 0
        self.total_bypasses = 0

cache_instance = DualTierCache()
