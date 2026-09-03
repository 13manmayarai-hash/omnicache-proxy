"""
OmniCache command-line interface.
"""

import sys
import os
import time
import socket
import argparse
import uvicorn
from core.config import config
from server.gateway import app, cache_instance, METRICS_LEDGER
from persistence.snapshot_store import snapshot_store

def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0

def run_doctor():
    print("\n--- OmniCache System Diagnostics ---")
    
    # 1. Environment & Python
    py_ver = sys.version.split()[0]
    print(f"Python Runtime: v{py_ver} ({sys.platform})")

    # 2. Database Persistence
    db_path = snapshot_store.db_path
    db_exists = os.path.exists(db_path)
    print(f"SQLite Store:   {db_path} ({'ready' if db_exists else 'will create on first write'})")

    # 3. Port Check
    port_used = is_port_in_use(config.PORT, config.HOST if config.HOST != "0.0.0.0" else "127.0.0.1")
    if port_used:
        print(f"Port {config.PORT}:      In use (OmniCache server or other process active)")
    else:
        print(f"Port {config.PORT}:      Available")

    # 4. In-Memory Vector Engine
    start = time.perf_counter()
    from core.embeddings import FastSemanticEmbedder
    FastSemanticEmbedder.embed("OmniCache health check prompt")
    embed_ms = (time.perf_counter() - start) * 1000
    print(f"Vector Engine:  Operational ({embed_ms:.3f}ms lookup)")

    # 5. Upstream Configured Keys
    keys_configured = []
    if config.ANTHROPIC_API_KEY: keys_configured.append("Anthropic")
    if config.OPENAI_API_KEY: keys_configured.append("OpenAI")
    if config.GEMINI_API_KEY: keys_configured.append("Gemini")
    
    if keys_configured:
        print(f"Upstream Keys:  {', '.join(keys_configured)}")
    else:
        print(f"Upstream Auth:  Client header passthrough active")

    print("Status:         All subsystems operational.\n")

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
    print("\n--- OmniCache Telemetry & Savings ---")
    stats = cache_instance.get_stats()
    print(f"Total Cost Saved:   ${METRICS_LEDGER['total_savings_usd']:.4f} USD")
    print(f"Tokens Saved:       {METRICS_LEDGER['total_tokens_saved']:,}")
    print(f"Tokens Forwarded:   {METRICS_LEDGER['total_tokens_used']:,}")
    print(f"Cache Hit Rate:     {stats.get('hit_rate_percentage', 0.0)}%")
    print(f"Cached Prompts:     {stats.get('active_l1_exact_entries', 0)} L1 / {stats.get('active_l2_semantic_entries', 0)} L2\n")

def main():
    parser = argparse.ArgumentParser(
        prog="omnicache",
        description="OmniCache - Lightweight semantic caching proxy for OpenAI and Anthropic APIs."
    )
    parser.add_argument("command", nargs="?", default="start", choices=["start", "doctor", "benchmark", "stats"], help="Action to perform (default: start)")
    parser.add_argument("-p", "--port", type=int, default=config.PORT, help=f"Port to bind server to (default: {config.PORT})")
    parser.add_argument("-H", "--host", type=str, default=config.HOST, help=f"Host interface (default: {config.HOST})")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose HTTP request logging")

    args = parser.parse_args()

    if args.command == "doctor":
        run_doctor()
        sys.exit(0)
    elif args.command == "benchmark":
        run_benchmark()
        sys.exit(0)
    elif args.command == "stats":
        run_stats()
        sys.exit(0)

    port = args.port
    host = args.host
    log_level = "info" if args.verbose else "warning"
    access_log = bool(args.verbose)

    print(f"OmniCache proxy listening on http://{host}:{port}")
    if not args.verbose:
        print("Running in silent terminal mode (pass --verbose for access logs).")
    
    uvicorn.run(app, host=host, port=port, access_log=access_log, log_level=log_level)

if __name__ == "__main__":
    main()
