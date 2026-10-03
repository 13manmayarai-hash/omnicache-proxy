"""
Unit & Integration tests for hosted OmniCache persistence:
1. Refresh works (exchange code -> refresh -> new access token & new refresh token -> call /mcp).
2. Single use (replaying consumed refresh token returns 400 invalid_grant).
3. Survives a restart (OAUTH_TOKENS.clear() simulates memory loss; SQLite persistence satisfies refresh).
4. Wrong client (refresh with mismatching client_id returns 400 invalid_grant).
5. No scope widening (narrowing allowed, widening beyond granted scope returns 400 invalid_scope).
6. Stored hashed (oauth_refresh_tokens stores SHA-256 hashes, never raw omni_ref_ tokens).
7. Salt location (OMNICACHE_DATA_DIR/.privacy_salt created and stable across calls).
"""

import os
import tempfile
import pytest
from starlette.testclient import TestClient

from core.config import get_or_generate_privacy_salt
from persistence.snapshot_store import snapshot_store
from server.gateway import app, OAUTH_TOKENS


@pytest.fixture
def client():
    return TestClient(app)


def test_01_oauth_refresh_flow_and_mcp_call(client):
    """1. Refresh works: authorize -> token -> refresh -> verify new tokens -> call /mcp."""
    # Authorize
    resp_auth = client.get("/oauth/authorize?client_id=claude-test&response_type=code&scope=mcp:read%20mcp:write")
    assert resp_auth.status_code == 200
    code = resp_auth.json()["code"]

    # Exchange code
    resp_tok = client.post("/oauth/token", json={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": "claude-test"
    })
    assert resp_tok.status_code == 200
    tok_data = resp_tok.json()
    access_token_1 = tok_data["access_token"]
    refresh_token_1 = tok_data["refresh_token"]
    assert access_token_1.startswith("omni_tok_")
    assert refresh_token_1.startswith("omni_ref_")

    # Refresh
    resp_ref = client.post("/oauth/token", json={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token_1,
        "client_id": "claude-test"
    })
    assert resp_ref.status_code == 200
    ref_data = resp_ref.json()
    access_token_2 = ref_data["access_token"]
    refresh_token_2 = ref_data["refresh_token"]

    assert access_token_2 != access_token_1
    assert refresh_token_2 != refresh_token_1
    assert access_token_2.startswith("omni_tok_")
    assert refresh_token_2.startswith("omni_ref_")
    assert ref_data["token_type"] == "Bearer"
    assert ref_data["expires_in"] == 86400

    # Call /mcp with new access token
    resp_mcp = client.get("/mcp", headers={"Authorization": f"Bearer {access_token_2}"})
    assert resp_mcp.status_code == 200
    assert resp_mcp.json().get("service") == "omnicache-mcp"


def test_02_oauth_refresh_token_single_use(client):
    """2. Single use: posting the same refresh token again returns 400 invalid_grant."""
    resp_auth = client.get("/oauth/authorize?client_id=claude-test-single&response_type=code")
    code = resp_auth.json()["code"]

    resp_tok = client.post("/oauth/token", json={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": "claude-test-single"
    })
    refresh_token = resp_tok.json()["refresh_token"]

    # First refresh succeeds
    resp_ref1 = client.post("/oauth/token", json={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": "claude-test-single"
    })
    assert resp_ref1.status_code == 200

    # Second refresh with same token MUST fail (already consumed / deleted)
    resp_ref2 = client.post("/oauth/token", json={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": "claude-test-single"
    })
    assert resp_ref2.status_code == 400
    assert resp_ref2.json().get("error") == "invalid_grant"


