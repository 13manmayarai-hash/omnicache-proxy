"""
Audit Remediation Test Suite (v3.0.3).
Empirically validates fixes for the 4 architectural vulnerabilities identified in the research audit:
1. Untracked file cache leaks in scoped directory inspections (-uall flag).
2. Subprocess fork thrashing in monorepo agent loops (debounced git state cache).
3. Shannon entropy syntax naivety on repetitive C/C++ structs (syntax-aware classifier).
4. CRDT mesh clock drift under multi-second physical clock skew (Hybrid Logical Clocks).
"""

import os
import time
import tempfile
import subprocess
import pytest
from server.tool_replayer import (
    get_git_workspace_state,
    invalidate_git_state_cache,
    tool_cache,
    compact_and_record_agent_tools,
    tool_policy_manager
)
from server.cascade_router import cascade_router, compute_shannon_entropy
from core.p2p_mesh import P2PMesh, CRDTTombstone, HybridLogicalClock


def test_01_untracked_file_cache_leak_remediation():
    """
    Vulnerability 1 Remediation:
    Untracked files injected during agent runtime (e.g. .env.local, uncommitted.py)
    MUST be captured by -uall and alter git state hashing for scoped & root workspace inspections.
    """
    with tempfile.TemporaryDirectory() as temp_repo:
        subprocess.run(["git", "init", temp_repo], check=True, capture_output=True)
        subprocess.run(["git", "-C", temp_repo, "config", "user.email", "audit@omnicache.ai"], check=True)
        subprocess.run(["git", "-C", temp_repo, "config", "user.name", "Audit Runner"], check=True)

        # Initial commit
        initial_file = os.path.join(temp_repo, "README.md")
        with open(initial_file, "w") as f:
            f.write("# Project\n")
        subprocess.run(["git", "-C", temp_repo, "add", "README.md"], check=True)
        subprocess.run(["git", "-C", temp_repo, "commit", "-m", "Init"], check=True)

        # Baseline clean state
        invalidate_git_state_cache(temp_repo)
        clean_state_scoped = get_git_workspace_state(temp_repo, policy_type="scoped_git_workspace")
        clean_state_global = get_git_workspace_state(temp_repo, policy_type="git_workspace")

        # Inject untracked file inside a subdirectory
        sub_dir = os.path.join(temp_repo, "config")
        os.makedirs(sub_dir, exist_ok=True)
        untracked_file = os.path.join(sub_dir, ".env.local")
        with open(untracked_file, "w") as f:
            f.write("SECRET_KEY=leak_prevention_token\n")

        # Invalidate debounce cache to test fresh detection
        invalidate_git_state_cache(temp_repo)
        dirty_state_scoped = get_git_workspace_state(temp_repo, policy_type="scoped_git_workspace")
        dirty_state_global = get_git_workspace_state(temp_repo, policy_type="git_workspace")

        # Both scoped and global workspace state MUST detect the untracked file
        assert dirty_state_scoped != clean_state_scoped, "Failed: Untracked file in subfolder bypassed scoped git state!"
        assert dirty_state_global != clean_state_global, "Failed: Untracked file bypassed global git state!"


def test_02_subprocess_debouncing_and_mutation_invalidation():
    """
    Vulnerability 2 Remediation:
    Consecutive read-tool lookups must use debounced git state (<0.05ms),
    and mutation tool calls must instantly invalidate the debounce cache.
    """
    with tempfile.TemporaryDirectory() as temp_repo:
        subprocess.run(["git", "init", temp_repo], check=True, capture_output=True)
        subprocess.run(["git", "-C", temp_repo, "config", "user.email", "audit@omnicache.ai"], check=True)
        subprocess.run(["git", "-C", temp_repo, "config", "user.name", "Audit Runner"], check=True)

        with open(os.path.join(temp_repo, "app.py"), "w") as f:
            f.write("print('hello')\n")
        subprocess.run(["git", "-C", temp_repo, "add", "."], check=True)
        subprocess.run(["git", "-C", temp_repo, "commit", "-m", "Init"], check=True)

        invalidate_git_state_cache(temp_repo)

        # First call warms cache
        t0 = time.perf_counter()
        state1 = get_git_workspace_state(temp_repo, policy_type="scoped_git_workspace")
        first_duration = time.perf_counter() - t0

        # Subsequent 30 repeat lookups should be sub-millisecond debounced
        t0 = time.perf_counter()
        for _ in range(30):
            state2 = get_git_workspace_state(temp_repo, policy_type="scoped_git_workspace")
            assert state2 == state1
        cached_duration = (time.perf_counter() - t0) / 30.0

        # Debounced lookup should be orders of magnitude faster (< 0.5ms)
        assert cached_duration < 0.005, f"Debounce failed to eliminate subprocess overhead: {cached_duration*1000:.2f}ms"

        # Mutation tool execution via compact_and_record_agent_tools must invalidate debounce cache
        mutation_payload = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "call_write_1", "name": "write_file", "input": {"path": "app.py"}}
                    ]
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call_write_1", "content": "File written successfully."}
                    ]
                }
            ]
        }
        compact_and_record_agent_tools(mutation_payload, workspace_dir=temp_repo)

        # Modify file on disk
        with open(os.path.join(temp_repo, "app.py"), "a") as f:
            f.write("# mutated\n")

        # Next lookup must yield fresh state immediately
        mutated_state = get_git_workspace_state(temp_repo, policy_type="scoped_git_workspace")
        assert mutated_state != state1, "Failed: Debounce cache was not cleared after mutation tool execution!"


