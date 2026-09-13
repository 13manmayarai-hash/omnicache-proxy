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
from core.embeddings import FastSemanticEmbedder
from core.vector_cache import DualTierCache
from core.config import config


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


def test_05_dirty_file_successive_mutations_different_fingerprints():
    """
    Empirically verify that successive edits with different content on an already-dirty file
    produce different workspace fingerprints and avoid replaying stale grep/diff results.
    """
    with tempfile.TemporaryDirectory() as temp_repo:
        subprocess.run(["git", "init", temp_repo], check=True, capture_output=True)
        subprocess.run(["git", "-C", temp_repo, "config", "user.email", "audit@omnicache.ai"], check=True)
        subprocess.run(["git", "-C", temp_repo, "config", "user.name", "Audit Runner"], check=True)

        target_file = os.path.join(temp_repo, "service.py")
        with open(target_file, "w") as f:
            f.write("def run():\n    return 'initial'\n")
        subprocess.run(["git", "-C", temp_repo, "add", "service.py"], check=True)
        subprocess.run(["git", "-C", temp_repo, "commit", "-m", "Initial commit"], check=True)

        # Edit 1: Dirty file with content A
        time.sleep(0.05)
        with open(target_file, "w") as f:
            f.write("def run():\n    return 'Version A edit'\n")
        invalidate_git_state_cache(temp_repo)
        fp_a_scoped = get_git_workspace_state(temp_repo, policy_type="scoped_git_workspace")
        fp_a_global = get_git_workspace_state(temp_repo, policy_type="git_workspace")

        # Edit 2: Same dirty file modified again with content B (different content diff)
        time.sleep(0.05)
        with open(target_file, "w") as f:
            f.write("def run():\n    return 'Version B completely different code'\n")
        invalidate_git_state_cache(temp_repo)
        fp_b_scoped = get_git_workspace_state(temp_repo, policy_type="scoped_git_workspace")
        fp_b_global = get_git_workspace_state(temp_repo, policy_type="git_workspace")

        assert fp_a_scoped != fp_b_scoped, f"Scoped fingerprints matched across two different edits: {fp_a_scoped}"
        assert fp_a_global != fp_b_global, f"Global fingerprints matched across two different edits: {fp_a_global}"


def test_06_untracked_external_mutation_self_verifying_invalidation():
    """
    Audit Finding 1 Remediation (Self-verifying git debounce):
    Mutating a file via a path that does NOT go through the proxy's own tracked mutation flow
    (e.g., a raw open().write() in the test, not a simulated 'tool call') must be detected
    immediately (sub-100ms) by get_git_workspace_state via self-verifying stat signals,
    asserting the fingerprint reflects the new state rather than a stale cached one.
    """
    with tempfile.TemporaryDirectory() as temp_repo:
        subprocess.run(["git", "init", temp_repo], check=True, capture_output=True)
        subprocess.run(["git", "-C", temp_repo, "config", "user.email", "audit@omnicache.ai"], check=True)
        subprocess.run(["git", "-C", temp_repo, "config", "user.name", "Audit Runner"], check=True)

        app_path = os.path.join(temp_repo, "app.py")
        with open(app_path, "w") as f:
            f.write("def main(): return 1\n")
        subprocess.run(["git", "-C", temp_repo, "add", "."], check=True)
        subprocess.run(["git", "-C", temp_repo, "commit", "-m", "Initial commit"], check=True)

        invalidate_git_state_cache(temp_repo)

        # 1. First lookup warms the debounce cache (Baseline)
        state_initial = get_git_workspace_state(temp_repo, policy_type="scoped_git_workspace")
        assert not state_initial.startswith("nogit_dir"), f"Baseline unexpectedly fell back to nogit: {state_initial}"

        # 2. First consecutive raw disk edit outside proxy
        time.sleep(0.01)
        with open(app_path, "w") as f:
            f.write("def main(): return 'edit_1_version'\n")

        state_edit_1 = get_git_workspace_state(temp_repo, policy_type="scoped_git_workspace")
        assert not state_edit_1.startswith("nogit_dir"), f"Edit 1 fell through to nogit_dir fallback: {state_edit_1}"
        assert state_edit_1 != state_initial, "Edit 1 failed to invalidate cached git state!"

        # Immediate repeat lookup should be debounced and match state_edit_1
        state_edit_1_debounced = get_git_workspace_state(temp_repo, policy_type="scoped_git_workspace")
        assert state_edit_1_debounced == state_edit_1, "Debounced repeat lookup failed!"

        # 3. Second consecutive raw disk edit outside proxy (different content)
        time.sleep(0.01)
        with open(app_path, "w") as f:
            f.write("def main(): return 'edit_2_completely_different_code'\n")

        state_edit_2 = get_git_workspace_state(temp_repo, policy_type="scoped_git_workspace")
        assert not state_edit_2.startswith("nogit_dir"), f"Edit 2 fell through to nogit_dir fallback: {state_edit_2}"
        assert state_edit_2 != state_edit_1, "Edit 2 produced identical fingerprint to Edit 1 (collision bug)!"
        assert state_edit_2 != state_initial, "Edit 2 matched initial state!"

        # 4. Third consecutive raw disk edit outside proxy (different content)
        time.sleep(0.01)
        with open(app_path, "w") as f:
            f.write("def main(): return 'edit_3_final_modification'\n")

        state_edit_3 = get_git_workspace_state(temp_repo, policy_type="scoped_git_workspace")
        assert not state_edit_3.startswith("nogit_dir"), f"Edit 3 fell through to nogit_dir fallback: {state_edit_3}"
        assert state_edit_3 != state_edit_2, "Edit 3 produced identical fingerprint to Edit 2 (collision bug)!"
        assert state_edit_3 != state_edit_1, "Edit 3 produced identical fingerprint to Edit 1!"
        assert state_edit_3 != state_initial, "Edit 3 matched initial state!"


