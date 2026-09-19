#!/usr/bin/env python3
"""
OmniCache Python SDK — OpenAI Quickstart Example.
Demonstrates:
  1. Cold Ingestion (Cache MISS)
  2. Sub-Millisecond Exact Replay (Cache HIT_EXACT)
  3. Semantic Similarity Match (Cache HIT_SEMANTIC)
"""

import sys
import os
import time

# Add root directory to sys.path for direct imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

from core.client import OmniCacheClient
from server.gateway import app


def main():
    print("\n========================================================")
    print("  OmniCache Python SDK — OpenAI Quickstart")
    print("========================================================\n")

    # Connect to in-process ASGI app (or pass base_url="http://127.0.0.1:8000" for live server)
    client = OmniCacheClient(base_url="http://testserver", app=app, org_id="org_quickstart")

    # Check proxy health
    healthy = client.is_healthy()
    print(f"Proxy Online: {healthy}\n")

    prompt = "Explain Dijkstra's shortest path algorithm in three concise sentences."

    # 1. Cold Request
    print("[1] Sending Cold Request...")
    t0 = time.perf_counter()
    r1 = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        extra_headers={"x-dashboard-playground": "true"}
    )
    t1 = (time.perf_counter() - t0) * 1000.0

    print(f"    Status:       {r1.cache.status}")
    print(f"    Latency:      {t1:.2f} ms")
    print(f"    Tokens Saved: {r1.cache.tokens_saved}")
    print(f"    Is Hit:       {r1.cache.is_hit()}\n")

    # 2. Warm Request (Exact Match)
    print("[2] Sending Warm Request (Identical Prompt)...")
    t0 = time.perf_counter()
    r2 = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        extra_headers={"x-dashboard-playground": "true"}
    )
    t2 = (time.perf_counter() - t0) * 1000.0

    print(f"    Status:       {r2.cache.status}")
    print(f"    Latency:      {t2:.2f} ms (Speedup: ~{t1/max(t2, 0.001):.1f}x)")
    print(f"    Tokens Saved: {r2.cache.tokens_saved}")
    print(f"    Cost Saved:   ${r2.cache.cost_saved_usd:.6f}")
    print(f"    Is Hit:       {r2.cache.is_hit()}\n")

    print("✓ OpenAI Quickstart completed successfully!\n")


if __name__ == "__main__":
    main()
