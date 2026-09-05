import unittest
import json
import time
from core.vector_cache import DualTierCache
from core.config import config
from server.tool_replayer import ToolExecutionCache, tool_cache
from mcp.server import process_mcp_jsonrpc


class TestV263AuditFixes(unittest.TestCase):

    def setUp(self):
        self.cache = DualTierCache()

    def test_01_default_semantic_threshold_paraphrase(self):
        """Verify natural conversational paraphrase hits under the default threshold (0.68)."""
        model = "claude-3-5-sonnet-20241022"
        # 1. Store base prompt
        self.cache.store(
            payload={
                "model": model,
                "messages": [{"role": "user", "content": "What is the capital of France?"}]
            },
            response_payload={
                "choices": [{"message": {"role": "assistant", "content": "The capital of France is Paris."}}]
            }
        )

        # 2. Lookup near-trivial paraphrase under default threshold (no custom threshold passed)
        status, entry, sim, reason = self.cache.lookup(
            payload={
                "model": model,
                "messages": [{"role": "user", "content": "Tell me France's capital city."}]
            }
        )

        self.assertEqual(status, "HIT_SEMANTIC", f"Paraphrase should hit under default threshold. Reason: {reason}")
        self.assertGreaterEqual(sim, 0.68, f"Similarity {sim} should be >= default threshold 0.68")
        self.assertIn("Paris", entry.response_payload["choices"][0]["message"]["content"])

        # 3. Lookup genuinely different question -> Must MISS
        diff_status, diff_entry, diff_sim, diff_reason = self.cache.lookup(
            payload={
                "model": model,
                "messages": [{"role": "user", "content": "What is the capital of Germany?"}]
            }
        )
        self.assertEqual(diff_status, "MISS", f"Different country should MISS. Got {diff_status}")
        self.assertLess(diff_sim, 0.68)

    def test_02_custom_tool_record_and_replay(self):
        """Verify custom tool names not in legacy allowlist are replayable with reordered JSON keys."""
        t_cache = ToolExecutionCache()
        tool_name = "fetch_weather_metrics"
        store_args = {"city": "Paris", "units": "metric", "detailed": True}
        replay_args = {"detailed": True, "units": "metric", "city": "Paris"}  # Reordered keys
        output = '{"temp": 22.5, "conditions": "Sunny"}'

        # 1. Store tool call
        key = t_cache.store_tool_call(
            tool_name=tool_name,
            arguments=store_args,
            output=output,
            workspace_fingerprint="test_ws_001",
            workspace_state="state_fixed_abc"
        )
        self.assertIsNotNone(key)

        # 2. Replay with reordered keys
        is_hit, cached_out, ret_key = t_cache.lookup_tool_call(
            tool_name=tool_name,
            arguments=replay_args,
            workspace_fingerprint="test_ws_001",
            workspace_state="state_fixed_abc"
        )
        self.assertTrue(is_hit, "Custom tool call should hit on immediate replay")
        self.assertEqual(cached_out, output)
        self.assertEqual(ret_key, key)

    def test_03_tool_cache_sqlite_cross_process_durability(self):
        """Verify tool records are persisted in SQLite and readable by fresh cache instances."""
        writer_cache = ToolExecutionCache()
        tool_name = "custom_data_analyzer"
        args = {"dataset": "metrics_2026", "aggregate": "p99"}
        output = '{"p99_latency_ms": 14.2}'
        unique_ws = f"ws_durability_{int(time.time() * 1000)}"

        key = writer_cache.store_tool_call(
            tool_name=tool_name,
            arguments=args,
            output=output,
            workspace_fingerprint=unique_ws,
            workspace_state="clean"
        )

        # Fresh instance simulating another process or daemon restart
        reader_cache = ToolExecutionCache()
        self.assertNotIn(key, reader_cache._cache, "Fresh instance should not have key in RAM yet")

        is_hit, cached_out, ret_key = reader_cache.lookup_tool_call(
            tool_name=tool_name,
            arguments=args,
            workspace_fingerprint=unique_ws,
            workspace_state="clean"
        )
        self.assertTrue(is_hit, "Fresh cache instance must hydrate and HIT from SQLite store")
        self.assertEqual(cached_out, output)
        self.assertIn(key, reader_cache._cache, "Should be rehydrated into RAM")

    def test_04_mcp_record_and_replay_custom_tools(self):
        """Verify MCP omnicache_record_tool and omnicache_replay_tool with custom tool names."""
        record_req = {
            "jsonrpc": "2.0",
            "id": 101,
            "method": "tools/call",
            "params": {
                "name": "omnicache_record_tool",
                "arguments": {
                    "tool_name": "arbitrary_agent_tool",
                    "arguments": {"target": "user_profile", "user_id": 42},
                    "output": '{"name": "Alice", "role": "developer"}'
                }
            }
        }
        res_rec = process_mcp_jsonrpc(record_req)
        self.assertEqual(res_rec.get("jsonrpc"), "2.0")
        rec_data = json.loads(res_rec["result"]["content"][0]["text"])
        self.assertEqual(rec_data.get("status"), "STORED")
        self.assertTrue(rec_data.get("cached"))

        replay_req = {
            "jsonrpc": "2.0",
            "id": 102,
            "method": "tools/call",
            "params": {
                "name": "omnicache_replay_tool",
                "arguments": {
                    "tool_name": "arbitrary_agent_tool",
                    "arguments": {"user_id": 42, "target": "user_profile"}
                }
            }
        }
        res_rep = process_mcp_jsonrpc(replay_req)
        self.assertEqual(res_rep.get("jsonrpc"), "2.0")
        rep_data = json.loads(res_rep["result"]["content"][0]["text"])
        self.assertEqual(rep_data.get("status"), "HIT")
        self.assertTrue(rep_data.get("cached"))
        self.assertIn("Alice", rep_data.get("output"))


if __name__ == "__main__":
    unittest.main()