def test_03_oauth_refresh_survives_restart(client):
    """3. Survives a restart: issue tokens, then OAUTH_TOKENS.clear(), refresh still succeeds."""
    resp_auth = client.get("/oauth/authorize?client_id=claude-restart-test&response_type=code")
    code = resp_auth.json()["code"]

    resp_tok = client.post("/oauth/token", json={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": "claude-restart-test"
    })
    refresh_token = resp_tok.json()["refresh_token"]

    # Simulate server process restart: memory state cleared, SQLite persisted
    OAUTH_TOKENS.clear()

    resp_ref = client.post("/oauth/token", json={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": "claude-restart-test"
    })
    assert resp_ref.status_code == 200
    new_access_token = resp_ref.json()["access_token"]
    assert new_access_token.startswith("omni_tok_")

    # Newly minted access token works on /mcp
    resp_mcp = client.get("/mcp", headers={"Authorization": f"Bearer {new_access_token}"})
    assert resp_mcp.status_code == 200


def test_04_oauth_refresh_wrong_client(client):
    """4. Wrong client: refresh with a different client_id returns 400 invalid_grant."""
    resp_auth = client.get("/oauth/authorize?client_id=legit-client&response_type=code")
    code = resp_auth.json()["code"]

    resp_tok = client.post("/oauth/token", json={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": "legit-client"
    })
    refresh_token = resp_tok.json()["refresh_token"]

    resp_ref = client.post("/oauth/token", json={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": "impostor-client"
    })
    assert resp_ref.status_code == 400
    assert resp_ref.json().get("error") == "invalid_grant"


def test_05_oauth_refresh_no_scope_widening(client):
    """5. No scope widening: requesting wider scope returns 400 invalid_scope, matching/narrowing succeeds."""
    # 5a. Scope widening rejected
    resp_auth1 = client.get("/oauth/authorize?client_id=scope-test1&response_type=code&scope=mcp:read")
    code1 = resp_auth1.json()["code"]
    resp_tok1 = client.post("/oauth/token", json={
        "grant_type": "authorization_code",
        "code": code1,
        "client_id": "scope-test1"
    })
    refresh_token1 = resp_tok1.json()["refresh_token"]

    resp_wide = client.post("/oauth/token", json={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token1,
        "client_id": "scope-test1",
        "scope": "mcp:read mcp:write"
    })
    assert resp_wide.status_code == 400
    assert resp_wide.json().get("error") == "invalid_scope"

    # 5b. Asking for granted scope succeeds
    resp_auth2 = client.get("/oauth/authorize?client_id=scope-test2&response_type=code&scope=mcp:read")
    code2 = resp_auth2.json()["code"]
    resp_tok2 = client.post("/oauth/token", json={
        "grant_type": "authorization_code",
        "code": code2,
        "client_id": "scope-test2"
    })
    refresh_token2 = resp_tok2.json()["refresh_token"]

    resp_ok = client.post("/oauth/token", json={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token2,
        "client_id": "scope-test2",
        "scope": "mcp:read"
    })
    assert resp_ok.status_code == 200
    assert resp_ok.json().get("scope") == "mcp:read"


def test_06_oauth_refresh_tokens_stored_hashed():
    """6. Stored hashed: SELECT token_hash FROM oauth_refresh_tokens never contains raw omni_ref_."""
    conn = snapshot_store._get_connection()
    with conn:
        cursor = conn.cursor()
        cursor.execute("SELECT token_hash FROM oauth_refresh_tokens")
        rows = cursor.fetchall()
        for row in rows:
            token_hash = row[0]
            assert not token_hash.startswith("omni_ref_")
            assert "omni_ref_" not in token_hash
            assert len(token_hash) == 64  # SHA-256 hex string


def test_07_privacy_salt_location_in_data_dir(monkeypatch):
    """7. Salt location: OMNICACHE_DATA_DIR/.privacy_salt created and stable across calls."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        monkeypatch.setenv("OMNICACHE_DATA_DIR", tmp_dir)
        monkeypatch.delenv("PRIVACY_SALT", raising=False)
        monkeypatch.delenv("OMNICACHE_PRIVACY_SALT", raising=False)

        salt_1 = get_or_generate_privacy_salt()
        assert len(salt_1) == 64

        expected_file = os.path.join(tmp_dir, ".privacy_salt")
        assert os.path.exists(expected_file)
        with open(expected_file, "r", encoding="utf-8") as f:
            assert f.read().strip() == salt_1

        salt_2 = get_or_generate_privacy_salt()
        assert salt_2 == salt_1
