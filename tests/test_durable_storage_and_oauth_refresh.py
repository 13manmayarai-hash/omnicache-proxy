"""
Unit & Integration tests for durable storage and OAuth refresh token rotation:
1. OMNICACHE_DATA_DIR routing for privacy salt and consumed OAuth tokens.
2. Privacy salt persistence across simulated restarts.
3. RFC 6749 Section 6 OAuth refresh token grant with atomic single-use rotation.
4. Token reuse detection and rejection.
5. Durability status in health checks (omnicache_health and /healthz).
"""

import hashlib
import json
import os
import tempfile
import time
import pytest
from starlette.testclient import TestClient

from core.config import config, get_omnicache_data_dir, get_or_generate_privacy_salt
from persistence.snapshot_store import SnapshotStore, snapshot_store
from server import gateway
from server.gateway import app, quota_manager


@pytest.fixture
def client():
    return TestClient(app)


def test_data_dir_routing(monkeypatch):
    """Verify OMNICACHE_DATA_DIR environment variable is respected."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        monkeypatch.setenv("OMNICACHE_DATA_DIR", tmp_dir)
        resolved = get_omnicache_data_dir()
        assert resolved == tmp_dir
        assert os.path.exists(resolved)

        # Consumed oauth db path
        oauth_db_path = gateway._get_consumed_oauth_db_path()
        assert oauth_db_path == os.path.join(tmp_dir, "oauth_consumed.db")


def test_privacy_salt_persists_in_data_dir(monkeypatch):
    """Verify privacy salt is written to OMNICACHE_DATA_DIR/.privacy_salt and survives across restarts."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        monkeypatch.setenv("OMNICACHE_DATA_DIR", tmp_dir)
        monkeypatch.delenv("PRIVACY_SALT", raising=False)
        monkeypatch.delenv("OMNICACHE_PRIVACY_SALT", raising=False)

        salt_1 = get_or_generate_privacy_salt()
        assert len(salt_1) == 64  # 32 bytes hex

        salt_file = os.path.join(tmp_dir, ".privacy_salt")
        assert os.path.exists(salt_file)
        with open(salt_file, "r", encoding="utf-8") as f:
            assert f.read().strip() == salt_1

        # Second call (simulating process restart) loads the exact same salt from disk
        salt_2 = get_or_generate_privacy_salt()
        assert salt_2 == salt_1


