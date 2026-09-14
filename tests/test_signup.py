"""
Test suite for Public Self-Service Signup Endpoint (POST /v1/signup).
Validates guardrails:
1. No authentication required.
2. Accepts only email and optional team_name.
3. Server-side generated org_id and omni_live_* key_id (tamper-proof).
4. Free tier constants strictly enforced (5.0 USD budget, 30 RPM, role=tenant).
5. Email format and disposable domain validation.
6. Duplicate email rejection (409 Conflict).
7. IP rate limiting (5 signups per hour per IP, 429 Too Many Requests).
8. Durable SQLite audit logging.
9. Immediate usability of minted key on authenticated endpoints.
"""

import time
import uuid
import pytest
from starlette.testclient import TestClient
from server.gateway import (
    app,
    _SIGNUP_IP_TIMESTAMPS,
    _SIGNUP_LOCK,
    FREE_TIER_MONTHLY_BUDGET_USD,
    FREE_TIER_RATE_LIMIT_RPM,
    FREE_TIER_ROLE
)
from server.quotas import quota_manager
from persistence.snapshot_store import snapshot_store


@pytest.fixture(autouse=True)
def reset_signup_state():
    with _SIGNUP_LOCK:
        _SIGNUP_IP_TIMESTAMPS.clear()
    conn = snapshot_store._get_connection()
    with conn:
        conn.execute("DELETE FROM signups")
    yield
    with _SIGNUP_LOCK:
        _SIGNUP_IP_TIMESTAMPS.clear()
    conn = snapshot_store._get_connection()
    with conn:
        conn.execute("DELETE FROM signups")


@pytest.fixture
def client():
    return TestClient(app)


def test_signup_success_with_team_name(client):
    email = f"user_{uuid.uuid4().hex[:8]}@example.com"
    team = "Acme AI Labs"
    resp = client.post("/v1/signup", json={"email": email, "team_name": team})
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "success"
    assert data["api_key"].startswith("omni_live_")
    assert data["org_id"].startswith("org_")
    assert data["team_name"] == team
    assert data["monthly_budget_usd"] == FREE_TIER_MONTHLY_BUDGET_USD
    assert data["rate_limit_rpm"] == FREE_TIER_RATE_LIMIT_RPM
    assert data["role"] == FREE_TIER_ROLE

    # Verify registered in quota manager
    key_info = quota_manager.storage.get_key(data["api_key"])
    assert key_info is not None
    assert key_info["team_name"] == team
    assert key_info["monthly_budget_usd"] == 5.0
    assert key_info["role"] == "tenant"

    # Verify recorded in durable SQLite store
    stored = snapshot_store.get_signup_by_email(email)
    assert stored is not None
    assert stored["email"] == email
    assert stored["org_id"] == data["org_id"]


def test_signup_derived_team_name(client):
    email = f"sarah.connor_{uuid.uuid4().hex[:6]}@skynet.org"
    resp = client.post("/v1/signup", json={"email": email})
    assert resp.status_code == 201
    data = resp.json()
    assert "Sarah Connor" in data["team_name"] or "Workspace" in data["team_name"]


def test_signup_tampering_ignored(client):
    """Caller attempts to self-grant admin role, unlimited budget, and existing org_id."""
    email = f"hacker_{uuid.uuid4().hex[:8]}@example.com"
    payload = {
        "email": email,
        "team_name": "Hacker Org",
        "role": "admin",
        "monthly_budget_usd": 999999.0,
        "rate_limit_rpm": 100000,
        "org_id": "super_admin_org"
    }
    resp = client.post("/v1/signup", json=payload)
    assert resp.status_code == 201
    data = resp.json()

    # Budget and role must strictly match free tier constants
    assert data["monthly_budget_usd"] == 5.0
    assert data["rate_limit_rpm"] == 30
    assert data["role"] == "tenant"
    assert data["org_id"] != "super_admin_org"
    assert data["org_id"].startswith("org_")

    # In quota manager
    info = quota_manager.storage.get_key(data["api_key"])
    assert info["role"] == "tenant"
    assert info["monthly_budget_usd"] == 5.0
    assert not quota_manager.is_admin(data["api_key"])


