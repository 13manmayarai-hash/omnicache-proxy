"""
Test Suite for Phase 4: Remote Authenticated HTTP/JSON-RPC MCP Transport.
"""

import json
import unittest
from starlette.testclient import TestClient
from server.gateway import app
from server.quotas import quota_manager
from core.vector_cache import cache_instance


class TestPhase4RemoteMCP(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        quota_manager.register_key("mcp_tenant_key", team_name="MCP Tenant", org_id="mcp_tenant_org")
        quota_manager.register_key("mcp_tenant_2_key", team_name="MCP Tenant 2", org_id="mcp_tenant_2_org")
        cache_instance.purge()

    def test_01_mcp_get_discovery(self):
        """Verify GET /mcp returns service discovery metadata and tenant context."""
        resp = self.client.get("/mcp", headers={"x-api-key": "mcp_tenant_key"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("service"), "omnicache-mcp")
        self.assertEqual(data.get("tenant_org_id"), "mcp_tenant_org")
        self.assertGreaterEqual(data.get("tools_count", 0), 5)

    def test_02_mcp_initialize_and_ping(self):
        """Verify MCP initialize and ping JSON-RPC 2.0 handshake."""
        init_req = {
            "jsonrpc": "2.0",
            "id": "req-init-1",
            "method": "initialize",
            "params": {"clientInfo": {"name": "cursor", "version": "1.0"}}
        }
        resp = self.client.post("/mcp", json=init_req, headers={"x-api-key": "mcp_tenant_key"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("id"), "req-init-1")
        self.assertEqual(data.get("result", {}).get("serverInfo", {}).get("name"), "omnicache-mcp")

        ping_req = {
            "jsonrpc": "2.0",
            "id": "req-ping-1",
            "method": "ping"
        }
        resp_ping = self.client.post("/mcp", json=ping_req, headers={"x-api-key": "mcp_tenant_key"})
        self.assertEqual(resp_ping.status_code, 200)
        self.assertEqual(resp_ping.json().get("result"), {})

    def test_03_mcp_tools_list(self):
        """Verify MCP tools/list returns complete set of OmniCache tools."""
        list_req = {
            "jsonrpc": "2.0",
            "id": "req-tools-list",
            "method": "tools/list"
        }
        resp = self.client.post("/mcp", json=list_req, headers={"x-api-key": "mcp_tenant_key"})
        self.assertEqual(resp.status_code, 200)
        tools = resp.json().get("result", {}).get("tools", [])
        tool_names = [t["name"] for t in tools]
        self.assertIn("omnicache_query", tool_names)
        self.assertIn("omnicache_store", tool_names)
        self.assertIn("omnicache_search", tool_names)
        self.assertIn("omnicache_invalidate", tool_names)
        self.assertIn("omnicache_stats", tool_names)

    def test_04_mcp_store_query_and_search_lifecycle(self):
        """Verify storing, querying, and semantic searching via remote MCP endpoint."""
        # 1. Store prompt and solution
        store_req = {
            "jsonrpc": "2.0",
            "id": "store-1",
            "method": "tools/call",
            "params": {
                "name": "omnicache_store",
                "arguments": {
                    "prompt": "How do I reverse a linked list in Python?",
                    "answer": "Iterate through the list reversing the next pointers: prev, curr = None, head...",
                    "model": "gpt-4o",
                    "tag": "algorithms"
                }
            }
        }
        resp_store = self.client.post("/mcp", json=store_req, headers={"x-api-key": "mcp_tenant_key"})
        self.assertEqual(resp_store.status_code, 200)
        self.assertIn("Successfully stored", resp_store.json()["result"]["content"][0]["text"])

        # 2. Query exact/semantic hit
        query_req = {
            "jsonrpc": "2.0",
            "id": "query-1",
            "method": "tools/call",
            "params": {
                "name": "omnicache_query",
                "arguments": {
                    "prompt": "How do I reverse a linked list in Python?",
                    "model": "gpt-4o"
                }
            }
        }
        resp_query = self.client.post("/mcp", json=query_req, headers={"x-api-key": "mcp_tenant_key"})
        self.assertEqual(resp_query.status_code, 200)
        query_res = json.loads(resp_query.json()["result"]["content"][0]["text"])
        self.assertIn(query_res["cache_status"], ("HIT_EXACT", "HIT_SEMANTIC"))
        self.assertIn("reversing the next pointers", query_res["cached_response"])

        # 3. Vector semantic search
        search_req = {
            "jsonrpc": "2.0",
            "id": "search-1",
            "method": "tools/call",
            "params": {
                "name": "omnicache_search",
                "arguments": {
                    "query": "reverse singly linked list",
                    "top_k": 3
                }
            }
        }
        resp_search = self.client.post("/mcp", json=search_req, headers={"x-api-key": "mcp_tenant_key"})
        self.assertEqual(resp_search.status_code, 200)
        search_res = json.loads(resp_search.json()["result"]["content"][0]["text"])
        self.assertGreaterEqual(len(search_res), 1)

    def test_05_mcp_tenant_isolation_and_auth(self):
        """Verify remote MCP endpoints reject unauthenticated calls and maintain tenant separation."""
        # Store for Tenant 1
        store_req = {
            "jsonrpc": "2.0",
            "id": "store-t1",
            "method": "tools/call",
            "params": {
                "name": "omnicache_store",
                "arguments": {
                    "prompt": "Secret tenant 1 financial data query",
                    "answer": "Revenue was 100M.",
                    "model": "gpt-4o"
                }
            }
        }
        self.client.post("/mcp", json=store_req, headers={"x-api-key": "mcp_tenant_key"})

        # Tenant 2 query should MISS due to tenant partition
        query_req = {
            "jsonrpc": "2.0",
            "id": "query-t2",
            "method": "tools/call",
            "params": {
                "name": "omnicache_query",
                "arguments": {
                    "prompt": "Secret tenant 1 financial data query",
                    "model": "gpt-4o"
                }
            }
        }
        resp_t2 = self.client.post("/mcp", json=query_req, headers={"x-api-key": "mcp_tenant_2_key"})
        self.assertEqual(resp_t2.status_code, 200)
        res_t2 = json.loads(resp_t2.json()["result"]["content"][0]["text"])
        self.assertEqual(res_t2["cache_status"], "MISS")

    def test_06_mcp_streamable_http_and_session_id(self):
        """Verify Streamable HTTP transport: SSE stream, Mcp-Session-Id, and protocol negotiation."""
        # Test SSE Stream initiation on GET /mcp using streaming context
        with self.client.stream(
            "GET",
            "/mcp?max_events=1",
            headers={
                "x-api-key": "mcp_tenant_key",
                "Accept": "text/event-stream",
                "MCP-Protocol-Version": "2024-11-05"
            }
        ) as resp:
            self.assertEqual(resp.status_code, 200)
            self.assertIn("text/event-stream", resp.headers.get("content-type", ""))
            self.assertIn("Mcp-Session-Id", resp.headers)
            self.assertEqual(resp.headers.get("MCP-Protocol-Version"), "2024-11-05")

            session_id = resp.headers["Mcp-Session-Id"]
            first_chunk = next(resp.iter_text())
            # Verify initial endpoint SSE announcement
            self.assertIn("event: endpoint", first_chunk)
            self.assertIn(f"data: /mcp?sessionId={session_id}", first_chunk)

        # Test POST with session ID header
        init_req = {
            "jsonrpc": "2.0",
            "id": "req-stream-post",
            "method": "ping"
        }
        resp_post = self.client.post(
            "/mcp",
            json=init_req,
            headers={
                "x-api-key": "mcp_tenant_key",
                "Mcp-Session-Id": session_id,
                "MCP-Protocol-Version": "2024-11-05"
            }
        )
        self.assertEqual(resp_post.status_code, 200)
        self.assertEqual(resp_post.headers.get("Mcp-Session-Id"), session_id)
        self.assertEqual(resp_post.headers.get("MCP-Protocol-Version"), "2024-11-05")

        # Test DELETE /mcp to close session
        resp_del = self.client.delete(
            "/mcp",
            headers={"x-api-key": "mcp_tenant_key", "Mcp-Session-Id": session_id}
        )
        self.assertEqual(resp_del.status_code, 204)

    def test_07_oauth2_discovery_metadata(self):
        """Verify RFC 8414 and RFC 9728 OAuth 2.0 discovery metadata endpoints."""
        # 1. Authorization server metadata
        resp_auth = self.client.get("/.well-known/oauth-authorization-server")
        self.assertEqual(resp_auth.status_code, 200)
        auth_meta = resp_auth.json()
        self.assertIn("authorization_endpoint", auth_meta)
        self.assertIn("token_endpoint", auth_meta)
        self.assertIn("scopes_supported", auth_meta)
        self.assertIn("mcp:read", auth_meta["scopes_supported"])

        # 2. Protected resource metadata
        resp_res = self.client.get("/.well-known/oauth-protected-resource")
        self.assertEqual(resp_res.status_code, 200)
        res_meta = resp_res.json()
        self.assertIn("resource", res_meta)
        self.assertIn("authorization_servers", res_meta)

    def test_08_oauth2_flow_and_token_call(self):
        """Verify complete OAuth 2.0 authorize -> token -> MCP execution cycle."""
        # 1. Authorize endpoint
        resp_auth = self.client.get("/oauth/authorize?client_id=claude-test&response_type=code")
        self.assertEqual(resp_auth.status_code, 200)
        auth_data = resp_auth.json()
        self.assertEqual(auth_data.get("status"), "authorized")
        code = auth_data.get("code")
        self.assertTrue(code.startswith("omni_code_"))

        # 2. Token endpoint
        resp_tok = self.client.post(
            "/oauth/token",
            json={"grant_type": "authorization_code", "code": code, "client_id": "claude-test"}
        )
        self.assertEqual(resp_tok.status_code, 200)
        tok_data = resp_tok.json()
        access_token = tok_data.get("access_token")
        self.assertTrue(access_token.startswith("omni_tok_"))
        self.assertEqual(tok_data.get("token_type"), "Bearer")

        # 3. Call /mcp using issued OAuth Bearer token
        resp_mcp = self.client.get("/mcp", headers={"Authorization": f"Bearer {access_token}"})
        self.assertEqual(resp_mcp.status_code, 200)
        self.assertEqual(resp_mcp.json().get("service"), "omnicache-mcp")

    def test_08b_oauth_security_and_pkce_enforcement(self):
        """Verify strict authorization code redemption, single-use, PKCE, and secretless rejection."""
        import hashlib
        import base64

        # 1. Calling /oauth/token directly with client_credentials without a valid secret MUST fail (401)
        resp_unauth = self.client.post("/oauth/token", json={"grant_type": "client_credentials", "client_id": "attacker_id"})
        self.assertEqual(resp_unauth.status_code, 401)
        self.assertEqual(resp_unauth.json().get("error"), "invalid_client")

        # 2. Calling with an invalid client_secret MUST fail (401)
        resp_bad_secret = self.client.post("/oauth/token", json={"grant_type": "client_credentials", "client_id": "attacker", "client_secret": "wrong_secret"})
        self.assertEqual(resp_bad_secret.status_code, 401)
        self.assertEqual(resp_bad_secret.json().get("error"), "invalid_client")

        # 3. Valid client_credentials with registered tenant key MUST succeed
        resp_good_secret = self.client.post("/oauth/token", json={"grant_type": "client_credentials", "client_id": "mcp_tenant", "client_secret": "mcp_tenant_key"})
        self.assertEqual(resp_good_secret.status_code, 200)
        self.assertTrue(resp_good_secret.json().get("access_token").startswith("omni_tok_"))

        # 4. PKCE authorization flow: generate verifier and S256 challenge
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

        resp_auth = self.client.get(f"/oauth/authorize?client_id=claude-pkce&response_type=code&code_challenge={challenge}&code_challenge_method=S256&scope=mcp:read")
        self.assertEqual(resp_auth.status_code, 200)
        code = resp_auth.json().get("code")

        # 5. Redeeming with WRONG code_verifier MUST fail (400)
        resp_bad_pkce = self.client.post("/oauth/token", json={"grant_type": "authorization_code", "code": code, "client_id": "claude-pkce", "code_verifier": "wrong_verifier"})
        self.assertEqual(resp_bad_pkce.status_code, 400)
        self.assertEqual(resp_bad_pkce.json().get("error"), "invalid_grant")

        # Re-authorize since bad redemption consumed the code
        resp_auth2 = self.client.get(f"/oauth/authorize?client_id=claude-pkce&response_type=code&code_challenge={challenge}&code_challenge_method=S256&scope=mcp:read")
        code2 = resp_auth2.json().get("code")

        # 6. Redeeming with CORRECT code_verifier MUST succeed (200)
        resp_good_pkce = self.client.post("/oauth/token", json={"grant_type": "authorization_code", "code": code2, "client_id": "claude-pkce", "code_verifier": verifier})
        self.assertEqual(resp_good_pkce.status_code, 200)
        tok_data = resp_good_pkce.json()
        read_token = tok_data.get("access_token")

        # 7. Single-use replay protection: attempting to reuse code2 MUST fail (400)
        resp_replay = self.client.post("/oauth/token", json={"grant_type": "authorization_code", "code": code2, "client_id": "claude-pkce", "code_verifier": verifier})
        self.assertEqual(resp_replay.status_code, 400)
        self.assertEqual(resp_replay.json().get("error"), "invalid_grant")

    def test_08c_oauth_scope_enforcement(self):
        """Verify token scopes: read-only token cannot call destructive tools like omnicache_invalidate."""
        # 1. Issue read-only token
        resp_auth = self.client.get("/oauth/authorize?client_id=readonly-client&response_type=code&scope=mcp:read")
        code = resp_auth.json().get("code")
        resp_tok = self.client.post("/oauth/token", json={"grant_type": "authorization_code", "code": code, "client_id": "readonly-client"})
        read_token = resp_tok.json().get("access_token")

        # 2. Reading via omnicache_query with read_token is PERMITTED
        query_req = {
            "jsonrpc": "2.0",
            "id": "q-scope",
            "method": "tools/call",
            "params": {"name": "omnicache_query", "arguments": {"prompt": "test query", "model": "gpt-4o"}}
        }
        resp_q = self.client.post("/mcp", json=query_req, headers={"Authorization": f"Bearer {read_token}"})
        self.assertEqual(resp_q.status_code, 200)
        self.assertNotIn("error", resp_q.json())

        # 3. Purging/Invalidating with read-only token MUST BE FORBIDDEN (-32600)
        inv_req = {
            "jsonrpc": "2.0",
            "id": "inv-scope",
            "method": "tools/call",
            "params": {"name": "omnicache_invalidate", "arguments": {}}
        }
        resp_inv = self.client.post("/mcp", json=inv_req, headers={"Authorization": f"Bearer {read_token}"})
        self.assertEqual(resp_inv.status_code, 200)
        self.assertIn("error", resp_inv.json())
        self.assertEqual(resp_inv.json()["error"]["code"], -32600)
        self.assertIn("Forbidden", resp_inv.json()["error"]["message"])

        # 4. Writing with read-only token MUST ALSO BE FORBIDDEN (-32600)
        store_req = {
            "jsonrpc": "2.0",
            "id": "st-scope",
            "method": "tools/call",
            "params": {"name": "omnicache_store", "arguments": {"prompt": "p", "answer": "a"}}
        }
        resp_st = self.client.post("/mcp", json=store_req, headers={"Authorization": f"Bearer {read_token}"})
        self.assertEqual(resp_st.status_code, 200)
        self.assertIn("error", resp_st.json())
        self.assertEqual(resp_st.json()["error"]["code"], -32600)

        # 5. Issue admin/write token
        resp_auth_admin = self.client.get("/oauth/authorize?client_id=admin-client&response_type=code&scope=mcp:admin")
        code_admin = resp_auth_admin.json().get("code")
        resp_tok_admin = self.client.post("/oauth/token", json={"grant_type": "authorization_code", "code": code_admin, "client_id": "admin-client"})
        admin_token = resp_tok_admin.json().get("access_token")

        # 6. Admin token CAN execute omnicache_invalidate
        resp_admin_inv = self.client.post("/mcp", json=inv_req, headers={"Authorization": f"Bearer {admin_token}"})
        self.assertEqual(resp_admin_inv.status_code, 200)
        self.assertNotIn("error", resp_admin_inv.json())

    def test_08d_oauth_authorize_gated_by_authentication(self):
        """Verify /oauth/authorize requires real authentication when REQUIRE_AUTH=true."""
        from core.config import config
        old_require_auth = getattr(config, "REQUIRE_AUTH", False)
        try:
            config.REQUIRE_AUTH = True
            # API request without credentials returns 401
            resp_no_key = self.client.get(
                "/oauth/authorize?client_id=claude&response_type=code",
                headers={"Accept": "application/json"}
            )
            self.assertEqual(resp_no_key.status_code, 401)
            self.assertEqual(resp_no_key.json().get("error"), "access_denied")

            # Browser request without credentials returns HTML consent screen
            resp_html = self.client.get(
                "/oauth/authorize?client_id=claude&response_type=code",
                headers={"Accept": "text/html"}
            )
            self.assertEqual(resp_html.status_code, 200)
            self.assertIn("Authorize MCP Client", resp_html.text)
            self.assertIn("OmniCache API Key", resp_html.text)

            # Request with valid x-api-key succeeds
            resp_with_key = self.client.get(
                "/oauth/authorize?client_id=claude&response_type=code",
                headers={"x-api-key": "mcp_tenant_key"}
            )
            self.assertEqual(resp_with_key.status_code, 200)
            self.assertEqual(resp_with_key.json().get("status"), "authorized")
        finally:
            config.REQUIRE_AUTH = old_require_auth

    def test_09_unauthenticated_returns_www_authenticate(self):
        """Verify unauthenticated requests return 401 with RFC 9728 WWW-Authenticate challenge."""
        from core.config import config
        old_require_auth = getattr(config, "REQUIRE_AUTH", False)
        try:
            config.REQUIRE_AUTH = True
            resp = self.client.get("/mcp", headers={"Authorization": "Bearer invalid_unregistered_token"})
            self.assertEqual(resp.status_code, 401)
            self.assertIn("WWW-Authenticate", resp.headers)
            self.assertIn("Bearer", resp.headers["WWW-Authenticate"])
            self.assertIn('realm="OmniCache"', resp.headers["WWW-Authenticate"])
        finally:
            config.REQUIRE_AUTH = old_require_auth

    def test_10_mcp_tools_metadata_annotations(self):
        """Verify all advertised MCP tools carry complete behavioral annotations."""
        list_req = {
            "jsonrpc": "2.0",
            "id": "req-anno-list",
            "method": "tools/list"
        }
        resp = self.client.post("/mcp", json=list_req, headers={"x-api-key": "mcp_tenant_key"})
        self.assertEqual(resp.status_code, 200)
        tools = resp.json().get("result", {}).get("tools", [])

        # Check that duplicate aliases are removed
        tool_names = [t["name"] for t in tools]
        self.assertNotIn("replay_tool", tool_names)
        self.assertNotIn("record_tool", tool_names)

        # Check every tool has annotations
        for tool in tools:
            self.assertIn("annotations", tool, f"Tool {tool['name']} missing annotations block")
            anno = tool["annotations"]
            self.assertIn("title", anno)
            self.assertIn("readOnlyHint", anno)
            self.assertIn("destructiveHint", anno)
            self.assertIn("idempotentHint", anno)

        # Verify omnicache_invalidate is marked destructive
        inv_tool = next(t for t in tools if t["name"] == "omnicache_invalidate")
        self.assertTrue(inv_tool["annotations"]["destructiveHint"])
        self.assertFalse(inv_tool["annotations"]["readOnlyHint"])

    def test_11_graceful_error_handling(self):
        """Verify exceptions return standard JSON-RPC -32603 Internal error payloads."""
        # Unknown tool
        req_unknown = {
            "jsonrpc": "2.0",
            "id": "err-1",
            "method": "tools/call",
            "params": {"name": "non_existent_tool", "arguments": {}}
        }
        resp = self.client.post("/mcp", json=req_unknown, headers={"x-api-key": "mcp_tenant_key"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("error", resp.json())
        self.assertEqual(resp.json()["error"]["code"], -32601)

        # Unknown method
        req_bad_method = {
            "jsonrpc": "2.0",
            "id": "err-2",
            "method": "invalid/method",
            "params": {}
        }
        resp = self.client.post("/mcp", json=req_bad_method, headers={"x-api-key": "mcp_tenant_key"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["error"]["code"], -32601)

    def test_12_pii_sanitization_in_mcp_tools(self):
        """Verify PrivacyShield sanitizes sensitive data before persistence via MCP tools."""
        from server.tool_replayer import tool_cache
        # 1. Storing prompt containing email and SSN
        store_req = {
            "jsonrpc": "2.0",
            "id": "store-pii",
            "method": "tools/call",
            "params": {
                "name": "omnicache_store",
                "arguments": {
                    "prompt": "Contact user at alice.smith@secure-domain.com with SSN 000-12-3456",
                    "answer": "Confirmation sent to alice.smith@secure-domain.com",
                    "model": "gpt-4o",
                    "tag": "pii_test"
                }
            }
        }
        resp_store = self.client.post("/mcp", json=store_req, headers={"x-api-key": "mcp_tenant_key"})
        self.assertEqual(resp_store.status_code, 200)

        # 2. Recording tool output containing sensitive API key
        rec_req = {
            "jsonrpc": "2.0",
            "id": "rec-pii",
            "method": "tools/call",
            "params": {
                "name": "omnicache_record_tool",
                "arguments": {
                    "tool_name": "fetch_user_credentials",
                    "output": "API Key: sk-ant-api03-abcdef1234567890abcdef1234567890-test_token_1234",
                    "arguments": {"user": "alice"}
                }
            }
        }
        resp_rec = self.client.post("/mcp", json=rec_req, headers={"x-api-key": "mcp_tenant_key"})
        self.assertEqual(resp_rec.status_code, 200)
        tool_key = json.loads(resp_rec.json()["result"]["content"][0]["text"])["tool_key"]

        # Verify the recorded output was sanitized before storage
        entry = tool_cache._cache.get(tool_key)
        self.assertIsNotNone(entry)
        self.assertNotIn("sk-ant-api03-abcdef1234567890abcdef1234567890-test_token_1234", entry["output"])
        self.assertIn("REDACTED", entry["output"])

    def test_14_mcp_cross_tenant_isolation(self):
        """Verify that non-admin tenants cannot read, poison, or destroy other tenants' caches via MCP arguments."""
        # 1. Tenant B stores confidential data in its own organization (mcp_tenant_2_org)
        store_req = {
            "jsonrpc": "2.0",
            "id": "tenant-b-store",
            "method": "tools/call",
            "params": {
                "name": "omnicache_store",
                "arguments": {
                    "prompt": "Q4 Strategy and Layoff Plan",
                    "answer": "TENANT_B_CONFIDENTIAL: Q4 reorganization details",
                    "model": "gpt-4o"
                }
            }
        }
        resp_b = self.client.post("/mcp", json=store_req, headers={"x-api-key": "mcp_tenant_2_key"})
        self.assertEqual(resp_b.status_code, 200)

        # 2. Tenant A attempts to query Tenant B's cache by providing org_id="mcp_tenant_2_org"
        query_req = {
            "jsonrpc": "2.0",
            "id": "tenant-a-query-steal",
            "method": "tools/call",
            "params": {
                "name": "omnicache_query",
                "arguments": {
                    "prompt": "Q4 Strategy and Layoff Plan",
                    "org_id": "mcp_tenant_2_org"
                }
            }
        }
        resp_steal = self.client.post("/mcp", json=query_req, headers={"x-api-key": "mcp_tenant_key"})
        self.assertEqual(resp_steal.status_code, 200)
        steal_json = resp_steal.json()
        self.assertIn("error", steal_json)
        self.assertEqual(steal_json["error"]["code"], -32600)
        self.assertIn("Forbidden", steal_json["error"]["message"])

        # 3. Tenant A attempts to invalidate Tenant B's cache
        inval_req = {
            "jsonrpc": "2.0",
            "id": "tenant-a-inval-destroy",
            "method": "tools/call",
            "params": {
                "name": "omnicache_invalidate",
                "arguments": {
                    "org_id": "mcp_tenant_2_org"
                }
            }
        }
        resp_destroy = self.client.post("/mcp", json=inval_req, headers={"x-api-key": "mcp_tenant_key"})
        self.assertEqual(resp_destroy.status_code, 200)
        destroy_json = resp_destroy.json()
        self.assertIn("error", destroy_json)
        self.assertEqual(destroy_json["error"]["code"], -32600)
        self.assertIn("Forbidden", destroy_json["error"]["message"])

        # 4. Verify Tenant B's cache is still completely intact
        query_b = {
            "jsonrpc": "2.0",
            "id": "tenant-b-verify",
            "method": "tools/call",
            "params": {
                "name": "omnicache_query",
                "arguments": {
                    "prompt": "Q4 Strategy and Layoff Plan"
                }
            }
        }
        resp_b_check = self.client.post("/mcp", json=query_b, headers={"x-api-key": "mcp_tenant_2_key"})
        self.assertEqual(resp_b_check.status_code, 200)
        self.assertIn("TENANT_B_CONFIDENTIAL", resp_b_check.json()["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
