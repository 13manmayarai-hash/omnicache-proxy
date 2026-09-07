"""
Tests for OmniCache Real-Time WebSocket Telemetry Streaming and Event Broadcast.
Verifies bidirectional communication, event broadcasting on tool executions,
mutation guards, and context compaction.
"""

import pytest
from starlette.testclient import TestClient
from server.gateway import app, emit_telemetry_event


@pytest.fixture
def client():
    return TestClient(app)


def test_ws_connection_handshake(client):
    """Verifies that WebSocket connection establishes properly and delivers initial handshake."""
    with client.websocket_connect("/ws") as ws:
        data = ws.receive_json()
        assert data.get("type") == "connection_established"
        assert data.get("service") == "omnicache-proxy"
        assert data.get("status") == "connected"
        assert "recent_events" in data
        assert isinstance(data["recent_events"], list)


def test_ws_ping_pong_and_stats(client):
    """Verifies ping/pong and stats query actions over WebSocket."""
    with client.websocket_connect("/ws") as ws:
        # Handshake
        ws.receive_json()

        # Ping
        ws.send_text("ping")
        resp = ws.receive_text()
        assert resp == "pong"

        # Stats query
        ws.send_json({"action": "stats"})
        stats = ws.receive_json()
        assert stats.get("type") == "stats"
        assert "tokens_saved" in stats
        assert "savings_usd" in stats
        assert "agent_tools_recorded" in stats
        assert "agent_tokens_compacted" in stats

        # Events query
        ws.send_json({"action": "events"})
        events_resp = ws.receive_json()
        assert events_resp.get("type") == "events_replay"
        assert isinstance(events_resp.get("events"), list)


def test_ws_live_event_broadcast(client):
    """Verifies that tool recordings and replays broadcast live events to connected WebSockets."""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # Handshake

        # Record a tool
        rec_res = client.post("/v1/agent/tool_record", json={
            "tool_name": "read_ws_file",
            "arguments": {"path": "test_ws.py"},
            "output": "print('live websocket')",
            "workspace_fingerprint": "ws_live_test"
        }, headers={"Authorization": "Bearer test-key", "x-org-id": "ws_org"})
        assert rec_res.status_code == 200

        # Receive broadcast event
        event = ws.receive_json()
        assert event.get("type") == "event"
        assert event.get("event_type") == "tool_recorded"
        assert event["data"]["tool_name"] == "read_ws_file"

        # Replay the tool
        rep_res = client.post("/v1/agent/tool_replay", json={
            "tool_name": "read_ws_file",
            "arguments": {"path": "test_ws.py"},
            "workspace_fingerprint": "ws_live_test"
        }, headers={"Authorization": "Bearer test-key", "x-org-id": "ws_org"})
        assert rep_res.status_code == 200
        assert rep_res.json().get("status") == "HIT"

        # Receive replay event
        event2 = ws.receive_json()
        assert event2.get("type") == "event"
        assert event2.get("event_type") == "tool_replay"
        assert event2["data"]["tool_name"] == "read_ws_file"


def test_ws_mutation_blocked_broadcast(client):
    """Verifies that mutative tool executions broadcast a mutation_blocked safety event."""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # Handshake

        # Attempt to cache destructive tool
        res = client.post("/v1/agent/tool_record", json={
            "tool_name": "delete_database_cluster",
            "arguments": {"cluster_id": "prod-1"},
            "output": "deleted",
            "workspace_fingerprint": "ws_live_test"
        }, headers={"Authorization": "Bearer test-key", "x-org-id": "ws_org"})
        assert res.status_code == 200
        assert res.json().get("status") == "REJECTED"

        # Receive safety event
        event = ws.receive_json()
        assert event.get("type") == "event"
        assert event.get("event_type") == "mutation_blocked"
        assert event["data"]["tool_name"] == "delete_database_cluster"


from unittest.mock import patch, AsyncMock


def test_ws_context_compacted_broadcast(client):
    """Verifies that context compaction broadcasts live telemetry event over WebSocket."""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # Handshake

        bulky_content = "\n".join([f"line_{i:02d}: def func_{i}(): pass" for i in range(1, 35)])
        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [
                {"role": "user", "content": "Read gateway file"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "t_ws_01", "name": "view_file", "input": {"path": "server/gateway.py"}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t_ws_01", "content": bulky_content}]},
                {"role": "assistant", "content": "I examined the file."},
                {"role": "user", "content": "Turn 5"},
                {"role": "assistant", "content": "Turn 6"},
                {"role": "user", "content": "Turn 7"},
                {"role": "assistant", "content": "Turn 8"}
            ]
        }
        mock_reply = {
            "id": "msg_mock_01",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "All clear."}],
            "usage": {"input_tokens": 50, "output_tokens": 20}
        }
        with patch("server.upstream.upstream_client.forward_anthropic_messages", new=AsyncMock(return_value=(200, mock_reply, {}))):
            res = client.post("/v1/messages", json=payload, headers={"x-api-key": "test", "x-org-id": "ws_org"})
            assert res.status_code == 200

        # Receive context_compacted event
        event = ws.receive_json()
        assert event.get("type") == "event"
        assert event.get("event_type") == "context_compacted"
        assert event["data"]["tokens_compacted"] > 0
