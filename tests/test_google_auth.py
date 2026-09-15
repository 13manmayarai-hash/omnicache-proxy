"""
Test suite for Google OAuth 2.0 Identity Federation in OmniCache:
- GET /auth/google/login (initiates Google OAuth, preserves MCP client state, CSRF token)
- GET /auth/google/callback (exchanges token, provisions tenant, redirects to client with auth code)
- Integration with /oauth/authorize consent screen (Google sign-in button & API key fallback)
"""

import time
import uuid
from urllib.parse import urlparse, parse_qs
from unittest.mock import patch, MagicMock
import pytest
from starlette.testclient import TestClient

from core.config import config
from server.gateway import (
    app,
    GOOGLE_OAUTH_STATES,
    OAUTH_CODES,
    FREE_TIER_MONTHLY_BUDGET_USD,
    FREE_TIER_RATE_LIMIT_RPM,
    FREE_TIER_ROLE,
    reset_consumed_oauth_states
)
from server.quotas import quota_manager
from persistence.snapshot_store import snapshot_store


@pytest.fixture(autouse=True)
def clean_state():
    GOOGLE_OAUTH_STATES.clear()
    OAUTH_CODES.clear()
    reset_consumed_oauth_states()
    conn = snapshot_store._get_connection()
    with conn:
        conn.execute("DELETE FROM signups")
    yield
    GOOGLE_OAUTH_STATES.clear()
    OAUTH_CODES.clear()
    reset_consumed_oauth_states()
    conn = snapshot_store._get_connection()
    with conn:
        conn.execute("DELETE FROM signups")


@pytest.fixture
def client():
    return TestClient(app)


def test_google_login_unconfigured_json(client, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "")

    resp = client.get("/auth/google/login", headers={"accept": "application/json"})
    assert resp.status_code == 503
    data = resp.json()
    assert data["error"] == "server_error"
    assert "not configured" in data["error_description"]


def test_google_login_unconfigured_html(client, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "")

    resp = client.get("/auth/google/login", headers={"accept": "text/html"})
    assert resp.status_code == 503
    assert "Google Sign-In Unconfigured" in resp.text
    assert "Return to Manual Login" in resp.text


def test_google_login_redirects_with_csrf_state(client, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "mock-google-client-id")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "mock-google-client-secret")
    monkeypatch.setattr(config, "GOOGLE_REDIRECT_URI", "https://omnicache.example.com/auth/google/callback")

    params = {
        "client_id": "claude-desktop",
        "redirect_uri": "https://claude.ai/oauth/callback",
        "state": "client-state-12345",
        "scope": "mcp:read mcp:write",
        "code_challenge": "mock_code_challenge_val",
        "code_challenge_method": "S256"
    }

    resp = client.get("/auth/google/login", params=params, follow_redirects=False)
    assert resp.status_code == 302
    redirect_url = resp.headers["location"]
    assert redirect_url.startswith("https://accounts.google.com/o/oauth2/v2/auth")

    parsed = urlparse(redirect_url)
    query = parse_qs(parsed.query)

    assert query["client_id"][0] == "mock-google-client-id"
    assert query["redirect_uri"][0] == "https://omnicache.example.com/auth/google/callback"
    assert query["response_type"][0] == "code"
    assert "openid" in query["scope"][0]

    state_token = query["state"][0]
    assert state_token in GOOGLE_OAUTH_STATES
    stored_state = GOOGLE_OAUTH_STATES[state_token]
    assert stored_state["client_id"] == "claude-desktop"
    assert stored_state["redirect_uri"] == "https://claude.ai/oauth/callback"
    assert stored_state["client_state"] == "client-state-12345"
    assert stored_state["code_challenge"] == "mock_code_challenge_val"


def test_google_callback_missing_or_invalid_state(client):
    resp = client.get("/auth/google/callback?code=mock_code&state=non_existent_state")
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"] == "invalid_request"
    assert "state" in data["error_description"]


def test_google_callback_google_error(client):
    resp = client.get("/auth/google/callback?error=access_denied&error_description=User+denied")
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"] == "access_denied"


def test_google_callback_new_tenant_and_client_redirect(client, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "mock-google-id")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "mock-google-secret")

    # Register state
    state_token = "valid_test_state_123"
    GOOGLE_OAUTH_STATES[state_token] = {
        "client_id": "claude-desktop",
        "redirect_uri": "https://claude.ai/oauth/callback",
        "client_state": "client_session_xyz",
        "scope": "mcp:read mcp:write",
        "code_challenge": "challenge_123",
        "code_challenge_method": "S256",
        "response_type": "code",
        "created_at": time.time(),
        "expires_at": time.time() + 600
    }

    test_email = "alice@example.com"
    mock_token_resp = MagicMock()
    mock_token_resp.status_code = 200
    mock_token_resp.json.return_value = {
        "access_token": "mock_google_access_token",
        "token_type": "Bearer",
        "expires_in": 3600
    }

    mock_userinfo_resp = MagicMock()
    mock_userinfo_resp.status_code = 200
    mock_userinfo_resp.json.return_value = {
        "email": test_email,
        "email_verified": True,
        "name": "Alice Developer"
    }

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, data=None, **kwargs):
            return mock_token_resp
        async def get(self, url, headers=None, **kwargs):
            return mock_userinfo_resp

    with patch("server.gateway.httpx.AsyncClient", MockAsyncClient):
        resp = client.get(f"/auth/google/callback?code=valid_google_code&state={state_token}", follow_redirects=False)

    assert resp.status_code == 302
    redirect_target = resp.headers["location"]
    assert redirect_target.startswith("https://claude.ai/oauth/callback")

    parsed = urlparse(redirect_target)
    q = parse_qs(parsed.query)
    assert q["state"][0] == "client_session_xyz"
    omni_code = q["code"][0]
    assert omni_code.startswith("omni_code_")

    # Verify authorization code registration in OmniCache
    assert omni_code in OAUTH_CODES
    code_entry = OAUTH_CODES[omni_code]
    assert code_entry["client_id"] == "claude-desktop"
    assert code_entry["email"] == test_email
    assert code_entry["org_id"].startswith("org_")
    assert code_entry["team_name"] == "Alice Developer Workspace"

    # Verify auto-provisioned tenant in snapshot_store and quota_manager
    signup = snapshot_store.get_signup_by_email(test_email)
    assert signup is not None
    assert signup["org_id"] == code_entry["org_id"]
    assert signup["key_id"].startswith("omni_live_")

    key_info = quota_manager.get_key(signup["key_id"])
    assert key_info is not None
    assert key_info["role"] == FREE_TIER_ROLE
    assert key_info["monthly_budget_usd"] == FREE_TIER_MONTHLY_BUDGET_USD
    assert key_info["rate_limit_rpm"] == FREE_TIER_RATE_LIMIT_RPM


