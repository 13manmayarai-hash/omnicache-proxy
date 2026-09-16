"""
Test Suite for v3.1.0 Roadmap Enhancements:
1. Streaming tool-call persistence in Anthropic/OpenAI recorders
2. MCP streamable HTTP SSE response queue forwarding
3. SQLite WAL periodic checkpointing
4. Dynamic ANN dimension alignment with active embedder
"""

import pytest
import asyncio
import json
import sqlite3
from starlette.testclient import TestClient

from server.gateway import app, MCP_ACTIVE_SESSIONS, process_mcp_jsonrpc
from server.stream_replayer import StreamReplayer
from core.vector_cache import cache_instance, CacheEntry
from core.embeddings import FastSemanticEmbedder
from core.ann_index import ANNIndexFactory
from persistence.snapshot_store import SnapshotStore
from server.quotas import quota_manager


def test_stream_replayer_anthropic_tool_use_streaming():
    """Verify StreamReplayer accurately emits tool_use blocks in Anthropic SSE stream."""
    entry_payload = {
        "id": "msg_tool_test",
        "model": "claude-3-5-sonnet",
        "content": [
            {"type": "text", "text": "Let me calculate that for you."},
            {
                "type": "tool_use",
                "id": "toolu_calc_42",
                "name": "calculator",
                "input": {"expression": "21 * 2"}
            }
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 20, "output_tokens": 15}
    }

    async def run():
        chunks = []
        async for chunk in StreamReplayer.replay_cached_anthropic_stream(entry_payload, tokens_per_sec=0):
            chunks.append(chunk)
        return chunks

    events = asyncio.run(run())
    combined = "".join(events)
    assert "event: message_start" in combined
    assert "event: content_block_start" in combined
    assert "tool_use" in combined
    assert "toolu_calc_42" in combined
    assert "calculator" in combined
    assert "input_json_delta" in combined
    assert "21 * 2" in combined
    assert "event: content_block_stop" in combined
    assert "event: message_delta" in combined
    assert "tool_use" in combined
    assert "event: message_stop" in combined


def test_stream_replayer_openai_tool_calls_streaming():
    """Verify StreamReplayer emits tool_calls in OpenAI SSE stream."""
    entry_payload = {
        "id": "chatcmpl_tool_test",
        "model": "gpt-4o",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Fetching stock price...",
                "tool_calls": [{
                    "id": "call_stock_1",
                    "type": "function",
                    "function": {
                        "name": "get_stock_quote",
                        "arguments": '{"ticker": "AAPL"}'
                    }
                }]
            },
            "finish_reason": "tool_calls"
        }]
    }

    async def run():
        chunks = []
        async for chunk in StreamReplayer.replay_cached_stream(entry_payload, tokens_per_sec=0):
            chunks.append(chunk)
        return chunks

    events = asyncio.run(run())
    combined = "".join(events)
    assert "data: " in combined
    assert "tool_calls" in combined
    assert "get_stock_quote" in combined
    assert "AAPL" in combined
    assert "data: [DONE]" in combined


def test_mcp_sse_session_queue_forwarding():
    """Verify POST /mcp pushes responses into active MCP session queue for SSE subscribers."""
    client = TestClient(app)
    session_id = "test_sse_forwarding_session_42"

    # Initialize active session
    queue = asyncio.Queue()
    MCP_ACTIVE_SESSIONS[session_id] = {
        "created_at": 1000.0,
        "org_id": "test_mcp_org",
        "queue": queue
    }

    try:
        ping_req = {
            "jsonrpc": "2.0",
            "id": "req-ping-sse-1",
            "method": "ping"
        }
        resp = client.post(
            f"/mcp?sessionId={session_id}",
            json=ping_req,
            headers={"x-api-key": "default"}
        )
        assert resp.status_code == 200
        assert resp.json().get("id") == "req-ping-sse-1"

        # Verify message was also pushed into the active session queue for SSE listeners
        assert not queue.empty()
        queued_msg = queue.get_nowait()
        assert queued_msg.get("id") == "req-ping-sse-1"
        assert "result" in queued_msg
    finally:
        MCP_ACTIVE_SESSIONS.pop(session_id, None)


def test_sqlite_wal_checkpoint_and_periodic_maintenance(tmp_path):
    """Verify SQLite WAL checkpointing executes cleanly without locking errors."""
    db_file = str(tmp_path / "test_wal_checkpoint.db")
    store = SnapshotStore(db_path=db_file, enable_write_behind=False)

    # Verify checkpoint API
    success = store.checkpoint(mode="PASSIVE")
    assert success is True

    # Insert a dummy cache entry
    dummy_entry = CacheEntry(
        key="test_wal_k1",
        org_id="org_test",
        model="gpt-4o",
        user_prompt="test prompt",
        system_prompt="",
        schema_hash="no_schema",
        tools_hash="no_tools",
        vector=[0.1] * 512,
        response_payload={"choices": [{"message": {"role": "assistant", "content": "hi"}}]},
        ttl_seconds=3600
    )
    store.persist_entry(dummy_entry, synchronous=True)

    # Flush triggers WAL checkpoint
    store.flush()

    # Verify query works cleanly after checkpoint
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.execute("SELECT key, org_id FROM cache_records WHERE key = ?", ("test_wal_k1",))
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == "test_wal_k1"
    conn.close()

    # Clean close truncates WAL
    store.close()


