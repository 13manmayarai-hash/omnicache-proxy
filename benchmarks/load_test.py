"""
OmniCache Production Load Test & Concurrency Benchmark Runner.
Executes high-throughput synthetic LLM traffic across asynchronous workers and profiles latency spectra.
"""

import sys
import os
import time
import asyncio
import argparse
from typing import List, Dict, Any, Optional
import httpx

# Ensure parent path is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from server.gateway import app
from benchmarks.scenarios import (
    BenchmarkRequest,
    get_copilot_workload,
    get_agent_swarm_workload,
    get_burst_workload,
    get_multi_tenant_workload,
    BASE_PROMPTS
)
from benchmarks.reporter import (
    BenchmarkResult,
    print_terminal_report,
    generate_markdown_report,
    generate_json_report
)
from server.quotas import quota_manager

BENCHMARK_KEY = "bench_admin_key"
quota_manager.register_key(
    BENCHMARK_KEY,
    team_name="Benchmark Harness",
    org_id="admin",
    role="admin",
    monthly_budget_usd=10_000_000.0,
    rate_limit_rpm=1_000_000
)

async def warmup_cache(client: httpx.AsyncClient, count: int = 5):
    """Primes cache with base prompts to establish warm cache baseline."""
    for prompt in BASE_PROMPTS[:count]:
        try:
            await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0
                },
                headers={
                    "x-org-id": "warmup_tenant",
                    "x-dashboard-playground": "true",
                    "x-api-key": BENCHMARK_KEY
                },
                timeout=5.0
            )
        except Exception:
            pass


async def execute_request(
    client: httpx.AsyncClient,
    req: BenchmarkRequest,
    sem: asyncio.Semaphore
) -> Dict[str, Any]:
    """Executes a single benchmark request within concurrency semaphore."""
    async with sem:
        headers = dict(req.headers)
        if "x-api-key" not in headers and "authorization" not in headers:
            headers["x-api-key"] = BENCHMARK_KEY

        t0 = time.perf_counter_ns()
        try:
            resp = await client.post(
                req.endpoint,
                json=req.payload,
                headers=headers,
                timeout=10.0
            )
            t1 = time.perf_counter_ns()
            duration_ms = (t1 - t0) / 1_000_000.0

            cache_status = resp.headers.get("x-cache-status", "MISS").upper()
            tokens_saved = int(resp.headers.get("x-tokens-saved", "0"))
            cost_saved = float(resp.headers.get("x-cost-saved-usd", "0.0"))
            success = (resp.status_code == 200)

            return {
                "success": success,
                "duration_ms": duration_ms,
                "cache_status": cache_status,
                "tokens_saved": tokens_saved,
                "cost_saved": cost_saved,
                "status_code": resp.status_code
            }
        except Exception as e:
            t1 = time.perf_counter_ns()
            return {
                "success": False,
                "duration_ms": (t1 - t0) / 1_000_000.0,
                "cache_status": "ERROR",
                "tokens_saved": 0,
                "cost_saved": 0.0,
                "status_code": 0,
                "error": str(e)
            }


async def run_scenario(
    client: httpx.AsyncClient,
    scenario_name: str,
    requests: List[BenchmarkRequest],
    concurrency: int,
    target_url: str = "internal",
    quiet: bool = False
) -> BenchmarkResult:
    """Executes a benchmark scenario across concurrent worker tasks."""
    if not quiet:
        print(f"\n🚀 Running Scenario: \033[1;33m{scenario_name}\033[0m ({len(requests)} requests, concurrency={concurrency})...")

    sem = asyncio.Semaphore(concurrency)
    t_start = time.perf_counter()

    tasks = [execute_request(client, req, sem) for req in requests]
    raw_results = await asyncio.gather(*tasks)

    t_end = time.perf_counter()
    duration = t_end - t_start

    res = BenchmarkResult(
        scenario_name=scenario_name,
        total_requests=len(requests),
        successful_requests=sum(1 for r in raw_results if r["success"]),
        failed_requests=sum(1 for r in raw_results if not r["success"]),
        duration_seconds=duration,
        rps=0.0,
        concurrency=concurrency,
        target_url=target_url
    )

    for r in raw_results:
        res.latencies_ms.append(r["duration_ms"])
        st = r["cache_status"]
        if "EXACT" in st:
            res.exact_hits += 1
        elif "SEMANTIC" in st:
            res.semantic_hits += 1
        elif "TOOL" in st:
            res.tool_hits += 1
        else:
            res.misses += 1

        res.total_tokens_saved += r["tokens_saved"]
        res.total_cost_saved_usd += r["cost_saved"]

    res.compute_statistics()
    return res


