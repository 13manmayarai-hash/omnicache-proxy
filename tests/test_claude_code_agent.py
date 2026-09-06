import pytest
from starlette.testclient import TestClient
from unittest.mock import patch, AsyncMock
from server.gateway import app, METRICS_LEDGER, cache_instance

@pytest.fixture
def client():
    # Clean state before test
    METRICS_LEDGER["total_savings_usd"] = 0.0
    METRICS_LEDGER["total_tokens_saved"] = 0
    METRICS_LEDGER["exact_tokens_saved"] = 0
    METRICS_LEDGER["privacy_scrubbed_count"] = 0
    cache_instance.storage.purge()
    return TestClient(app)

def test_claude_code_session_agent_caching(client):
    """
    Simulates real Claude Code usage:
    1. Session 1: Claude Code sends dynamic timestamps and git email in system prompt with 'Hi'.
       - Must return MISS from upstream.
       - PII counter must NOT increase (system prompt boilerplate ignored).
    2. Session 2: Claude Code sends DIFFERENT timestamp 15 minutes later with 'Hi'.
       - Must hit L1 Exact Cache (HIT_EXACT).
       - Latency < 10ms, Tokens Saved > 0.
       - PII counter still 0.
    3. User explicitly sends sensitive data in their prompt:
       - PII counter increments as intended.
    """
    # 1. Initial State
    res_stats_0 = client.get("/v1/cache/stats")
    stats_0 = res_stats_0.json()
    assert stats_0["enterprise_engine"]["privacy_redactions_total"] == 0
    assert stats_0["financial_telemetry"]["total_tokens_saved"] == 0

    mock_anthropic_reply = {
        "id": "msg_mock_turn1",
        "type": "message",
        "role": "assistant",
        "model": "claude-3-5-sonnet-20241022",
        "content": [{"type": "text", "text": "Hello! How can I help you today?"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1500, "output_tokens": 15}
    }

    # 2. Session 1 at 5:00 PM
    payload_session_1 = {
        "model": "claude-3-5-sonnet-20241022",
        "system": "You are Claude Code.\nCurrent date: Saturday, September 5, 2026\nCurrent time: 2026-09-05T17:00:00Z\nGit user: developer@mycompany.com",
        "messages": [{"role": "user", "content": "Hi"}],
        "tools": [{"name": "bash", "description": "Run bash command"}]
    }

    with patch("server.upstream.upstream_client.forward_anthropic_messages", new=AsyncMock(return_value=(200, mock_anthropic_reply, {}))):
        res_1 = client.post("/v1/messages", json=payload_session_1, headers={"x-api-key": "test-key"})

    assert res_1.status_code == 200
    assert res_1.headers.get("X-Cache-Status") == "MISS"

    # Verify PII was NOT falsely attributed to the user
    stats_1 = client.get("/v1/cache/stats").json()
    assert stats_1["enterprise_engine"]["privacy_redactions_total"] == 0

    # 3. Session 2 at 5:15 PM (different timestamp and date string)
    payload_session_2 = {
        "model": "claude-3-5-sonnet-20241022",
        "system": "You are Claude Code.\nCurrent date: Saturday, September 5, 2026\nCurrent time: 2026-09-05T17:15:32Z\nGit user: developer@mycompany.com",
        "messages": [{"role": "user", "content": "Hi"}],
        "tools": [{"name": "bash", "description": "Run bash command"}]
    }

    res_2 = client.post("/v1/messages", json=payload_session_2, headers={"x-api-key": "test-key"})
    assert res_2.status_code == 200

    # Must be an L1 Exact HIT!
    assert res_2.headers.get("X-Cache-Status") == "HIT_EXACT"
    assert res_2.headers.get("X-Cache-Similarity") == "1.0000"
    tokens_saved = int(res_2.headers.get("X-Tokens-Saved", 0))
    assert tokens_saved > 0

    # Verify stats after cache hit
    stats_2 = client.get("/v1/cache/stats").json()
    assert stats_2["enterprise_engine"]["privacy_redactions_total"] == 0
    assert stats_2["financial_telemetry"]["total_tokens_saved"] > 0

    # 4. User sends actual PII in their message
    payload_user_pii = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Please send report to user@company.com or call 555-123-4567"}]
    }
    with patch("server.upstream.upstream_client.forward_anthropic_messages", new=AsyncMock(return_value=(200, mock_anthropic_reply, {}))):
        client.post("/v1/messages", json=payload_user_pii, headers={"x-api-key": "test-key"})

    # Real user PII must be detected and incremented
    stats_3 = client.get("/v1/cache/stats").json()
    assert stats_3["enterprise_engine"]["privacy_redactions_total"] == 2
