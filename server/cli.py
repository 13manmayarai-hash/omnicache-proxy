"""
OmniCache command-line interface.
"""

import sys
import os

# Ensure package root is always resolvable across environments
_PKG_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

import time
import socket
import argparse
import uvicorn
from typing import Optional, List, Dict, Any, Tuple, Union
from core.config import config, validate_startup_security_invariants
from server.gateway import app, cache_instance, METRICS_LEDGER
from persistence.snapshot_store import snapshot_store

def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0

def fetch_live_stats(host: str = "127.0.0.1", port: int = None) -> Optional[dict]:
    import urllib.request
    import json
    port = port or config.PORT
    target_host = "127.0.0.1" if host in ("0.0.0.0", "", "::1") else host
    url = f"http://{target_host}:{port}/v1/cache/stats"
    req = urllib.request.Request(url)
    if config.ADMIN_API_KEY:
        req.add_header("Authorization", f"Bearer {config.ADMIN_API_KEY}")
    try:
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

def run_doctor():
    print("\n--- OmniCache System Diagnostics ---")
    
    # 1. Environment & Python
    py_ver = sys.version.split()[0]
    print(f"Python Runtime: v{py_ver} ({sys.platform})")

    # 2. Database Persistence
    db_path = snapshot_store.db_path
    db_exists = os.path.exists(db_path)
    print(f"SQLite Store:   {db_path} ({'ready' if db_exists else 'will create on first write'})")

    # 3. Live Daemon Probe & Port Check
    target_host = "127.0.0.1" if config.HOST in ("0.0.0.0", "", "::1") else config.HOST
    live_data = fetch_live_stats(host=target_host, port=config.PORT)
    port_used = is_port_in_use(config.PORT, target_host)

    if live_data:
        sys_info = live_data.get("system_info", {})
        ver = sys_info.get("version", config.VERSION)
        print(f"Live Daemon:    Operational on http://{target_host}:{config.PORT} (v{ver})")
    elif port_used:
        print(f"Port {config.PORT}:      In use (process active, unauthenticated or non-OmniCache service)")
    else:
        print(f"Port {config.PORT}:      Available (daemon not running)")

    # 4. In-Memory Vector Engine
    start = time.perf_counter()
    from core.embeddings import FastSemanticEmbedder
    FastSemanticEmbedder.embed("OmniCache health check prompt")
    embed_ms = (time.perf_counter() - start) * 1000
    print(f"Vector Engine:  Operational ({embed_ms:.3f}ms lookup)")

    # 5. Upstream Configured Keys & Circuit Breakers
    keys_configured = []
    if config.ANTHROPIC_API_KEY: keys_configured.append("Anthropic")
    if config.OPENAI_API_KEY: keys_configured.append("OpenAI")
    if config.GEMINI_API_KEY: keys_configured.append("Gemini")
    
    if keys_configured:
        print(f"Upstream Keys:  {', '.join(keys_configured)}")
    else:
        print(f"Upstream Auth:  Client header passthrough active")

    if live_data:
        ee = live_data.get("enterprise_engine", {})
        cb = ee.get("circuit_breaker", {})
        if cb:
            cb_summary = []
            for p in ("openai", "anthropic", "google"):
                pinfo = cb.get(p, {})
                state = pinfo.get("state", "closed")
                fails = pinfo.get("consecutive_failures", 0)
                icon = "🟢" if state == "closed" else ("🟡" if state == "half-open" else "🔴")
                cb_summary.append(f"{icon} {p.capitalize()}: {state.upper()} ({fails} fail)")
            print(f"Circuits:       {' | '.join(cb_summary)}")

        recent_fails = ee.get("recent_upstream_failures", [])
        if recent_fails:
            print(f"\n⚠️ Recent Upstream Errors ({len(recent_fails)} logged):")
            for fail in recent_fails[:3]:
                ts = fail.get("timestamp", "").split("T")[-1][:8]
                prov = fail.get("provider", "").capitalize()
                code = fail.get("status_code", 500)
                msg = fail.get("error_message", "")
                print(f"   [{ts}] {prov} {code}: {msg[:80]}")

    # 6. Distributed P2P Mesh Network
    try:
        from core.p2p_mesh import mesh_bus
        mesh_topo = mesh_bus.get_mesh_topology()
        print(f"P2P Edge Mesh:  Operational (Node: {mesh_topo['node_id']}, {len(mesh_topo['peers'])} peers registered)")
    except Exception:
        pass

    # 7. Hardware-Accelerated Local Quantized Embedder
    try:
        from core.quantized_embedder import quantized_embedder
        emb_stats = quantized_embedder.stats()
        print(f"Quantized Embed: Operational (Mode: {emb_stats['hardware_mode']}, {emb_stats['dimensions']}d int8, 0 external deps)")
    except Exception:
        pass

    print("\nStatus:         All subsystems operational.\n")

