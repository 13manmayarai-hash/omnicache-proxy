"""
Multi-Agent Swarm & Subagent Delegation Bus (v2.9.9).
Provides a shared sub-millisecond memory fabric, inter-agent task coalescing,
and cross-agent state invalidation for concurrent AI agent swarms
(Claude Code subagents, OpenHands, CrewAI, AutoGen, LangGraph, and Telephony Agent Handoffs).
"""

import time
import json
import hashlib
import threading
from typing import Dict, Any, Tuple, Optional, List, Set

from core.config import config


class SwarmEntry:
    """Represents a shared execution result in a swarm session."""
    __slots__ = (
        "swarm_id", "origin_agent_id", "task_type", "task_fingerprint",
        "result_payload", "tokens_saved", "created_at", "ttl_seconds",
        "affected_resources"
    )

    def __init__(
        self,
        swarm_id: str,
        origin_agent_id: str,
        task_type: str,
        task_fingerprint: str,
        result_payload: Any,
        tokens_saved: int = 0,
        ttl_seconds: float = 3600.0,
        affected_resources: Optional[List[str]] = None
    ):
        self.swarm_id = swarm_id
        self.origin_agent_id = origin_agent_id
        self.task_type = task_type
        self.task_fingerprint = task_fingerprint
        self.result_payload = result_payload
        self.tokens_saved = tokens_saved
        self.created_at = time.time()
        self.ttl_seconds = ttl_seconds
        self.affected_resources = affected_resources or []

    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl_seconds

    def age_seconds(self) -> float:
        return time.time() - self.created_at


