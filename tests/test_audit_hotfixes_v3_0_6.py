import pytest
import asyncio
import json
from starlette.testclient import TestClient

from server.gateway import app, is_allowed_redirect_uri, get_cors_headers
from server.stream_replayer import StreamReplayer
from core.radix_tree import RadixPrefixTree
import server.mcp_server as mcp_compat
from mcp.server import process_mcp_jsonrpc


def test_stream_replayer_anthropic_stream():
    """Verify StreamReplayer generates valid Anthropic SSE stream chunks."""
    entry_payload = {
        "id": "msg_test_123",
        "model": "claude-3-5-sonnet",
        "content": [{"type": "text", "text": "Hello world!"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 15, "output_tokens": 3}
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
    assert "event: content_block_delta" in combined
    assert "event: content_block_stop" in combined
    assert "event: message_delta" in combined
    assert "event: message_stop" in combined
    assert "Hello " in combined
    assert "world!" in combined


def test_radix_tree_clear_and_breakpoint_cap():
    """Verify RadixPrefixTree.clear() and 4-breakpoint ceiling."""
    tree = RadixPrefixTree(max_nodes=100)
    messages = [
        {"role": "user", "content": "What is Python?"},
        {"role": "assistant", "content": "Python is a language."}
    ]
    node = tree.insert_conversation(messages, {"answer": "Python"})
    assert tree.total_nodes > 1
    tree.clear()
    assert tree.total_nodes == 1
    assert len(tree.root.children) == 0

    # Test 4-breakpoint limit
    big_turn = "word " * 1200
    long_conversation = [
        {"role": "user", "content": big_turn},
        {"role": "assistant", "content": big_turn},
        {"role": "user", "content": big_turn},
        {"role": "assistant", "content": big_turn},
        {"role": "user", "content": big_turn},
        {"role": "assistant", "content": big_turn},
    ]
    aligned = tree.align_ephemeral_cache_blocks(long_conversation, block_size_tokens=500)
    total_breakpoints = 0
    for turn in aligned:
        content = turn.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "cache_control" in block:
                    total_breakpoints += 1
    assert total_breakpoints <= 4, f"Expected <= 4 breakpoints, got {total_breakpoints}"


def test_mcp_server_compatibility_shim():
    """Verify server/mcp_server.py compatibility shim."""
    assert hasattr(mcp_compat, "process_mcp_jsonrpc")
    assert hasattr(mcp_compat, "TOOLS_METADATA")
    assert hasattr(mcp_compat, "run_stdio_server")
    assert hasattr(mcp_compat, "handle_tool_call")


def test_mcp_jsonrpc_batch_and_notifications():
    """Verify JSON-RPC 2.0 batch support and notification handling."""
    # Batch query
    batch_req = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    ]
    batch_res = process_mcp_jsonrpc(batch_req)
    assert isinstance(batch_res, list)
    assert len(batch_res) == 2
    assert batch_res[0]["id"] == 1
    assert batch_res[1]["id"] == 2

    # Notification (no "id") -> returns None
    notif_req = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    notif_res = process_mcp_jsonrpc(notif_req)
    assert notif_res is None

    # Stubs
    res_resources = process_mcp_jsonrpc({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
    assert res_resources["result"]["resources"] == []
    res_prompts = process_mcp_jsonrpc({"jsonrpc": "2.0", "id": 4, "method": "prompts/list"})
    assert res_prompts["result"]["prompts"] == []


def test_readyz_and_purge_security():
    """Verify /readyz endpoint and purge method restriction."""
    client = TestClient(app)
    # /readyz
    r_ready = client.get("/readyz")
    assert r_ready.status_code == 200

    # /v1/cache/purge with GET must be rejected (405 Method Not Allowed)
    r_purge_get = client.get("/v1/cache/purge")
    assert r_purge_get.status_code == 405

    # /v1/cache/invalidate-tag with GET must be rejected (405 Method Not Allowed)
    r_inval_get = client.get("/v1/cache/invalidate-tag?tag=test")
    assert r_inval_get.status_code == 405


def test_oauth_and_cors_security_tightening():
    """Verify redirect_uri validation and CORS hostname matching."""
    # Open redirect attempt
    assert not is_allowed_redirect_uri("https://evil-attacker.com/oauth/callback")
    assert not is_allowed_redirect_uri("javascript:alert(1)")
    assert is_allowed_redirect_uri("https://claude.ai/api/oauth")
    assert is_allowed_redirect_uri("https://omnicache.rawwgrid.com/dashboard")
    assert is_allowed_redirect_uri("http://localhost:8000/callback")
    assert is_allowed_redirect_uri("/dashboard")

    # CORS origin check: rogue subdomain/suffix match
    from starlette.datastructures import Headers
    class MockRequest:
        def __init__(self, origin):
            self.headers = Headers({"origin": origin})

    cors_bad = get_cors_headers(MockRequest("https://attackerrawwgrid.com"))
    # Should NOT reflect attackerrawwgrid.com as allowed
    assert cors_bad["Access-Control-Allow-Origin"] != "https://attackerrawwgrid.com"

    cors_good = get_cors_headers(MockRequest("https://omnicache.rawwgrid.com"))
    assert cors_good["Access-Control-Allow-Origin"] == "https://omnicache.rawwgrid.com"
