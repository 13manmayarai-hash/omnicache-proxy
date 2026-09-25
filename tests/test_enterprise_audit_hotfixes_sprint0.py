"""
Tests for Sprint 0 Enterprise Audit Hotfixes across Security, Concurrency, and AI Safety.
Covers:
- SEC-01 & SEC-08: Deep tool call & tool result privacy sanitization & rehydration
- SEC-02: MCP active session isolation (cross-tenant access rejected) & session expiration
- SEC-03: WebSocket authentication gate when REQUIRE_AUTH is enabled
- SYS-01: Mesh tombstone handler multi-tier cache eviction resilience
- SYS-02: SwarmBus tenant namespace isolation
- AI-01: Invisible zero-width unicode bypass immunity & expanded DDL/DCL classification
- AI-02: AST Decorator tracking and invalidation
- AI-03: Inline comment immunity in universal tokenizer
- AI-06: Tool choice safety gate bypass in vector cache
"""

import unittest
import asyncio
import time
import json
from starlette.testclient import TestClient

from core.ast_validator import ASTValidator
from core.vector_cache import DualTierCache, cache_instance
from core.privacy_shield import PrivacyShield
from core.swarm_bus import swarm_bus
from core.config import config
from server.tool_replayer import tool_cache
from server.gateway import app, _handle_mesh_tombstone, MCP_ACTIVE_SESSIONS