class SwarmBus:
    """
    Thread-safe, high-performance Inter-Agent Delegation & Shared Memory Bus.
    Enables parallel and sequential subagents in a swarm to share tool outputs,
    deduplicate common research queries, and invalidate shared state on file mutations.
    """

    def __init__(self, ttl_seconds: Optional[float] = None, max_entries: Optional[int] = None):
        self._lock = threading.RLock()
        self.default_ttl_seconds = ttl_seconds if ttl_seconds is not None else getattr(config, "SWARM_CACHE_TTL_SECONDS", 86400)
        self.max_entries = max_entries if max_entries is not None else getattr(config, "SWARM_MAX_ENTRIES_PER_SWARM", 5000)
        # swarm_id -> { task_fingerprint -> SwarmEntry }
        self._swarm_store: Dict[str, Dict[str, SwarmEntry]] = {}
        # swarm_id -> { parent_agent -> Set[child_agent] }
        self._delegation_graph: Dict[str, Dict[str, Set[str]]] = {}
        # swarm_id -> List[Dict[str, Any]]
        self._activity_ledger: Dict[str, List[Dict[str, Any]]] = {}

        # Cumulative Metrics
        self.total_swarm_requests: int = 0
        self.cross_agent_hits: int = 0
        self.swarm_tokens_saved: int = 0
        self.mutations_propagated: int = 0

    @staticmethod
    def compute_task_fingerprint(task_type: str, payload: Any) -> str:
        """
        Computes a deterministic SHA-256 fingerprint for a swarm task or tool execution.
        Normalizes JSON dicts, strings, and parameter objects.
        """
        try:
            if isinstance(payload, dict):
                norm_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            elif isinstance(payload, (list, tuple)):
                norm_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            else:
                norm_str = str(payload).strip()
        except Exception:
            norm_str = str(payload)

        raw = f"{task_type.lower().strip()}::{norm_str}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def record_delegation(
        self,
        swarm_id: str,
        parent_agent: str,
        subagent_id: str,
        task_prompt: str = "",
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Records parent-to-child delegation relationship within a swarm session."""
        if not swarm_id or not parent_agent or not subagent_id:
            return {}

        with self._lock:
            if swarm_id not in self._delegation_graph:
                self._delegation_graph[swarm_id] = {}
            if parent_agent not in self._delegation_graph[swarm_id]:
                self._delegation_graph[swarm_id][parent_agent] = set()

            self._delegation_graph[swarm_id][parent_agent].add(subagent_id)

            rec = {
                "type": "delegation",
                "parent": parent_agent,
                "subagent": subagent_id,
                "task_prompt": task_prompt[:120],
                "metadata": metadata or {},
                "timestamp": time.time()
            }
            if swarm_id not in self._activity_ledger:
                self._activity_ledger[swarm_id] = []
            self._activity_ledger[swarm_id].append(rec)
            return rec

    def record_shared_result(
        self,
        swarm_id: str,
        agent_id: str,
        task_type: str,
        payload: Any,
        result_payload: Any,
        tokens_saved: int = 0,
        ttl_seconds: Optional[float] = None,
        affected_resources: Optional[List[str]] = None
    ) -> str:
        """
        Stores an executed tool output or reasoning step in the swarm memory fabric.
        Peer subagents in the same swarm can reuse this result immediately.
        """
        if not swarm_id:
            return ""

        effective_ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        fingerprint = self.compute_task_fingerprint(task_type, payload)
        entry = SwarmEntry(
            swarm_id=swarm_id,
            origin_agent_id=agent_id or "agent_lead",
            task_type=task_type,
            task_fingerprint=fingerprint,
            result_payload=result_payload,
            tokens_saved=tokens_saved,
            ttl_seconds=effective_ttl,
            affected_resources=affected_resources
        )

        with self._lock:
            if swarm_id not in self._swarm_store:
                self._swarm_store[swarm_id] = {}

            # Prune if swarm cache exceeds capacity limit
            if len(self._swarm_store[swarm_id]) >= self.max_entries:
                oldest_fp = next(iter(self._swarm_store[swarm_id]))
                del self._swarm_store[swarm_id][oldest_fp]

            self._swarm_store[swarm_id][fingerprint] = entry

            if swarm_id not in self._activity_ledger:
                self._activity_ledger[swarm_id] = []
            self._activity_ledger[swarm_id].append({
                "type": "result_recorded",
                "agent_id": agent_id,
                "task_type": task_type,
                "fingerprint": fingerprint[:12],
                "tokens_saved": tokens_saved,
                "timestamp": time.time()
            })

        return fingerprint

    def lookup_shared_result(
        self,
        swarm_id: str,
        agent_id: str,
        task_type: str,
        payload: Any
    ) -> Tuple[bool, Optional[Any], Dict[str, Any]]:
        """
        Checks if another agent in the swarm has already executed an identical task.

        Returns:
            Tuple[is_hit, result_payload, metadata_dict]
        """
        if not swarm_id:
            return False, None, {}

        fingerprint = self.compute_task_fingerprint(task_type, payload)

        with self._lock:
            self.total_swarm_requests += 1
            swarm_dict = self._swarm_store.get(swarm_id)
            if not swarm_dict:
                return False, None, {}

            entry = swarm_dict.get(fingerprint)
            if not entry:
                return False, None, {}

            # Check expiration
            if entry.is_expired():
                del swarm_dict[fingerprint]
                return False, None, {}

            # Hit detected!
            self.cross_agent_hits += 1
            self.swarm_tokens_saved += entry.tokens_saved

            meta = {
                "swarm_id": swarm_id,
                "origin_agent_id": entry.origin_agent_id,
                "requesting_agent_id": agent_id,
                "is_cross_agent": (entry.origin_agent_id != agent_id),
                "age_seconds": round(entry.age_seconds(), 3),
                "tokens_saved": entry.tokens_saved,
                "task_type": entry.task_type
            }

            if swarm_id not in self._activity_ledger:
                self._activity_ledger[swarm_id] = []
            self._activity_ledger[swarm_id].append({
                "type": "cache_hit",
                "requesting_agent": agent_id,
                "origin_agent": entry.origin_agent_id,
                "task_type": task_type,
                "tokens_saved": entry.tokens_saved,
                "timestamp": time.time()
            })

            return True, entry.result_payload, meta

    def invalidate_on_mutation(
        self,
        swarm_id: str,
        mutating_agent_id: Optional[str] = None,
        mutated_resource: Optional[str] = None,
        agent_id: Optional[str] = None
    ) -> int:
        """
        Invalidates cached read/view/grep tool results in the swarm when any agent
        performs a mutative change (file edit, write, commit, delete).
        """
        if not swarm_id:
            return 0

        effective_agent = mutating_agent_id or agent_id or "mutating_agent"

        invalidated_count = 0
        with self._lock:
            swarm_dict = self._swarm_store.get(swarm_id)
            if not swarm_dict:
                return 0

            fps_to_remove = []
            for fp, entry in swarm_dict.items():
                # Invalidate if tool is a read/search/view or matches mutated_resource
                is_read_tool = any(entry.task_type.startswith(prefix) for prefix in ("view_", "read_", "grep_", "find_", "list_", "git_status"))
                if mutated_resource:
                    if entry.affected_resources:
                        should_invalidate = any(mutated_resource in r or r in mutated_resource for r in entry.affected_resources)
                    else:
                        should_invalidate = is_read_tool
                else:
                    should_invalidate = is_read_tool

                if should_invalidate:
                    fps_to_remove.append(fp)

            for fp in fps_to_remove:
                del swarm_dict[fp]
                invalidated_count += 1

            self.mutations_propagated += invalidated_count

            if swarm_id not in self._activity_ledger:
                self._activity_ledger[swarm_id] = []
            self._activity_ledger[swarm_id].append({
                "type": "mutation_invalidated",
                "mutating_agent": effective_agent,
                "mutated_resource": mutated_resource or "all_reads",
                "invalidated_count": invalidated_count,
                "timestamp": time.time()
            })

        return invalidated_count

    def get_swarm_topology(self, swarm_id: str) -> Dict[str, Any]:
        """Returns the live subagent delegation topology and memory footprint of a swarm."""
        with self._lock:
            delegations = self._delegation_graph.get(swarm_id, {})
            serializable_delegations = {k: list(v) for k, v in delegations.items()}
            entries = self._swarm_store.get(swarm_id, {})
            active_entries = sum(1 for e in entries.values() if not e.is_expired())
            recent_events = (self._activity_ledger.get(swarm_id, []))[-15:]

            # Distinct agents participating in this swarm
            agents: Set[str] = set()
            for p, children in delegations.items():
                agents.add(p)
                agents.update(children)
            for e in entries.values():
                agents.add(e.origin_agent_id)

            nodes = {}
            for agent in agents:
                parent = None
                for p, ch in delegations.items():
                    if agent in ch:
                        parent = p
                        break
                children = list(delegations.get(agent, []))
                nodes[agent] = {
                    "agent_id": agent,
                    "parent": parent,
                    "children": children
                }

            return {
                "swarm_id": swarm_id,
                "active_agents": list(agents),
                "agent_count": len(agents),
                "delegation_graph": serializable_delegations,
                "nodes": nodes,
                "active_cached_entries": active_entries,
                "recent_events": recent_events
            }

    def get_stats(self) -> Dict[str, Any]:
        """Returns global telemetry for the Multi-Agent Swarm Delegation Bus."""
        with self._lock:
            active_swarms = len(self._swarm_store)
            total_cached_entries = sum(len(s) for s in self._swarm_store.values())
            hit_ratio = round(self.cross_agent_hits / max(1, self.total_swarm_requests), 4)

            return {
                "active_swarms": active_swarms,
                "total_cached_entries": total_cached_entries,
                "total_swarm_requests": self.total_swarm_requests,
                "cross_agent_hits": self.cross_agent_hits,
                "swarm_tokens_saved": self.swarm_tokens_saved,
                "mutations_propagated": self.mutations_propagated,
                "swarm_hit_ratio": hit_ratio
            }

    def reset_stats(self) -> None:
        """Clears counters and cache store."""
        with self._lock:
            self._swarm_store.clear()
            self._delegation_graph.clear()
            self._activity_ledger.clear()
            self.total_swarm_requests = 0
            self.cross_agent_hits = 0
            self.swarm_tokens_saved = 0
            self.mutations_propagated = 0


# Global singleton instance
swarm_bus = SwarmBus()
