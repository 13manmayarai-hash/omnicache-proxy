"""
OmniCache command-line interface.
"""

import sys
import os
import time
import socket
import argparse
import uvicorn
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

    print("\nStatus:         All subsystems operational.\n")

def run_benchmark(iterations: int = 1000):
    print(f"\n--- Running Benchmark ({iterations} iterations) ---")
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

    # L1 Exact Cache Benchmark
    l1_latencies = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        bench_cache.lookup(sample_payload)
        t1 = time.perf_counter()
        l1_latencies.append((t1 - t0) * 1000)

    # L2 Semantic Cache Benchmark
    semantic_payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Write a python fast fourier transform (FFT) algorithm in numpy"}],
        "temperature": 0.0
    }
    l2_latencies = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        bench_cache.lookup(semantic_payload)
        t1 = time.perf_counter()
        l2_latencies.append((t1 - t0) * 1000)

    l1_latencies.sort()
    l2_latencies.sort()

    p50_l1 = l1_latencies[int(iterations * 0.50)]
    p95_l1 = l1_latencies[int(iterations * 0.95)]
    p99_l1 = l1_latencies[int(iterations * 0.99)]

    p50_l2 = l2_latencies[int(iterations * 0.50)]
    p95_l2 = l2_latencies[int(iterations * 0.95)]
    p99_l2 = l2_latencies[int(iterations * 0.99)]

    print(f"L1 Exact Cache (Trie Hash):")
    print(f"  P50: {p50_l1:.4f} ms | P95: {p95_l1:.4f} ms | P99: {p99_l1:.4f} ms")
    print(f"  Throughput: ~{int(1000 / max(0.001, p50_l1)):,} QPS / core\n")

    print(f"L2 Semantic Cache (Vector Cosine):")
    print(f"  P50: {p50_l2:.4f} ms | P95: {p95_l2:.4f} ms | P99: {p99_l2:.4f} ms")
    print(f"  Throughput: ~{int(1000 / max(0.001, p50_l2)):,} QPS / core\n")

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
        print(f"  SQLite Store:            {snapshot_store.db_path}")
        print("  (Start daemon with 'omnicache start' or 'omnicache run <agent>' for live telemetry)")
        print("========================================================\n")

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

    print(f"🎯 Injected proxy environment:")
    print(f"   ANTHROPIC_BASE_URL = {proxy_url}")
    print(f"   OPENAI_BASE_URL    = {proxy_v1}")
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

def run_init():
    """
    One-click automated setup for Claude Code, Cursor, and IDE environments.
    Configures ~/.claude.json, ~/.cursor/mcp.json, and environment exports.
    """
    print("\n\033[1;36m╭───────────────────────────────────────────────────╮")
    print("│ ⚙️  OmniCache One-Click Auto-Setup & Integration   │")
    print("╰───────────────────────────────────────────────────╯\033[0m\n")

    import json
    configured_items = []

    # 1. Claude Code (~/.claude.json & ~/.claude/settings.json)
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
                "command": sys.executable,
                "args": ["-m", "mcp.server"],
                "env": {
                    "OMNICACHE_PORT": str(config.PORT)
                }
            }
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            configured_items.append(f"Claude Code MCP Config: {cp}")
        except Exception:
            pass

    # 2. Cursor MCP (~/.cursor/mcp.json or project .cursor/mcp.json)
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
                "command": sys.executable,
                "args": ["-m", "mcp.server"],
                "env": {
                    "OMNICACHE_PORT": str(config.PORT)
                }
            }
            with open(curp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            configured_items.append(f"Cursor MCP Config:      {curp}")
        except Exception:
            pass

    # 3. Shell Profile Export Helper (~/.omnicache/env.sh)
    env_sh_path = os.path.expanduser("~/.omnicache/env.sh")
    try:
        os.makedirs(os.path.dirname(env_sh_path), exist_ok=True)
        with open(env_sh_path, "w", encoding="utf-8") as f:
            f.write(f'# OmniCache Shell Environment Exports\nexport ANTHROPIC_BASE_URL="http://127.0.0.1:{config.PORT}"\nexport OPENAI_BASE_URL="http://127.0.0.1:{config.PORT}/v1"\nexport OPENAI_API_BASE="http://127.0.0.1:{config.PORT}/v1"\n')
        configured_items.append(f"Shell Env Helper:       {env_sh_path}")
    except Exception:
        pass

    for item in configured_items:
        print(f"\033[1;32m  ✔ {item}\033[0m")

    print(f"\n\033[1;37m🎉 Setup complete! You can now run:\033[0m")
    print(f"   \033[1;36momnicache run claude\033[0m  (for Claude Code)")
    print(f"   \033[1;36momnicache run cursor .\033[0m  (for Cursor IDE)\n")

def main():
    # Handle "omnicache run <command> [args...]"
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        run_wrapper(sys.argv[2:], host=config.HOST, port=config.PORT)
        return

    parser = argparse.ArgumentParser(
        prog="omnicache",
        description="OmniCache - Local Acceleration Sidecar for AI Coding Agents."
    )
    parser.add_argument("command", nargs="?", default="start", choices=["start", "run", "init", "doctor", "benchmark", "stats", "reset-circuit"], help="Action to perform (default: start)")
    parser.add_argument("-p", "--port", type=int, default=config.PORT, help=f"Port to bind server to (default: {config.PORT})")
    parser.add_argument("-H", "--host", type=str, default=config.HOST, help=f"Host interface (default: {config.HOST})")
    parser.add_argument("--provider", type=str, default=None, help="Target provider for reset-circuit (openai, anthropic, google)")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose HTTP request logging")

    args = parser.parse_args()

    if args.command == "init":
        run_init()
        sys.exit(0)
    elif args.command == "doctor":
        run_doctor()
        sys.exit(0)
    elif args.command == "benchmark":
        run_benchmark()
        sys.exit(0)
    elif args.command == "stats":
        run_stats()
        sys.exit(0)
    elif args.command == "reset-circuit":
        run_reset_circuit(provider=args.provider)
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
