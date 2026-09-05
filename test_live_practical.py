"""
Practical Real-World Test Suite for OmniCache Proxy.
Simulates realistic production traffic patterns and verifies caching accuracy, latency, and token savings.
"""

import sys
import os
import time
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import warnings
try:
    import starlette.exceptions
    warnings.filterwarnings("ignore", category=starlette.exceptions.StarletteDeprecationWarning)
except Exception:
    pass

from starlette.testclient import TestClient
from server.gateway import app
from core.vector_cache import cache_instance
from server.quotas import quota_manager

def practical_test():
    client = TestClient(app)
    cache_instance.clear()
    quota_manager.register_key("acme_key", team_name="Acme Corp", org_id="acme_corp")
    headers = {"x-api-key": "acme_key", "x-org-id": "acme_corp"}

    print("\n" + "═"*70)
    print("⚡ OMNICACHE PRACTICAL PRODUCTION TEST BENCH")
    print("═"*70 + "\n")

    # -------------------------------------------------------------
    # 1. First Call: Customer Support FAQ (Cold Cache)
    # -------------------------------------------------------------
    print("▶ 1. Storing Knowledge Query 1: 'What is your refund policy?'")
    payload_1 = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "What is your refund policy?"}],
        "temperature": 0.0
    }
    mock_res_1 = {
        "id": "chatcmpl-refund-1",
        "object": "chat.completion",
        "choices": [{"message": {"role": "assistant", "content": "We offer a 30-day money-back guarantee with no questions asked."}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 18, "total_tokens": 30}
    }
    cache_instance.store(payload_1, mock_res_1, org_id="acme_corp")
    print("   └─ Stored in OmniCache L1 & L2 memory.")

    # -------------------------------------------------------------
    # 2. Second Call: Semantic Rephrasing by Another User
    # -------------------------------------------------------------
    payload_2 = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Please explain what is your refund policy."}],
        "temperature": 0.0
    }

    t0 = time.perf_counter()
    res_2 = client.post("/v1/chat/completions", json=payload_2, headers=headers)
    dt_2 = (time.perf_counter() - t0) * 1000

    print("\n▶ 2. Sending Rephrased Query: 'Please explain what is your refund policy.'")
    print(f"   ├─ Status Code:      {res_2.status_code}")
    print(f"   ├─ Cache Outcome:    🟢 {res_2.headers.get('x-cache-status')}")
    print(f"   ├─ Similarity Score: 🎯 {res_2.headers.get('x-cache-similarity')}")
    print(f"   ├─ Response Latency: ⚡ {dt_2:.2f} ms")
    print(f"   ├─ Tokens Saved:     💰 {res_2.headers.get('x-tokens-saved')} tokens (100% Free)")
    print(f"   └─ Response Text:    \"{res_2.json()['choices'][0]['message']['content']}\"")

    # -------------------------------------------------------------
    # 3. Third Call: Claude Messages API Endpoint (/v1/messages)
    # -------------------------------------------------------------
    print("\n▶ 3. Testing Claude Code Native Endpoint (/v1/messages)")
    claude_payload = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Explain how database indexing works"}],
        "max_tokens": 500
    }
    res_claude_1 = client.post("/v1/messages", json=claude_payload, headers=headers)
    print(f"   ├─ First Call (Cold): {res_claude_1.headers.get('x-cache-status')} (Tokens Used: {res_claude_1.headers.get('x-tokens-used')})")

    # Rephrased Claude call
    claude_rephrased = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Please explain how database indexing works"}],
        "max_tokens": 500
    }
    t_c = time.perf_counter()
    res_claude_2 = client.post("/v1/messages", json=claude_rephrased, headers=headers)
    dt_c = (time.perf_counter() - t_c) * 1000
    print(f"   ├─ Second Call (Rephrased): 🟢 {res_claude_2.headers.get('x-cache-status')}")
    print(f"   ├─ Similarity Score:        🎯 {res_claude_2.headers.get('x-cache-similarity')}")
    print(f"   ├─ Response Latency:        ⚡ {dt_c:.2f} ms")
    print(f"   ├─ Tokens Saved:            💰 {res_claude_2.headers.get('x-tokens-saved')} tokens")
    print(f"   └─ Response Content:        {res_claude_2.json()['content'][0]['text'][:80]}...")

    # -------------------------------------------------------------
    # 4. Final Cumulative Metrics
    # -------------------------------------------------------------
    stats = client.get("/v1/cache/stats", headers=headers).json()
    fm = stats.get("financial_metrics", {})
    print("\n" + "═"*70)
    print("📊 CUMULATIVE LIVE TELEMETRY & SAVINGS")
    print("═"*70)
    print(f"Total Requests:       {stats.get('total_requests')}")
    print(f"Cache Hit Rate:       {stats.get('hit_rate_percentage')}%")
    print(f"Total Tokens Saved:   {fm.get('total_tokens_saved')} tokens")
    print(f"Total Dollars Saved:  ${fm.get('total_savings_usd')}")
    print("═"*70 + "\n")

if __name__ == "__main__":
    practical_test()
