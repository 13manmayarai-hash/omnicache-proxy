"""
L1/L2 Dual-Tier Caching Engine with Intent Gating, Strict Model-Family Matching,
Multi-Tenancy Isolation, Explainable Decision Reasons, and Proactive Freshness Validation.
"""

import time
import re
from typing import Dict, Any, List, Optional, Tuple
from .config import config
from .embeddings import FastSemanticEmbedder
from .hasher import RequestHasher
from .storage import BaseCacheStorage, InMemoryCacheStorage, RedisCacheStorage
from .ann_index import ANNIndexFactory, BaseANNIndex

def get_model_family(model: str) -> str:
    """
    Maps arbitrary model identifiers into strict vendor and architectural families.
    Guarantees zero cross-family semantic contamination.
    """
    m = model.lower().strip()
    if "claude" in m:
        if "sonnet" in m:
            return "anthropic-claude-sonnet"
        elif "haiku" in m:
            return "anthropic-claude-haiku"
        elif "opus" in m:
            return "anthropic-claude-opus"
        return "anthropic-claude"
    elif "gpt-4o-mini" in m or "gpt-3.5" in m:
        return "openai-gpt4o-mini"
    elif "gpt-4o" in m or "gpt-4" in m:
        return "openai-gpt4o"
    elif "o1" in m or "o3" in m:
        return "openai-reasoning"
    elif "gemini" in m:
        if "flash" in m:
            return "google-gemini-flash"
        elif "pro" in m:
            return "google-gemini-pro"
        return "google-gemini"
    elif "llama" in m:
        return "meta-llama"
    elif "mistral" in m or "mixtral" in m:
        return "mistral-ai"
    return f"generic-{m.split(':')[0].split('/')[0]}"


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
        ttl_seconds: int = 604800,
        is_exact_tokens: bool = True,
        prompt_tokens: int = 0,
        completion_tokens: int = 0
    ):
        self.key = key
        self.org_id = org_id
        self.model = model
        self.model_family = get_model_family(model)
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
        self.is_exact_tokens = is_exact_tokens
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "org_id": self.org_id,
            "model": self.model,
            "model_family": self.model_family,
            "user_prompt": self.user_prompt,
            "system_prompt": self.system_prompt,
            "schema_hash": self.schema_hash,
            "tools_hash": self.tools_hash,
            "vector": self.vector,
            "response_payload": self.response_payload,
            "tag": self.tag,
            "is_stream": self.is_stream,
            "stream_chunks": self.stream_chunks,
            "created_at": self.created_at,
            "last_accessed_at": self.last_accessed_at,
            "ttl_seconds": self.ttl_seconds,
            "hit_count": self.hit_count,
            "is_exact_tokens": self.is_exact_tokens,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CacheEntry":
        entry = cls(
            key=d.get("key", ""),
            org_id=d.get("org_id", "default"),
            model=d.get("model", ""),
            user_prompt=d.get("user_prompt", ""),
            system_prompt=d.get("system_prompt", ""),
            schema_hash=d.get("schema_hash", "no_schema"),
            tools_hash=d.get("tools_hash", "no_tools"),
            vector=d.get("vector", []),
            response_payload=d.get("response_payload", {}),
            tag=d.get("tag", None),
            is_stream=d.get("is_stream", False),
            stream_chunks=d.get("stream_chunks", []),
            ttl_seconds=d.get("ttl_seconds", 604800),
            is_exact_tokens=d.get("is_exact_tokens", True),
            prompt_tokens=d.get("prompt_tokens", 0),
            completion_tokens=d.get("completion_tokens", 0)
        )
        entry.created_at = d.get("created_at", entry.created_at)
        entry.last_accessed_at = d.get("last_accessed_at", entry.last_accessed_at)
        entry.hit_count = d.get("hit_count", 0)
        return entry

    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl_seconds

    def ttl_remaining(self) -> int:
        remaining = self.ttl_seconds - (time.time() - self.created_at)
        return max(0, int(remaining))

    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.created_at)

    def touch(self):
        self.last_accessed_at = time.time()
        self.hit_count += 1