def test_signup_invalid_email_format(client):
    for bad_email in ["", "notanemail", "@nodomain.com", "user@", "user@nodot", 12345, None]:
        resp = client.post("/v1/signup", json={"email": bad_email})
        assert resp.status_code == 400
        assert "error" in resp.json()


def test_signup_disposable_email_rejected(client):
    disposable_emails = [
        "test@mailinator.com",
        "user@tempmail.com",
        "bot@10minutemail.com",
        "anon@guerrillamail.com",
        "spam@trashmail.com"
    ]
    for disp in disposable_emails:
        resp = client.post("/v1/signup", json={"email": disp})
        assert resp.status_code == 400
        assert "not allowed" in resp.json()["error"]["message"].lower()


def test_signup_duplicate_email_rejected(client):
    email = f"duplicate_{uuid.uuid4().hex[:8]}@example.com"
    resp1 = client.post("/v1/signup", json={"email": email})
    assert resp1.status_code == 201

    resp2 = client.post("/v1/signup", json={"email": email})
    assert resp2.status_code == 409
    assert "already exists" in resp2.json()["error"]["message"].lower()


def test_signup_ip_rate_limiting(client):
    test_ip = "198.51.100.42"
    headers = {"x-forwarded-for": test_ip}

    # First 5 signups from this IP should succeed
    for i in range(5):
        unique_email = f"user_{i}_{uuid.uuid4().hex[:6]}@example.com"
        r = client.post("/v1/signup", json={"email": unique_email}, headers=headers)
        assert r.status_code == 201, f"Request {i+1} failed: {r.text}"

    # 6th signup within the same hour must be blocked by rate limiter (429)
    sixth_email = f"user_sixth_{uuid.uuid4().hex[:6]}@example.com"
    r6 = client.post("/v1/signup", json={"email": sixth_email}, headers=headers)
    assert r6.status_code == 429
    assert "rate limit exceeded" in r6.json()["error"]["message"].lower()


def test_signup_cors_options(client):
    resp = client.options("/v1/signup")
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-methods") is not None


def test_minted_key_works_on_authenticated_routes(client):
    email = f"active_{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post("/v1/signup", json={"email": email})
    assert resp.status_code == 201
    api_key = resp.json()["api_key"]

    # Test that the newly generated key authenticates against /v1/cache/stats
    stats_resp = client.get("/v1/cache/stats", headers={"x-api-key": api_key})
    assert stats_resp.status_code == 200
    assert "cache_stats" in stats_resp.json()


def test_signup_case_insensitivity_and_trimming(client):
    raw = f"  MyUser_{uuid.uuid4().hex[:6]}@Domain.COM  "
    resp1 = client.post("/v1/signup", json={"email": raw})
    assert resp1.status_code == 201
    data = resp1.json()

    # Second signup with lowercase must trigger duplicate detection
    resp2 = client.post("/v1/signup", json={"email": raw.strip().lower()})
    assert resp2.status_code == 409


def test_signup_non_json_body(client):
    resp = client.post("/v1/signup", content=b"not a json", headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
    assert "error" in resp.json()


def test_signup_different_ips_isolated(client):
    ip_a = "203.0.113.1"
    ip_b = "203.0.113.2"

    # Exhaust IP A
    for i in range(5):
        r = client.post("/v1/signup", json={"email": f"user_a_{i}_{uuid.uuid4().hex[:4]}@example.com"}, headers={"x-forwarded-for": ip_a})
        assert r.status_code == 201
    
    r_a_fail = client.post("/v1/signup", json={"email": f"user_a_overflow@{uuid.uuid4().hex[:4]}example.com"}, headers={"x-forwarded-for": ip_a})
    assert r_a_fail.status_code == 429

    # IP B should still be allowed
    r_b = client.post("/v1/signup", json={"email": f"user_b_{uuid.uuid4().hex[:4]}@example.com"}, headers={"x-forwarded-for": ip_b})
    assert r_b.status_code == 201