def test_google_callback_existing_tenant_reuse(client, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "mock-google-id")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "mock-google-secret")

    # Pre-register existing tenant
    existing_email = "bob@example.com"
    existing_org = "org_bob_existing_123"
    existing_key = "omni_live_bob_existing_key"
    existing_team = "Bob Research Org"

    snapshot_store.record_signup(
        email=existing_email,
        team_name=existing_team,
        org_id=existing_org,
        key_id=existing_key,
        ip_address="127.0.0.1",
        created_at=time.time(),
        synchronous=True
    )
    quota_manager.register_key(
        key_id=existing_key,
        team_name=existing_team,
        org_id=existing_org,
        role="tenant"
    )

    state_token = "bob_state_token"
    GOOGLE_OAUTH_STATES[state_token] = {
        "client_id": "cursor-agent",
        "redirect_uri": "http://localhost:54321/auth/callback",
        "client_state": "cursor_state_abc",
        "scope": "mcp:read",
        "code_challenge": "",
        "code_challenge_method": "S256",
        "created_at": time.time(),
        "expires_at": time.time() + 600
    }

    mock_token_resp = MagicMock()
    mock_token_resp.status_code = 200
    mock_token_resp.json.return_value = {"access_token": "bob_token"}

    mock_userinfo_resp = MagicMock()
    mock_userinfo_resp.status_code = 200
    mock_userinfo_resp.json.return_value = {
        "email": existing_email,
        "email_verified": True,
        "name": "Bob Smith"
    }

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, data=None, **kwargs):
            return mock_token_resp
        async def get(self, url, headers=None, **kwargs):
            return mock_userinfo_resp

    with patch("server.gateway.httpx.AsyncClient", MockAsyncClient):
        resp = client.get(f"/auth/google/callback?code=mock_code&state={state_token}", follow_redirects=False)

    assert resp.status_code == 302
    q = parse_qs(urlparse(resp.headers["location"]).query)
    omni_code = q["code"][0]
    code_entry = OAUTH_CODES[omni_code]

    # Reuses existing org_id and team_name, does not create duplicate
    assert code_entry["org_id"] == existing_org
    assert code_entry["team_name"] == existing_team


def test_oauth_authorize_consent_screen_displays_google_sign_in(client, monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_AUTH", True)

    resp = client.get("/oauth/authorize?client_id=test-mcp&redirect_uri=https://example.com/cb", headers={"accept": "text/html"})
    assert resp.status_code == 200
    assert "Sign in with Google" in resp.text
    assert "/auth/google/login?" in resp.text
    assert "client_id=test-mcp" in resp.text
    assert "OR USE API KEY" in resp.text
    assert "OmniCache API Key or Admin Key:" in resp.text


def test_google_callback_survives_container_restart(client, monkeypatch):
    """Validates that HMAC-signed state tokens remain valid even if in-memory state is wiped."""
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "mock-google-id")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "mock-google-secret")

    # Step 1: Initiate login to get a signed state token
    resp = client.get("/auth/google/login?client_id=claude-test&redirect_uri=https://claude.ai/cb&state=mystate", follow_redirects=False)
    assert resp.status_code == 302
    q = parse_qs(urlparse(resp.headers["location"]).query)
    signed_state = q["state"][0]

    # Step 2: SIMULATE SERVER RESTART / REDEPLOY by wiping in-memory dictionary
    GOOGLE_OAUTH_STATES.clear()

    # Step 3: Callback arrives at the newly started server
    mock_token_resp = MagicMock()
    mock_token_resp.status_code = 200
    mock_token_resp.json.return_value = {"access_token": "mock_token"}

    mock_userinfo_resp = MagicMock()
    mock_userinfo_resp.status_code = 200
    mock_userinfo_resp.json.return_value = {
        "email": "restart_test@example.com",
        "email_verified": True,
        "name": "Restart Tester"
    }

    class MockAsyncClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, data=None, **kwargs): return mock_token_resp
        async def get(self, url, headers=None, **kwargs): return mock_userinfo_resp

    with patch("server.gateway.httpx.AsyncClient", MockAsyncClient):
        cb_resp = client.get(f"/auth/google/callback?code=mock_code&state={signed_state}", follow_redirects=False)

    # Must succeed and redirect to claude.ai with minted code!
    assert cb_resp.status_code == 302
    assert cb_resp.headers["location"].startswith("https://claude.ai/cb")
