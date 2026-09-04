import pytest
from starlette.testclient import TestClient
from server.gateway import app
from core.config import config

@pytest.fixture
def client():
    return TestClient(app)

class TestToolReplayAndVersionE2E:
    def test_01_version_consistency_across_endpoints(self, client):
        """Verify /healthz, /v1/cache/stats, and MCP initialize return the single source of truth version."""
        # 1. Healthz
        res_health = client.get("/healthz")
        assert res_health.status_code == 200
        assert res_health.json().get("version") == config.VERSION

        # 2. Stats
        res_stats = client.get("/v1/cache/stats")
        assert res_stats.status_code == 200
        assert res_stats.json().get("system_info", {}).get("version") == config.VERSION

        # 3. MCP initialize
        mcp_init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "test-client", "version": "1.0.0"}
            }
        }
        res_mcp = client.post("/v1/mcp", json=mcp_init_req)
        assert res_mcp.status_code == 200
        assert res_mcp.json()["result"]["serverInfo"]["version"] == config.VERSION

    def test_02_tool_replay_http_store_and_hit_lifecycle(self, client):
        """Verify the complete HTTP tool replay lifecycle: Miss -> Record -> Hit -> Invalidate."""
        tool_payload = {
            "tool_name": "read_file",
            "arguments": {"filepath": "server/gateway.py"},
            "workspace_fingerprint": "e2e_workspace",
            "workspace_state": "git_commit_abc:clean"
        }

        # 1. First lookup over HTTP -> Must be MISS
        res_miss = client.post("/v1/agent/tool_replay", json=tool_payload)
        assert res_miss.status_code == 200
        assert res_miss.json().get("status") == "MISS"
        assert res_miss.json().get("cached") is False

        # 2. Store execution output over HTTP via tool_record
        record_payload = {
            **tool_payload,
            "output": "# Simulated file content of gateway.py"
        }
        res_store = client.post("/v1/agent/tool_record", json=record_payload)
        assert res_store.status_code == 200
        assert res_store.json().get("status") == "STORED"
        assert res_store.json().get("cached") is True

        # 3. Subsequent lookup over HTTP with same workspace state -> Must be HIT
        res_hit = client.post("/v1/agent/tool_replay", json=tool_payload)
        assert res_hit.status_code == 200
        assert res_hit.json().get("status") == "HIT"
        assert res_hit.json().get("cached") is True
        assert res_hit.json().get("output") == "# Simulated file content of gateway.py"

        # 4. Lookup with altered workspace state (e.g. file modified or git commit changed) -> Must MISS
        altered_payload = {
            **tool_payload,
            "workspace_state": "git_commit_abc:dirty_hash_999"
        }
        res_invalidated = client.post("/v1/agent/tool_replay", json=altered_payload)
        assert res_invalidated.status_code == 200
        assert res_invalidated.json().get("status") == "MISS"

    def test_03_mcp_tool_replay_and_record(self, client):
        """Verify MCP JSON-RPC tools for tool replay and recording."""
        # 1. Record via MCP tools/call
        record_mcp_req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "omnicache_record_tool",
                "arguments": {
                    "tool_name": "git_status",
                    "arguments": {},
                    "output": "On branch main\nnothing to commit, working tree clean",
                    "workspace_fingerprint": "mcp_ws",
                    "workspace_state": "mcp_state_1"
                }
            }
        }
        res_mcp_rec = client.post("/v1/mcp", json=record_mcp_req)
        assert res_mcp_rec.status_code == 200
        assert "STORED" in res_mcp_rec.json()["result"]["content"][0]["text"]

        # 2. Replay via MCP tools/call
        replay_mcp_req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "omnicache_replay_tool",
                "arguments": {
                    "tool_name": "git_status",
                    "arguments": {},
                    "workspace_fingerprint": "mcp_ws",
                    "workspace_state": "mcp_state_1"
                }
            }
        }
        res_mcp_rep = client.post("/v1/mcp", json=replay_mcp_req)
        assert res_mcp_rep.status_code == 200
        assert "HIT" in res_mcp_rep.json()["result"]["content"][0]["text"]
        assert "nothing to commit" in res_mcp_rep.json()["result"]["content"][0]["text"]
