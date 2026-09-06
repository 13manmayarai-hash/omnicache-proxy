import os
import json
import pytest
from unittest.mock import patch, AsyncMock
from starlette.testclient import TestClient
from server.gateway import app, METRICS_LEDGER
from server.tool_replayer import tool_cache, compact_and_record_agent_tools

@pytest.fixture
def client():
    METRICS_LEDGER["total_savings_usd"] = 0.0
    METRICS_LEDGER["total_tokens_saved"] = 0
    METRICS_LEDGER["agent_tool_recorded_count"] = 0
    METRICS_LEDGER["agent_tool_compacted_tokens"] = 0
    tool_cache.clear()
    return TestClient(app)

def test_anthropic_tool_recording_and_compaction():
    """
    Verifies that when Claude Code sends multiple turns with repeated tool executions:
    1. Tools are auto-recorded in tool_cache.
    2. Older duplicate tool outputs are compacted.
    3. The latest tool output remains completely intact.
    """
    clean_git_status = (
        "On branch main\n"
        "Your branch is up to date with 'origin/main'.\n"
        "nothing to commit, working tree clean\n"
        "Untracked files not present. Everything synchronized.\n"
        "Status report generated cleanly without conflicts."
    )

    anthropic_payload = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {"role": "user", "content": "Check status of git"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Running git status..."},
                    {"type": "tool_use", "id": "toolu_01", "name": "bash", "input": {"command": "git status"}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_01", "content": clean_git_status}
                ]
            },
            {"role": "assistant", "content": "Working tree is clean. Let's do some work."},
            {"role": "user", "content": "Now check git status again to be sure"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Checking status again..."},
                    {"type": "tool_use", "id": "toolu_02", "name": "bash", "input": {"command": "git status"}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_02", "content": clean_git_status}
                ]
            }
        ]
    }

    compacted_payload, tokens_saved, tools_recorded = compact_and_record_agent_tools(anthropic_payload)

    # 1. Verification of Step 1: Auto-Recording
    assert tools_recorded >= 1
    # Check that tool_cache recorded it
    is_hit, cached_out, _ = tool_cache.lookup_tool_call("bash", {"command": "git status"})
    assert is_hit is True
    assert cached_out == clean_git_status

    # 2. Verification of Step 2: Compaction
    assert tokens_saved > 0
    msgs = compacted_payload["messages"]

    # The first tool_result (turn index 2) must be compacted!
    turn_2_content = msgs[2]["content"][0]["content"]
    assert "OmniCache: Output identical to subsequent execution" in turn_2_content
    assert "toolu_02" in turn_2_content

    # The second tool_result (turn index 6, the latest) must remain FULL and intact!
    turn_6_content = msgs[6]["content"][0]["content"]
    assert turn_6_content == clean_git_status


def test_openai_tool_compaction():
    """
    Verifies that OpenAI /v1/chat/completions format also supports auto-recording and compaction.
    """
    big_file_content = "def calculate_hash():\n    return 'abc123xyz456'\n" * 20

    openai_payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": "read config"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_01",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": json.dumps({"path": "core/config.py"})}
                    }
                ]
            },
            {"role": "tool", "tool_call_id": "call_01", "content": big_file_content},
            {"role": "assistant", "content": "I see the code."},
            {"role": "user", "content": "read config again"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_02",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": json.dumps({"path": "core/config.py"})}
                    }
                ]
            },
            {"role": "tool", "tool_call_id": "call_02", "content": big_file_content}
        ]
    }

    compacted_payload, tokens_saved, tools_recorded = compact_and_record_agent_tools(openai_payload)
    assert tools_recorded >= 1
    assert tokens_saved > 0

    msgs = compacted_payload["messages"]
    # First tool message (turn index 2) must be compacted
    assert "OmniCache: Output identical" in msgs[2]["content"]
    # Second tool message (turn index 6) must remain intact
    assert msgs[6]["content"] == big_file_content


def test_end_to_end_gateway_tool_compaction_metrics(client):
    """
    End-to-end integration test: verifies that sending requests with duplicate tool outputs
    updates METRICS_LEDGER and /v1/cache/stats correctly.
    """
    mock_reply = {
        "id": "msg_mock_compact",
        "type": "message",
        "role": "assistant",
        "model": "claude-3-5-sonnet-20241022",
        "content": [{"type": "text", "text": "All done!"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 100, "output_tokens": 10}
    }

    test_output = "Build passed: 106 tests executed successfully with zero failures.\n" * 5

    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {"role": "user", "content": "Run tests"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "bash", "input": {"command": "pytest"}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": test_output}
                ]
            },
            {"role": "assistant", "content": "Tests passed. Running again to verify stability."},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t2", "name": "bash", "input": {"command": "pytest"}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t2", "content": test_output}
                ]
            }
        ]
    }

    with patch("server.upstream.upstream_client.forward_anthropic_messages", new=AsyncMock(return_value=(200, mock_reply, {}))):
        res = client.post("/v1/messages", json=payload, headers={"x-api-key": "test"})
        assert res.status_code == 200

    # Query /v1/cache/stats
    stats_res = client.get("/v1/cache/stats")
    stats = stats_res.json()
    ee = stats["enterprise_engine"]
    fin = stats["financial_telemetry"]

    assert ee["agent_tools_recorded"] >= 1
    assert ee["agent_tokens_compacted"] > 0
    assert fin["total_tokens_saved"] > 0
    assert fin["total_savings_usd"] > 0.0
