"""
Unit test for OmniCache MCP JSON-RPC Server.
"""

import unittest
import json
import subprocess
import os

class TestOmniCacheMCP(unittest.TestCase):
    def test_mcp_jsonrpc_protocol(self):
        mcp_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "mcp", "server.py"))

        proc = subprocess.Popen(
            ["python3", mcp_script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        def send_and_recv(msg):
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            out_line = proc.stdout.readline()
            return json.loads(out_line)

        # 1. Initialize
        res_init = send_and_recv({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(res_init["result"]["serverInfo"]["name"], "omnicache-mcp")

        # 2. List tools
        res_tools = send_and_recv({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tool_names = [t["name"] for t in res_tools["result"]["tools"]]
        self.assertIn("omnicache_query", tool_names)
        self.assertIn("omnicache_store", tool_names)
        self.assertIn("omnicache_search", tool_names)
        self.assertIn("omnicache_stats", tool_names)

        # 3. Store knowledge via MCP
        res_store = send_and_recv({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "omnicache_store",
                "arguments": {
                    "prompt": "How do I create an index in Postgres?",
                    "answer": "CREATE INDEX idx_user_email ON users(email);",
                    "tag": "db_tips",
                    "org_id": "mcp_tenant"
                }
            }
        })
        self.assertIn("Successfully stored entry", res_store["result"]["content"][0]["text"])

        # 4. Query knowledge via MCP (expect HIT)
        res_query = send_and_recv({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "omnicache_query",
                "arguments": {
                    "prompt": "How do I create an index in Postgres?",
                    "org_id": "mcp_tenant"
                }
            }
        })
        parsed_query = json.loads(res_query["result"]["content"][0]["text"])
        self.assertEqual(parsed_query["cache_status"], "HIT_EXACT")
        self.assertIn("CREATE INDEX", parsed_query["cached_response"])

        # 5. Semantic Search via MCP
        res_search = send_and_recv({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "omnicache_search",
                "arguments": {
                    "query": "Postgres indexing",
                    "org_id": "mcp_tenant"
                }
            }
        })
        search_results = json.loads(res_search["result"]["content"][0]["text"])
        self.assertGreaterEqual(len(search_results), 1)

        # Cleanup
        proc.terminate()

if __name__ == "__main__":
    unittest.main()
