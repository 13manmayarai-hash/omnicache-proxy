import os
import time
import json
import pytest
from starlette.testclient import TestClient
from server.gateway import app, METRICS_LEDGER
from server.tool_replayer import (
    tool_cache,
    tool_policy_manager,
    compact_and_record_agent_tools,
    DEFAULT_BUILTIN_POLICIES
)

@pytest.fixture
def client():
    METRICS_LEDGER["agent_tool_hits"] = 0
    tool_cache.clear()
    tool_policy_manager.clear_custom_policies()
    yield TestClient(app)
    tool_cache.clear()
    tool_policy_manager.clear_custom_policies()


def test_business_tool_prefix_inference():
    """Verify safe prefixes infer business_api with 300s TTL and cacheable=True."""
    safe_tools = [
        "lookup_customer",
        "check_order_status",
        "query_inventory_db",
        "scan_asset_tag",
        "describe_resource",
        "fetch_invoice"
    ]
    for tool_name in safe_tools:
        pol = tool_policy_manager.get_policy(tool_name)
        assert pol["cacheable"] is True, f"{tool_name} should be cacheable"
        assert pol["ttl_seconds"] == 300, f"{tool_name} should have default 300s TTL"
        assert pol["deduplicate_history"] is True
        assert tool_policy_manager.is_cacheable(tool_name) is True
        assert tool_cache.is_eligible(tool_name) is True


def test_mutation_tool_prefix_blocking():
    """Verify mutation prefixes infer non-cacheable mutation with 0s TTL and are blocked from storage."""
    mutation_tools = [
        "process_payment",
        "charge_credit_card",
        "pay_vendor",
        "delete_user_record",
        "execute_trade",
        "modify_account_balance",
        "cancel_reservation",
        "refund_customer_charge"
    ]
    for tool_name in mutation_tools:
        pol = tool_policy_manager.get_policy(tool_name)
        assert pol["cacheable"] is False, f"{tool_name} must NOT be cacheable"
        assert pol["ttl_seconds"] == 0
        assert pol["deduplicate_history"] is False
        assert tool_policy_manager.is_cacheable(tool_name) is False
        assert tool_cache.is_eligible(tool_name) is False

        # Attempting to store mutation tool must be rejected
        key = tool_cache.store_tool_call(
            tool_name=tool_name,
            arguments={"id": "tx_123", "amount": 100},
            output='{"status": "CHARGED"}'
        )
        assert key == "", f"store_tool_call must reject mutation {tool_name}"

        # Lookup must return miss
        is_hit, out, _ = tool_cache.lookup_tool_call(tool_name, {"id": "tx_123", "amount": 100})
        assert is_hit is False
        assert out is None


def test_custom_tool_policy_management_and_sqlite_persistence():
    """Verify runtime policy overrides, SQLite durability across instances, and policy reset."""
    tool_name = "lookup_order_details"
    args = {"order_id": "ord_999"}
    output = '{"order_id": "ord_999", "status": "shipped"}'

    # 1. Default safe policy
    def_pol = tool_policy_manager.get_policy(tool_name)
    assert def_pol["ttl_seconds"] == 300

    # 2. Update policy to custom TTL 600s
    updated = tool_policy_manager.set_policy(tool_name, {
        "ttl_seconds": 600,
        "cacheable": True,
        "type": "business_api",
        "deduplicate_history": True
    })
    assert updated["ttl_seconds"] == 600

    # Verify active policy
    assert tool_policy_manager.get_ttl(tool_name) == 600

    # 3. Verify SQLite durability across fresh ToolPolicyManager instance
    from server.tool_replayer import ToolPolicyManager
    fresh_mgr = ToolPolicyManager()
    assert fresh_mgr.get_ttl(tool_name) == 600

    # 4. Store and replay with custom policy
    key = tool_cache.store_tool_call(tool_name, args, output)
    assert key != ""
    is_hit, cached_val, _ = tool_cache.lookup_tool_call(tool_name, args)
    assert is_hit is True
    assert cached_val == output

    # 5. Delete policy override (revert to default)
    deleted = tool_policy_manager.delete_policy(tool_name)
    assert deleted is True
    assert tool_policy_manager.get_ttl(tool_name) == 300


def test_business_tool_ttl_expiration():
    """Verify business tool execution expires precisely after TTL elapsed."""
    tool_name = "lookup_stock_price"
    args = {"symbol": "GOOG"}
    output = '{"price": 182.50}'

    # Configure short 1-second TTL
    tool_policy_manager.set_policy(tool_name, {
        "ttl_seconds": 1,
        "cacheable": True
    })

    tool_cache.store_tool_call(tool_name, args, output)

    # Immediate lookup -> HIT
    is_hit, cached_out, _ = tool_cache.lookup_tool_call(tool_name, args)
    assert is_hit is True
    assert cached_out == output

    # Sleep past TTL
    time.sleep(1.1)

    # Subsequent lookup -> MISS (expired)
    is_hit_after, cached_after, _ = tool_cache.lookup_tool_call(tool_name, args)
    assert is_hit_after is False
    assert cached_after is None