async def run_benchmark_suite(
    scenario: str = "all",
    num_requests: int = 100,
    concurrency: int = 10,
    target_url: str = "internal",
    warmup_count: int = 5,
    export_md: Optional[str] = None,
    export_json: Optional[str] = None,
    quiet: bool = False
) -> List[BenchmarkResult]:
    """Runs one or all benchmark scenarios and produces reports."""
    
    if target_url == "internal":
        transport = httpx.ASGITransport(app=app)
        client_ctx = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    else:
        client_ctx = httpx.AsyncClient(base_url=target_url)

    async with client_ctx as client:
        if warmup_count > 0:
            if not quiet:
                print(f"🔥 Warming up cache ({warmup_count} prompts)...")
            await warmup_cache(client, warmup_count)

        scenario_runners = {
            "copilot": lambda: get_copilot_workload(num_requests),
            "agent_swarm": lambda: get_agent_swarm_workload(num_requests),
            "burst": lambda: get_burst_workload(num_requests),
            "multi_tenant": lambda: get_multi_tenant_workload(num_requests)
        }

        if scenario == "all":
            selected = list(scenario_runners.keys())
        elif scenario in scenario_runners:
            selected = [scenario]
        else:
            raise ValueError(f"Unknown scenario '{scenario}'. Options: {list(scenario_runners.keys()) + ['all']}")

        results: List[BenchmarkResult] = []
        for s_name in selected:
            workload = scenario_runners[s_name]()
            res = await run_scenario(client, s_name, workload, concurrency, target_url, quiet=quiet)
            results.append(res)
            print_terminal_report(res)

        if export_md:
            generate_markdown_report(results, export_md)
            print(f"\n📄 Markdown benchmark report saved to: \033[1;32m{export_md}\033[0m")

        if export_json:
            generate_json_report(results, export_json)
            print(f"📊 JSON benchmark metrics saved to: \033[1;32m{export_json}\033[0m\n")

        return results


def main():
    parser = argparse.ArgumentParser(
        description="OmniCache Production Load Test & Concurrency Benchmark Harness",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--scenario", "-s",
        type=str,
        default="all",
        choices=["all", "copilot", "agent_swarm", "burst", "multi_tenant"],
        help="Scenario workload to simulate"
    )
    parser.add_argument(
        "--requests", "-n",
        type=int,
        default=100,
        help="Total requests per scenario"
    )
    parser.add_argument(
        "--concurrency", "-c",
        type=int,
        default=10,
        help="Concurrent worker count"
    )
    parser.add_argument(
        "--url", "-u",
        type=str,
        default="internal",
        help="Gateway target URL ('internal' for zero-config in-process ASGI engine)"
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=5,
        help="Number of initial cache warmup requests"
    )
    parser.add_argument(
        "--export-md",
        type=str,
        default=None,
        help="Output path for Markdown benchmark report"
    )
    parser.add_argument(
        "--export-json",
        type=str,
        default=None,
        help="Output path for JSON performance metrics"
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress verbose progress output"
    )

    args = parser.parse_args()

    asyncio.run(run_benchmark_suite(
        scenario=args.scenario,
        num_requests=args.requests,
        concurrency=args.concurrency,
        target_url=args.url,
        warmup_count=args.warmup,
        export_md=args.export_md,
        export_json=args.export_json,
        quiet=args.quiet
    ))


if __name__ == "__main__":
    main()
