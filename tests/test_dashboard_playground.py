"""
Unit and integration tests for Dashboard Playground Sandbox & Semantic Rephrasing.
Verifies that playground requests without pre-configured API keys succeed,
store responses in the cache, and achieve 100% semantic hit rate and token savings
when 'Test Rephrasing' is clicked.
"""

import pytest
from starlette.testclient import TestClient
from server.gateway import app


@pytest.fixture
def client():
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
