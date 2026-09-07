"""
Unit and integration tests for Distributed P2P / Edge Mesh State Sync (v3.0.0-rc1).
Verifies peer discovery, CRDT tombstone LWW convergence, vector clock causality,
anti-entropy reconciliation, and HTTP mesh gateway endpoints.
"""

import time
import pytest
from starlette.testclient import TestClient

from core.config import config
from core.p2p_mesh import P2PMesh, CRDTTombstone, PeerNode
from server.gateway import app, cache_instance, mesh_bus


@pytest.fixture
def client():
    return TestClient(app)


def test_peer_node_lifecycle():
    peer = PeerNode(endpoint="http://192.168.1.10:8000", node_id="node-alpha")
    assert peer.node_id == "node-alpha"
    assert peer.endpoint == "http://192.168.1.10:8000"
    assert peer.status == "alive"
    assert peer.is_alive(timeout_seconds=5.0) is True

    # RTT moving average
    peer.mark_seen(rtt_ms=10.0)
    assert peer.rtt_ms == 10.0
    peer.mark_seen(rtt_ms=20.0)
    assert peer.rtt_ms == pytest.approx(13.0, 0.1)

    peer.mark_suspect()
    assert peer.status == "suspect"

    peer.mark_offline()
    assert peer.status == "offline"
    assert peer.is_alive() is False


def test_lamport_clock_and_vector_clock():
    mesh = P2PMesh(node_id="node-local")
    assert mesh.get_lamport_clock() == 0
    assert mesh.get_vector_clock()["node-local"] == 0

    lclock1, vclock1 = mesh.increment_clock()
    assert lclock1 == 1
    assert vclock1["node-local"] == 1

    lclock2, vclock2 = mesh.increment_clock()
    assert lclock2 == 2
    assert vclock2["node-local"] == 2

    # Merge remote vector clock
    mesh.merge_vector_clock("node-remote-1", {"node-local": 1, "node-remote-1": 10, "node-remote-2": 5})
    merged = mesh.get_vector_clock()
    assert merged["node-local"] == 2  # Keeps local higher
    assert merged["node-remote-1"] == 10
    assert merged["node-remote-2"] == 5


def test_crdt_tombstone_lww_total_ordering():
    t_old = CRDTTombstone(
        resource_id="cache_key_1",
        timestamp=100.0,
        lamport_clock=1,
        node_id="node-a",
        reason="mutation"
    )
    t_newer_lamport = CRDTTombstone(
        resource_id="cache_key_1",
        timestamp=90.0,
        lamport_clock=2,  # Higher lamport clock wins
        node_id="node-b",
        reason="mutation"
    )
    assert t_newer_lamport.is_newer_than(t_old) is True
    assert t_old.is_newer_than(t_newer_lamport) is False

    # Same lamport clock, higher timestamp wins
    t_same_clock_older = CRDTTombstone(
        resource_id="cache_key_1",
        timestamp=100.0,
        lamport_clock=5,
        node_id="node-a"
    )
    t_same_clock_newer = CRDTTombstone(
        resource_id="cache_key_1",
        timestamp=105.0,
        lamport_clock=5,
        node_id="node-b"
    )
    assert t_same_clock_newer.is_newer_than(t_same_clock_older) is True
    assert t_same_clock_older.is_newer_than(t_same_clock_newer) is False

    # Same lamport and timestamp, node_id tie-break
    t_tie_a = CRDTTombstone(resource_id="key", timestamp=100.0, lamport_clock=3, node_id="node-a")
    t_tie_z = CRDTTombstone(resource_id="key", timestamp=100.0, lamport_clock=3, node_id="node-z")
    assert t_tie_z.is_newer_than(t_tie_a) is True


def test_tombstone_application_and_invalidation_handler():
    mesh = P2PMesh(node_id="test-mesh-node")
    invalidation_log = []

    def on_inval(res_id, reason, meta):
        invalidation_log.append((res_id, reason, meta))

    mesh.register_invalidation_handler(on_inval)

    # 1. Record local mutation
    tomb1 = mesh.record_local_mutation("file:/app/service.py", reason="code_edit", metadata={"author": "alice"})
    assert tomb1.resource_id == "file:/app/service.py"
    assert tomb1.lamport_clock == 1

    # 2. Remote node sends newer tombstone
    newer_remote = {
        "resource_id": "file:/app/service.py",
        "timestamp": time.time() + 1.0,
        "lamport_clock": 5,
        "node_id": "remote-node-99",
        "reason": "git_pull",
        "metadata": {"commit": "abc123"}
    }
    applied, status = mesh.apply_remote_tombstone(newer_remote)
    assert applied is True
    assert status == "applied"
    assert len(invalidation_log) == 1
    assert invalidation_log[0][0] == "file:/app/service.py"
    assert invalidation_log[0][1] == "git_pull"

    # 3. Remote node sends older tombstone -> Must be rejected
    older_remote = {
        "resource_id": "file:/app/service.py",
        "timestamp": time.time() - 100.0,
        "lamport_clock": 2,
        "node_id": "remote-node-1",
        "reason": "stale_update"
    }
    applied_old, status_old = mesh.apply_remote_tombstone(older_remote)
    assert applied_old is False
    assert status_old == "superseded_by_existing"
    assert len(invalidation_log) == 1  # No additional dispatch


