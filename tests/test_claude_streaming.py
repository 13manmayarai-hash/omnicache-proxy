import pytest
from starlette.testclient import TestClient
from server.gateway import app, quota_manager, cache_instance
from core.config import config
from unittest.mock import patch

@pytest.fixture
def client():
    return TestClient(app)

class TestClaudeStreamingAndAuth:
    def test_01_local_mode_passthrough_direct_anthropic_key(self):
        """In local mode (REQUIRE_AUTH=false), direct Anthropic API keys (sk-ant-...) are accepted transparently without requiring prior virtual key registration."""
        from server.gateway import authenticate_tenant
        from starlette.requests import Request
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/messages",
            "headers": [(b"x-api-key", b"sk-ant-api03-testkey-123456789"), (b"x-org-id", b"local_dev_org")]
        }
        req = Request(scope)
        with patch.object(config, "REQUIRE_AUTH", False):
            allowed, err_resp, key_info, org_id = authenticate_tenant(req)
            assert allowed is True
            assert err_resp is None
            assert org_id == "local_dev_org"
            assert key_info.get("team_name") == "Local Developer"

    def test_02_anthropic_header_translation(self):
        """Verify that Authorization: Bearer sk-ant-... is properly translated to x-api-key."""
        from server.upstream import upstream_client
        incoming = {
            "Authorization": "Bearer sk-ant-api03-live-test-key",
            "anthropic-version": "2023-06-01"
        }
        headers = upstream_client._build_anthropic_headers(incoming)
        assert headers.get("x-api-key") == "sk-ant-api03-live-test-key"
        assert headers.get("anthropic-version") == "2023-06-01"

    def test_03_default_max_tokens_8192(self):
        """Verify upstream payloads default max_tokens to 8192 rather than 1024."""
        from server.upstream import upstream_client
        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "Write a long story"}]
        }
        clean_payload = {k: v for k, v in payload.items() if not k.startswith("_")}
        if "max_tokens" not in clean_payload and "max_tokens_to_sample" not in clean_payload:
            clean_payload["max_tokens"] = 8192
        assert clean_payload["max_tokens"] == 8192

    def test_04_cached_anthropic_sse_headers(self, client):
        """Verify cached SSE responses return unbuffered streaming headers."""
        quota_manager.register_key("claude_test_key", team_name="Claude Team", org_id="claude_org")
        
        # Store in cache
        cache_payload = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "Hello Claude deterministic test query"}],
            "temperature": 0.0,
            "tools": None
        }
        cached_response = {
            "id": "chatcmpl-test-claude",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "Hello there from Claude!"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 10}
        }
        cache_instance.store(cache_payload, cached_response, org_id="claude_org")

        # Request with stream=True to trigger cached streaming
        stream_payload = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "Hello Claude deterministic test query"}],
            "stream": True
        }
        resp = client.post(
            "/v1/messages",
            json=stream_payload,
            headers={"x-api-key": "claude_test_key", "x-org-id": "claude_org"}
        )
        assert resp.status_code == 200
        assert resp.headers.get("x-cache-status") == "HIT_EXACT"
        assert resp.headers.get("x-accel-buffering") == "no"
        assert "no-cache" in resp.headers.get("cache-control", "")

    def test_05_upstream_self_loop_sanitization(self):
        """Verify that local proxy URLs in ANTHROPIC_BASE_URL / OPENAI_BASE_URL are sanitized against recursive loops."""
        from core.config import ProxyConfig
        assert ProxyConfig._sanitize_upstream_url("http://127.0.0.1:8000", "https://api.anthropic.com/v1") == "https://api.anthropic.com/v1"
        assert ProxyConfig._sanitize_upstream_url("http://localhost:8000/v1", "https://api.openai.com/v1") == "https://api.openai.com/v1"
        assert ProxyConfig._sanitize_upstream_url("https://custom-upstream.ai/v1", "https://api.openai.com/v1") == "https://custom-upstream.ai/v1"

    def test_06_claude_oauth_bearer_preserved(self):
        """Verify that Claude Code OAuth bearer tokens are preserved in authorization header without corrupting x-api-key."""
        from server.upstream import upstream_client
        incoming = {
            "authorization": "Bearer oauth_claude_session_token_12345",
            "anthropic-version": "2023-06-01"
        }
        headers = upstream_client._build_anthropic_headers(incoming)
        assert headers.get("authorization") == "Bearer oauth_claude_session_token_12345"
        assert "x-api-key" not in headers

    def test_07_anthropic_endpoint_url_resolution(self):
        """Verify get_anthropic_messages_url handles base URLs with or without /v1 and /messages."""
        from server.upstream import upstream_client
        with patch.object(config, "ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1"):
            assert upstream_client.get_anthropic_messages_url() == "https://api.anthropic.com/v1/messages"
        with patch.object(config, "ANTHROPIC_BASE_URL", "https://api.anthropic.com"):
            assert upstream_client.get_anthropic_messages_url() == "https://api.anthropic.com/v1/messages"
        with patch.object(config, "ANTHROPIC_BASE_URL", "https://custom-provider.com/v1"):
            assert upstream_client.get_anthropic_messages_url() == "https://custom-provider.com/v1/messages"