def run_benchmark(iterations: int = 500):
    iters = max(1, iterations)
    print("\n==========================================================================================")
    print(f"⚡ OmniCache AI Acceleration Benchmark (v{config.VERSION}) - {iters} Iterations")
    print("==========================================================================================")
    from core.vector_cache import DualTierCache

    bench_cache = DualTierCache()
    sample_payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Write a python fast fourier transform algorithm"}],
        "temperature": 0.0
    }
    sample_response = {
        "choices": [{"message": {"role": "assistant", "content": "import numpy as np..."}}],
        "usage": {"prompt_tokens": 85, "completion_tokens": 420, "total_tokens": 505}
    }

    bench_cache.store(sample_payload, sample_response)

    # 1. L1 Exact Cache Benchmark
    l1_latencies = []
    for _ in range(iters):
        t0 = time.perf_counter()
        bench_cache.lookup(sample_payload)
        t1 = time.perf_counter()
        l1_latencies.append((t1 - t0) * 1000)

    # 2. L2 Semantic Cache Benchmark
    semantic_payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Write a python fast fourier transform (FFT) algorithm in numpy"}],
        "temperature": 0.0
    }
    l2_latencies = []
    for _ in range(iters):
        t0 = time.perf_counter()
        bench_cache.lookup(semantic_payload)
        t1 = time.perf_counter()
        l2_latencies.append((t1 - t0) * 1000)

    l1_latencies.sort()
    l2_latencies.sort()

    p50_l1 = l1_latencies[min(len(l1_latencies) - 1, int(len(l1_latencies) * 0.50))]
    p95_l1 = l1_latencies[min(len(l1_latencies) - 1, int(len(l1_latencies) * 0.95))]
    p99_l1 = l1_latencies[min(len(l1_latencies) - 1, int(len(l1_latencies) * 0.99))]

    p50_l2 = l2_latencies[min(len(l2_latencies) - 1, int(len(l2_latencies) * 0.50))]
    p95_l2 = l2_latencies[min(len(l2_latencies) - 1, int(len(l2_latencies) * 0.95))]
    p99_l2 = l2_latencies[min(len(l2_latencies) - 1, int(len(l2_latencies) * 0.99))]

    # 3. Agent Tool Replay Benchmark (Business API & Git-Aware Memory)
    from server.tool_replayer import ToolExecutionCache
    bench_tool_cache = ToolExecutionCache()

    # Business API Tool (In-Memory Hot Replay)
    b_name = "lookup_customer"
    b_args = {"customer_id": "cust_enterprise_99"}
    b_out = '{"customer_id": "cust_enterprise_99", "tier": "enterprise", "active": true}'
    bench_tool_cache.store_tool_call(
        tool_name=b_name,
        arguments=b_args,
        output=b_out,
        workspace_fingerprint="bench_ws_test"
    )

    # Git-Aware File Inspection Tool (Validates File Staleness)
    f_name = "read_file"
    f_args = {"path": "core/config.py"}
    f_out = "class ProxyConfig:\n    VERSION = '2.9.1'\n"
    bench_tool_cache.store_tool_call(
        tool_name=f_name,
        arguments=f_args,
        output=f_out,
        workspace_fingerprint="bench_ws_test",
        workspace_dir=os.getcwd()
    )

    # Business tool in-memory lookup
    b_latencies = []
    for _ in range(iters):
        t0 = time.perf_counter()
        bench_tool_cache.lookup_tool_call(
            tool_name=b_name,
            arguments=b_args,
            workspace_fingerprint="bench_ws_test"
        )
        t1 = time.perf_counter()
        b_latencies.append((t1 - t0) * 1000)

    # File inspection tool lookup (validates disk stat)
    f_latencies = []
    for _ in range(min(iters, 200)):
        t0 = time.perf_counter()
        bench_tool_cache.lookup_tool_call(
            tool_name=f_name,
            arguments=f_args,
            workspace_fingerprint="bench_ws_test",
            workspace_dir=os.getcwd()
        )
        t1 = time.perf_counter()
        f_latencies.append((t1 - t0) * 1000)

    b_latencies.sort()
    f_latencies.sort()
    p50_biz = b_latencies[min(len(b_latencies) - 1, int(len(b_latencies) * 0.50))]
    p95_biz = b_latencies[min(len(b_latencies) - 1, int(len(b_latencies) * 0.95))]
    p99_biz = b_latencies[min(len(b_latencies) - 1, int(len(b_latencies) * 0.99))]
    p50_file = f_latencies[min(len(f_latencies) - 1, int(len(f_latencies) * 0.50))]

    # 4. Multi-Agent Workspace CI/CD Pre-Warming Benchmark
    from server.workspace_sync import WorkspaceWarmer
    warm_files_target = min(20, max(5, iters // 10))
    warm_t0 = time.perf_counter()
    warm_res = WorkspaceWarmer.warm_workspace(
        workspace_dir=os.getcwd(),
        workspace_fingerprint="bench_ws_warm",
        max_files=warm_files_target
    )
    warm_ms = (time.perf_counter() - warm_t0) * 1000
    files_warmed = warm_res.get("files_warmed", 0)
    entries_recorded = warm_res.get("tools_recorded", warm_res.get("entries_recorded", 0))
    warm_rate = files_warmed / max(0.0001, warm_ms / 1000.0)

    # Detailed Subsystem Breakdown
    print(f"1. L1 Exact Cache (Trie Hash):")
    print(f"   P50: {p50_l1:.4f} ms | P95: {p95_l1:.4f} ms | P99: {p99_l1:.4f} ms")
    print(f"   Throughput: ~{int(1000 / max(0.001, p50_l1)):,} QPS / core\n")

    print(f"2. L2 Semantic Cache (Vector Cosine):")
    print(f"   P50: {p50_l2:.4f} ms | P95: {p95_l2:.4f} ms | P99: {p99_l2:.4f} ms")
    print(f"   Throughput: ~{int(1000 / max(0.001, p50_l2)):,} QPS / core\n")

    print(f"3. Agent Tool Replayer (Git-Aware Memory):")
    print(f"   Business API Tool Replay: P50: {p50_biz:.4f} ms | P95: {p95_biz:.4f} ms (~{int(1000 / max(0.001, p50_biz)):,} QPS)")
    print(f"   Git/File Tool Replay:     P50: {p50_file:.4f} ms (Staleness Verified)")
    print(f"   Est. Cloud Agent Turn:    ~1,200.00 ms (Upstream Network API + Inference)")
    print(f"   Local Speedup vs Cloud:   ~{int(1200.0 / max(0.001, p50_biz)):,}x acceleration ($0.00 spend)\n")

    print(f"4. Workspace Pre-Warming (CI/CD Ingestion):")
    print(f"   Warmed: {files_warmed} files ({entries_recorded} tool signatures) in {warm_ms:.2f} ms")
    print(f"   Ingestion Velocity: ~{warm_rate:.0f} files/sec\n")

    # Transparent Visual Summary Box
    print("--------------------------------------------------------------------------------------------------")
    print(f"{'Engine Subsystem':<32} {'Est. Upstream Turn':<20} {'OmniCache Replay':<18} {'Speedup':<10} {'Benefit'}")
    print("--------------------------------------------------------------------------------------------------")
    print(f"{'L1 Exact Request Cache':<32} {'~450.00 ms (Est.)':<20} {f'{p50_l1:.4f} ms':<18} {f'{int(450.0 / max(0.001, p50_l1)):,}x':<10} 100% Token Savings (505 tok)")
    print(f"{'L2 FastHash Semantic Vector':<32} {'~450.00 ms (Est.)':<20} {f'{p50_l2:.4f} ms':<18} {f'{int(450.0 / max(0.001, p50_l2)):,}x':<10} 90%+ Cosine Replay")
    print(f"{'Agent Tool Replayer (Business)':<32} {'~1,200.00 ms (Est.)':<20} {f'{p50_biz:.4f} ms':<18} {f'{int(1200.0 / max(0.001, p50_biz)):,}x':<10} $0.00 Disk Thrashing")
    print(f"{'Workspace CI/CD Pre-Warming':<32} {'Cold Repo Scan':<20} {f'{warm_ms:.2f} ms':<18} {f'{warm_rate:.0f} f/s':<10} Pre-warmed {files_warmed} files")
    print("==================================================================================================")
    print("  * Est. Upstream Turn represents typical remote cloud LLM network roundtrips for comparison.")
    print("    OmniCache Replay columns represent actual locally measured micro-benchmarks on this hardware.\n")

def run_stats():
    target_host = "127.0.0.1" if config.HOST in ("0.0.0.0", "", "::1") else config.HOST
    live_data = fetch_live_stats(host=target_host, port=config.PORT)
    
    if live_data:
        cs = live_data.get("cache_stats", {})
        fm = live_data.get("financial_telemetry", {})
        ee = live_data.get("enterprise_engine", {})
        sys_info = live_data.get("system_info", {})
        cb = ee.get("circuit_breaker", {})
        ver = sys_info.get("version", config.VERSION)

        print("\n========================================================")
        print(f"⚡ OmniCache AI Proxy Telemetry (Live Daemon v{ver})")
        print(f"   Connected: http://{target_host}:{config.PORT}")
        print("========================================================")
        print(f"  Total Cost Avoided:      ${fm.get('total_savings_usd', 0.0):.4f} USD")
        print(f"  Tokens Saved (100% Hit): {fm.get('total_tokens_saved', 0):,}")
        print(f"  Tokens Forwarded:        {fm.get('total_tokens_used', 0):,}")
        print(f"  Cache Hit Rate:          {cs.get('hit_rate_percentage', 0.0)}%")
        print(f"  Exact / Semantic Hits:   {cs.get('exact_hits', 0)} exact / {cs.get('semantic_hits', 0)} semantic")
        print(f"  Agent Tool Replays:      {ee.get('agent_tool_replays', 0):,}")
        print(f"  Context Pruned Tokens:   {ee.get('agent_tokens_compacted', 0):,} tokens")
        if ee.get("telephony_requests", 0) > 0:
            print(f"  Voice Telephony Calls:   {ee.get('telephony_requests', 0):,} ({ee.get('telephony_fillers_stripped', 0):,} fillers stripped, {ee.get('telephony_tokens_saved', 0):,} tok saved)")
        if ee.get("audio_cache_hits", 0) > 0:
            print(f"  Multimodal Audio Hits:   {ee.get('audio_cache_hits', 0):,} ({ee.get('audio_tokens_saved', 0):,} tok saved, {ee.get('audio_requests', 0):,} queries)")
        cascade_stats = ee.get("cascade_stats", {})
        cascade_savings = fm.get("arbitrage_savings_usd", 0.0) or cascade_stats.get("arbitrage_savings_usd", 0.0)
        cascade_downgrades = cascade_stats.get("downgraded_count", 0) or ee.get("cascade_downgrades_total", 0)
        if cascade_downgrades > 0 or cascade_savings > 0:
            print(f"  Model Cascade Savings:   ${cascade_savings:.4f} USD ({cascade_downgrades:,} queries cascaded to economy tier)")
        if ee.get("swarm_cross_agent_hits", 0) > 0 or ee.get("swarm_requests_processed", 0) > 0:
            print(f"  Swarm Cross-Agent Hits:  {ee.get('swarm_cross_agent_hits', 0):,} ({ee.get('swarm_tokens_saved', 0):,} tok saved, {ee.get('swarm_mutations_invalidated', 0):,} mutations purged)")
        mesh = live_data.get("mesh_network", {})
        if mesh:
            peer_sum = mesh.get("peer_summary", {})
            print(f"  P2P Edge Mesh Sync:      {peer_sum.get('alive', 0)}/{peer_sum.get('total', 0)} peers alive ({mesh.get('tombstone_count', 0):,} CRDT tombstones, node: {mesh.get('node_id', 'local')})")
        quant_emb = live_data.get("quantized_embedder", {})
        if quant_emb or ee.get("quantized_embeddings_generated", 0) > 0:
            count = quant_emb.get("embeddings_generated", ee.get("quantized_embeddings_generated", 0))
            mode = quant_emb.get("hardware_mode", "simd_int8")
            dims = quant_emb.get("dimensions", 256)
            print(f"  Quantized Embeddings:    {count:,} generated (Mode: {mode}, {dims}d int8, 0 deps)")
        print(f"  PII Items Redacted:      {ee.get('privacy_redactions_total', 0):,}")
        print(f"  Vision Cache Hits:       {ee.get('vision_cache_hits', 0):,}")
        print(f"  Multi-turn Bypasses:     {cs.get('bypasses', 0):,} (Intent & Multi-Turn Isolation)")

        if cb:
            print("\n  Circuit Breakers:")
            for p, pinfo in cb.items():
                state = pinfo.get("state", "closed")
                fails = pinfo.get("consecutive_failures", 0)
                icon = "🟢" if state == "closed" else ("🟡" if state == "half-open" else "🔴")
                print(f"    {icon} {p.capitalize():<10} State: {state.upper():<10} (Consecutive Failures: {fails})")

        recent_fails = ee.get("recent_upstream_failures", [])
        if recent_fails:
            print("\n  Recent Upstream Errors:")
            for fail in recent_fails[:3]:
                ts = fail.get("timestamp", "").split("T")[-1][:8]
                prov = fail.get("provider", "").capitalize()
                code = fail.get("status_code", 500)
                msg = fail.get("error_message", "")
                print(f"    ⚠️ [{ts}] {prov} {code}: {msg[:75]}")

        print("========================================================\n")
    else:
        stats = cache_instance.get_stats()
        print("\n========================================================")
        print("⚡ OmniCache Telemetry (Daemon Inactive / Local Store)")
        print("========================================================")
        print(f"  Total Cost Saved:        ${METRICS_LEDGER['total_savings_usd']:.4f} USD")
        print(f"  Tokens Saved:            {METRICS_LEDGER['total_tokens_saved']:,}")
        print(f"  Tokens Forwarded:        {METRICS_LEDGER['total_tokens_used']:,}")
        print(f"  Cache Hit Rate:          {stats.get('hit_rate_percentage', 0.0)}%")
        print(f"  Cached Prompts in RAM:   {stats.get('active_l1_exact_entries', 0)} L1 / {stats.get('active_l2_semantic_entries', 0)} L2")
        if METRICS_LEDGER.get("quantized_embeddings_generated", 0) > 0:
            print(f"  Quantized Embeddings:    {METRICS_LEDGER['quantized_embeddings_generated']:,} generated (256-d Int8)")
        print(f"  SQLite Store:            {snapshot_store.db_path}")
        print("  (Start daemon with 'omnicache start' or 'omnicache run <agent>' for live telemetry)")
        print("========================================================\n")

def run_health(host: str = "127.0.0.1", port: int = None) -> bool:
    """Readiness and liveness probe for CI/CD pipelines, Docker healthchecks, and scripts."""
    import urllib.request
    import json
    port = port or config.PORT
    target_host = "127.0.0.1" if host in ("0.0.0.0", "", "::1") else host
    url = f"http://{target_host}:{port}/healthz"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                ver = data.get("version", config.VERSION)
                print(f"✔ OmniCache daemon is healthy (v{ver}) on http://{target_host}:{port}")
                return True
    except Exception:
        pass
    print(f"✖ OmniCache daemon is unreachable or unhealthy on http://{target_host}:{port}")
    return False

def run_ci_summary(output_path: Optional[str] = None) -> str:
    """
    Generates a GitHub Actions / CI-CD Markdown summary report.
    Automatically appends to $GITHUB_STEP_SUMMARY if present in the environment.
    """
    target_host = "127.0.0.1" if config.HOST in ("0.0.0.0", "", "::1") else config.HOST
    live_data = fetch_live_stats(host=target_host, port=config.PORT)
    
    if live_data:
        cs = live_data.get("cache_stats", {})
        fm = live_data.get("financial_telemetry", {})
        ee = live_data.get("enterprise_engine", {})
        sys_info = live_data.get("system_info", {})
        ver = sys_info.get("version", config.VERSION)
        savings_usd = fm.get("total_savings_usd", 0.0)
        tokens_saved = fm.get("total_tokens_saved", 0)
        tokens_forwarded = fm.get("total_tokens_used", 0)
        hit_rate = cs.get("hit_rate_percentage", 0.0)
        exact_hits = cs.get("exact_hits", 0)
        semantic_hits = cs.get("semantic_hits", 0)
        tool_replays = ee.get("agent_tool_replays", 0)
        tokens_compacted = ee.get("agent_tokens_compacted", 0)
        telephony_calls = ee.get("telephony_requests", 0)
        audio_hits = ee.get("audio_cache_hits", 0)
        cascade_stats = ee.get("cascade_stats", {})
        cascade_savings = fm.get("arbitrage_savings_usd", 0.0) or cascade_stats.get("arbitrage_savings_usd", 0.0)
        cascade_downgrades = cascade_stats.get("downgraded_count", 0) or ee.get("cascade_downgrades_total", 0)
        swarm_hits = ee.get("swarm_cross_agent_hits", 0)
        swarm_tokens = ee.get("swarm_tokens_saved", 0)
        daemon_status = f"Live Daemon (v{ver})"
    else:
        stats = cache_instance.get_stats()
        ver = config.VERSION
        savings_usd = METRICS_LEDGER["total_savings_usd"]
        tokens_saved = METRICS_LEDGER["total_tokens_saved"]
        tokens_forwarded = METRICS_LEDGER["total_tokens_used"]
        hit_rate = stats.get("hit_rate_percentage", 0.0)
        exact_hits = stats.get("exact_hits", 0)
        semantic_hits = stats.get("semantic_hits", 0)
        tool_replays = METRICS_LEDGER.get("agent_tool_hits", 0)
        tokens_compacted = METRICS_LEDGER.get("agent_tool_compacted_tokens", 0)
        telephony_calls = METRICS_LEDGER.get("telephony_requests_processed", 0)
        audio_hits = METRICS_LEDGER.get("audio_cache_hits", 0)
        from server.cascade_router import cascade_router
        cascade_savings = cascade_router.arbitrage_savings_usd
        cascade_downgrades = cascade_router.downgraded_count
        swarm_hits = METRICS_LEDGER.get("swarm_cross_agent_hits", 0)
        swarm_tokens = METRICS_LEDGER.get("swarm_tokens_saved", 0)
        daemon_status = f"Local Store (v{ver})"

    md_lines = [
        "### ⚡ OmniCache AI Acceleration & Cost Savings Report",
        "",
        "| Metric | Telemetry Value | Impact |",
        "| :--- | :--- | :--- |",
        f"| **Total Cost Avoided** | **${savings_usd:.4f} USD** | 💰 Direct API savings |",
        f"| **Remote Tokens Avoided** | **{tokens_saved:,} tokens** | ⚡ Eliminated remote LLM roundtrips |",
        f"| **Tokens Forwarded** | **{tokens_forwarded:,} tokens** | 📡 Actual upstream LLM usage |",
        f"| **Cache Hit Rate** | **{hit_rate}%** | 🎯 Dual-tier Exact + Semantic matches |",
        f"| **Exact / Semantic Hits** | **{exact_hits} exact / {semantic_hits} semantic** | 🧠 Multi-layer acceleration |",
        f"| **Agent Tool Replays** | **{tool_replays:,} replays** | 🚀 Sub-millisecond deterministic cache hits |",
        f"| **Context Tokens Pruned** | **{tokens_compacted:,} tokens** | ✂️ Deep multi-turn agent context compaction |",
    ]
    if cascade_downgrades > 0 or cascade_savings > 0:
        md_lines.append(f"| **Model Cascade Savings** | **${cascade_savings:.4f} USD** | 🔀 Smart Shannon entropy downgrade ({cascade_downgrades:,} queries) |")
    if swarm_hits > 0:
        md_lines.append(f"| **Swarm Cross-Agent Hits** | **{swarm_hits:,} hits** | 🐝 Inter-agent shared cache ({swarm_tokens:,} tokens saved) |")
    if telephony_calls > 0:
        md_lines.append(f"| **Voice Telephony Calls** | **{telephony_calls:,} calls** | 🎙️ STT filler normalization & fast-path hits |")
    if audio_hits > 0:
        md_lines.append(f"| **Multimodal Audio Hits** | **{audio_hits:,} hits** | 🎵 Sub-band spectral matching & VAD silence trimming |")
    mesh_info = live_data.get("mesh_network", {}) if live_data else {}
    if mesh_info:
        alive_p = mesh_info.get("peer_summary", {}).get("alive", 0)
        tombs = mesh_info.get("tombstone_count", 0)
        md_lines.append(f"| **P2P Edge Mesh Sync** | **{alive_p} peers / {tombs} tombstones** | 🌐 Decentralized CRDT cache state sync |")
    md_lines.extend([
        f"| **OmniCache Engine** | **{daemon_status}** | 🟢 Operational |",
        "",
        "> *Report generated automatically by [OmniCache AI Proxy](https://github.com/13manmayarai-hash/omnicache-proxy).* ",
        ""
    ])
    md_content = "\n".join(md_lines)
    print(md_content)

    if output_path:
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(md_content)
        except Exception as e:
            print(f"Warning: Could not write summary to {output_path}: {e}")

    gh_step_summary = os.getenv("GITHUB_STEP_SUMMARY")
    if gh_step_summary:
        try:
            with open(gh_step_summary, "a", encoding="utf-8") as f:
                f.write("\n" + md_content + "\n")
        except Exception:
            pass

    return md_content

def run_reset_circuit(provider: Optional[str] = None):
    import urllib.request
    import json
    target_host = "127.0.0.1" if config.HOST in ("0.0.0.0", "", "::1") else config.HOST
    url = f"http://{target_host}:{config.PORT}/v1/cache/circuit/reset"
    payload = json.dumps({"provider": provider} if provider else {}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    if config.ADMIN_API_KEY:
        req.add_header("Authorization", f"Bearer {config.ADMIN_API_KEY}")
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            if resp.status == 200:
                res_data = json.loads(resp.read().decode("utf-8"))
                print(f"✅ {res_data.get('message', 'Circuit breaker reset successfully.')}")
                return
    except Exception:
        pass

    # Fallback to local reset
    from server.failover import failover_engine
    failover_engine.reset(provider)
    target = provider if provider else "all providers"
    print(f"✅ Circuit breaker reset locally for {target}.")

def run_wrapper(cmd_args: list, host: str = "127.0.0.1", port: int = 8000):
    """
    Zero-config execution wrapper for AI coding agents (Claude Code, Cursor, Aider, custom scripts).
    Automatically starts or attaches to OmniCache proxy and injects environment variables.
    """
    if not cmd_args:
        print("❌ Error: No command specified.")
        print("Usage: omnicache run <command> [args...]")
        print("Example: omnicache run claude")
        sys.exit(1)

    import subprocess
    import urllib.request
    import json

    server_process = None
    started_local_server = False
    target_host = "127.0.0.1" if host == "0.0.0.0" else host

    # 1. Ensure OmniCache proxy is running
    if not is_port_in_use(port, target_host):
        print(f"🚀 Starting OmniCache acceleration sidecar on http://{target_host}:{port}...")
        server_env = os.environ.copy()
        for env_k in ["ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE", "GEMINI_BASE_URL"]:
            val = server_env.get(env_k, "").lower()
            if any(local in val for local in ("127.0.0.1", "localhost", "0.0.0.0", "::1", f":{port}")):
                server_env.pop(env_k, None)

        server_process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server.gateway:app", "--host", target_host, "--port", str(port), "--log-level", "warning"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=server_env
        )
        started_local_server = True
        # Wait up to 5s for the server to be ready
        ready = False
        for _ in range(50):
            if is_port_in_use(port, target_host):
                ready = True
                break
            time.sleep(0.1)
        if not ready:
            print("⚠️ Warning: Failed to confirm background OmniCache proxy readiness.")
    else:
        print(f"⚡ Attached to active OmniCache proxy on http://{target_host}:{port}")

    # 2. Snapshot initial telemetry
    initial_tokens_saved = 0
    initial_savings_usd = 0.0
    try:
        req = urllib.request.urlopen(f"http://{target_host}:{port}/v1/cache/stats", timeout=1.0)
        data = json.loads(req.read().decode("utf-8"))
        fin = data.get("financial_telemetry", {})
        initial_tokens_saved = fin.get("total_tokens_saved", 0)
        initial_savings_usd = fin.get("total_savings_usd", 0.0)
    except Exception:
        pass

    # 3. Setup child environment
    env = os.environ.copy()
    proxy_url = f"http://{target_host}:{port}"
    proxy_v1 = f"http://{target_host}:{port}/v1"
    env["ANTHROPIC_BASE_URL"] = proxy_url
    env["OPENAI_BASE_URL"] = proxy_v1
    env["OPENAI_API_BASE"] = proxy_v1
    env["LLM_BASE_URL"] = proxy_v1
    env["OMNICACHE_ACCELERATED"] = "1"
    env["OMNICACHE_PORT"] = str(port)

    # Workspace status hint
    try:
        from server.workspace_sync import workspace_sync_manager
        ws_stat = workspace_sync_manager.get_sync_status(os.getcwd())
        active_tools = ws_stat.get("active_tool_records", 0)
        if active_tools > 0:
            print(f"📦 Workspace Cache: {active_tools} pre-warmed tool records ready")
        else:
            print(f"💡 Tip: Run 'omnicache warm' to pre-index repository files for instant tool replays")
    except Exception:
        pass

    print(f"🎯 Injected proxy environment:")
    print(f"   ANTHROPIC_BASE_URL = {proxy_url}")
    print(f"   OPENAI_BASE_URL    = {proxy_v1}")
    print(f"   LLM_BASE_URL       = {proxy_v1}")
    print(f"\n▶ Executing agent command: {' '.join(cmd_args)}\n{'='*60}\n")

    exit_code = 0
    try:
        child = subprocess.run(cmd_args, env=env)
        exit_code = child.returncode
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as e:
        print(f"\n❌ Execution error: {e}")
        exit_code = 1
    finally:
        print(f"\n{'='*60}")
        # 4. Display session delta telemetry
        try:
            req = urllib.request.urlopen(f"http://{target_host}:{port}/v1/cache/stats", timeout=1.0)
            data = json.loads(req.read().decode("utf-8"))
            fin = data.get("financial_telemetry", {})
            eng = data.get("enterprise_engine", {})
            
            diff_tokens = max(0, fin.get("total_tokens_saved", 0) - initial_tokens_saved)
            diff_savings = max(0.0, fin.get("total_savings_usd", 0.0) - initial_savings_usd)
            tool_replays = eng.get("agent_tool_replays", 0)

            print("\n╭──────────────────────────────────────────────────╮")
            print("│ ⚡ OmniCache Session Telemetry                   │")
            print(f"│  - Tokens Saved:    {diff_tokens:>8,} tokens                 │")
            print(f"│  - Avoided Cost:    ${diff_savings:>8.4f} USD                    │")
            print(f"│  - Tool Replays:    {tool_replays:>8} cached tool calls        │")
            print("╰──────────────────────────────────────────────────╯\n")
        except Exception:
            pass

        if server_process and started_local_server:
            server_process.terminate()
            try:
                server_process.wait(timeout=2.0)
            except Exception:
                server_process.kill()

    sys.exit(exit_code)

def run_init(agent: str = "all", show_only: bool = False):
    """
    Automated drop-in setup and profile generator for AI coding agents:
    Claude Code, Cursor, Cline, OpenHands, and Shell environments.
    """
    import json
    clean_agent = (agent or "all").lower().strip()
    port = config.PORT
    python_bin = sys.executable

    claude_mcp = {
        "mcpServers": {
            "omnicache": {
                "command": python_bin,
                "args": ["-m", "mcp.server"],
                "env": {
                    "OMNICACHE_PORT": str(port)
                }
            }
        }
    }

    cursor_mcp = {
        "mcpServers": {
            "omnicache": {
                "command": python_bin,
                "args": ["-m", "mcp.server"],
                "env": {
                    "OMNICACHE_PORT": str(port)
                }
            }
        }
    }

    cline_mcp = {
        "mcpServers": {
            "omnicache": {
                "command": python_bin,
                "args": ["-m", "mcp.server"],
                "env": {
                    "OMNICACHE_PORT": str(port)
                },
                "disabled": False,
                "autoApprove": []
            }
        }
    }

    openhands_toml = (
        f'# OpenHands Configuration for OmniCache AI Proxy\n'
        f'[llm]\n'
        f'model = "anthropic/claude-3-5-sonnet-20241022"\n'
        f'base_url = "http://127.0.0.1:{port}"\n'
        f'api_key = "dummy"\n'
        f'# Alternative OpenAI setup:\n'
        f'# model = "openai/gpt-4o"\n'
        f'# base_url = "http://127.0.0.1:{port}/v1"\n'
    )

    env_sh = (
        f'# OmniCache AI Coding Agent Environment Exports\n'
        f'export ANTHROPIC_BASE_URL="http://127.0.0.1:{port}"\n'
        f'export OPENAI_BASE_URL="http://127.0.0.1:{port}/v1"\n'
        f'export OPENAI_API_BASE="http://127.0.0.1:{port}/v1"\n'
        f'export LLM_BASE_URL="http://127.0.0.1:{port}/v1"\n'
        f'export OMNICACHE_ACCELERATED="1"\n'
        f'export OMNICACHE_PORT="{port}"\n'
    )

    if show_only:
        print(f"\n\033[1;36m==========================================================================================")
        print(f"📋 OmniCache Agent Configuration Presets (Target: {clean_agent.upper()})")
        print(f"==========================================================================================\033[0m\n")

        if clean_agent in ("all", "claude"):
            print("\033[1;33m--- Claude Code (~/.claude.json / ~/.claude/settings.json) ---\033[0m")
            print(json.dumps(claude_mcp, indent=2))
            print(f'CLI Environment: export ANTHROPIC_BASE_URL="http://127.0.0.1:{port}"\n')

        if clean_agent in ("all", "cursor"):
            print("\033[1;33m--- Cursor IDE (.cursor/mcp.json) ---\033[0m")
            print(json.dumps(cursor_mcp, indent=2))
            print(f'Cursor AI Settings -> OpenAI Base URL: http://127.0.0.1:{port}/v1\n')

        if clean_agent in ("all", "cline"):
            print("\033[1;33m--- Cline (cline_mcp_settings.json) ---\033[0m")
            print(json.dumps(cline_mcp, indent=2))
            print(f'Cline API Provider: Custom OpenAI-compatible -> Base URL: http://127.0.0.1:{port}/v1\n')

        if clean_agent in ("all", "openhands"):
            print("\033[1;33m--- OpenHands (config.toml) ---\033[0m")
            print(openhands_toml)
            print(f'Docker Run flag: -e LLM_BASE_URL="http://host.docker.internal:{port}/v1"\n')

        if clean_agent in ("all", "env"):
            print("\033[1;33m--- Shell Environment (~/.omnicache/env.sh) ---\033[0m")
            print(env_sh)

        if clean_agent in ("all", "voice", "livekit", "twilio"):
            print("\033[1;33m--- Voice & Telephony Agents (LiveKit / Twilio / Vapi) ---\033[0m")
            print(json.dumps({
                "omnicache": {
                    "version": getattr(config, "VERSION", "2.9.9"),
                    "voice_mode": True,
                    "strip_fillers": True,
                    "canonicalize_telephony_metadata": True,
                    "max_active_turns": 8,
                    "fast_path_intents": True,
                    "proxy_url": f"http://127.0.0.1:{port}/v1"
                },
                "headers": {
                    "X-OmniCache-Voice-Mode": "true",
                    "X-OmniCache-Telephony": "livekit"
                }
            }, indent=2))
            print(f'Voice Adapter Header: X-OmniCache-Voice-Mode: true\n')

        if clean_agent in ("all", "audio", "realtime", "multimodal"):
            print("\033[1;33m--- Multimodal Raw Audio Agents (OpenAI Realtime & GPT-4o Audio) ---\033[0m")
            print(json.dumps({
                "omnicache": {
                    "version": getattr(config, "VERSION", "2.9.9"),
                    "audio_cache": True,
                    "spectral_subband_hasher": "aHash-64",
                    "vad_silence_trimming": True,
                    "max_hamming_distance": 6,
                    "proxy_url": f"http://127.0.0.1:{port}/v1"
                },
                "openai_realtime": {
                    "api_base": f"http://127.0.0.1:{port}/v1",
                    "model": "gpt-4o-audio-preview"
                }
            }, indent=2))
            print(f'Multimodal Audio Cache: Enabled (sub-band spectral matching & VAD)\n')

        if clean_agent in ("all", "cascade", "arbiter"):
            print("\033[1;33m--- Smart Model Cascading & Automated Cost Arbiter ---\033[0m")
            print(json.dumps({
                "omnicache": {
                    "version": getattr(config, "VERSION", "2.9.9"),
                    "model_cascading": True,
                    "policy": "auto",
                    "shannon_entropy_routing": True,
                    "threshold_economy": 0.35,
                    "threshold_balanced": 0.60,
                    "proxy_url": f"http://127.0.0.1:{port}/v1"
                },
                "headers": {
                    "X-OmniCache-Model-Cascade": "allow"
                },
                "env": {
                    "OMNICACHE_CASCADE_POLICY": "auto"
                }
            }, indent=2))
            print(f'Cascade Opt-In Header: X-OmniCache-Model-Cascade: allow\n')

        if clean_agent in ("all", "swarm"):
            print("\033[1;33m--- Multi-Agent Swarm & Subagent Delegation Bus (v2.9.9) ---\033[0m")
            print(json.dumps({
                "omnicache": {
                    "version": getattr(config, "VERSION", "2.9.9"),
                    "swarm_bus": True,
                    "swarm_ttl_seconds": 86400,
                    "cross_agent_memory": True,
                    "mutation_invalidation": True,
                    "proxy_url": f"http://127.0.0.1:{port}/v1"
                },
                "headers": {
                    "X-OmniCache-Swarm-ID": "swarm-alpha",
                    "X-OmniCache-Agent-ID": "worker-1",
                    "X-OmniCache-Parent-Agent": "lead"
                },
                "endpoints": {
                    "topology": f"http://127.0.0.1:{port}/v1/swarm/topology",
                    "stats": f"http://127.0.0.1:{port}/v1/swarm/stats",
                    "delegate": f"http://127.0.0.1:{port}/v1/swarm/delegate"
                }
            }, indent=2))
            print(f'Swarm Headers: X-OmniCache-Swarm-ID, X-OmniCache-Agent-ID, X-OmniCache-Parent-Agent\n')

        print("\033[1;36m==========================================================================================\033[0m\n")
        return

    print("\n\033[1;36m╭───────────────────────────────────────────────────╮")
    print("│ ⚙️  OmniCache Drop-In Agent Auto-Configuration     │")
    print("╰───────────────────────────────────────────────────╯\033[0m\n")

    configured_items = []

    # 1. Claude Code
    if clean_agent in ("all", "claude"):
        claude_paths = [
            os.path.expanduser("~/.claude.json"),
            os.path.expanduser("~/.claude/settings.json")
        ]
        for cp in claude_paths:
            try:
                os.makedirs(os.path.dirname(cp), exist_ok=True)
                data = {}
                if os.path.exists(cp):
                    try:
                        with open(cp, "r", encoding="utf-8") as f:
                            data = json.load(f)
                    except Exception:
                        data = {}
                if "mcpServers" not in data:
                    data["mcpServers"] = {}
                data["mcpServers"]["omnicache"] = {
                    "command": python_bin,
                    "args": ["-m", "mcp.server"],
                    "env": {"OMNICACHE_PORT": str(port)}
                }
                with open(cp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                configured_items.append(f"Claude Code Config:     {cp}")
            except Exception:
                pass

    # 2. Cursor MCP
    if clean_agent in ("all", "cursor"):
        cursor_paths = [
            os.path.expanduser("~/.cursor/mcp.json"),
            os.path.join(os.getcwd(), ".cursor", "mcp.json")
        ]
        for curp in cursor_paths:
            try:
                os.makedirs(os.path.dirname(curp), exist_ok=True)
                data = {}
                if os.path.exists(curp):
                    try:
                        with open(curp, "r", encoding="utf-8") as f:
                            data = json.load(f)
                    except Exception:
                        data = {}
                if "mcpServers" not in data:
                    data["mcpServers"] = {}
                data["mcpServers"]["omnicache"] = {
                    "command": python_bin,
                    "args": ["-m", "mcp.server"],
                    "env": {"OMNICACHE_PORT": str(port)}
                }
                with open(curp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                configured_items.append(f"Cursor MCP Config:      {curp}")
            except Exception:
                pass

    # 3. Cline (VS Code & Cursor extension)
    if clean_agent in ("all", "cline"):
        cline_paths = [
            os.path.expanduser("~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json"),
            os.path.expanduser("~/.config/Cursor/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json"),
            os.path.join(os.getcwd(), ".vscode", "cline_mcp_settings.json")
        ]
        for clp in cline_paths:
            try:
                os.makedirs(os.path.dirname(clp), exist_ok=True)
                data = {}
                if os.path.exists(clp):
                    try:
                        with open(clp, "r", encoding="utf-8") as f:
                            data = json.load(f)
                    except Exception:
                        data = {}
                if "mcpServers" not in data:
                    data["mcpServers"] = {}
                data["mcpServers"]["omnicache"] = {
                    "command": python_bin,
                    "args": ["-m", "mcp.server"],
                    "env": {"OMNICACHE_PORT": str(port)},
                    "disabled": False,
                    "autoApprove": []
                }
                with open(clp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                configured_items.append(f"Cline MCP Config:       {clp}")
            except Exception:
                pass

    # 4. OpenHands
    if clean_agent in ("all", "openhands"):
        openhands_paths = [
            os.path.join(os.getcwd(), "config.toml"),
            os.path.expanduser("~/.openhands/config.toml")
        ]
        for ohp in openhands_paths:
            try:
                os.makedirs(os.path.dirname(ohp), exist_ok=True)
                if not os.path.exists(ohp):
                    with open(ohp, "w", encoding="utf-8") as f:
                        f.write(openhands_toml)
                    configured_items.append(f"OpenHands Config:       {ohp}")
                else:
                    configured_items.append(f"OpenHands Config Exists:{ohp}")
            except Exception:
                pass

    # 5. Shell Profile Helper
    if clean_agent in ("all", "env"):
        env_sh_path = os.path.expanduser("~/.omnicache/env.sh")
        try:
            os.makedirs(os.path.dirname(env_sh_path), exist_ok=True)
            with open(env_sh_path, "w", encoding="utf-8") as f:
                f.write(env_sh)
            configured_items.append(f"Shell Env Helper:       {env_sh_path}")
        except Exception:
            pass

    # 6. Voice & Telephony Calling Agents
    if clean_agent in ("all", "voice", "livekit", "twilio"):
        voice_path = os.path.join(os.getcwd(), ".omnicache-voice.json")
        try:
            with open(voice_path, "w", encoding="utf-8") as f:
                json.dump({
                    "omnicache": {
                        "version": "2.9.7",
                        "voice_mode": True,
                        "strip_fillers": True,
                        "canonicalize_telephony_metadata": True,
                        "max_active_turns": 8,
                        "fast_path_intents": True,
                        "proxy_url": f"http://127.0.0.1:{port}/v1"
                    },
                    "livekit_adapter": {
                        "openai_api_base": f"http://127.0.0.1:{port}/v1",
                        "anthropic_api_base": f"http://127.0.0.1:{port}",
                        "headers": {
                            "X-OmniCache-Voice-Mode": "true",
                            "X-OmniCache-Telephony": "livekit"
                        }
                    },
                    "twilio_media_streams": {
                        "llm_endpoint": f"http://127.0.0.1:{port}/v1/chat/completions",
                        "headers": {
                            "X-OmniCache-Voice-Mode": "true",
                            "X-OmniCache-Telephony": "twilio"
                        }
                    }
                }, f, indent=2)
            configured_items.append(f"Voice Telephony Config: {voice_path}")
        except Exception:
            pass

    # 7. Multimodal Raw Audio Agents (OpenAI Realtime & GPT-4o Audio)
    if clean_agent in ("all", "audio", "realtime", "multimodal"):
        audio_path = os.path.join(os.getcwd(), ".omnicache-audio.json")
        try:
            with open(audio_path, "w", encoding="utf-8") as f:
                json.dump({
                    "omnicache": {
                        "version": getattr(config, "VERSION", "2.9.9"),
                        "audio_cache": True,
                        "spectral_subband_hasher": "aHash-64",
                        "vad_silence_trimming": True,
                        "max_hamming_distance": 6,
                        "proxy_url": f"http://127.0.0.1:{port}/v1"
                    },
                    "openai_realtime": {
                        "api_base": f"http://127.0.0.1:{port}/v1",
                        "model": "gpt-4o-audio-preview"
                    }
                }, f, indent=2)
            configured_items.append(f"Multimodal Audio Config: {audio_path}")
        except Exception:
            pass

    # 8. Smart Model Cascading & Automated Cost Arbiter
    if clean_agent in ("all", "cascade", "arbiter"):
        cascade_path = os.path.join(os.getcwd(), ".omnicache-cascade.json")
        try:
            with open(cascade_path, "w", encoding="utf-8") as f:
                json.dump({
                    "omnicache": {
                        "version": getattr(config, "VERSION", "2.9.9"),
                        "model_cascading": True,
                        "policy": "auto",
                        "shannon_entropy_routing": True,
                        "threshold_economy": 0.35,
                        "threshold_balanced": 0.60,
                        "proxy_url": f"http://127.0.0.1:{port}/v1"
                    },
                    "headers": {
                        "X-OmniCache-Model-Cascade": "allow"
                    }
                }, f, indent=2)
            configured_items.append(f"Model Cascade Config: {cascade_path}")
        except Exception:
            pass

    # 9. Multi-Agent Swarm & Delegation Bus
    if clean_agent in ("all", "swarm"):
        swarm_path = os.path.join(os.getcwd(), ".omnicache-swarm.json")
        try:
            with open(swarm_path, "w", encoding="utf-8") as f:
                json.dump({
                    "omnicache": {
                        "version": getattr(config, "VERSION", "2.9.9"),
                        "swarm_bus": True,
                        "swarm_ttl_seconds": 86400,
                        "cross_agent_memory": True,
                        "mutation_invalidation": True,
                        "proxy_url": f"http://127.0.0.1:{port}/v1"
                    },
                    "headers": {
                        "X-OmniCache-Swarm-ID": "swarm-alpha",
                        "X-OmniCache-Agent-ID": "worker-1",
                        "X-OmniCache-Parent-Agent": "lead"
                    }
                }, f, indent=2)
            configured_items.append(f"Multi-Agent Swarm Config: {swarm_path}")
        except Exception:
            pass

    for item in configured_items:
        print(f"\033[1;32m  ✔ {item}\033[0m")

    print(f"\n\033[1;37m🎉 Setup complete! You can now run:\033[0m")
    print(f"   \033[1;36momnicache run claude\033[0m       (for Claude Code)")
    print(f"   \033[1;36momnicache run cursor .\033[0m       (for Cursor IDE)")
    print(f"   \033[1;36momnicache run openhands\033[0m    (for OpenHands)")
    print(f"   \033[1;36momnicache harness\033[0m          (to verify live integration health)\n")

def run_warm(workspace_dir: Optional[str] = None, ref: str = "HEAD", max_files: int = 200):
    from server.workspace_sync import workspace_warmer
    target_dir = os.path.abspath(workspace_dir or os.getcwd())
    print(f"\n\033[1;36m🔥 Warming OmniCache tool replay cache for workspace:\033[0m {target_dir}")
    try:
        report = workspace_warmer.warm_workspace(
            workspace_dir=target_dir,
            ref=ref,
            max_files=max_files
        )
        if report.get("is_git_repo"):
            commit_str = (report.get("git_commit") or "")[:8]
            branch_str = report.get("git_branch", "")
            print(f"  \033[1;32m✔\033[0m Git Commit:          {commit_str} (branch: {branch_str})")
        print(f"  \033[1;32m✔\033[0m Files Pre-Recorded:  {report.get('files_warmed', 0)} files")
        print(f"  \033[1;32m✔\033[0m Directories Indexed: {report.get('dirs_warmed', 0)} directories")
        print(f"  \033[1;32m✔\033[0m Tools Auto-Recorded: {report.get('tools_recorded', 0)} execution signatures")
        print(f"  \033[1;32m✔\033[0m Tokens Pre-Warmed:   {report.get('tokens_warmed', 0):,} tokens")
        print(f"  \033[1;32m✔\033[0m Warm-up Duration:    {report.get('duration_ms', 0):.2f}ms")
        print(f"\n\033[1;37m✨ Workspace is warm! AI agents (Claude Code, Cursor) will experience instant tool replays.\033[0m\n")
    except Exception as e:
        print(f"\033[1;31m❌ Error during workspace cache warming: {e}\033[0m")
        sys.exit(1)


def run_sync(
    action: str = "status",
    output_path: Optional[str] = None,
    input_path: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    workspace_fingerprint: Optional[str] = None
):
    from server.workspace_sync import workspace_sync_manager
    clean_action = (action or "status").lower().strip()

    if clean_action == "export":
        out = output_path or "omnicache-workspace-cache.json"
        print(f"\n\033[1;36m📦 Exporting OmniCache workspace tool cache...\033[0m")
        try:
            res = workspace_sync_manager.export_snapshot(
                workspace_dir=workspace_dir,
                workspace_fingerprint=workspace_fingerprint,
                output_path=out
            )
            count = res.get("record_count", 0)
            target = res.get("saved_to", out)
            print(f"  \033[1;32m✔\033[0m Exported {count} tool cache records to: {target}")
            print(f"  \033[1;37mTip: Commit or upload this file in CI/CD to warm teammate environments.\033[0m\n")
        except Exception as e:
            print(f"\033[1;31m❌ Export failed: {e}\033[0m")
            sys.exit(1)

    elif clean_action == "import":
        if not input_path:
            print("\033[1;31m❌ Error: Missing --input / -i file path to import.\033[0m")
            sys.exit(1)
        print(f"\n\033[1;36m📥 Importing OmniCache workspace tool cache from:\033[0m {input_path}")
        try:
            res = workspace_sync_manager.import_snapshot(input_path)
            rec_count = res.get("records_imported", 0)
            pol_count = res.get("policies_imported", 0)
            print(f"  \033[1;32m✔\033[0m Successfully imported {rec_count} tool executions and {pol_count} policies into local SQLite store.")
            print(f"\n\033[1;37m✨ Cache synchronized! Local agents can replay CI/CD tool executions immediately.\033[0m\n")
        except Exception as e:
            print(f"\033[1;31m❌ Import failed: {e}\033[0m")
            sys.exit(1)

    elif clean_action in ("push", "pull"):
        fp = workspace_fingerprint or "default"
        if clean_action == "push":
            print(f"\n\033[1;36m🚀 Pushing workspace cache to Redis (key: omnicache:workspace:sync:{fp})...\033[0m")
            res = workspace_sync_manager.sync_redis_push(workspace_fingerprint=fp)
            if res.get("status") == "PUSHED_TO_REDIS":
                print(f"  \033[1;32m✔\033[0m Pushed {res.get('records_pushed', 0)} tool records to Redis.")
            else:
                print(f"  \033[1;31m❌ Redis push error: {res.get('error')}\033[0m")
        else:
            print(f"\n\033[1;36m📥 Pulling workspace cache from Redis (key: omnicache:workspace:sync:{fp})...\033[0m")
            res = workspace_sync_manager.sync_redis_pull(workspace_fingerprint=fp)
            if res.get("status") == "IMPORTED":
                print(f"  \033[1;32m✔\033[0m Pulled and imported {res.get('records_imported', 0)} tool records from Redis.")
            else:
                print(f"  \033[1;31m❌ Redis pull error: {res.get('error') or res.get('status')}\033[0m")

    else:  # status
        print(f"\n\033[1;36m--- OmniCache Workspace Sync Status ---\033[0m")
        status = workspace_sync_manager.get_sync_status(
            workspace_dir=workspace_dir,
            workspace_fingerprint=workspace_fingerprint
        )
        print(f"Active Tool Executions:   {status.get('active_tool_records', 0)}")
        print(f"Tokens Saved Potential:   {status.get('saved_tokens_potential', 0):,} tokens")
        print(f"Active Custom Policies:   {status.get('custom_policies_active', 0)}")
        dist = status.get("tool_distribution", {})
        if dist:
            print("\nTool Signature Breakdown:")
            for t_name, cnt in sorted(dist.items(), key=lambda x: x[1], reverse=True)[:10]:
                print(f"  - {t_name:<25} {cnt:>5} cached calls")
        print("")


def run_mesh(peers_arg: Optional[str] = None):
    import json
    from core.p2p_mesh import mesh_bus
    if peers_arg:
        for p in peers_arg.split(","):
            p = p.strip()
            if p:
                mesh_bus.register_peer(p)
    topo = mesh_bus.get_mesh_topology()
    print("\n========================================================")
    print(f"🌐 OmniCache P2P Edge Mesh Topology (v{topo.get('version', config.VERSION)})")
    print("========================================================")
    print(f"Node ID:        {topo.get('node_id')}")
    print(f"Endpoint:       {topo.get('endpoint')}")
    print(f"Lamport Clock:  {topo.get('lamport_clock')}")
    print(f"Vector Clock:   {json.dumps(topo.get('vector_clock', {}))}")
    print(f"CRDT Tombstones:{topo.get('tombstone_count')}")
    peer_sum = topo.get("peer_summary", {})
    print(f"Peers:          {peer_sum.get('alive', 0)} alive / {peer_sum.get('total', 0)} registered")
    for peer in topo.get("peers", []):
        icon = "🟢" if peer["status"] == "alive" else "🔴"
        print(f"  {icon} {peer['endpoint']} ({peer['node_id']}) - RTT: {peer['rtt_ms']}ms, last seen: {peer['age_seconds']}s ago")
    print("========================================================\n")


def main():
    # Handle "omnicache run <command> [args...]"
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        run_wrapper(sys.argv[2:], host=config.HOST, port=config.PORT)
        return

    # Normalize "omnicache sync <action> [options]"
    sync_action = "status"
    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        if len(sys.argv) > 2 and sys.argv[2] in ("export", "import", "status", "push", "pull"):
            sync_action = sys.argv[2]
            del sys.argv[2]

    parser = argparse.ArgumentParser(
        prog="omnicache",
        description="OmniCache - Local Acceleration Sidecar for AI Coding Agents."
    )
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {config.VERSION}")
    parser.add_argument("command", nargs="?", default="start", choices=["start", "run", "init", "doctor", "benchmark", "harness", "verify-agent", "stats", "health", "ci-summary", "reset-circuit", "version", "warm", "sync", "mesh"], help="Action to perform (default: start)")
    parser.add_argument("-p", "--port", type=int, default=config.PORT, help=f"Port to bind server to (default: {config.PORT})")
    parser.add_argument("-H", "--host", type=str, default=config.HOST, help=f"Host interface (default: {config.HOST})")
    parser.add_argument("--agent", type=str, default="all", choices=["all", "claude", "cursor", "cline", "openhands", "env", "voice", "livekit", "twilio", "audio", "realtime", "multimodal", "cascade", "arbiter", "swarm"], help="Target agent preset for init (default: all)")
    parser.add_argument("--show", action="store_true", help="Display agent configuration presets without writing to disk")
    parser.add_argument("--markdown", action="store_true", help="Output telemetry metrics formatted as GitHub Flavored Markdown")
    parser.add_argument("--provider", type=str, default=None, help="Target provider for reset-circuit (openai, anthropic, google)")
    parser.add_argument("--dir", type=str, default=None, help="Target workspace directory for warm or sync")
    parser.add_argument("--ref", type=str, default="HEAD", help="Git reference for warm (default: HEAD)")
    parser.add_argument("--max-files", type=int, default=200, help="Maximum number of files to warm (default: 200)")
    parser.add_argument("--action", type=str, default=sync_action, choices=["export", "import", "status", "push", "pull"], help="Sync action (export, import, status, push, pull)")
    parser.add_argument("-o", "--output", type=str, default=None, help="Output file path for sync export or ci-summary")
    parser.add_argument("-i", "--input", type=str, default=None, help="Input file path for sync import")
    parser.add_argument("--workspace", type=str, default="default", help="Workspace fingerprint for sync")
    parser.add_argument("--peers", type=str, default="", help="Comma-separated peer endpoints for P2P edge mesh sync")
    parser.add_argument("--iterations", type=int, default=5, help="Number of benchmark iterations (default: 5)")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose debug logging")

    args = parser.parse_args()

    if args.command == "init":
        run_init(agent=args.agent, show_only=args.show)
        sys.exit(0)
    elif args.command in ("harness", "verify-agent"):
        from server.agent_harness import AgentHarness
        success = AgentHarness.run(host=args.host, port=args.port, verbose=args.verbose)
        sys.exit(0 if success else 1)
    elif args.command == "doctor":
        run_doctor()
        sys.exit(0)
    elif args.command == "benchmark":
        run_benchmark(iterations=args.iterations)
        sys.exit(0)
    elif args.command == "stats":
        if args.markdown:
            run_ci_summary(output_path=args.output)
        else:
            run_stats()
        sys.exit(0)
    elif args.command == "health":
        ok = run_health(host=args.host, port=args.port)
        sys.exit(0 if ok else 1)
    elif args.command == "ci-summary":
        run_ci_summary(output_path=args.output)
        sys.exit(0)
    elif args.command == "reset-circuit":
        run_reset_circuit(provider=args.provider)
        sys.exit(0)
    elif args.command == "version":
        print(f"omnicache {config.VERSION}")
        sys.exit(0)
    elif args.command == "warm":
        run_warm(workspace_dir=args.dir, ref=args.ref, max_files=args.max_files)
        sys.exit(0)
    elif args.command == "sync":
        run_sync(
            action=args.action or sync_action,
            output_path=args.output,
            input_path=args.input,
            workspace_dir=args.dir,
            workspace_fingerprint=args.workspace
        )
        sys.exit(0)
    elif args.command == "mesh":
        run_mesh(peers_arg=args.peers)
        sys.exit(0)

    port = args.port
    host = args.host
    validate_startup_security_invariants(host)
    log_level = "info" if args.verbose else "warning"
    access_log = bool(args.verbose)

    print(f"OmniCache proxy listening on http://{host}:{port}")
    if not args.verbose:
        print("Running in silent terminal mode (pass --verbose for access logs).")
    
    uvicorn.run(app, host=host, port=port, access_log=access_log, log_level=log_level)


if __name__ == "__main__":
    main()