def test_ann_quantized_dimension_alignment():
    """Verify FastSemanticEmbedder dynamically exposes dimension property to ANN factory."""
    dims = FastSemanticEmbedder.DIMENSIONS
    assert isinstance(dims, int)
    assert dims in (256, 384, 512)

    # Verify ANNIndexFactory accepts this dimension without mismatch
    index = ANNIndexFactory.create(dimensions=dims)
    assert index.dimensions == dims


def test_anthropic_gateway_cache_hit_preserves_tool_use():
    """Verify /v1/messages returns complete tool_use blocks on both non-stream and stream cache hits."""
    client = TestClient(app)
    quota_manager.register_key("tool_test_key", team_name="Tool Team", org_id="tool_org")
    cache_instance.purge()
    
    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "What is the weather in Tokyo?"}],
        "temperature": 0.0
    }
    response_payload = {
        "id": "msg_cached_tool_tokyo",
        "type": "message",
        "role": "assistant",
        "model": "claude-3-5-sonnet-20241022",
        "content": [
            {"type": "text", "text": "Checking weather for Tokyo..."},
            {
                "type": "tool_use",
                "id": "toolu_weather_tokyo",
                "name": "get_weather",
                "input": {"city": "Tokyo"}
            }
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 12, "output_tokens": 24}
    }
    cache_instance.store(payload, response_payload, org_id="tool_org", is_exact_tokens=True)

    # 1. Non-streaming request -> verify cache hit and tool_use structure preserved
    resp = client.post(
        "/v1/messages",
        json={**payload, "stream": False},
        headers={"x-api-key": "tool_test_key"}
    )
    assert resp.status_code == 200
    assert resp.headers.get("x-cache-status") in ("HIT_EXACT", "HIT_SEMANTIC")
    data = resp.json()
    assert data["stop_reason"] == "tool_use"
    assert len(data["content"]) == 2
    assert data["content"][0]["type"] == "text"
    assert data["content"][1]["type"] == "tool_use"
    assert data["content"][1]["name"] == "get_weather"
    assert data["content"][1]["input"] == {"city": "Tokyo"}

    # 2. Streaming request -> verify SSE cache hit replays tool_use
    with client.stream(
        "POST",
        "/v1/messages",
        json={**payload, "stream": True},
        headers={"x-api-key": "tool_test_key"}
    ) as stream_resp:
        assert stream_resp.status_code == 200
        assert stream_resp.headers.get("x-cache-status") in ("HIT_EXACT", "HIT_SEMANTIC")
        stream_text = "".join(stream_resp.iter_text())
        assert "event: content_block_start" in stream_text
        assert "tool_use" in stream_text
        assert "toolu_weather_tokyo" in stream_text
        assert "get_weather" in stream_text
        assert "Tokyo" in stream_text
        assert "event: message_delta" in stream_text
        assert "tool_use" in stream_text
        assert "event: message_stop" in stream_text


def test_openai_gateway_cache_hit_preserves_tool_calls():
    """Verify /v1/chat/completions returns tool_calls on both non-stream and stream cache hits."""
    client = TestClient(app)
    quota_manager.register_key("tool_test_key", team_name="Tool Team", org_id="tool_org")
    cache_instance.purge()

    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Book a table for two at 7pm"}],
        "temperature": 0.0
    }
    response_payload = {
        "id": "chatcmpl_tool_table_1",
        "object": "chat.completion",
        "model": "gpt-4o",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Booking table...",
                "tool_calls": [{
                    "id": "call_reserve_123",
                    "type": "function",
                    "function": {
                        "name": "reserve_table",
                        "arguments": '{"party_size": 2, "time": "19:00"}'
                    }
                }]
            },
            "finish_reason": "tool_calls"
        }],
        "usage": {"prompt_tokens": 15, "completion_tokens": 20, "total_tokens": 35}
    }
    cache_instance.store(payload, response_payload, org_id="tool_org", is_exact_tokens=True)

    # 1. Non-streaming cache hit
    resp = client.post(
        "/v1/chat/completions",
        json={**payload, "stream": False},
        headers={"x-api-key": "tool_test_key"}
    )
    assert resp.status_code == 200
    assert resp.headers.get("x-cache-status") in ("HIT_EXACT", "HIT_SEMANTIC")
    data = resp.json()
    msg = data["choices"][0]["message"]
    assert "tool_calls" in msg
    assert msg["tool_calls"][0]["function"]["name"] == "reserve_table"

    # 2. Streaming cache hit
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={**payload, "stream": True},
        headers={"x-api-key": "tool_test_key"}
    ) as stream_resp:
        assert stream_resp.status_code == 200
        assert stream_resp.headers.get("x-cache-status") in ("HIT_EXACT", "HIT_SEMANTIC")
        stream_text = "".join(stream_resp.iter_text())
        assert "tool_calls" in stream_text
        assert "reserve_table" in stream_text
        assert "data: [DONE]" in stream_text

