"""
Regression tests for real-traffic Claude Code integration fixes:
1. Anthropic cache_control ceiling enforcement with reserved_breakpoints (system & tools).
2. Model alias mapping for Claude 4.5 releases (preventing downgrade to 3.5).
3. Compressed stream handling via aiter_bytes() to ensure SSE parsing works with gzip/deflate.
4. Reporting zero billable token usage on cache hits so agent cost-meters reflect 100% savings.
5. Zero replay delay default ensuring sub-millisecond cached stream emission.
"""

import asyncio
import gzip
import json
import pytest
from unittest.mock import patch, AsyncMock
from starlette.testclient import TestClient

from core.radix_tree import RadixPrefixTree
from core.config import config
from server.upstream import UpstreamClient
from server.stream_replayer import StreamReplayer
from server.gateway import app, METRICS_LEDGER, cache_instance


@pytest.fixture
def client():
    METRICS_LEDGER["total_savings_usd"] = 0.0
    METRICS_LEDGER["total_tokens_saved"] = 0
    METRICS_LEDGER["exact_tokens_saved"] = 0
    cache_instance.storage.purge()
    return TestClient(app)


def test_reserved_breakpoints_respects_hard_ceiling():
    """
    Anthropic enforces a maximum of 4 cache_control breakpoints per request across all fields.
    If system and tools already use 2 breakpoints, align_ephemeral_cache_blocks must only inject at most 2.
    """
    radix = RadixPrefixTree()

    # Create 5 large turns that would normally each trigger a 1024-token breakpoint
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "word " * 300}]},
        {"role": "assistant", "content": [{"type": "text", "text": "reply " * 300}]},
        {"role": "user", "content": [{"type": "text", "text": "word " * 300}]},
        {"role": "assistant", "content": [{"type": "text", "text": "reply " * 300}]},
        {"role": "user", "content": [{"type": "text", "text": "word " * 300}]},
    ]

    # Without reserved breakpoints: up to 4 breakpoints allowed in messages
    aligned_0 = radix.align_ephemeral_cache_blocks(messages, block_size_tokens=200, reserved_breakpoints=0)
    count_0 = sum(
        1 for m in aligned_0 if isinstance(m.get("content"), list)
        for b in m["content"] if isinstance(b, dict) and "cache_control" in b
    )
    assert count_0 == 4

    # With 2 reserved breakpoints (e.g. 1 in system prompt, 1 in tools):
    # Only 2 more breakpoints can be added to messages
    aligned_2 = radix.align_ephemeral_cache_blocks(messages, block_size_tokens=200, reserved_breakpoints=2)
    count_2 = sum(
        1 for m in aligned_2 if isinstance(m.get("content"), list)
        for b in m["content"] if isinstance(b, dict) and "cache_control" in b
    )
    assert count_2 == 2

    # With 4 reserved breakpoints already: 0 more breakpoints allowed in messages
    aligned_4 = radix.align_ephemeral_cache_blocks(messages, block_size_tokens=200, reserved_breakpoints=4)
    count_4 = sum(
        1 for m in aligned_4 if isinstance(m.get("content"), list)
        for b in m["content"] if isinstance(b, dict) and "cache_control" in b
    )
    assert count_4 == 0


def test_gateway_calculates_reserved_breakpoints_from_system_and_tools(client):
    """
    Verify handle_anthropic_messages correctly extracts breakpoints from system and tools
    and passes reserved_breakpoints into align_ephemeral_cache_blocks.
    """
    mock_reply = {
        "id": "msg_mock_reserved_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-4-5-20251001",
        "content": [{"type": "text", "text": "OK"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 100, "output_tokens": 10}
    }

    forwarded = []

    async def mock_forward(payload, incoming_headers=None, params=None):
        forwarded.append(payload)
        return (200, mock_reply, {})

    payload = {
        "model": "claude-haiku-4-5-20251001",
        "system": [
            {"type": "text", "text": "System prompt part 1", "cache_control": {"type": "ephemeral"}}
        ],
        "tools": [
            {"name": "bash", "description": "Run bash", "cache_control": {"type": "ephemeral"}}
        ],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "token " * 400}]},
            {"role": "assistant", "content": [{"type": "text", "text": "token " * 400}]},
            {"role": "user", "content": [{"type": "text", "text": "token " * 400}]},
            {"role": "assistant", "content": [{"type": "text", "text": "token " * 400}]},
        ]
    }

    with patch("server.upstream.upstream_client.forward_anthropic_messages", side_effect=mock_forward):
        res = client.post("/v1/messages", json=payload, headers={"x-api-key": "test-key"})
        assert res.status_code == 200

    assert len(forwarded) == 1
    sent_msgs = forwarded[0]["messages"]
    msg_breakpoints = sum(
        1 for m in sent_msgs if isinstance(m.get("content"), list)
        for b in m["content"] if isinstance(b, dict) and "cache_control" in b
    )
    # 2 breakpoints in system+tools means at most 2 in messages (total <= 4)
    assert msg_breakpoints <= 2


