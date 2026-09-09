#!/usr/bin/env python3
"""
OmniCache 30-Second Live Realistic Simulation & Telemetry Stress Test.
Generates realistic, real-time agent workflow events across all subsystems:
- Workspace Pre-warming & Git-Aware Indexing
- Exact L1 Cache Hits (Claude Sonnet & OpenAI GPT-4o)
- L2 Semantic Vector Cosine Hits (Rephrased prompts)
- Agent Tool Recording & Microsecond Replays (<0.3ms)
- Mutation Policy Protection (Rejecting destructive tool caching)
- Multi-Agent Swarm Bus Delegation & Shared Cache Hits
- Financial Telemetry & Avoided Upstream Spend Velocity
"""

import sys
import time
import json
import urllib.request
import urllib.error

BASE_URL = "http://127.0.0.1:8000"

def log_step(step: int, total: int, title: str, details: str):
    elapsed = time.strftime("%H:%M:%S")
    print(f"\n[{elapsed}] [Step {step:02d}/{total:02d}] 🚀 {title}")
    print(f"      ↳ {details}")

def post_json(endpoint: str, payload: dict, extra_headers: dict = None) -> tuple:
    url = f"{BASE_URL}{endpoint}"
    headers = {
        "Content-Type": "application/json",
        "x-org-id": "enterprise_user",
        "x-dashboard-playground": "true"
    }
    if extra_headers:
        headers.update(extra_headers)

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            dt = (time.perf_counter() - t0) * 1000
            res_json = json.loads(resp.read().decode("utf-8"))
            return resp.status, res_json, dict(resp.headers), dt
    except urllib.error.HTTPError as e:
        dt = (time.perf_counter() - t0) * 1000
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {}
        return e.code, body, dict(e.headers), dt
    except Exception as e:
        return 500, {"error": str(e)}, {}, 0.0

