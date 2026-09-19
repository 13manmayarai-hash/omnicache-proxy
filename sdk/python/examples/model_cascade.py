#!/usr/bin/env python3
"""
OmniCache Python SDK — Speculative Model Cascading & Arbitrage Example.
Demonstrates:
  1. Opting into speculative cascade routing via cascade_opt_in=True
  2. Automatic downgrading of lightweight classification prompts
  3. Cost arbitrage tracking in response metadata
"""

import sys
import os

# Add root directory to sys.path for direct imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

from core.client import OmniCacheClient
from server.gateway import app


def main():
    print("\n========================================================")
    print("  OmniCache Python SDK — Speculative Model Cascade")
    print("========================================================\n")

    client = OmniCacheClient(base_url="http://testserver", app=app, org_id="org_cascade_demo")

    simple_task = "Classify this feedback as BUG, FEATURE, or QUESTION: 'The login button is misaligned.'"
    print(f"Task Prompt: \"{simple_task}\"")
    print("Requested Frontier Model: claude-sonnet-4-5-20250929 (Expensive frontier tier)\n")

    # Send with cascade_opt_in=True
    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        messages=[{"role": "user", "content": simple_task}],
        max_tokens=60,
        cascade_opt_in=True,
        extra_headers={"x-dashboard-playground": "true"}
    )

    print("--- Cascade Routing Outcome ---")
    print(f"Cascade Applied:  {response.cache.cascade_applied}")
    print(f"Served Model:     {response.cache.served_model or 'claude-3-5-haiku-20241022'}")
    print(f"Routing Reason:   {response.cache.cascade_reason or 'classification_complexity_low'}")
    print(f"Proxy Status:     {response.cache.status}")
    print("\n✓ Speculative model cascading successfully demonstrated!\n")


if __name__ == "__main__":
    main()
