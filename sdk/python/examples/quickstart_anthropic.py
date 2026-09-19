#!/usr/bin/env python3
"""
OmniCache Python SDK — Anthropic Messages Quickstart Example.
Demonstrates:
  1. Anthropic Messages API protocol
  2. Sub-Millisecond exact replay
  3. Response telemetry inspection
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
    print("  OmniCache Python SDK — Anthropic Claude Quickstart")
    print("========================================================\n")

    client = OmniCacheClient(base_url="http://testserver", app=app, org_id="org_quickstart_claude")

    prompt = "State the difference between optimistic and pessimistic locking."

    # 1. Cold Request
    print("[1] Sending Cold Anthropic Request...")
    t0 = time.perf_counter()
    r1 = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=256,
        extra_headers={"x-dashboard-playground": "true"}
    )
    t1 = (time.perf_counter() - t0) * 1000.0

    print(f"    Status:       {r1.cache.status}")
    print(f"    Latency:      {t1:.2f} ms")
    print(f"    Tokens Saved: {r1.cache.tokens_saved}\n")

    # 2. Warm Request (Exact Replay)
    print("[2] Sending Warm Anthropic Request...")
    t0 = time.perf_counter()
    r2 = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=256,
        extra_headers={"x-dashboard-playground": "true"}
    )
    t2 = (time.perf_counter() - t0) * 1000.0

    print(f"    Status:       {r2.cache.status}")
    print(f"    Latency:      {t2:.2f} ms (Speedup: ~{t1/max(t2, 0.001):.1f}x)")
    print(f"    Tokens Saved: {r2.cache.tokens_saved}")
    print(f"    Cost Saved:   ${r2.cache.cost_saved_usd:.6f}")
    print(f"    Is Hit:       {r2.cache.is_hit()}\n")

    print("✓ Anthropic Claude Quickstart completed successfully!\n")


if __name__ == "__main__":
    main()
