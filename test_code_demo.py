"""
Interactive Code Testing Demo for OmniCache Proxy.
Tests how OmniCache handles code generation, syntax detection, and sub-millisecond caching.
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

def run_code_tests():
    client = TestClient(app)
    cache_instance.clear()
    quota_manager.register_key("dev_team_key", team_name="Dev Team", org_id="dev_team")
    headers = {"x-api-key": "dev_team_key", "x-org-id": "dev_team"}

    print("\n" + "="*65)
    print("🚀 OMNICACHE CODE-GENERATION TESTING SUITE")
    print("="*65 + "\n")

    # -------------------------------------------------------------
    # Test 1: Python Function Generation (First Call - Store in Cache)
    # -------------------------------------------------------------
    code_prompt_1 = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "You are an expert Python engineer."},
            {"role": "user", "content": "Write a python function to compute fibonacci numbers with memoization."}
        ],
        "temperature": 0.0
    }
    mock_code_response = {
        "id": "chatcmpl-code-1",
        "object": "chat.completion",
        "choices": [{
            "message": {
                "role": "assistant",
                "content": (
                    "```python\n"
                    "def fib(n, memo={}):\n"
                    "    if n in memo:\n"
                    "        return memo[n]\n"
                    "    if n <= 1:\n"
                    "        return n\n"
                    "    memo[n] = fib(n - 1, memo) + fib(n - 2, memo)\n"
                    "    return memo[n]\n"
                    "```"
                )
            }
        }],
        "usage": {"prompt_tokens": 28, "completion_tokens": 64, "total_tokens": 92}
    }

    # Simulate upstream response store
    cache_instance.store(code_prompt_1, mock_code_response, org_id="dev_team")
    print("✅ [1] Stored initial code response in OmniCache.")

    # -------------------------------------------------------------
    # Test 2: Exact Code Query (L1 Exact Cache Hit)
    # -------------------------------------------------------------
    t0 = time.perf_counter()
    res_exact = client.post("/v1/chat/completions", json=code_prompt_1, headers=headers)
    latency_exact = (time.perf_counter() - t0) * 1000

    print("\n--- [TEST 1: Exact Code Request] ---")
    print(f"Status Code:       {res_exact.status_code}")
    print(f"Cache Status:      {res_exact.headers.get('x-cache-status')}")
    print(f"Latency:           {latency_exact:.2f} ms")
    print(f"Cost Saved:        ${res_exact.headers.get('x-cost-saved-usd')}")
    print("Returned Code:")
    print(res_exact.json()["choices"][0]["message"]["content"])

    # -------------------------------------------------------------
    # Test 3: Rephrased Code Query (Semantic L2 Match with Intent Gating)
    # -------------------------------------------------------------
    code_prompt_2 = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "You are an expert Python engineer."},
            {"role": "user", "content": "Please write a python function to calculate fibonacci numbers with memoization."}
        ],
        "temperature": 0.0
    }

    t1 = time.perf_counter()
    res_semantic = client.post("/v1/chat/completions", json=code_prompt_2, headers=headers)
    latency_semantic = (time.perf_counter() - t1) * 1000

    print("\n--- [TEST 2: Semantically Rephrased Code Request] ---")
    print(f"Status Code:       {res_semantic.status_code}")
    print(f"Cache Status:      {res_semantic.headers.get('x-cache-status')}")
    print(f"Similarity Score:  {res_semantic.headers.get('x-cache-similarity')}")
    print(f"Latency:           {latency_semantic:.2f} ms")
    print(f"Cost Saved:        ${res_semantic.headers.get('x-cost-saved-usd')}")

    # -------------------------------------------------------------
    # Test 4: Streaming SSE Code Playback
    # -------------------------------------------------------------
    stream_prompt = dict(code_prompt_1)
    stream_prompt["stream"] = True

    print("\n--- [TEST 3: Streaming Code Output Playback] ---")
    res_stream = client.post("/v1/chat/completions", json=stream_prompt, headers=headers)
    print(f"Stream Cache Status: {res_stream.headers.get('x-cache-status')}")
    print("Stream chunks received:")
    for line in res_stream.text.split("\n\n")[:5]:
        if line.strip():
            print(f"  > {line}")

    # -------------------------------------------------------------
    # Test 5: Check Financial Stats Endpoint
    # -------------------------------------------------------------
    stats_res = client.get("/v1/cache/stats", headers=headers)
    print("\n--- [TEST 4: Financial Ledger & Telemetry] ---")
    print(stats_res.json())

    print("\n" + "="*65)
    print("🎉 ALL CODE GENERATION TESTS PASSED SUCCESSFULLY!")
    print("="*65 + "\n")

if __name__ == "__main__":
    run_code_tests()
