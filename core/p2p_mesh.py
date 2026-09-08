"""
Distributed P2P / Edge Mesh State Sync (v3.0.0-rc1).
Provides decentralized peer discovery, CRDT tombstone-based cache invalidation,
vector clock synchronization, and anti-entropy reconciliation across edge nodes,
CI/CD runners, and distributed agent pods without requiring centralized Redis.
"""

import os
import time
import uuid
import json
import socket
import asyncio
import threading
from typing import Dict, Any, List, Optional, Tuple, Set, Callable, Union
import httpx

from core.config import config


def _generate_node_id() -> str:
    """Generates a stable or random unique node identifier."""
    configured = getattr(config, "MESH_NODE_ID", "").strip()
    if configured:
        return configured
    try:
        hostname = socket.gethostname() or "node"
    except Exception:
        hostname = "node"
    return f"{hostname}-{uuid.uuid4().hex[:8]}"


class CRDTTombstone:
    """
    Conflict-Free Replicated Data Type (CRDT) Tombstone.
    Implements Last-Write-Wins (LWW-Element-Set) semantics with Lamport logical clock
    and physical wall-clock timestamp tie-breaking for deterministic convergence.
    """
    __slots__ = (
        "resource_id", "timestamp", "lamport_clock", "node_id", "reason", "metadata"
    )

    def __init__(
        self,
        resource_id: str,
        timestamp: Optional[float] = None,
        lamport_clock: int = 1,
        node_id: str = "",
        reason: str = "mutation",
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.resource_id = str(resource_id).strip()
        self.timestamp = float(timestamp if timestamp is not None else time.time())
        self.lamport_clock = int(lamport_clock)
        self.node_id = str(node_id).strip()
        self.reason = str(reason).strip()
        self.metadata = metadata or {}

    def is_newer_than(self, other: "CRDTTombstone") -> bool:
        """
        Total ordering evaluation:
        1. Compare Lamport logical clock (causal precedence)
        2. Compare physical wall clock timestamp
        3. Lexicographical tie-break on node_id
        """
        if self.lamport_clock != other.lamport_clock:
            return self.lamport_clock > other.lamport_clock
        if abs(self.timestamp - other.timestamp) > 0.0001:
            return self.timestamp > other.timestamp
        return self.node_id > other.node_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "timestamp": self.timestamp,
            "lamport_clock": self.lamport_clock,
            "node_id": self.node_id,
            "reason": self.reason,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CRDTTombstone":
        return cls(
            resource_id=data.get("resource_id", ""),
            timestamp=data.get("timestamp"),
            lamport_clock=data.get("lamport_clock", 1),
            node_id=data.get("node_id", ""),
            reason=data.get("reason", "mutation"),
            metadata=data.get("metadata", {})
        )

    def __repr__(self) -> str:
        return f"<CRDTTombstone id={self.resource_id} lclock={self.lamport_clock} node={self.node_id}>"


class PeerNode:
    """Represents a discovered or configured peer node in the mesh."""
    __slots__ = (
        "node_id", "endpoint", "last_seen", "status", "rtt_ms", "vector_clock", "metadata"
    )

    def __init__(
        self,
        endpoint: str,
        node_id: Optional[str] = None,
        last_seen: Optional[float] = None,
        status: str = "alive",
        rtt_ms: float = 0.0,
        vector_clock: Optional[Dict[str, int]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.endpoint = endpoint.rstrip("/")
        self.node_id = node_id or f"peer-{abs(hash(self.endpoint)) % 1000000:06d}"
        self.last_seen = last_seen if last_seen is not None else time.time()
        self.status = status
        self.rtt_ms = rtt_ms
        self.vector_clock = vector_clock or {}
        self.metadata = metadata or {}

    def is_alive(self, timeout_seconds: float = 30.0) -> bool:
        return (time.time() - self.last_seen) < timeout_seconds and self.status != "offline"

    def mark_seen(self, rtt_ms: Optional[float] = None):
        self.last_seen = time.time()
        self.status = "alive"
        if rtt_ms is not None:
            if self.rtt_ms <= 0.0:
                self.rtt_ms = rtt_ms
            else:
                self.rtt_ms = round(0.7 * self.rtt_ms + 0.3 * rtt_ms, 2)

    def mark_suspect(self):
        self.status = "suspect"

    def mark_offline(self):
        self.status = "offline"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "endpoint": self.endpoint,
            "last_seen": self.last_seen,
            "age_seconds": round(time.time() - self.last_seen, 2),
            "status": self.status,
            "rtt_ms": self.rtt_ms,
            "vector_clock": dict(self.vector_clock),
            "metadata": self.metadata
        }


class P2PMesh:
    """
    High-Performance Distributed P2P / Edge Mesh Coordinator.
    Manages peer discovery, heartbeat gossip, vector clock causality tracking,
    CRDT tombstone propagation, and anti-entropy state synchronization.
    """

    def __init__(
        self,
        node_id: Optional[str] = None,
        endpoint: Optional[str] = None,
        seed_peers: Optional[List[str]] = None,
        sync_interval: Optional[float] = None,
        heartbeat_timeout: Optional[float] = None,
        max_tombstones: Optional[int] = None
    ):
        self._lock = threading.RLock()
        self.node_id = node_id or _generate_node_id()
        self.host = getattr(config, "HOST", "127.0.0.1")
        self.port = getattr(config, "PORT", 8000)
        self.endpoint = (endpoint or f"http://{self.host}:{self.port}").rstrip("/")
        
        self.sync_interval = sync_interval if sync_interval is not None else getattr(config, "MESH_SYNC_INTERVAL_SECONDS", 10.0)
        self.heartbeat_timeout = heartbeat_timeout if heartbeat_timeout is not None else getattr(config, "MESH_HEARTBEAT_TIMEOUT_SECONDS", 30.0)
        self.max_tombstones = max_tombstones if max_tombstones is not None else getattr(config, "MESH_MAX_TOMBSTONES", 10000)

        # Peer registry: node_id -> PeerNode
        self._peers: Dict[str, PeerNode] = {}
        # Endpoint mapping: endpoint -> node_id
        self._endpoint_to_node: Dict[str, str] = {}

        # Vector clock: node_id -> sequence number
        self._vector_clock: Dict[str, int] = {self.node_id: 0}
        self._lamport_clock: int = 0

        # CRDT Tombstone registry: resource_id -> CRDTTombstone
        self._tombstones: Dict[str, CRDTTombstone] = {}

        # Invalidation listeners: callbacks when a tombstone is applied locally
        self._invalidation_handlers: List[Callable[[str, str, Dict[str, Any]], None]] = []

        # Metrics
        self.metrics = {
            "sync_packets_sent": 0,
            "sync_packets_received": 0,
            "tombstones_broadcast": 0,
            "tombstones_applied": 0,
            "tombstones_rejected": 0,
            "heartbeats_sent": 0,
            "heartbeats_received": 0,
            "anti_entropy_rounds": 0,
            "cross_node_invalidations": 0,
            "started_at": time.time()
        }

        # Seed initial peers from config
        configured_seeds = seed_peers if seed_peers is not None else getattr(config, "MESH_PEERS", [])
        for seed in configured_seeds:
            if seed:
                self.register_peer(seed)

    # -------------------------------------------------------------
    # Peer Management
    # -------------------------------------------------------------
    def register_peer(
        self,
        endpoint: str,
        node_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[PeerNode]:
        """Registers or updates a peer in the mesh."""
        norm_endpoint = endpoint.strip().rstrip("/")
        if not norm_endpoint:
            raise ValueError("Endpoint cannot be empty")
        
        # Don't register self as a peer
        if norm_endpoint == self.endpoint or (node_id and node_id == self.node_id):
            return None

        with self._lock:
            existing_id = self._endpoint_to_node.get(norm_endpoint)
            effective_id = node_id or existing_id

            if effective_id and effective_id in self._peers:
                peer = self._peers[effective_id]
                peer.endpoint = norm_endpoint
                peer.mark_seen()
                if metadata:
                    peer.metadata.update(metadata)
                self._endpoint_to_node[norm_endpoint] = effective_id
                return peer

            peer = PeerNode(
                endpoint=norm_endpoint,
                node_id=node_id,
                status="alive",
                metadata=metadata or {}
            )
            self._peers[peer.node_id] = peer
            self._endpoint_to_node[norm_endpoint] = peer.node_id
            return peer

    def unregister_peer(self, identifier: str) -> bool:
        """Unregisters a peer by node_id or endpoint."""
        with self._lock:
            target_id = identifier
            if identifier in self._endpoint_to_node:
                target_id = self._endpoint_to_node[identifier]

            if target_id in self._peers:
                peer = self._peers.pop(target_id)
                self._endpoint_to_node.pop(peer.endpoint, None)
                return True
            return False

    def list_peers(self, active_only: bool = False) -> List[Dict[str, Any]]:
        with self._lock:
            res = []
            for p in self._peers.values():
                is_alive = p.is_alive(self.heartbeat_timeout)
                if not is_alive and p.status == "alive":
                    p.mark_suspect()
                if active_only and not is_alive:
                    continue
                res.append(p.to_dict())
            return res

    def get_peer(self, identifier: str) -> Optional[PeerNode]:
        with self._lock:
            if identifier in self._peers:
                return self._peers[identifier]
            node_id = self._endpoint_to_node.get(identifier.rstrip("/"))
            if node_id and node_id in self._peers:
                return self._peers[node_id]
            return None

    # -------------------------------------------------------------
    # Vector Clock & Causality
    # -------------------------------------------------------------
    def get_vector_clock(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._vector_clock)

    def get_lamport_clock(self) -> int:
        with self._lock:
            return self._lamport_clock

    def increment_clock(self) -> Tuple[int, Dict[str, int]]:
        with self._lock:
            self._lamport_clock += 1
            self._vector_clock[self.node_id] = self._vector_clock.get(self.node_id, 0) + 1
            return self._lamport_clock, dict(self._vector_clock)

    def merge_vector_clock(self, remote_node_id: str, remote_clock: Dict[str, int]):
        """Merges remote vector clock using element-wise maximum."""
        with self._lock:
            for n_id, seq in remote_clock.items():
                if isinstance(seq, int):
                    self._vector_clock[n_id] = max(self._vector_clock.get(n_id, 0), seq)
            if remote_node_id:
                peer = self._peers.get(remote_node_id)
                if peer:
                    peer.vector_clock = dict(remote_clock)

    # -------------------------------------------------------------
    # CRDT Tombstones & State Invalidation
    # -------------------------------------------------------------
    def register_invalidation_handler(self, handler: Callable[[str, str, Dict[str, Any]], None]):
        """Registers a callback to be invoked when a resource tombstone is applied."""
        with self._lock:
            if handler not in self._invalidation_handlers:
                self._invalidation_handlers.append(handler)

    def record_local_mutation(
        self,
        resource_id: str,
        reason: str = "mutation",
        metadata: Optional[Dict[str, Any]] = None
    ) -> CRDTTombstone:
        """
        Records a local cache or resource mutation.
        Creates a CRDTTombstone with monotonic Lamport clock and updates local vector clock.
        """
        with self._lock:
            lamport, _ = self.increment_clock()
            tombstone = CRDTTombstone(
                resource_id=resource_id,
                timestamp=time.time(),
                lamport_clock=lamport,
                node_id=self.node_id,
                reason=reason,
                metadata=metadata or {}
            )
            self._tombstones[resource_id] = tombstone

            # Enforce max tombstone capacity with LRU pruning
            if len(self._tombstones) > self.max_tombstones:
                oldest_key = min(self._tombstones.keys(), key=lambda k: self._tombstones[k].timestamp)
                self._tombstones.pop(oldest_key, None)

            return tombstone

    def apply_remote_tombstone(
        self,
        tombstone_input: Union[CRDTTombstone, Dict[str, Any]]
    ) -> Tuple[bool, str]:
        """
        Applies an incoming CRDT tombstone from a peer node using LWW semantics.
        If accepted:
          - Advances local Lamport clock
          - Updates tombstone registry
          - Invokes all registered local cache invalidation handlers
        """
        if isinstance(tombstone_input, dict):
            tombstone = CRDTTombstone.from_dict(tombstone_input)
        else:
            tombstone = tombstone_input

        res_id = tombstone.resource_id
        if not res_id:
            return False, "empty_resource_id"

        with self._lock:
            existing = self._tombstones.get(res_id)
            if existing is not None:
                if not tombstone.is_newer_than(existing):
                    self.metrics["tombstones_rejected"] += 1
                    return False, "superseded_by_existing"

            # Accept tombstone
            self._lamport_clock = max(self._lamport_clock, tombstone.lamport_clock) + 1
            self._tombstones[res_id] = tombstone
            self.metrics["tombstones_applied"] += 1
            self.metrics["cross_node_invalidations"] += 1

            handlers = list(self._invalidation_handlers)

        # Call handlers outside lock to prevent deadlocks
        for h in handlers:
            try:
                h(tombstone.resource_id, tombstone.reason, tombstone.metadata)
            except Exception:
                pass

        return True, "applied"

    def get_tombstone(self, resource_id: str) -> Optional[CRDTTombstone]:
        with self._lock:
            return self._tombstones.get(resource_id)

    def list_tombstones(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            all_t = sorted(self._tombstones.values(), key=lambda t: t.timestamp, reverse=True)
            return [t.to_dict() for t in all_t[:limit]]

    # -------------------------------------------------------------
    # Sync Protocol & Anti-Entropy Reconciliation
    # -------------------------------------------------------------
    def create_sync_packet(self, max_tombstones: int = 200) -> Dict[str, Any]:
        """Creates a state synchronization packet to gossip with peers."""
        with self._lock:
            self.metrics["sync_packets_sent"] += 1
            recent_t = sorted(self._tombstones.values(), key=lambda t: t.timestamp, reverse=True)
            return {
                "node_id": self.node_id,
                "endpoint": self.endpoint,
                "version": getattr(config, "VERSION", "3.0.2"),
                "lamport_clock": self._lamport_clock,
                "vector_clock": dict(self._vector_clock),
                "tombstones": [t.to_dict() for t in recent_t[:max_tombstones]],
                "active_tombstone_count": len(self._tombstones),
                "timestamp": time.time()
            }

    def process_sync_packet(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        """
        Processes an incoming sync packet from a peer:
        - Registers/updates peer status
        - Merges vector clocks
        - Applies missing or newer tombstones
        - Computes missing tombstones for the peer (anti-entropy return response)
        """
        sender_id = packet.get("node_id", "")
        sender_endpoint = packet.get("endpoint", "")
        sender_clock = packet.get("vector_clock", {})
        sender_lamport = packet.get("lamport_clock", 0)
        incoming_tombstones = packet.get("tombstones", [])

        if not sender_id or sender_id == self.node_id:
            return {"status": "ignored", "reason": "self_or_empty_node"}

        applied_count = 0
        rejected_count = 0

        with self._lock:
            self.metrics["sync_packets_received"] += 1
            self.metrics["anti_entropy_rounds"] += 1

            if sender_endpoint:
                self.register_peer(
                    endpoint=sender_endpoint,
                    node_id=sender_id,
                    metadata={"version": packet.get("version")}
                )

            self.merge_vector_clock(sender_id, sender_clock)
            self._lamport_clock = max(self._lamport_clock, sender_lamport) + 1

        for t_dict in incoming_tombstones:
            applied, _ = self.apply_remote_tombstone(t_dict)
            if applied:
                applied_count += 1
            else:
                rejected_count += 1

        missing_for_peer: List[Dict[str, Any]] = []
        with self._lock:
            sender_seq = sender_clock.get(self.node_id, 0)
            local_seq = self._vector_clock.get(self.node_id, 0)
            if local_seq > sender_seq:
                local_t = sorted(self._tombstones.values(), key=lambda t: t.timestamp, reverse=True)
                missing_for_peer = [t.to_dict() for t in local_t[:50]]

            return {
                "status": "synchronized",
                "node_id": self.node_id,
                "endpoint": self.endpoint,
                "lamport_clock": self._lamport_clock,
                "vector_clock": dict(self._vector_clock),
                "tombstones_applied": applied_count,
                "tombstones_rejected": rejected_count,
                "return_tombstones": missing_for_peer
            }

    def process_heartbeat(
        self,
        sender_id: str,
        sender_endpoint: str,
        sender_clock: Optional[Dict[str, int]] = None
    ) -> Dict[str, Any]:
        """Processes an incoming heartbeat ping."""
        with self._lock:
            self.metrics["heartbeats_received"] += 1
            if sender_endpoint and sender_id != self.node_id:
                peer = self.register_peer(sender_endpoint, sender_id)
                if peer:
                    peer.mark_seen()
            if sender_clock:
                self.merge_vector_clock(sender_id, sender_clock)

            return {
                "status": "pong",
                "node_id": self.node_id,
                "endpoint": self.endpoint,
                "lamport_clock": self._lamport_clock,
                "vector_clock": dict(self._vector_clock),
                "active_peers": len(self.list_peers(active_only=True)),
                "timestamp": time.time()
            }

    # -------------------------------------------------------------
    # Async Network Gossip Dispatcher
    # -------------------------------------------------------------
    async def broadcast_tombstone_async(self, tombstone: CRDTTombstone) -> int:
        """
        Asynchronously broadcasts a tombstone to all alive peers via HTTP POST.
        Non-blocking and resilient to individual peer connection timeouts.
        """
        with self._lock:
            self.metrics["tombstones_broadcast"] += 1
            alive_peers = [p for p in self._peers.values() if p.is_alive(self.heartbeat_timeout)]

        if not alive_peers:
            return 0

        success_count = 0
        async with httpx.AsyncClient(timeout=2.0) as client:
            tasks = []
            for peer in alive_peers:
                url = f"{peer.endpoint}/v1/mesh/sync"
                sync_body = {
                    "node_id": self.node_id,
                    "endpoint": self.endpoint,
                    "lamport_clock": self._lamport_clock,
                    "vector_clock": dict(self._vector_clock),
                    "tombstones": [tombstone.to_dict()]
                }
                tasks.append(self._post_to_peer(client, peer, url, sync_body))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, dict) and res.get("status") == "synchronized":
                    success_count += 1

        return success_count

    async def _post_to_peer(
        self,
        client: httpx.AsyncClient,
        peer: PeerNode,
        url: str,
        payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        t0 = time.perf_counter()
        try:
            resp = await client.post(url, json=payload)
            rtt_ms = (time.perf_counter() - t0) * 1000
            if resp.status_code == 200:
                data = resp.json()
                peer.mark_seen(rtt_ms)
                return data
            else:
                peer.mark_suspect()
                return None
        except Exception:
            peer.mark_suspect()
            return None

    async def send_heartbeat_to_peer(self, peer_endpoint: str) -> Optional[Dict[str, Any]]:
        """Sends a heartbeat ping to a specific peer endpoint."""
        url = f"{peer_endpoint.rstrip('/')}/v1/mesh/heartbeat"
        payload = {
            "node_id": self.node_id,
            "endpoint": self.endpoint,
            "vector_clock": dict(self._vector_clock)
        }
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                self.metrics["heartbeats_sent"] += 1
                resp = await client.post(url, json=payload)
                rtt_ms = (time.perf_counter() - t0) * 1000
                if resp.status_code == 200:
                    data = resp.json()
                    sender_id = data.get("node_id")
                    with self._lock:
                        peer = self.register_peer(peer_endpoint, sender_id)
                        if peer:
                            peer.mark_seen(rtt_ms)
                    return data
        except Exception:
            pass
        return None

    # -------------------------------------------------------------
    # Topology & Diagnostics
    # -------------------------------------------------------------
    def get_mesh_topology(self) -> Dict[str, Any]:
        """Provides complete introspection into the mesh network."""
        with self._lock:
            peers_list = self.list_peers()
            alive_count = sum(1 for p in peers_list if p["status"] == "alive")
            suspect_count = sum(1 for p in peers_list if p["status"] == "suspect")
            offline_count = sum(1 for p in peers_list if p["status"] == "offline")

            return {
                "mesh_enabled": getattr(config, "MESH_ENABLED", True),
                "node_id": self.node_id,
                "endpoint": self.endpoint,
                "version": getattr(config, "VERSION", "3.0.2"),
                "lamport_clock": self._lamport_clock,
                "vector_clock": dict(self._vector_clock),
                "peer_summary": {
                    "total": len(peers_list),
                    "alive": alive_count,
                    "suspect": suspect_count,
                    "offline": offline_count
                },
                "peers": peers_list,
                "tombstone_count": len(self._tombstones),
                "metrics": dict(self.metrics),
                "uptime_seconds": round(time.time() - self.metrics["started_at"], 1)
            }


# Global singleton mesh bus instance
mesh_bus = P2PMesh()