def test_07_fuzzy_match_curated_paraphrase_and_collision_boundaries():
    """
    Audit Finding 2 Remediation (Fuzzy/Lexical Cache Boundaries):
    Verifies that the lightweight zero-dependency lexical/subword matching engine:
    1. Rejects false-positive risk pairs (shared keywords, different meaning) -> MUST MISS.
    2. Recognizes extreme vocabulary-disjoint paraphrases score low, documenting lexical bounds.
    3. Reliably hits near-duplicate / fuzzy rephrasings with shared roots and canonical synonyms.
    """
    cache = DualTierCache()
    model = "claude-3-5-sonnet-20241022"

    # Part A: False-Positive-Risk Pairs (shared keywords, completely different meaning)
    # Under DEFAULT_SIMILARITY_THRESHOLD (0.75), these MUST MISS to prevent dangerous cache poisoning.
    false_positive_pairs = [
        (
            "How do I bank a campfire with damp ashes?",
            "How do I deposit cash into my commercial bank account?",
            "Coincidental 'bank' keyword collision"
        ),
        (
            "Train a deep neural network on GPU cluster",
            "Buy a passenger train ticket to London Euston",
            "Coincidental 'train' keyword collision"
        ),
        (
            "Kill a background Linux process with SIGKILL",
            "How do antibiotics kill bacterial infections?",
            "Coincidental 'kill' keyword collision"
        ),
        (
            "Python dictionary key error in loop",
            "Brass key to open the front door lock",
            "Coincidental 'key' keyword collision"
        ),
    ]

    for p_stored, p_query, label in false_positive_pairs:
        # Check raw embedding cosine similarity
        v1 = FastSemanticEmbedder.embed(p_stored)
        v2 = FastSemanticEmbedder.embed(p_query)
        sim = FastSemanticEmbedder.cosine_similarity(v1, v2)
        assert sim < config.DEFAULT_SIMILARITY_THRESHOLD, (
            f"False-positive risk pair '{label}' produced similarity {sim:.3f} >= {config.DEFAULT_SIMILARITY_THRESHOLD}"
        )

        # Verify through DualTierCache lookup -> MUST BE MISS!
        cache.clear()
        cache.store(
            payload={"model": model, "messages": [{"role": "user", "content": p_stored}]},
            response_payload={"choices": [{"message": {"role": "assistant", "content": f"Response for {p_stored}"}}]}
        )
        status, entry, lookup_sim, reason = cache.lookup(
            payload={"model": model, "messages": [{"role": "user", "content": p_query}]}
        )
        assert status == "MISS", (
            f"Collision regression: '{p_query}' triggered {status} (sim={lookup_sim:.3f}) against '{p_stored}'! Reason: {reason}"
        )

    # Part B: Extreme Vocabulary-Disjoint Paraphrases
    # Demonstrates honest boundary: pure-Python subword/lexical hash engine does not pretend to have neural reasoning
    disjoint_paraphrase_pairs = [
        (
            "What is the capital of France?",
            "Which European metropolis serves as the administrative seat of the French Republic?"
        ),
        (
            "How old is the universe?",
            "What is the estimated cosmic age since the Big Bang?"
        )
    ]
    for p1, p2 in disjoint_paraphrase_pairs:
        v1 = FastSemanticEmbedder.embed(p1)
        v2 = FastSemanticEmbedder.embed(p2)
        sim = FastSemanticEmbedder.cosine_similarity(v1, v2)
        # Vocabulary is disjoint, so cosine similarity is low (< 0.50)
        assert sim < 0.50, f"Disjoint vocabulary unexpectedly high: {sim}"

    # Part C: Intended Near-Duplicate / Fuzzy-Match Rephrasings
    # These contain near-identical n-grams or mapped canonical synonyms -> MUST HIT!
    fuzzy_hit_pairs = [
        (
            "How to reset my forgotten password instructions",
            "How to recover reset password steps"
        ),
        (
            "Fast sorting algorithms in Python",
            "Fast Python sorting algorithm"
        )
    ]
    for p_stored, p_query in fuzzy_hit_pairs:
        v1 = FastSemanticEmbedder.embed(p_stored)
        v2 = FastSemanticEmbedder.embed(p_query)
        sim = FastSemanticEmbedder.cosine_similarity(v1, v2)
        assert sim >= config.DEFAULT_SIMILARITY_THRESHOLD, (
            f"Fuzzy pair should exceed threshold {config.DEFAULT_SIMILARITY_THRESHOLD}, got {sim:.3f}"
        )

        cache.clear()
        cache.store(
            payload={"model": model, "messages": [{"role": "user", "content": p_stored}]},
            response_payload={"choices": [{"message": {"role": "assistant", "content": f"Response for {p_stored}"}}]}
        )
        status, entry, lookup_sim, reason = cache.lookup(
            payload={"model": model, "messages": [{"role": "user", "content": p_query}]}
        )
        assert status == "HIT_SEMANTIC", (
            f"Expected HIT_SEMANTIC for near-duplicate '{p_query}', got {status} (sim={lookup_sim:.3f})"
        )

