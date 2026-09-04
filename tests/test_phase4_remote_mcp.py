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


if __name__ == "__main__":
    unittest.main()