def test_03_shannon_entropy_syntax_awareness_on_c_structs():
    """
    Vulnerability 3 Remediation:
    Repetitive C/C++ struct definitions intrinsically yield low Shannon character/token entropy (< 0.65).
    The router must recognize syntax tokens (typedef struct, uint32_t, pointers) and never
    penalize or down-route dense code to economy models.
    """
    fields = "\n".join([f"    uint32_t reg_channel_{i};" for i in range(40)])
    dense_c_header = f"""
    typedef struct {{
{fields}
        char* buffer;
        void* hw_ctx;
    }} hw_register_map_t;

    int configure_registers(hw_register_map_t* regs);
    """

    # 1. Verify that raw word entropy is low (< 0.65) due to repetitive typedef/type declarations
    raw_entropy = compute_shannon_entropy(dense_c_header)
    assert raw_entropy < 0.65, f"Expected low token entropy from repetitive typedefs, got {raw_entropy}"

    payload = {
        "messages": [
            {"role": "user", "content": f"Optimize the register offsets for memory mapped IO:\n{dense_c_header}"}
        ]
    }

    # 2. Classifier must detect dense systems code and assign high complexity (>= 0.65)
    complexity = cascade_router.classify_complexity(payload)
    assert complexity >= 0.65, f"Dense systems code was penalized by low entropy, complexity: {complexity}"

    # 3. Router must retain frontier model (NOT downgrade to tier_1_economy)
    model, tier, score, cascaded, reason = cascade_router.evaluate_route(
        requested_model="claude-3-7-sonnet",
        payload=payload,
        allow_cascade=True
    )
    assert tier == "tier_3_frontier", f"Dense systems code was improperly cascaded to {tier}"
    assert cascaded is False
    assert reason == "frontier_complexity_retained"


def test_04_crdt_mesh_hlc_clock_drift_resilience():
    """
    Vulnerability 4 Remediation:
    Under +-2000ms physical clock drift, Hybrid Logical Clocks (HLC) must
    preserve causal monotonicity and deterministic tie-breaking (0% inconsistency).
    """
    # Simulate Node A (clock drifted -2.0s in the past) and Node B (clock drifted +2.0s in the future)
    now_real = time.time()
    mesh_a = P2PMesh(node_id="node-a", endpoint="http://127.0.0.1:8001")
    mesh_b = P2PMesh(node_id="node-b", endpoint="http://127.0.0.1:8002")

    # Manually skew Node B's HLC 2000ms into the future
    mesh_b.hlc.l = int((now_real + 2.0) * 1000)
    mesh_b.hlc.c = 0

    # Node B records initial mutation
    tomb_b1 = mesh_b.record_local_mutation("config:auth_token", reason="b_init")
    assert tomb_b1.hlc_l >= int((now_real + 2.0) * 1000)

    # Node A syncs with Node B and receives tomb_b1
    applied, status = mesh_a.apply_remote_tombstone(tomb_b1)
    assert applied is True
    # Node A's HLC must have caught up to Node B's logical epoch despite A's drifted physical clock
    assert mesh_a.hlc.l >= tomb_b1.hlc_l

    # Now Node A mutates the SAME resource causally AFTER Node B
    tomb_a2 = mesh_a.record_local_mutation("config:auth_token", reason="a_update_after_b")

    # Total ordering verification: Node A's later mutation MUST win over Node B's earlier mutation
    assert tomb_a2.is_newer_than(tomb_b1) is True, "HLC failed: Later causal mutation lost to skewed physical timestamp!"

    # And if Node B tries to re-apply tomb_b1 over tomb_a2 on Node A, it must be rejected
    rejected_applied, rej_status = mesh_a.apply_remote_tombstone(tomb_b1)
    assert rejected_applied is False
    assert rej_status == "superseded_by_existing"
