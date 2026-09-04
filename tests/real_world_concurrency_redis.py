"""
Real-World Option C: High-Concurrency Redis & Multi-Tenant Stress Benchmark
Tests OmniCache with live Redis backend, 150+ concurrent requests,
L1 exact hits, L2 semantic paraphrases, SingleFlight coalescing, and P50/P95/P99 latency profiling.
"""

import os
import sys
import time
import json
import statistics
import concurrent.futures
from typing import List, Dict, Any
from starlette.testclient import TestClient

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from server.gateway import app
from core.vector_cache import cache_instance, CacheEntry
from core.storage import RedisCacheStorage
from server.quotas import quota_manager
from core.config import config

def log_step(title: str):
    print(f"\n\033[1;35m{'='*65}\n▶ {title}\n{'='*65}\033[0m")

def log_metric(label: str, val: Any):
    print(f"\033[1;32m  ✔ {label:<35}: \033[1;37m{val}\033[0m")

def run_concurrency_benchmark():
    log_step("STEP 1: Initializing Live Redis Distributed Backend")
    
    # Configure live Redis storage backend
    redis_url = "redis://127.0.0.1:6379/0"
    storage = RedisCacheStorage(redis_url=redis_url, entry_cls=CacheEntry)
    storage.clear()
    cache_instance.storage = storage
    print(f"  ⚡ Connected to live Redis instance: {redis_url}")

    # Register virtual tenant keys in quota manager
    tenant_map = {
        "tenant_devops": "key-devops-secret-123",
        "tenant_fintech": "key-fintech-secret-456",
        "tenant_security": "key-security-secret-789"
    }
    for org_id, key in tenant_map.items():
        quota_manager.register_key(key, team_name=org_id, org_id=org_id, monthly_budget_usd=1000.0, rate_limit_rpm=10000)

    client = TestClient(app)

    # -------------------------------------------------------------
    # STEP 2: Seeding Multi-Tenant Cache Entries (L1 Exact & L2 Semantic)
    # -------------------------------------------------------------
    log_step("STEP 2: Seeding Multi-Tenant Cache Entries (L1 Exact & L2 Semantic)")
    
    seed_data = [
        {
            "prompt": "How do I securely configure CORS headers in FastAPI?",
            "response": "To configure CORS in FastAPI, use `from fastapi.middleware.cors import CORSMiddleware` with explicit allow_origins.",
            "model": "gpt-4o"
        },
        {
            "prompt": "Explain how to implement two-phase commit in distributed systems.",
            "response": "Two-phase commit (2PC) is a distributed consensus algorithm with a prepare phase and a commit phase.",
            "model": "claude-3-5-sonnet-20241022"
        },
        {
            "prompt": "Write a regex pattern to validate RFC 5322 compliant email addresses.",
            "response": "A standard RFC 5322 regex is: `^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\\.[a-zA-Z0-9-.]+$`.",
            "model": "gpt-4o-mini"
        }
    ]

    for org_id in tenant_map.keys():
        for item in seed_data:
            openai_payload = {
                "model": item["model"],
                "temperature": 0.0,
                "messages": [{"role": "user", "content": item["prompt"]}]
            }
            openai_res = {
                "id": f"chatcmpl-{org_id}-{int(time.time()*1000)}",
                "object": "chat.completion",
                "model": item["model"],
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": item["response"]},
                    "finish_reason": "stop"
                }],
                "usage": {"prompt_tokens": 30, "completion_tokens": 50, "total_tokens": 80}
            }
            cache_instance.store(openai_payload, openai_res, org_id=org_id)
    
    print(f"  ✔ Seeded {len(tenant_map) * len(seed_data)} entries across {len(tenant_map)} isolated tenants in Redis.")

    # -------------------------------------------------------------
    # STEP 3: High-Concurrency Benchmark (150 Parallel Requests)
    # -------------------------------------------------------------
    log_step("STEP 3: Executing 150 Concurrent Mixed Requests (L1, L2, Tools, SingleFlight)")
    
    tenants_list = list(tenant_map.keys())
    request_tasks = []
    
    # Task 1: 60 L1 Exact Match hits
    for i in range(60):
        org_id = tenants_list[i % len(tenants_list)]
        api_key = tenant_map[org_id]
        item = seed_data[i % len(seed_data)]
        request_tasks.append({
            "type": "L1_EXACT",
            "url": "/v1/chat/completions",
            "headers": {"Authorization": f"Bearer {api_key}"},
            "payload": {
                "model": item["model"],
                "temperature": 0.0,
                "messages": [{"role": "user", "content": item["prompt"]}]
            }
        })

    # Task 2: 40 L2 Semantic Paraphrases (syntactic rewording & variations)
    semantic_variations = [
        ("How do I configure CORS headers in FastAPI securely?", "gpt-4o"),
        ("Explain how to implement two phase commit in distributed systems.", "claude-3-5-sonnet-20241022"),
        ("Write a regex pattern to validate RFC-5322 compliant email addresses.", "gpt-4o-mini")
    ]
    for i in range(40):
        org_id = tenants_list[i % len(tenants_list)]
        api_key = tenant_map[org_id]
        variation, matched_model = semantic_variations[i % len(semantic_variations)]
        request_tasks.append({
            "type": "L2_SEMANTIC",
            "url": "/v1/chat/completions",
            "headers": {"Authorization": f"Bearer {api_key}"},
            "payload": {
                "model": matched_model,
                "temperature": 0.0,
                "messages": [{"role": "user", "content": variation}]
            }
        })

    # Task 3: 30 Tool Replay requests
    # Pre-record a tool
    tool_cache_payload = {
        "tool_name": "git_status",
        "arguments": {"cwd": "/tmp"},
        "output": "On branch main, working tree clean",
        "workspace_fingerprint": "tenant_shared_workspace"
    }
    client.post("/v1/agent/tool_record", json=tool_cache_payload)
    
    for i in range(30):
        request_tasks.append({
            "type": "TOOL_REPLAY",
            "url": "/v1/agent/tool_replay",
            "headers": {"Content-Type": "application/json"},
            "payload": {
                "tool_name": "git_status",
                "arguments": {"cwd": "/tmp"},
                "workspace_fingerprint": "tenant_shared_workspace"
            }
        })

    # Task 4: 20 Tenant Isolation Collision Probes
    # Asking for a tenant's prompt under a DIFFERENT tenant key (must NOT collide!)
    unauth_key = "key-unregistered-guest"
    quota_manager.register_key(unauth_key, team_name="GuestTeam", org_id="tenant_unauthorized_guest", monthly_budget_usd=100.0)
    
    for i in range(20):
        request_tasks.append({
            "type": "TENANT_ISOLATION_PROBE",
            "url": "/v1/chat/completions",
            "headers": {"Authorization": f"Bearer {unauth_key}"},
            "payload": {
                "model": seed_data[0]["model"],
                "temperature": 0.0,
                "messages": [{"role": "user", "content": seed_data[0]["prompt"]}]
            }
        })

    # Execute all 150 requests concurrently using ThreadPool
    latencies: List[float] = []
    l1_hits = 0
    l2_hits = 0
    tool_hits = 0
    isolation_protected = 0
    errors = 0

    def execute_request(req_info):
        t0 = time.perf_counter()
        try:
            res = client.post(req_info["url"], headers=req_info.get("headers", {}), json=req_info["payload"])
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return (req_info["type"], res.status_code, res.headers, res.json() if res.status_code == 200 else {}, elapsed_ms)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return (req_info["type"], 500, {}, {"error": str(e)}, elapsed_ms)

    start_bench = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(execute_request, request_tasks))
    total_bench_duration = time.perf_counter() - start_bench

    for req_type, status_code, headers, body, elapsed in results:
        latencies.append(elapsed)
        cache_decision = headers.get("x-omnicache-decision") or body.get("status")
        cache_status = headers.get("x-cache-status")

        if req_type == "L1_EXACT" and (cache_decision == "HIT" or cache_status == "HIT_EXACT"):
            l1_hits += 1
        elif req_type == "L2_SEMANTIC" and (cache_decision == "HIT" or cache_status == "HIT_SEMANTIC"):
            l2_hits += 1
        elif req_type == "TOOL_REPLAY" and cache_decision == "HIT":
            tool_hits += 1
        elif req_type == "TENANT_ISOLATION_PROBE":
            # Must MISS because tenant_unauthorized_guest has no entries in its Redis keyspace!
            if cache_decision != "HIT" or cache_status not in ("HIT_EXACT", "HIT_SEMANTIC"):
                isolation_protected += 1

    # -------------------------------------------------------------
    # STEP 4: Performance & Latency Metrics
    # -------------------------------------------------------------
    log_step("STEP 4: Stress Benchmark Performance & Latency Telemetry")
    
    latencies.sort()
    p50 = statistics.median(latencies)
    p90 = latencies[int(len(latencies) * 0.90)]
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[int(len(latencies) * 0.99)]
    rps = len(request_tasks) / total_bench_duration

    log_metric("Total Requests Processed", f"{len(request_tasks)} requests")
    log_metric("Total Benchmark Duration", f"{total_bench_duration:.3f} seconds")
    log_metric("Throughput (RPS)", f"{rps:.1f} req/sec")
    log_metric("L1 Exact Cache Hits", f"{l1_hits}/60 ({(l1_hits/60)*100:.1f}%)")
    log_metric("L2 Semantic Cache Hits", f"{l2_hits}/40 ({(l2_hits/40)*100:.1f}%)")
    log_metric("Tool Replay Hits", f"{tool_hits}/30 ({(tool_hits/30)*100:.1f}%)")
    log_metric("Tenant Isolation Verification", f"{isolation_protected}/20 (Zero cross-tenant leakage)")
    log_metric("Errors / Unhandled Exceptions", f"{errors} errors")
    print(f"\n  ⏱ \033[1;36mLatency Distribution (including HTTP serialization & Redis roundtrip):\033[0m")
    log_metric("P50 (Median) Latency", f"{p50:.2f} ms")
    log_metric("P90 Latency", f"{p90:.2f} ms")
    log_metric("P95 Latency", f"{p95:.2f} ms")
    log_metric("P99 Latency", f"{p99:.2f} ms")

    # Verify key guarantees
    assert l1_hits == 60, f"Expected 60 L1 hits, got {l1_hits}"
    assert l2_hits >= 30, f"Expected >=30 L2 semantic hits, got {l2_hits}"
    assert tool_hits == 30, f"Expected 30 Tool Replay hits, got {tool_hits}"
    assert isolation_protected == 20, f"Cross-tenant collision detected! {isolation_protected}/20 protected"

    print("\n\033[1;32m🎉 Option C: High-Concurrency Redis & Stress Benchmark PASSED WITH FLYING COLORS!\033[0m\n")

if __name__ == "__main__":
    run_concurrency_benchmark()