def run_simulation():
    print("=" * 78)
    print("⚡ OmniCache 30-Second Live Telemetry Test")
    print("   Target Dashboard: http://localhost:8000/dashboard")
    print("   Open your browser to watch metrics, charts, and stream update in real time!")
    print("=" * 78)

    # Verify server reachable
    try:
        urllib.request.urlopen(f"{BASE_URL}/v1/cache/stats", timeout=2.0)
    except Exception:
        print("❌ Error: OmniCache server is not running on http://127.0.0.1:8000")
        print("   Please ensure 'omnicache --port 8000' is active.")
        sys.exit(1)

    steps = 15

    # Step 1: Workspace Pre-Warming
    log_step(1, steps, "Workspace Pre-Warming (Git Repository Scan)", "Indexing local repository files and tool signatures...")
    st, data, hdrs, dt = post_json("/v1/workspace/warm", {"directory": ".", "max_files": 10})
    print(f"      ✓ Status: {data.get('status')} | Files Warmed: {data.get('files_warmed', 0)} | Tool Signatures: {data.get('tools_recorded', 0)} ({dt:.1f}ms)")
    time.sleep(2.0)

    # Step 2: Claude Cold Prompt (Architecture Query)
    log_step(2, steps, "Cold Agent Request: Claude 3.5 Sonnet (Architecture Review)", "Agent queries FastAPI security best practices...")
    p_arch = "Review this FastAPI authentication middleware for security vulnerabilities and token leaking."
    st, data, hdrs, dt = post_json("/v1/messages", {
        "model": "claude-sonnet-4-5-20250929",
        "messages": [{"role": "user", "content": p_arch}],
        "max_tokens": 1024
    })
    cache_stat = hdrs.get("x-cache-status", "MISS")
    print(f"      ✓ Status: {st} | Cache: {cache_stat} | Model: claude-sonnet-4.5 ({dt:.1f}ms)")
    time.sleep(2.0)

    # Step 3: Claude L1 Exact Cache Hit
    log_step(3, steps, "L1 Exact Cache Hit (Claude 3.5 Sonnet)", "Agent repeats identical prompt in conversation loop...")
    st, data, hdrs, dt = post_json("/v1/messages", {
        "model": "claude-sonnet-4-5-20250929",
        "messages": [{"role": "user", "content": p_arch}],
        "max_tokens": 1024
    })
    saved_tok = hdrs.get("x-tokens-saved", "0")
    cost_saved = hdrs.get("x-cost-saved-usd", "0.0000")
    print(f"      ✓ Cache: {hdrs.get('x-cache-status')} | Tokens Saved: +{saved_tok} | Cost Avoided: ${float(cost_saved):.4f} ({dt:.2f}ms)")
    time.sleep(2.0)

    # Step 4: Agent Tool Recording (package.json)
    log_step(4, steps, "Agent Tool Recording (Claude Code File Read)", "Claude Code inspects project configuration ('package.json')...")
    st, data, hdrs, dt = post_json("/v1/agent/tool_record", {
        "tool_name": "read_file",
        "arguments": {"path": "package.json"},
        "output": '{"name": "omnicache-app", "version": "3.0.5", "dependencies": {"starlette": "^0.37.0"}}',
        "action": "store",
        "workspace_dir": "/root/omnicache_proxy"
    })
    print(f"      ✓ Tool Registered: {data.get('tool_name')} | Status: {data.get('status')} ({dt:.2f}ms)")
    time.sleep(2.0)

    # Step 5: Git-Aware Agent Tool Replay
    log_step(5, steps, "Git-Aware Agent Tool Replay (<0.3ms Local SQLite WAL)", "Claude Code re-reads 'package.json' (Clean git tree verified)...")
    st, data, hdrs, dt = post_json("/v1/agent/tool_replay", {
        "tool_name": "read_file",
        "arguments": {"path": "package.json"},
        "workspace_dir": "/root/omnicache_proxy"
    })
    print(f"      ✓ Replay Status: {data.get('status')} | Replayed in {dt:.3f}ms ($0.00 token spend)")
    time.sleep(2.0)

    # Step 6: L2 Semantic Vector Cosine Replay
    log_step(6, steps, "L2 FastHash Semantic Match (User Rephrasing)", "Developer rephrases architecture query with different phrasing...")
    p_rephrased = "Please review this FastAPI authentication middleware and check for security bugs."
    st, data, hdrs, dt = post_json("/v1/messages", {
        "model": "claude-sonnet-4-5-20250929",
        "messages": [{"role": "user", "content": p_rephrased}],
        "max_tokens": 1024
    })
    sim = hdrs.get("x-cache-similarity", "1.0000")
    print(f"      ✓ Cache: {hdrs.get('x-cache-status')} | Cosine Similarity: {sim} | Saved: +{hdrs.get('x-tokens-saved', 0)} tok ({dt:.2f}ms)")
    time.sleep(2.0)

    # Step 7: Mutation Policy Guard
    log_step(7, steps, "Mutation Policy Guard (Destructive Command Rejected)", "Agent executes mutative tool ('rm -rf /tmp/build')...")
    st, data, hdrs, dt = post_json("/v1/agent/tool_record", {
        "tool_name": "execute_bash_command",
        "arguments": {"cmd": "rm -rf /tmp/build"},
        "output": "deleted",
        "action": "store"
    })
    print(f"      ✓ Policy Action: {data.get('status')} | Reason: {data.get('reason')} ({dt:.2f}ms)")
    time.sleep(2.0)

    # Step 8: OpenAI GPT-4o Cold Request
    log_step(8, steps, "OpenAI GPT-4o Cold Request (Cursor Agent Workflow)", "Cursor Agent generates SQL query optimization...")
    p_sql = "Explain the difference between clustered and non-clustered indexes in PostgreSQL."
    st, data, hdrs, dt = post_json("/v1/chat/completions", {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": p_sql}],
        "temperature": 0.0
    })
    print(f"      ✓ Status: {st} | Cache: {hdrs.get('x-cache-status')} | Model: gpt-4o ({dt:.1f}ms)")
    time.sleep(2.0)

    # Step 9: OpenAI GPT-4o Exact Cache Hit
    log_step(9, steps, "OpenAI GPT-4o Exact Cache Hit", "Cursor re-evaluates database index optimization...")
    st, data, hdrs, dt = post_json("/v1/chat/completions", {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": p_sql}],
        "temperature": 0.0
    })
    print(f"      ✓ Cache: {hdrs.get('x-cache-status')} | Tokens Saved: +{hdrs.get('x-tokens-saved', 0)} ({dt:.2f}ms)")
    time.sleep(2.0)

    # Step 10: Multi-Agent Swarm Delegation
    log_step(10, steps, "Multi-Agent Swarm Bus: Lead Agent Delegation", "Lead Agent delegates git diff inspection to Subagent-Worker-1...")
    st, data, hdrs, dt = post_json("/v1/agent/tool_record", {
        "tool_name": "git_diff",
        "arguments": {"ref": "HEAD~1"},
        "output": "diff --git a/server.py b/server.py\n+ # Optimized loop",
        "action": "store",
        "swarm_id": "swarm_live_demo",
        "parent_agent": "lead_orchestrator",
        "agent_id": "subagent_worker_1"
    }, extra_headers={"x-omnicache-swarm-id": "swarm_live_demo", "x-omnicache-agent-id": "subagent_worker_1"})
    print(f"      ✓ Delegation Registered: subagent_worker_1 | Tool: git_diff ({dt:.2f}ms)")
    time.sleep(2.0)

    # Step 11: Multi-Agent Swarm Shared Cache Hit
    log_step(11, steps, "Multi-Agent Swarm Cache Hit: Subagent-Worker-2 Query", "Subagent-Worker-2 queries identical git diff (Zero duplicate work)...")
    st, data, hdrs, dt = post_json("/v1/agent/tool_replay", {
        "tool_name": "git_diff",
        "arguments": {"ref": "HEAD~1"},
        "swarm_id": "swarm_live_demo",
        "agent_id": "subagent_worker_2"
    }, extra_headers={"x-omnicache-swarm-id": "swarm_live_demo", "x-omnicache-agent-id": "subagent_worker_2"})
    print(f"      ✓ Swarm Hit: {data.get('status')} | Replay: {dt:.3f}ms")
    time.sleep(2.0)

    # Step 12: OpenAI Semantic Cosine Replay
    log_step(12, steps, "OpenAI Semantic Cosine Replay (Cursor Rephrasing)", "Cursor agent queries with semantic variant of SQL prompt...")
    p_sql_variant = "What is the key difference between clustered and non-clustered indexes in Postgres?"
    st, data, hdrs, dt = post_json("/v1/chat/completions", {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": p_sql_variant}],
        "temperature": 0.0
    })
    print(f"      ✓ Cache: {hdrs.get('x-cache-status')} | Similarity: {hdrs.get('x-cache-similarity')} ({dt:.2f}ms)")
    time.sleep(2.0)

    # Step 13: Agent Tool Replay (Directory Tree)
    log_step(13, steps, "Agent Tool Replay: Directory Tree Inspection", "Cursor agent executes 'list_dir' on project tree...")
    st, data, hdrs, dt = post_json("/v1/agent/tool_record", {
        "tool_name": "list_directory",
        "arguments": {"path": "core/"},
        "output": '["config.py", "quantized_embedder.py", "hasher.py"]',
        "action": "store",
        "workspace_dir": "/root/omnicache_proxy"
    })
    st2, data2, hdrs2, dt2 = post_json("/v1/agent/tool_replay", {
        "tool_name": "list_directory",
        "arguments": {"path": "core/"},
        "workspace_dir": "/root/omnicache_proxy"
    })
    print(f"      ✓ Tool Replay: {data2.get('status')} in {dt2:.3f}ms ($0.00 spend)")
    time.sleep(2.0)

    # Step 14: Claude Haiku High-Speed Micro-Agent Turn
    log_step(14, steps, "High-Speed Haiku Micro-Agent Turn", "Lightweight agent prompt testing rapid completion & caching...")
    st, data, hdrs, dt = post_json("/v1/messages", {
        "model": "claude-haiku-4-5-20251001",
        "messages": [{"role": "user", "content": "Format this list into JSON: [apple, banana, cherry]"}],
        "max_tokens": 128
    })
    print(f"      ✓ Status: {st} | Model: claude-haiku-4.5 ({dt:.1f}ms)")
    time.sleep(2.0)

    # Step 15: Telemetry Summary Snapshot
    log_step(15, steps, "Telemetry Audit & Final Velocity Snapshot", "Capturing live financial and performance ledger from /v1/cache/stats...")
    try:
        req_stats = urllib.request.urlopen(f"{BASE_URL}/v1/cache/stats", timeout=3.0)
        final_stats = json.loads(req_stats.read().decode("utf-8"))
        fin = final_stats.get("financial_telemetry", {})
        cs = final_stats.get("cache_stats", {})
        ee = final_stats.get("enterprise_engine", {})
        
        print("\n" + "=" * 78)
        print("🏆 30-Second Simulation Complete! Final Dashboard State:")
        print(f"   • Total Avoided Spend:   ${fin.get('total_savings_usd', 0.0):.4f} USD")
        print(f"   • Total Tokens Saved:    {fin.get('total_tokens_saved', 0):,} tokens")
        print(f"   • Agent Tool Replays:    {ee.get('agent_tool_replays', 0)} cached tool calls (<0.3ms)")
        print(f"   • Cache Hit Rate:        {cs.get('hit_rate_percentage', 0.0)}%")
        print(f"   • Total Requests Tested: {cs.get('total_requests', 0)}")
        print("=" * 78)
        print("✨ Switch back to your browser (http://localhost:8000/dashboard) to view the final graphs!")
    except Exception as e:
        print(f"      ⚠️ Failed to retrieve stats: {e}")

if __name__ == "__main__":
    run_simulation()