def test_gateway_tool_policy_endpoints(client):
    """Verify REST API GET, POST, DELETE at /v1/agent/tools/policies."""
    # 1. GET all policies
    res_list = client.get("/v1/agent/tools/policies")
    assert res_list.status_code == 200
    data = res_list.json()
    assert data["status"] == "OK"
    assert "read_file" in data["policies"]

    # 2. GET single tool policy
    res_single = client.get("/v1/agent/tools/policies?tool_name=read_file")
    assert res_single.status_code == 200
    assert res_single.json()["tool_name"] == "read_file"
    assert res_single.json()["policy"]["type"] == "target_file"

    # 3. POST single policy
    post_body = {
        "tool_name": "query_customer_crm",
        "ttl_seconds": 1200,
        "cacheable": True,
        "type": "business_api",
        "deduplicate_history": True
    }
    res_post = client.post("/v1/agent/tools/policies", json=post_body)
    assert res_post.status_code == 200
    assert res_post.json()["status"] == "UPDATED"
    assert res_post.json()["policy"]["ttl_seconds"] == 1200

    # Verify policy in effect
    assert tool_policy_manager.get_ttl("query_customer_crm") == 1200

    # 4. POST batch policies
    batch_body = {
        "policies": {
            "fetch_inventory": {"ttl_seconds": 180, "cacheable": True},
            "lookup_shipping": {"ttl_seconds": 450, "cacheable": True}
        }
    }
    res_batch = client.post("/v1/agent/tools/policies", json=batch_body)
    assert res_batch.status_code == 200
    assert res_batch.json()["count"] == 2
    assert tool_policy_manager.get_ttl("fetch_inventory") == 180
    assert tool_policy_manager.get_ttl("lookup_shipping") == 450

    # 5. DELETE policy
    res_del = client.delete("/v1/agent/tools/policies?tool_name=query_customer_crm")
    assert res_del.status_code == 200
    assert res_del.json()["status"] == "DELETED"
    # Reverted to default safe prefix (300)
    assert tool_policy_manager.get_ttl("query_customer_crm") == 300


def test_gateway_tool_record_and_replay_with_mutation_protection(client):
    """Verify /v1/agent/tool_record and /v1/agent/tool_replay enforce policies and reject mutations."""
    # 1. Safe business tool record and replay
    record_payload = {
        "tool_name": "lookup_account_tier",
        "arguments": {"user_id": "usr_42"},
        "output": '{"tier": "enterprise", "credits": 50000}'
    }
    res_rec = client.post("/v1/agent/tool_record", json=record_payload)
    assert res_rec.status_code == 200
    rec_data = res_rec.json()
    assert rec_data["status"] == "STORED"
    assert rec_data["cached"] is True
    assert rec_data["ttl_seconds"] == 300

    replay_payload = {
        "tool_name": "lookup_account_tier",
        "arguments": {"user_id": "usr_42"}
    }
    res_rep = client.post("/v1/agent/tool_replay", json=replay_payload)
    assert res_rep.status_code == 200
    rep_data = res_rep.json()
    assert rep_data["status"] == "HIT"
    assert rep_data["cached"] is True
    assert "enterprise" in rep_data["output"]

    # 2. Mutation tool record attempt -> REJECTED
    mutation_payload = {
        "tool_name": "charge_customer_card",
        "arguments": {"user_id": "usr_42", "amount": 250},
        "output": '{"status": "PAID", "tx_id": "tx_9999"}'
    }
    res_mut = client.post("/v1/agent/tool_record", json=mutation_payload)
    assert res_mut.status_code == 200
    mut_data = res_mut.json()
    assert mut_data["status"] == "REJECTED"
    assert mut_data["cached"] is False
    assert "non-cacheable" in mut_data["reason"]

    # Mutation replay attempt -> MISS
    res_mut_rep = client.post("/v1/agent/tool_replay", json={
        "tool_name": "charge_customer_card",
        "arguments": {"user_id": "usr_42", "amount": 250}
    })
    assert res_mut_rep.status_code == 200
    assert res_mut_rep.json()["status"] == "MISS"


def test_in_line_agent_business_tool_compaction_and_mutation_safety():
    """
    Verify in-line agent compaction auto-records cacheable business tools
    and refrains from recording/compacting mutations.
    """
    customer_info = '{"customer_id": "c1", "name": "Acme Corp", "balance": 15000}' * 5

    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {"role": "user", "content": "Fetch customer c1"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "call_c1_1", "name": "lookup_customer", "input": {"id": "c1"}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call_c1_1", "content": customer_info}
                ]
            },
            {"role": "assistant", "content": "Found customer Acme Corp. Now check again."},
            {"role": "user", "content": "Confirm customer c1"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "call_c1_2", "name": "lookup_customer", "input": {"id": "c1"}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call_c1_2", "content": customer_info}
                ]
            },
            # Now a mutating call
            {"role": "assistant", "content": "Now charging card."},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "call_pay_1", "name": "process_payment", "input": {"id": "c1", "amount": 100}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call_pay_1", "content": '{"status": "APPROVED", "auth": "OK"}'}
                ]
            }
        ]
    }

    compacted, tokens_saved, tools_recorded = compact_and_record_agent_tools(payload)

    # 1. lookup_customer should be recorded
    assert tools_recorded >= 1
    is_hit, val, _ = tool_cache.lookup_tool_call("lookup_customer", {"id": "c1"})
    assert is_hit is True
    assert val == customer_info

    # 2. process_payment should NOT be recorded
    is_pay_hit, _, _ = tool_cache.lookup_tool_call("process_payment", {"id": "c1", "amount": 100})
    assert is_pay_hit is False

    # 3. lookup_customer turn 1 should be compacted
    assert tokens_saved > 0
    msgs = compacted["messages"]
    turn_1_content = msgs[2]["content"][0]["content"]
    assert "OmniCache: Output identical to subsequent execution" in turn_1_content
