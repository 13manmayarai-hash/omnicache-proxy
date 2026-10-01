"""
Regression tests for the hosted MCP connector's directory-readiness requirements:
no model-steering instructions, no local-only tools on the hosted endpoint,
tenant-scoped WebSocket events, and no trust in client-supplied org headers.
"""

import json
import time

import pytest
from starlette.testclient import TestClient

from core.config import config
from mcp.server import (
    LOCAL_ONLY_TOOLS,
    TOOLS_METADATA,
    build_server_instructions,
    list_tools,
    process_mcp_jsonrpc,
)
from server import gateway
from server.gateway import app
from server.quotas import quota_manager

BANNED_PHRASES = ("call omnicache_query first", "<1ms", "100%", "sub-millisecond", "dollars saved")


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.parametrize("remote", [False, True])
def test_instructions_do_not_steer_or_overclaim(remote):
    text = build_server_instructions(remote).lower()
    for phrase in BANNED_PHRASES:
        assert phrase.lower() not in text
    assert "usage guidelines" not in text


def test_tool_descriptions_do_not_overclaim():
    for tool in TOOLS_METADATA:
        blob = json.dumps(tool).lower()
        for phrase in BANNED_PHRASES:
            assert phrase.lower() not in blob, (tool["name"], phrase)
        assert "gpt-4o" not in blob, tool["name"]


def test_every_tool_has_title_and_hints():
    for tool in TOOLS_METADATA:
        ann = tool["annotations"]
        assert ann.get("title")
        assert "readOnlyHint" in ann and "destructiveHint" in ann
    destructive = {t["name"] for t in TOOLS_METADATA if t["annotations"]["destructiveHint"]}
    assert destructive == {"omnicache_invalidate"}


def test_local_only_tools_hidden_on_remote_listing():
    remote_names = {t["name"] for t in list_tools(remote=True)}
    local_names = {t["name"] for t in list_tools(remote=False)}
    assert remote_names.isdisjoint(LOCAL_ONLY_TOOLS)
    assert LOCAL_ONLY_TOOLS <= local_names


def test_hosted_endpoint_lists_and_rejects_local_only_tools(client):
    quota_manager.register_key("ready_tenant_key", team_name="Ready Tenant", org_id="ready_org")
    headers = {"x-api-key": "ready_tenant_key"}

    listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=headers)
    names = {t["name"] for t in listed.json()["result"]["tools"]}
    assert names.isdisjoint(LOCAL_ONLY_TOOLS)

    init = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}}, headers=headers)
    instructions = init.json()["result"]["instructions"]
    assert "omnicache_replay_tool" not in instructions

    for name in LOCAL_ONLY_TOOLS:
        call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": name, "arguments": {"tool_name": "git_status", "output": "x"}}}
        res = client.post("/mcp", json=call, headers=headers)
        assert res.json()["error"]["code"] == -32601


def test_hosted_health_does_not_reveal_server_paths(client):
    quota_manager.register_key("ready_tenant_key", team_name="Ready Tenant", org_id="ready_org")
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "omnicache_health", "arguments": {}}}
    res = client.post("/mcp", json=call, headers={"x-api-key": "ready_tenant_key"})
    health = json.loads(res.json()["result"]["content"][0]["text"])
    assert "sqlite_path" not in health["persistence"]
    assert health["version"] == config.VERSION

    local = process_mcp_jsonrpc(call)
    assert "sqlite_path" in json.loads(local["result"]["content"][0]["text"])["persistence"]


def test_oauth_authorize_ignores_client_org_header(client):
    res = client.get(
        "/oauth/authorize",
        params={"response_type": "code", "client_id": "claude-connectors"},
        headers={"x-org-id": "someone_elses_org"},
    )
    assert res.status_code == 200
    code = res.json()["code"]
    assert gateway.OAUTH_CODES[code]["org_id"] == "default"


def _event(org_id, marker):
    return {"type": "event", "event_type": "cache_hit", "timestamp": time.time(), "time_str": "",
            "data": {"org_id": org_id, "marker": marker}}


def test_ws_event_filter_is_fail_closed():
    assert gateway.ws_event_visible(_event("a", 1), None)
    assert gateway.ws_event_visible(_event("a", 1), "a")
    assert not gateway.ws_event_visible(_event("a", 1), "b")
    assert not gateway.ws_event_visible({"type": "event", "data": {"marker": 1}}, "a")


def test_ws_tenant_sees_only_its_own_events(client, monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_AUTH", True)
    quota_manager.register_key("ws_tenant_a", team_name="WS A", org_id="ws_org_a")
    quota_manager.register_key("ws_tenant_b", team_name="WS B", org_id="ws_org_b")
    gateway.RECENT_WS_EVENTS.clear()
    gateway.RECENT_WS_EVENTS.append(_event("ws_org_a", "secret-a"))
    gateway.RECENT_WS_EVENTS.append(_event("ws_org_b", "visible-b"))
    gateway.RECENT_WS_EVENTS.append({"type": "event", "event_type": "new_signup", "data": {"email": "x@y.z"}})

    with client.websocket_connect("/ws?api_key=ws_tenant_b") as ws:
        hello = ws.receive_json()
        markers = [e["data"].get("marker") for e in hello["recent_events"]]
        assert markers == ["visible-b"]

        ws.send_text(json.dumps({"action": "events"}))
        replay = ws.receive_json()
        assert [e["data"].get("marker") for e in replay["events"]] == ["visible-b"]

        ws.send_text(json.dumps({"action": "stats"}))
        assert ws.receive_json()["type"] == "error"

    gateway.RECENT_WS_EVENTS.clear()


def test_ws_rejects_missing_credentials_when_auth_required(client, monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_AUTH", True)
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