def test_snapshot_store_refresh_token_crud():
    """Verify SQLite CRUD for hashed OAuth refresh tokens."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test_tokens.db")
        store = SnapshotStore(db_path=db_path, enable_write_behind=False)

        raw_token = "omni_ref_test12345"
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

        # 1. Store
        store.store_refresh_token(
            token_hash=token_hash,
            key_id="omni_tok_acc123",
            org_id="test_org",
            client_id="claude-connector",
            scope="mcp:read mcp:write",
            expires_at=time.time() + 86400
        )

        # 2. Retrieve
        entry = store.get_refresh_token(token_hash)
        assert entry is not None
        assert entry["key_id"] == "omni_tok_acc123"
        assert entry["org_id"] == "test_org"
        assert entry["consumed"] is False

        # 3. Consume (atomic rotation)
        consumed_ok = store.consume_refresh_token(token_hash)
        assert consumed_ok is True

        # 4. Consume again should fail (already consumed)
        reconsumed = store.consume_refresh_token(token_hash)
        assert reconsumed is False

        entry_after = store.get_refresh_token(token_hash)
        assert entry_after["consumed"] is True

        store.close()


def test_oauth_authorization_code_and_refresh_flow(client):
    """
    End-to-end OAuth test:
    1. Authorize to obtain authorization code.
    2. Redeem code for access_token and refresh_token.
    3. Use refresh_token to obtain new access_token and rotated refresh_token.
    4. Verify old refresh_token is rejected on reuse.
    5. Verify newly issued access_token can authenticate MCP calls.
    """
    # 1. Authorize
    auth_res = client.get("/oauth/authorize", params={
        "response_type": "code",
        "client_id": "test_claude_client",
        "redirect_uri": "https://claude.ai/api/mcp/callback",
        "scope": "mcp:read mcp:write"
    }, follow_redirects=False)
    assert auth_res.status_code == 302
    location = auth_res.headers.get("location", "")
    assert "code=" in location
    from urllib.parse import urlparse, parse_qs
    code = parse_qs(urlparse(location).query)["code"][0]

    # 2. Redeem authorization code
    token_res = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": "test_claude_client",
        "redirect_uri": "https://claude.ai/api/mcp/callback"
    })
    assert token_res.status_code == 200
    token_data = token_res.json()
    access_token_1 = token_data["access_token"]
    refresh_token_1 = token_data["refresh_token"]
    assert access_token_1.startswith("omni_tok_")
    assert refresh_token_1.startswith("omni_ref_")

    # 3. Refresh the token
    refresh_res = client.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token_1,
        "client_id": "test_claude_client"
    })
    assert refresh_res.status_code == 200
    refreshed_data = refresh_res.json()
    access_token_2 = refreshed_data["access_token"]
    refresh_token_2 = refreshed_data["refresh_token"]

    assert access_token_2 != access_token_1
    assert refresh_token_2 != refresh_token_1
    assert refreshed_data["token_type"] == "Bearer"
    assert refreshed_data["expires_in"] == 86400

    # 4. Reusing refresh_token_1 MUST fail with invalid_grant (single-use rotation)
    reuse_res = client.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token_1,
        "client_id": "test_claude_client"
    })
    assert reuse_res.status_code == 400
    assert reuse_res.json()["error"] == "invalid_grant"
    assert "already been consumed" in reuse_res.json()["error_description"]

    # 5. Invalid / unknown refresh token
    bad_res = client.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": "omni_ref_nonexistent_token",
        "client_id": "test_claude_client"
    })
    assert bad_res.status_code == 400
    assert bad_res.json()["error"] == "invalid_grant"

    # 6. Verify newly issued access_token_2 works for authenticated requests
    mcp_call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "omnicache_health",
            "arguments": {}
        }
    }
    mcp_res = client.post("/mcp", json=mcp_call, headers={"authorization": f"Bearer {access_token_2}"})
    assert mcp_res.status_code == 200
    health_content = json.loads(mcp_res.json()["result"]["content"][0]["text"])
    assert health_content["status"] == "healthy"


def test_health_checks_report_durability_status(client, monkeypatch):
    """Verify omnicache_health and /healthz accurately report persistent storage durability."""
    from mcp.server import handle_tool_call

    # 1. Ephemeral mode (OMNICACHE_DATA_DIR unset)
    monkeypatch.delenv("OMNICACHE_DATA_DIR", raising=False)
    hz_res_1 = client.get("/healthz")
    assert hz_res_1.status_code == 200
    p_info_1 = hz_res_1.json()["persistence"]
    assert p_info_1["connected"] is True
    assert p_info_1["durable"] is False
    assert p_info_1["storage_backend"] == "sqlite3_wal_ephemeral"

    mcp_h1 = json.loads(handle_tool_call("omnicache_health", {}, remote=True)["content"][0]["text"])
    assert mcp_h1["persistence"]["durable"] is False
    assert "sqlite_path" not in mcp_h1["persistence"]  # Verify remote MCP doesn't leak local paths

    # 2. Durable mode (OMNICACHE_DATA_DIR set)
    with tempfile.TemporaryDirectory() as tmp_dir:
        monkeypatch.setenv("OMNICACHE_DATA_DIR", tmp_dir)
        hz_res_2 = client.get("/healthz")
        assert hz_res_2.status_code == 200
        p_info_2 = hz_res_2.json()["persistence"]
        assert p_info_2["connected"] is True
        assert p_info_2["durable"] is True
        assert p_info_2["storage_backend"] == "sqlite3_wal_persistent"

        mcp_h2 = json.loads(handle_tool_call("omnicache_health", {}, remote=False)["content"][0]["text"])
        assert mcp_h2["persistence"]["durable"] is True
        assert "sqlite_path" in mcp_h2["persistence"]