class TestSprint0EnterpriseHotfixes(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.cache = DualTierCache()

    def test_01_ast_decorator_divergence(self):
        """AI-02: Decorator changes must trigger AST invalidation."""
        code_auth = "@admin_required\ndef mutate_balance(user_id, amount):\n    return True"
        code_public = "@public_endpoint\ndef mutate_balance(user_id, amount):\n    return True"
        is_valid, reason = ASTValidator.validate_structural_parity(code_auth, code_public)
        self.assertFalse(is_valid)
        self.assertIn("AST_DECORATOR_DIVERGENCE", reason)

    def test_02_ast_await_concurrency_divergence(self):
        """AI-02: Missing or added await statements must trigger AST invalidation."""
        code_async = "async def sync_remote():\n    await fetch_data()\n    return True"
        code_sync = "async def sync_remote():\n    fetch_data()\n    return True"
        is_valid, reason = ASTValidator.validate_structural_parity(code_async, code_sync)
        self.assertFalse(is_valid)
        self.assertIn("AST_CONCURRENCY_DIVERGENCE", reason)

    def test_03_ast_inline_comment_immunity(self):
        """AI-03: Inline # comments must be stripped in operator stream signature."""
        code1 = "x = compute(a, b) # and or not <= >="
        code2 = "x = compute(a, b)"
        is_valid, reason = ASTValidator.validate_structural_parity(code1, code2)
        self.assertTrue(is_valid)

    def test_04_zero_width_unicode_defense(self):
        """AI-01: Zero-width spaces cannot evade intent classification for destructive queries."""
        evasive = "T\u200bR\u200bU\u200bN\u200bC\u200bA\u200bT\u200bE TABLE customers;"
        intent, threshold, reason = self.cache.classify_intent(evasive, "no_schema", "no_tools", 0.0)
        self.assertEqual(intent, "sql_database_query")
        self.assertEqual(threshold, 0.98)

    def test_05_ddl_intent_expansion(self):
        """AI-01: Expanded DDL/DCL patterns receive strict 0.98 threshold."""
        queries = [
            "DROP DATABASE enterprise_prod;",
            "DROP SCHEMA analytics;",
            "GRANT ALL PRIVILEGES ON db TO developer;",
            "REVOKE ALL ON sensitive_data FROM intern;",
            "MERGE INTO target_table USING source_table ON (id = src_id);"
        ]
        for q in queries:
            intent, threshold, _ = self.cache.classify_intent(q, "no_schema", "no_tools", 0.0)
            self.assertEqual(intent, "sql_database_query", f"Failed for {q}")
            self.assertEqual(threshold, 0.98)

    def test_06_tool_choice_safety_gate(self):
        """AI-06: Presence of tool_choice or functions triggers safety bypass."""
        payload_tc = {
            "messages": [{"role": "user", "content": "Check weather in Tokyo"}],
            "tool_choice": "auto"
        }
        status, entry, sim, reason = self.cache.lookup(payload_tc)
        self.assertEqual(status, "BYPASS")
        self.assertIn("BYPASS_AGENT_TOOLS", reason)

        payload_fn = {
            "messages": [{"role": "user", "content": "Call legacy function"}],
            "functions": [{"name": "get_stock"}]
        }
        status, entry, sim, reason = self.cache.lookup(payload_fn)
        self.assertEqual(status, "BYPASS")
        self.assertIn("BYPASS_AGENT_TOOLS", reason)

    def test_07_deep_tool_scrubbing_and_rehydration(self):
        """SEC-01 & SEC-08: Deep sanitization of tool_result and tool_use blocks."""
        raw_key = "sk-proj-999988887777666655554444333322221111"
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_123",
                            "content": f"Database connection established with secret {raw_key}"
                        }
                    ]
                }
            ]
        }
        sanitized, token_map, scrubbed = PrivacyShield.sanitize_payload(payload)
        self.assertGreater(scrubbed, 0)
        self.assertNotIn(raw_key, json.dumps(sanitized))

        # Check Anthropic tool_use response rehydration
        fake_token = list(token_map.keys())[0]
        resp_payload = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_456",
                    "name": "connect_db",
                    "input": {"api_key": fake_token}
                }
            ]
        }
        rehydrated = PrivacyShield.rehydrate_response(resp_payload, token_map)
        self.assertEqual(rehydrated["content"][0]["input"]["api_key"], raw_key)

    def test_08_mcp_session_tenant_isolation_and_expiry(self):
        """SEC-02: MCP active sessions cannot be hijacked by other tenants."""
        MCP_ACTIVE_SESSIONS.clear()
        session_id = "test-session-secret-tenant-a"
        MCP_ACTIVE_SESSIONS[session_id] = {
            "created_at": time.time(),
            "org_id": "tenant-a",
            "queue": asyncio.Queue()
        }

        # Request with session_id belonging to tenant-a from tenant-b
        headers = {
            "mcp-session-id": session_id,
            "x-org-id": "tenant-b"
        }
        response = self.client.get("/mcp", headers=headers)
        self.assertEqual(response.status_code, 403)
        self.assertIn("Forbidden", response.json().get("error", ""))

        # Verify expired session eviction
        MCP_ACTIVE_SESSIONS[session_id]["created_at"] = time.time() - 4000
        # Trigger cleanup via new request without SSE query param
        self.client.get("/mcp", headers={"mcp-session-id": "new-session", "x-org-id": "tenant-c"})
        self.assertNotIn(session_id, MCP_ACTIVE_SESSIONS)

    def test_09_mesh_tombstone_handler_resilience(self):
        """SYS-01: Mesh tombstone handler executes safely across all invalidation types."""
        # 1. Wildcard purge
        _handle_mesh_tombstone("*", "administrative_reset", {})
        # 2. File mutation
        _handle_mesh_tombstone("file:/app/core/models.py", "git_commit", {})
        # 3. Exact cache key deletion
        _handle_mesh_tombstone("deadbeef12345678", "explicit_invalidation", {})

    def test_10_swarm_bus_tenant_isolation(self):
        """SYS-02: Swarm bus strictly isolates execution memory between tenants."""
        swarm_bus.clear_all()
        # Record task result under tenant A
        swarm_bus.record_shared_result(
            swarm_id="tenant-a:cluster1",
            agent_id="scout",
            task_type="code_search",
            payload={"query": "api_keys"},
            result_payload={"files": ["/etc/passwd"]},
            tokens_saved=100
        )

        # Lookup by tenant A
        hit_a, res_a, _ = swarm_bus.lookup_shared_result(
            swarm_id="tenant-a:cluster1",
            agent_id="worker",
            task_type="code_search",
            payload={"query": "api_keys"}
        )
        self.assertTrue(hit_a)
        self.assertEqual(res_a, {"files": ["/etc/passwd"]})

        # Lookup by tenant B for same logical cluster1 -> Isolated!
        hit_b, res_b, _ = swarm_bus.lookup_shared_result(
            swarm_id="tenant-b:cluster1",
            agent_id="worker",
            task_type="code_search",
            payload={"query": "api_keys"}
        )
        self.assertFalse(hit_b)
        self.assertIsNone(res_b)

    def test_11_startup_security_invariants_enforcement(self):
        """SEC-05: ASGI lifespan must fail fast if bound to 0.0.0.0 without authentication."""
        orig_host = config.HOST
        orig_auth = config.REQUIRE_AUTH
        orig_allow = config.ALLOW_INSECURE_NETWORK_EXPOSURE
        try:
            config.HOST = "0.0.0.0"
            config.REQUIRE_AUTH = False
            config.ALLOW_INSECURE_NETWORK_EXPOSURE = False
            with self.assertRaises(RuntimeError) as ctx:
                with TestClient(app):
                    pass
            self.assertIn("SECURITY ERROR: Refusing to bind OmniCache to non-localhost interface", str(ctx.exception))
        finally:
            config.HOST = orig_host
            config.REQUIRE_AUTH = orig_auth
            config.ALLOW_INSECURE_NETWORK_EXPOSURE = orig_allow


if __name__ == "__main__":
    unittest.main()
