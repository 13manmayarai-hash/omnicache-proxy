"""
Tests for Multi-Agent Swarm & Subagent Delegation Bus (v2.9.9).
Validates cross-agent shared memory, delegation lineage trees,
mutation invalidation guards, gateway headers, and swarm endpoints.
"""

import unittest
import time
from unittest.mock import patch, AsyncMock
from starlette.testclient import TestClient

from core.config import config
from core.swarm_bus import swarm_bus, SwarmBus
from server.gateway import app, METRICS_LEDGER


class TestSwarmDelegationBus(unittest.TestCase):

    def setUp(self):
        swarm_bus.reset_stats()
        from server.gateway import cache_instance, tool_cache
        cache_instance.purge()
        tool_cache.clear()
        self.client = TestClient(app)

    def tearDown(self):
        swarm_bus.reset_stats()

    def test_delegation_lineage_and_topology(self):
        bus = SwarmBus(ttl_seconds=3600, max_entries=100)
        swarm_id = "swarm-lineage-test"

        # Lead delegates to researcher and coder
        bus.record_delegation(swarm_id, "lead", "researcher", task_prompt="explore architecture")
        bus.record_delegation(swarm_id, "lead", "coder", task_prompt="implement feature")
        # Researcher delegates to searcher
        bus.record_delegation(swarm_id, "researcher", "searcher", task_prompt="grep codebase")

        topology = bus.get_swarm_topology(swarm_id)
        self.assertEqual(topology["swarm_id"], swarm_id)
        nodes = topology["nodes"]
        self.assertIn("lead", nodes)
        self.assertIn("researcher", nodes)
        self.assertIn("coder", nodes)
        self.assertIn("searcher", nodes)

        self.assertEqual(nodes["researcher"]["parent"], "lead")
        self.assertEqual(nodes["coder"]["parent"], "lead")
        self.assertEqual(nodes["searcher"]["parent"], "researcher")
        self.assertIn("searcher", nodes["researcher"]["children"])
        self.assertIn("researcher", nodes["lead"]["children"])
        self.assertIn("coder", nodes["lead"]["children"])

    def test_shared_memory_store_and_lookup(self):
        bus = SwarmBus(ttl_seconds=3600, max_entries=100)
        swarm_id = "swarm-shared-mem"

        # Agent 1 records result
        payload = {"cmd": "cat config.json", "env": "prod"}
        result = {"status": "success", "data": "key=val"}
        bus.record_shared_result(
            swarm_id=swarm_id,
            agent_id="worker-1",
            task_type="shell_exec",
            payload=payload,
            result_payload=result,
            tokens_saved=120
        )

        # Agent 2 looks up result
        is_hit, res_data, meta = bus.lookup_shared_result(
            swarm_id=swarm_id,
            agent_id="worker-2",
            task_type="shell_exec",
            payload=payload
        )
        self.assertTrue(is_hit)
        self.assertEqual(res_data, result)
        self.assertEqual(meta["origin_agent_id"], "worker-1")
        self.assertEqual(meta["tokens_saved"], 120)

        # Non-matching payload returns MISS
        miss_hit, _, _ = bus.lookup_shared_result(
            swarm_id=swarm_id,
            agent_id="worker-2",
            task_type="shell_exec",
            payload={"cmd": "cat other.json"}
        )
        self.assertFalse(miss_hit)

    def test_cross_agent_mutation_invalidation(self):
        bus = SwarmBus(ttl_seconds=3600, max_entries=100)
        swarm_id = "swarm-invalidation"

        # Worker 1 caches read of file A and file B
        bus.record_shared_result(
            swarm_id=swarm_id,
            agent_id="worker-1",
            task_type="view_file",
            payload={"path": "src/app.py"},
            result_payload="def main(): pass",
            affected_resources=["/root/project/src/app.py"]
        )
        bus.record_shared_result(
            swarm_id=swarm_id,
            agent_id="worker-1",
            task_type="view_file",
            payload={"path": "src/utils.py"},
            result_payload="def helper(): pass",
            affected_resources=["/root/project/src/utils.py"]
        )

        # Worker 2 mutates src/app.py
        invalidated_count = bus.invalidate_on_mutation(
            swarm_id=swarm_id,
            agent_id="worker-2",
            mutated_resource="/root/project/src/app.py"
        )
        self.assertEqual(invalidated_count, 1)

        # Worker 3 reads src/app.py -> Must MISS
        is_hit_app, _, _ = bus.lookup_shared_result(
            swarm_id=swarm_id,
            agent_id="worker-3",
            task_type="view_file",
            payload={"path": "src/app.py"}
        )
        self.assertFalse(is_hit_app)

        # Worker 3 reads src/utils.py -> Remains HIT
        is_hit_utils, out_utils, _ = bus.lookup_shared_result(
            swarm_id=swarm_id,
            agent_id="worker-3",
            task_type="view_file",
            payload={"path": "src/utils.py"}
        )
        self.assertTrue(is_hit_utils)
        self.assertEqual(out_utils, "def helper(): pass")

    def test_ttl_expiry(self):
        bus = SwarmBus(ttl_seconds=0.01, max_entries=100)
        swarm_id = "swarm-ttl"
        bus.record_shared_result(
            swarm_id=swarm_id,
            agent_id="worker-1",
            task_type="compute",
            payload={"x": 1},
            result_payload={"res": 2}
        )
        time.sleep(0.02)
        is_hit, _, _ = bus.lookup_shared_result(
            swarm_id=swarm_id,
            agent_id="worker-2",
            task_type="compute",
            payload={"x": 1}
        )
        self.assertFalse(is_hit)

    def test_gateway_swarm_tool_replay(self):
        swarm_id = f"swarm_test_gateway_{int(time.time()*1000)}"

        # 1. Lead delegates to subagent-A
        del_res = self.client.post("/v1/swarm/delegate", json={
            "swarm_id": swarm_id,
            "parent_agent": "lead",
            "agent_id": "subagent-A",
            "task_prompt": "analyze database migration"
        })
        self.assertEqual(del_res.status_code, 200)

        # 2. subagent-A records a read tool
        headers_a = {
            "x-omnicache-swarm-id": swarm_id,
            "x-omnicache-agent-id": "subagent-A",
            "x-omnicache-parent-agent": "lead"
        }
        rec_res = self.client.post("/v1/agent/tool_record", json={
            "action": "record",
            "tool_name": "read_schema",
            "arguments": {"table": "users"},
            "output": "CREATE TABLE users (id INT);",
            "workspace_dir": "/app"
        }, headers=headers_a)
        self.assertEqual(rec_res.status_code, 200)

        # 3. subagent-B replays identical tool -> Swarm HIT
        headers_b = {
            "x-omnicache-swarm-id": swarm_id,
            "x-omnicache-agent-id": "subagent-B",
            "x-omnicache-parent-agent": "lead"
        }
        rep_res = self.client.post("/v1/agent/tool_replay", json={
            "tool_name": "read_schema",
            "arguments": {"table": "users"},
            "workspace_dir": "/app"
        }, headers=headers_b)
        self.assertEqual(rep_res.status_code, 200)
        data = rep_res.json()
        self.assertEqual(data.get("status"), "HIT")
        self.assertTrue(data.get("swarm_hit"))
        self.assertEqual(data.get("origin_agent"), "subagent-A")
        self.assertEqual(rep_res.headers.get("x-omnicache-swarm-hit"), "true")
        self.assertEqual(rep_res.headers.get("x-omnicache-origin-agent"), "subagent-A")

        # 4. subagent-B executes mutative tool -> invalidates
        mut_res = self.client.post("/v1/agent/tool_record", json={
            "action": "record",
            "tool_name": "edit_file",
            "arguments": {"path": "schema.sql"},
            "workspace_dir": "/app"
        }, headers=headers_b)
        self.assertEqual(mut_res.status_code, 200)

        # 5. subagent-C re-requests read_schema -> returns MISS (invalidated)
        headers_c = {
            "x-omnicache-swarm-id": swarm_id,
            "x-omnicache-agent-id": "subagent-C",
            "x-omnicache-parent-agent": "lead"
        }
        miss_res = self.client.post("/v1/agent/tool_replay", json={
            "tool_name": "read_schema",
            "arguments": {"table": "users"},
            "workspace_dir": "/app"
        }, headers=headers_c)
        self.assertEqual(miss_res.status_code, 200)
        self.assertEqual(miss_res.json().get("status"), "MISS")

    def test_gateway_swarm_chat_completions(self):
        swarm_id = f"swarm_chat_{int(time.time()*1000)}"
        prompt_payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Explain raft leader election in 5 words"}]
        }
        mock_response = {
            "id": "chatcmpl-swarm-1",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "Heartbeat timeout triggers election term."}}],
            "usage": {"prompt_tokens": 15, "completion_tokens": 6, "total_tokens": 21}
        }

        # Subagent 1 sends query -> MISS, recorded into swarm bus
        headers_1 = {
            "x-omnicache-swarm-id": swarm_id,
            "x-omnicache-agent-id": "subagent-1",
            "x-omnicache-parent-agent": "lead"
        }
        with patch("server.upstream.upstream_client.forward_non_stream", new=AsyncMock(return_value=(200, mock_response, {}))):
            res_1 = self.client.post("/v1/chat/completions", json=prompt_payload, headers=headers_1)
            self.assertEqual(res_1.status_code, 200)
            self.assertEqual(res_1.headers.get("X-Cache-Status"), "MISS")

        # Subagent 2 sends identical query -> HIT_SWARM
        headers_2 = {
            "x-omnicache-swarm-id": swarm_id,
            "x-omnicache-agent-id": "subagent-2",
            "x-omnicache-parent-agent": "lead"
        }
        res_2 = self.client.post("/v1/chat/completions", json=prompt_payload, headers=headers_2)
        self.assertEqual(res_2.status_code, 200)
        self.assertEqual(res_2.headers.get("X-Cache-Status"), "HIT_SWARM")
        self.assertEqual(res_2.headers.get("X-OmniCache-Swarm-Hit"), "true")
        self.assertEqual(res_2.headers.get("X-OmniCache-Origin-Agent"), "subagent-1")
        self.assertEqual(res_2.headers.get("X-OmniCache-Swarm-ID"), swarm_id)

    def test_gateway_swarm_anthropic_messages(self):
        swarm_id = f"swarm_anthropic_{int(time.time()*1000)}"
        prompt_payload = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "Summarize map reduce"}]
        }
        mock_response = {
            "id": "msg-swarm-1",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Split, map, shuffle, reduce output."}],
            "usage": {"input_tokens": 12, "output_tokens": 8}
        }

        # Subagent 1 sends query -> MISS
        headers_1 = {
            "x-omnicache-swarm-id": swarm_id,
            "x-omnicache-agent-id": "subagent-1",
            "x-omnicache-parent-agent": "lead"
        }
        with patch("server.upstream.upstream_client.forward_anthropic_messages", new=AsyncMock(return_value=(200, mock_response, {}))):
            res_1 = self.client.post("/v1/messages", json=prompt_payload, headers=headers_1)
            self.assertEqual(res_1.status_code, 200)
            self.assertEqual(res_1.headers.get("X-Cache-Status"), "MISS")

        # Subagent 2 sends query -> HIT_SWARM
        headers_2 = {
            "x-omnicache-swarm-id": swarm_id,
            "x-omnicache-agent-id": "subagent-2",
            "x-omnicache-parent-agent": "lead"
        }
        res_2 = self.client.post("/v1/messages", json=prompt_payload, headers=headers_2)
        self.assertEqual(res_2.status_code, 200)
        self.assertEqual(res_2.headers.get("X-Cache-Status"), "HIT_SWARM")
        self.assertEqual(res_2.headers.get("X-OmniCache-Swarm-Hit"), "true")
        self.assertEqual(res_2.headers.get("X-OmniCache-Origin-Agent"), "subagent-1")

    def test_swarm_endpoints_and_telemetry(self):
        swarm_id = f"swarm_endpoints_{int(time.time()*1000)}"

        # 1. /v1/swarm/delegate
        del_res = self.client.post("/v1/swarm/delegate", json={
            "swarm_id": swarm_id,
            "parent_agent": "lead",
            "agent_id": "child-1",
            "task_prompt": "review security"
        })
        self.assertEqual(del_res.status_code, 200)
        self.assertEqual(del_res.json().get("status"), "success")

        # 2. /v1/swarm/topology
        topo_res = self.client.get(f"/v1/swarm/topology?swarm_id={swarm_id}")
        self.assertEqual(topo_res.status_code, 200)
        topo = topo_res.json()
        self.assertEqual(topo["swarm_id"], swarm_id)
        self.assertIn("child-1", topo["nodes"])

        # 3. /v1/swarm/stats
        stats_res = self.client.get("/v1/swarm/stats")
        self.assertEqual(stats_res.status_code, 200)
        stats_data = stats_res.json()
        self.assertEqual(stats_data.get("status"), "success")
        self.assertIn("swarm_stats", stats_data)
        self.assertIn("metrics_ledger", stats_data)

        # 4. /metrics Prometheus export includes swarm metrics
        prom_res = self.client.get("/metrics")
        self.assertEqual(prom_res.status_code, 200)
        self.assertIn("omnicache_swarm_requests_total", prom_res.text)
        self.assertIn("omnicache_swarm_cross_agent_hits_total", prom_res.text)
        self.assertIn("omnicache_swarm_tokens_saved_total", prom_res.text)
        self.assertIn("omnicache_swarm_mutations_invalidated_total", prom_res.text)


if __name__ == "__main__":
    unittest.main()