def test_anti_entropy_bilateral_state_sync():
    node_a = P2PMesh(node_id="node-a", endpoint="http://10.0.0.1:8000")
    node_b = P2PMesh(node_id="node-b", endpoint="http://10.0.0.2:8000")

    # Node A mutates key1
    t_a = node_a.record_local_mutation("key1", reason="put")

    # Node A creates sync packet, Node B processes it
    packet_a = node_a.create_sync_packet()
    res_b = node_b.process_sync_packet(packet_a)

    assert res_b["status"] == "synchronized"
    assert res_b["tombstones_applied"] == 1
    assert node_b.get_tombstone("key1") is not None
    assert node_b.get_vector_clock()["node-a"] == node_a.get_vector_clock()["node-a"]

    # Node B mutates key2
    t_b = node_b.record_local_mutation("key2", reason="update")

    # Node B syncs to Node A
    packet_b = node_b.create_sync_packet()
    res_a = node_a.process_sync_packet(packet_b)

    assert res_a["status"] == "synchronized"
    assert res_a["tombstones_applied"] == 1
    assert node_a.get_tombstone("key2") is not None

    # Both nodes now have identical tombstone sets
    assert set(node_a._tombstones.keys()) == set(node_b._tombstones.keys()) == {"key1", "key2"}


def test_mesh_http_api_endpoints(client):
    # 1. Register peer
    reg_res = client.post("/v1/mesh/peers", json={
        "endpoint": "http://10.0.0.55:8000",
        "node_id": "edge-peer-55",
        "metadata": {"region": "us-west", "dc": "pdx-1"}
    })
    assert reg_res.status_code == 200
    data = reg_res.json()
    assert data["status"] == "success"
    assert data["peer"]["node_id"] == "edge-peer-55"

    # 2. Query peers
    peers_res = client.get("/v1/mesh/peers")
    assert peers_res.status_code == 200
    topo = peers_res.json()
    assert topo["mesh_enabled"] is True
    assert topo["version"] == config.VERSION
    assert any(p["node_id"] == "edge-peer-55" for p in topo["peers"])

    # 3. Heartbeat ping
    hb_res = client.post("/v1/mesh/heartbeat", json={
        "node_id": "edge-peer-55",
        "endpoint": "http://10.0.0.55:8000",
        "vector_clock": {"edge-peer-55": 7}
    })
    assert hb_res.status_code == 200
    hb_data = hb_res.json()
    assert hb_data["status"] == "pong"
    assert hb_data["vector_clock"]["edge-peer-55"] >= 7

    # 4. State sync packet from peer
    sync_res = client.post("/v1/mesh/sync", json={
        "node_id": "edge-peer-55",
        "endpoint": "http://10.0.0.55:8000",
        "lamport_clock": 20,
        "vector_clock": {"edge-peer-55": 8},
        "tombstones": [
            {
                "resource_id": "tag:test_api_sync",
                "timestamp": time.time(),
                "lamport_clock": 20,
                "node_id": "edge-peer-55",
                "reason": "mutation"
            }
        ]
    })
    assert sync_res.status_code == 200
    sync_data = sync_res.json()
    assert sync_data["status"] == "synchronized"
    assert sync_data["tombstones_applied"] >= 1

    # 5. Broadcast endpoint
    bcast_res = client.post("/v1/mesh/broadcast", json={
        "resource_id": "tag:broadcast_test",
        "reason": "manual_purge"
    })
    assert bcast_res.status_code == 200
    assert bcast_res.json()["status"] == "success"

    # 6. Unregister peer
    del_res = client.delete("/v1/mesh/peers?node_id=edge-peer-55")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "success"


def test_mesh_cross_node_cache_purge(client):
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello mesh world"}]
    }
    response_payload = {
        "id": "chatcmpl-mesh-1",
        "choices": [{"message": {"role": "assistant", "content": "Mesh test response"}}]
    }
    cache_instance.store(payload, response_payload, org_id="default")
    hit_type, entry, _, _ = cache_instance.lookup(payload, org_id="default")
    assert hit_type == "HIT_EXACT"

    # Peer sends global purge tombstone '*'
    sync_res = client.post("/v1/mesh/sync", json={
        "node_id": "remote-controller",
        "endpoint": "http://10.0.0.99:8000",
        "lamport_clock": 99,
        "vector_clock": {"remote-controller": 1},
        "tombstones": [
            {
                "resource_id": "*",
                "timestamp": time.time(),
                "lamport_clock": 99,
                "node_id": "remote-controller",
                "reason": "cache_purge"
            }
        ]
    })
    assert sync_res.status_code == 200
    assert sync_res.json()["tombstones_applied"] == 1

    # Invalidation handler must have cleared cache!
    hit_type_after, entry_after, _, _ = cache_instance.lookup(payload, org_id="default")
    assert hit_type_after == "MISS"


def test_mesh_cross_node_tag_invalidation(client):
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Explain quantum entanglement"}]
    }
    response_payload = {
        "id": "chatcmpl-mesh-2",
        "choices": [{"message": {"role": "assistant", "content": "Quantum entanglement is..."}}]
    }
    cache_instance.store(payload, response_payload, org_id="default", tag="physics")
    hit_type, entry, _, _ = cache_instance.lookup(payload, org_id="default")
    assert hit_type == "HIT_EXACT"

    # Peer sends tag invalidation tombstone 'tag:physics'
    sync_res = client.post("/v1/mesh/sync", json={
        "node_id": "remote-researcher",
        "endpoint": "http://10.0.0.77:8000",
        "lamport_clock": 105,
        "vector_clock": {"remote-researcher": 3},
        "tombstones": [
            {
                "resource_id": "tag:physics",
                "timestamp": time.time(),
                "lamport_clock": 105,
                "node_id": "remote-researcher",
                "reason": "tag_invalidation"
            }
        ]
    })
    assert sync_res.status_code == 200
    assert sync_res.json()["tombstones_applied"] == 1

    # Tagged entry must be invalidated
    hit_type_after, entry_after, _, _ = cache_instance.lookup(payload, org_id="default")
    assert hit_type_after == "MISS"
