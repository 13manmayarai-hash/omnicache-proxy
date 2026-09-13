"""
Unit and integration tests for Dashboard Playground Sandbox & Semantic Rephrasing.
Verifies that playground requests without pre-configured API keys succeed,
store responses in the cache, and achieve 100% semantic hit rate and token savings
when 'Test Rephrasing' is clicked.
"""

import pytest
from starlette.testclient import TestClient
from server.gateway import app
from core.vector_cache import cache_instance


@pytest.fixture
def client():
    cache_instance.clear()
    return TestClient(app)


def test_dashboard_playground_claude_flow(client):
    # 1. First cold request: Send via OmniCache
    p1 = "Write a python function to sort a list using quicksort."
    r1 = client.post("/v1/messages", json={
        "model": "claude-sonnet-4-5-20250929",
        "messages": [{"role": "user", "content": p1}],
        "max_tokens": 1024
    }, headers={"x-org-id": "enterprise_user", "x-dashboard-playground": "true"})

    assert r1.status_code == 200
    assert r1.headers.get("X-Cache-Status") == "MISS"
    tokens_used = int(r1.headers.get("X-Tokens-Used", "0"))
    assert tokens_used > 0
    data1 = r1.json()
    assert "content" in data1
    assert "quicksort" in data1["content"][0]["text"].lower()

    # 2. Second request: Test Rephrasing
    p2 = "Please write a python function to sort a list using quicksort."
    r2 = client.post("/v1/messages", json={
        "model": "claude-sonnet-4-5-20250929",
        "messages": [{"role": "user", "content": p2}],
        "max_tokens": 1024
    }, headers={"x-org-id": "enterprise_user", "x-dashboard-playground": "true"})

    assert r2.status_code == 200
    assert r2.headers.get("X-Cache-Status") == "HIT_SEMANTIC"
    sim = float(r2.headers.get("X-Cache-Similarity", "0.0"))
    assert sim >= 0.68
    tokens_saved = int(r2.headers.get("X-Tokens-Saved", "0"))
    assert tokens_saved > 0
    cost_saved = float(r2.headers.get("X-Cost-Saved-USD", "0.0"))
    assert cost_saved > 0.0
    data2 = r2.json()
    assert "quicksort" in data2["content"][0]["text"].lower()


def test_dashboard_playground_openai_flow(client):
    # 1. First cold request: Send via OmniCache (OpenAI mode)
    p1 = "Explain how to reverse a linked list in Python."
    r1 = client.post("/v1/chat/completions", json={
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": p1}],
        "temperature": 0.0
    }, headers={"x-org-id": "enterprise_user", "x-dashboard-playground": "true"})

    assert r1.status_code == 200
    assert r1.headers.get("X-Cache-Status") == "MISS"
    tokens_used = int(r1.headers.get("X-Tokens-Used", "0"))
    assert tokens_used > 0
    data1 = r1.json()
    assert "reverse" in data1["choices"][0]["message"]["content"].lower()

    # 2. Second request: Test Rephrasing
    p2 = "Please explain how to reverse a linked list in Python."
    r2 = client.post("/v1/chat/completions", json={
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": p2}],
        "temperature": 0.0
    }, headers={"x-org-id": "enterprise_user", "x-dashboard-playground": "true"})

    assert r2.status_code == 200
    assert r2.headers.get("X-Cache-Status") == "HIT_SEMANTIC"
    tokens_saved = int(r2.headers.get("X-Tokens-Saved", "0"))
    assert tokens_saved > 0


def test_dashboards_endpoints(client):
    """Verify original dashboard and omnicache_2 redesign both serve 200 OK."""
    # Original dashboard
    r_dash = client.get("/dashboard")
    assert r_dash.status_code == 200
    assert "text/html" in r_dash.headers.get("content-type", "")
    assert "auth-modal" in r_dash.text

    # OmniCache 2 redesign
    r_dash2 = client.get("/omnicache_2")
    assert r_dash2.status_code == 200
    assert "text/html" in r_dash2.headers.get("content-type", "")
    assert "OmniCache 2" in r_dash2.text or "OMNICACHE_2" in r_dash2.text
    assert "topoCanvas" in r_dash2.text
    assert "auth-modal" in r_dash2.text


def test_dashboard_require_auth_modes(client):
    """Verify dashboard behavior under REQUIRE_AUTH=True for browser HTML vs JSON API callers."""
    from core.config import config
    from server.quotas import quota_manager

    old_auth = config.REQUIRE_AUTH
    try:
        config.REQUIRE_AUTH = True
        test_admin_key = "oc_live_dashboard_test_key_12345"
        quota_manager.register_key(
            key_id=test_admin_key,
            team_name="Dashboard Admin",
            org_id="admin",
            role="admin"
        )

        # 1. Non-HTML API request without auth must return 401 JSON
        r_json_unauth = client.get("/dashboard", headers={"accept": "application/json"})
        assert r_json_unauth.status_code == 401
        assert "authentication_error" in r_json_unauth.json()["error"]["type"]

        # 2. Browser request (Accept: text/html) without auth serves HTML with auth modal
        r_html_unauth = client.get("/dashboard", headers={"accept": "text/html,application/xhtml+xml"})
        assert r_html_unauth.status_code == 200
        assert "text/html" in r_html_unauth.headers.get("content-type", "")
        assert "auth-modal" in r_html_unauth.text

        # 3. Request with valid query parameter ?key= sets omnicache_key cookie and returns 200
        r_query_auth = client.get(f"/dashboard?key={test_admin_key}", headers={"accept": "text/html"})
        assert r_query_auth.status_code == 200
        assert "set-cookie" in r_query_auth.headers
        assert "omnicache_key=" in r_query_auth.headers["set-cookie"]

        # 4. Request using cookie authenticates correctly
        r_cookie_auth = client.get("/v1/cache/stats", cookies={"omnicache_key": test_admin_key})
        assert r_cookie_auth.status_code == 200
        assert "cache_stats" in r_cookie_auth.json()

    finally:
        config.REQUIRE_AUTH = old_auth