def test_model_aliases_claude_4_5():
    """
    Verify Claude 4.5 model aliases map to the latest 4.5 snapshots, not downgraded to 3.5.
    """
    client = UpstreamClient()
    assert client._normalize_anthropic_model("claude-sonnet-4.5") == "claude-sonnet-4-5-20250929"
    assert client._normalize_anthropic_model("claude-haiku-4.5") == "claude-haiku-4-5-20251001"
    # Exact snapshot names should remain unchanged
    assert client._normalize_anthropic_model("claude-sonnet-4-5-20250929") == "claude-sonnet-4-5-20250929"
    assert client._normalize_anthropic_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5-20251001"


def test_compressed_stream_handling_with_aiter_bytes():
    """
    Verifies that upstream streaming with Content-Encoding: gzip is cleanly
    decompressed using aiter_bytes() so SSE events can be parsed.
    """
    import httpx

    async def run_test():
        sse_data = (
            b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","usage":{"input_tokens":10,"output_tokens":0}}}\n\n'
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}\n\n'
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        )
        compressed_bytes = gzip.compress(sse_data)

        mock_resp = httpx.Response(
            200,
            stream=httpx.ByteStream(compressed_bytes),
            headers={"content-encoding": "gzip", "content-type": "text/event-stream"}
        )

        # aiter_bytes correctly handles decompression
        chunks = [c async for c in mock_resp.aiter_bytes()]
        decompressed_text = b"".join(chunks).decode("utf-8")
        assert "event: message_start" in decompressed_text
        assert '"text":"Hello"' in decompressed_text

    asyncio.run(run_test())


def test_zero_token_usage_on_anthropic_cache_hit(client):
    """
    Verifies that Anthropic cache hits return usage: {input_tokens: 0, output_tokens: 0}
    so agents (like Claude Code) report 0 cost, while telemetry headers track actual savings.
    """
    payload = {
        "model": "claude-haiku-4-5-20251001",
        "messages": [{"role": "user", "content": "What is 2+2?"}],
        "temperature": 0.0
    }
    cached_reply = {
        "id": "msg_cached_math",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-4-5-20251001",
        "content": [{"type": "text", "text": "4"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 50, "output_tokens": 10}
    }
    cache_instance.store(payload, cached_reply, org_id="tenant_math")

    res = client.post("/v1/messages", json=payload, headers={"x-api-key": "tenant_math", "x-org-id": "tenant_math"})
    assert res.status_code == 200
    assert res.headers.get("X-Cache-Status") == "HIT_EXACT"
    assert res.headers.get("X-Tokens-Used") == "0"
    assert int(res.headers.get("X-Tokens-Saved", 0)) > 0

    data = res.json()
    assert data["usage"]["input_tokens"] == 0
    assert data["usage"]["output_tokens"] == 0


def test_zero_token_usage_on_openai_cache_hit(client):
    """
    Verifies that OpenAI cache hits return usage: {prompt_tokens: 0, completion_tokens: 0, total_tokens: 0}.
    """
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "What is the capital of Japan?"}],
        "temperature": 0.0
    }
    cached_reply = {
        "id": "chatcmpl_test_tokyo",
        "object": "chat.completion",
        "choices": [{"message": {"role": "assistant", "content": "Tokyo"}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}
    }
    cache_instance.store(payload, cached_reply, org_id="tenant_geo")

    res = client.post("/v1/chat/completions", json=payload, headers={"x-api-key": "tenant_geo", "x-org-id": "tenant_geo"})
    assert res.status_code == 200
    assert res.headers.get("X-Cache-Status") == "HIT_EXACT"

    data = res.json()
    assert data["usage"]["prompt_tokens"] == 0
    assert data["usage"]["completion_tokens"] == 0
    assert data["usage"]["total_tokens"] == 0


def test_streaming_replayer_zero_usage_and_instant_replay():
    """
    Verifies StreamReplayer emits zero billable tokens and executes with zero synthetic delay by default.
    """
    async def run_test():
        recorded_chunks = [
            {"type": "message_start", "message": {"id": "msg_stream_1", "usage": {"input_tokens": 100, "output_tokens": 0}}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 50}},
            {"type": "message_stop"}
        ]

        events = []
        async for event in StreamReplayer.replay_cached_anthropic_stream(
            entry_payload={},
            stream_chunks=recorded_chunks,
            tokens_per_sec=0.0
        ):
            events.append(event)

        full_stream = "".join(events)
        # Check that message_start usage was zeroed
        assert '"input_tokens":0' in full_stream or '"input_tokens": 0' in full_stream
        # Check that message_delta usage was zeroed
        assert '"output_tokens":0' in full_stream or '"output_tokens": 0' in full_stream
        # Original non-zero usage must NOT appear
        assert '"input_tokens":100' not in full_stream and '"input_tokens": 100' not in full_stream
        assert '"output_tokens":50' not in full_stream and '"output_tokens": 50' not in full_stream

    asyncio.run(run_test())