class DualTierCache:
    def __init__(self, storage: Optional[BaseCacheStorage] = None):
        self.storage = storage or self._init_storage()
        self.ann_indices: Dict[str, BaseANNIndex] = {}
        self._tenant_l2_versions: Dict[str, int] = {}

        # Cumulative Telemetry
        self.total_exact_hits = 0
        self.total_semantic_hits = 0
        self.total_misses = 0
        self.total_bypasses = 0

    def _get_ann_index(self, org_id: str) -> BaseANNIndex:
        if org_id not in self.ann_indices:
            self.ann_indices[org_id] = ANNIndexFactory.create(dimensions=FastSemanticEmbedder.DIMENSIONS)
        return self.ann_indices[org_id]

    @property
    def l1_exact_cache(self) -> Dict[str, CacheEntry]:
        if isinstance(self.storage, InMemoryCacheStorage):
            return self.storage.l1_exact_cache
        return {}

    @property
    def l2_semantic_cache(self) -> Dict[str, List[CacheEntry]]:
        if isinstance(self.storage, InMemoryCacheStorage):
            return self.storage.l2_semantic_cache
        return {}

    def _init_storage(self) -> BaseCacheStorage:
        backend = getattr(config, "CACHE_STORAGE_BACKEND", "auto")
        redis_url = getattr(config, "REDIS_URL", "")
        prefix = getattr(config, "REDIS_KEY_PREFIX", "omnicache")
        if backend == "redis" or (backend == "auto" and redis_url):
            try:
                return RedisCacheStorage(redis_url=redis_url, prefix=prefix, entry_cls=CacheEntry)
            except Exception as exc:
                print(f"⚠️ [OmniCache] Failed to initialize Redis storage: {exc}. Using InMemory.")
                return InMemoryCacheStorage()
        return InMemoryCacheStorage()

    def classify_intent(self, prompt: str, schema_hash: str, tools_hash: str, temperature: float) -> Tuple[str, float, str]:
        """
        Determines the intent, dynamic similarity threshold, and gating explanation.
        """
        if schema_hash != "no_schema":
            return "structured_schema", 1.01, "BYPASS_STRUCTURED_SCHEMA: Strict JSON schema requested; semantic fuzzy matching disabled"

        if tools_hash != "no_tools":
            return "agent_tools", 1.01, "BYPASS_AGENT_TOOLS: Agent tools defined; semantic fuzzy matching disabled to protect tool call correctness"

        if temperature > config.TEMPERATURE_BYPASS_THRESHOLD:
            return "creative_bypass", 1.01, f"BYPASS_HIGH_TEMPERATURE: Temperature {temperature:.2f} > {config.TEMPERATURE_BYPASS_THRESHOLD:.2f} requires non-deterministic execution"

        code_patterns = [r"```", r"def\s+\w+\(", r"function\s+\w+\(", r"class\s+\w+:", r"SELECT\s+.+\s+FROM", r"import\s+\w+"]
        for pat in code_patterns:
            if re.search(pat, prompt, re.IGNORECASE):
                return "code_generation", 0.98, "INTENT_CODE_GENERATION: Strict 0.98 threshold applied for syntax fidelity"

        if re.search(r"(\d+\s*[\+\-\*\/\^]\s*\d+|\bcalculate\b|\bsolve\b|\bevaluate\b)", prompt, re.IGNORECASE):
            return "math_calculation", 0.98, "INTENT_MATH_CALCULATION: Strict 0.98 threshold applied for arithmetic accuracy"

        return "conversational_qa", config.DEFAULT_SIMILARITY_THRESHOLD, f"INTENT_CONVERSATIONAL_QA: Standard threshold {config.DEFAULT_SIMILARITY_THRESHOLD:.2f} applied"

    def lookup(
        self,
        payload: Dict[str, Any],
        org_id: str = "default",
        custom_threshold: Optional[float] = None
    ) -> Tuple[str, Optional[CacheEntry], float, str]:
        """
        Performs dual-tier lookup with explainability reasons.
        
        Returns:
            Tuple[status: 'HIT_EXACT'|'HIT_SEMANTIC'|'MISS'|'BYPASS', entry, similarity_score, decision_reason]
        """
        messages = payload.get("messages", [])
        system_prompt, user_prompt, is_multimodal = RequestHasher.extract_system_and_user_prompts(messages)
        temperature = float(payload.get("temperature", 0.0))
        model = payload.get("model", "default").strip()
        query_family = get_model_family(model)
        response_format = payload.get("response_format", None)
        tools = payload.get("tools", None)
        schema_hash = RequestHasher.compute_schema_hash(response_format)
        tools_hash = RequestHasher.compute_tools_hash(tools)
        
        # 1. Check L1 Exact Deterministic Cache
        exact_key = RequestHasher.compute_exact_hash(payload, org_id=org_id)
        entry = self.storage.get_exact(exact_key)
        if entry is not None:
            if not entry.is_expired():
                entry.touch()
                self.storage.set_exact(exact_key, entry, ttl_seconds=entry.ttl_remaining())
                self.total_exact_hits += 1
                reason = f"HIT_EXACT_L1: Deterministic SHA-256 hash match on payload for model '{entry.model}'"
                return "HIT_EXACT", entry, 1.0, reason
            else:
                self.storage.delete_exact(exact_key)

        # 2. Safety Gate: Multi-turn, Agent Tools, Schema, Multimodal
        if is_multimodal:
            self.total_bypasses += 1
            return "BYPASS", None, 0.0, "BYPASS_MULTIMODAL: Vision/multimodal payload requires deterministic L1 exact match"

        if tools or tools_hash != "no_tools":
            self.total_bypasses += 1
            return "BYPASS", None, 0.0, "BYPASS_AGENT_TOOLS: Tool/function execution present; fuzzy semantic matching bypassed"

        if schema_hash != "no_schema":
            self.total_bypasses += 1
            return "BYPASS", None, 0.0, "BYPASS_STRUCTURED_SCHEMA: Response format schema present; fuzzy semantic matching bypassed"

        if len(messages) > 1:
            self.total_bypasses += 1
            return "BYPASS", None, 0.0, f"BYPASS_MULTITURN_CONVERSATION: Multi-turn context ({len(messages)} messages) restricted to L1 exact cache"

        if not user_prompt.strip():
            self.total_misses += 1
            return "MISS", None, 0.0, "MISS_EMPTY_PROMPT: No substantive text prompt found in payload"

        # Intent Classification & Dynamic Thresholding
        intent, dynamic_threshold, intent_reason = self.classify_intent(user_prompt, schema_hash, tools_hash, temperature)
        effective_threshold = custom_threshold if custom_threshold is not None else dynamic_threshold
        
        if effective_threshold > 1.0:
            self.total_bypasses += 1
            return "BYPASS", None, 0.0, intent_reason

        # 3. Check L2 Semantic Cache (Strictly scoped to org_id and model_family)
        org_entries = self.storage.get_semantic_entries(org_id)
        if not org_entries:
            self.total_misses += 1
            return "MISS", None, 0.0, f"MISS_EMPTY_CACHE: No cached semantic entries for tenant '{org_id}'"

        query_vector = FastSemanticEmbedder.embed(user_prompt)
        best_score = 0.0
        best_entry: Optional[CacheEntry] = None
        candidates_checked = 0
        family_mismatches = 0
        system_mismatches = 0

        # Filter active entries
        active_entries: List[CacheEntry] = []
        for entry in org_entries:
            if not entry.is_expired():
                active_entries.append(entry)

        if len(active_entries) != len(org_entries):
            self.storage.update_semantic_entries(org_id, active_entries)

        if not active_entries:
            self.total_misses += 1
            return "MISS", None, 0.0, f"MISS_EMPTY_CACHE: All entries expired for tenant '{org_id}'"

        # ANN Candidate Pruning for high scale (> 50 cached entries)
        ann_enabled = getattr(config, "ANN_INDEX_ENABLED", True)
        if ann_enabled and len(active_entries) > 50:
            ann = self._get_ann_index(org_id)
            storage_ver = self.storage.get_l2_version(org_id)
            if self._tenant_l2_versions.get(org_id) != storage_ver or ann.size() != len(active_entries):
                ann.clear()
                for e in active_entries:
                    if e.vector:
                        ann.add(e.key, e.vector)
                self._tenant_l2_versions[org_id] = storage_ver

            top_matches = ann.search(query_vector, top_k=getattr(config, "ANN_TOP_K", 50), min_similarity=0.0)
            if top_matches:
                candidate_keys = {k for k, _ in top_matches}
                eval_candidates = [e for e in active_entries if e.key in candidate_keys]
            else:
                eval_candidates = active_entries
        else:
            eval_candidates = active_entries

        # Evaluate similarity and strict guardrails
        for entry in eval_candidates:
            # Strict Model Family Match Guardrail
            if entry.model_family != query_family:
                family_mismatches += 1
                continue

            # Strict System Prompt, Schema, and Tools Match Guardrail
            if entry.system_prompt != system_prompt:
                system_mismatches += 1
                continue
            if entry.schema_hash != schema_hash or entry.tools_hash != tools_hash:
                continue

            candidates_checked += 1
            similarity = FastSemanticEmbedder.cosine_similarity(query_vector, entry.vector)
            if similarity > best_score:
                best_score = similarity
                best_entry = entry

        if best_entry and best_score >= effective_threshold:
            best_entry.touch()
            self.storage.add_semantic_entry(org_id, best_entry, ttl_seconds=best_entry.ttl_remaining(), max_entries=config.MAX_CACHE_ENTRIES_PER_TENANT)
            self.total_semantic_hits += 1
            reason = f"HIT_SEMANTIC_L2: Semantic similarity {best_score:.4f} >= threshold {effective_threshold:.4f} (Family: '{query_family}')"
            return "HIT_SEMANTIC", best_entry, best_score, reason

        self.total_misses += 1
        if candidates_checked == 0 and family_mismatches > 0:
            reason = f"MISS_MODEL_FAMILY_MISMATCH: {family_mismatches} candidates rejected due to model family divergence from '{query_family}'"
        elif candidates_checked == 0 and system_mismatches > 0:
            reason = f"MISS_SYSTEM_PROMPT_MISMATCH: {system_mismatches} candidates rejected due to differing system instructions"
        else:
            reason = f"MISS_BELOW_THRESHOLD: Highest similarity {best_score:.4f} did not meet required threshold {effective_threshold:.4f}"

        return "MISS", None, best_score, reason

    def store(
        self,
        payload: Dict[str, Any],
        response_payload: Dict[str, Any],
        org_id: str = "default",
        tag: Optional[str] = None,
        custom_ttl: Optional[int] = None,
        stream_chunks: Optional[List[Dict[str, Any]]] = None,
        is_exact_tokens: bool = True,
        prompt_tokens: int = 0,
        completion_tokens: int = 0
    ) -> CacheEntry:
        """
        Stores an LLM completion into both L1 and L2 caches with token accounting metadata.
        """
        messages = payload.get("messages", [])
        system_prompt, user_prompt, is_multimodal = RequestHasher.extract_system_and_user_prompts(messages)
        model = payload.get("model", "").strip().lower()
        response_format = payload.get("response_format", None)
        tools = payload.get("tools", None)
        schema_hash = RequestHasher.compute_schema_hash(response_format)
        tools_hash = RequestHasher.compute_tools_hash(tools)
        
        exact_key = RequestHasher.compute_exact_hash(payload, org_id=org_id)
        is_single_turn_text = (len(messages) <= 1 and user_prompt and not is_multimodal and not tools and schema_hash == "no_schema")
        vector = FastSemanticEmbedder.embed(user_prompt) if is_single_turn_text else []
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
            ttl_seconds=ttl_seconds,
            is_exact_tokens=is_exact_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens
        )

        # Store in L1 Exact Cache
        self.storage.set_exact(exact_key, entry, ttl_seconds=ttl_seconds)

        # Store in L2 Semantic Cache if single-turn conversational
        if vector and is_single_turn_text:
            self.storage.add_semantic_entry(org_id, entry, ttl_seconds=ttl_seconds, max_entries=config.MAX_CACHE_ENTRIES_PER_TENANT)
            ann = self._get_ann_index(org_id)
            ann.add(exact_key, vector)

        return entry

    def purge_tenant(self, org_id: str) -> int:
        if org_id in self.ann_indices:
            self.ann_indices[org_id].clear()
        return self.storage.purge(org_id=org_id)

    def purge(self, org_id: Optional[str] = None) -> int:
        if org_id and org_id in self.ann_indices:
            self.ann_indices[org_id].clear()
        elif org_id is None:
            for idx in self.ann_indices.values():
                idx.clear()
            self.ann_indices.clear()
        return self.storage.purge(org_id=org_id)

    def invalidate_tag(self, tag: str, org_id: Optional[str] = None) -> int:
        if org_id and org_id in self.ann_indices:
            self.ann_indices[org_id].clear()
        elif org_id is None:
            for idx in self.ann_indices.values():
                idx.clear()
            self.ann_indices.clear()
        return self.storage.invalidate_tag(tag, org_id=org_id)

    def get_stats(self, org_id: Optional[str] = None) -> Dict[str, Any]:
        total_requests = self.total_exact_hits + self.total_semantic_hits + self.total_misses + self.total_bypasses
        hit_rate = (self.total_exact_hits + self.total_semantic_hits) / total_requests if total_requests > 0 else 0.0
        active_l1, active_l2 = self.storage.get_stats_counts(org_id=org_id)

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
        self.storage.clear()
        for idx in self.ann_indices.values():
            idx.clear()
        self.ann_indices.clear()
        self.total_exact_hits = 0
        self.total_semantic_hits = 0
        self.total_misses = 0
        self.total_bypasses = 0

cache_instance = DualTierCache()
