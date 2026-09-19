"""
Integration and regression test suite for OmniCache Benchmark & Stress Test Harness.
Verifies workload generators, concurrency runners, percentiles, and report exporters.
"""

import os
import json
import pytest
import asyncio
from benchmarks.scenarios import (
    get_copilot_workload,
    get_agent_swarm_workload,
    get_burst_workload,
    get_multi_tenant_workload,
    BASE_PROMPTS
)
from benchmarks.reporter import BenchmarkResult, generate_markdown_report, generate_json_report
from benchmarks.load_test import run_benchmark_suite


def test_workload_generators():
    """Verify workload scenario generators create valid requests."""
    copilot_reqs = get_copilot_workload(30)
    assert len(copilot_reqs) == 30
    assert all(r.endpoint == "/v1/chat/completions" for r in copilot_reqs)
    assert all("x-org-id" in r.headers for r in copilot_reqs)

    swarm_reqs = get_agent_swarm_workload(20)
    assert len(swarm_reqs) == 20
    endpoints = {r.endpoint for r in swarm_reqs}
    assert "/v1/chat/completions" in endpoints
    assert "/v1/messages" in endpoints

    burst_reqs = get_burst_workload(50)
    assert len(burst_reqs) == 50
    assert all(r.expected_type == "exact" for r in burst_reqs)

    multi_reqs = get_multi_tenant_workload(30)
    assert len(multi_reqs) == 30
    tenants = {r.headers["x-org-id"] for r in multi_reqs}
    assert len(tenants) >= 3


def test_benchmark_result_computations():
    """Verify percentile and statistical computations on BenchmarkResult."""
    res = BenchmarkResult(
        scenario_name="test_math",
        total_requests=10,
        successful_requests=10,
        failed_requests=0,
        duration_seconds=0.1,
        rps=0.0,
        latencies_ms=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0],
        exact_hits=8,
        semantic_hits=1,
        tool_hits=0,
        misses=1,
        total_tokens_saved=5000,
        total_cost_saved_usd=0.015
    )
    res.compute_statistics()

    assert res.min_ms == 1.0
    assert res.max_ms == 10.0
    assert res.mean_ms == 5.5
    assert res.p50_ms == 5.5
    assert res.p90_ms == 9.0
    assert res.hit_ratio_pct == 90.0
    assert res.rps == 100.0
    assert res.tokens_per_sec == 50000.0


@pytest.mark.anyio
async def test_run_benchmark_suite_e2e(tmp_path):
    """Verify end-to-end benchmark execution across async workers with Markdown and JSON exports."""
    md_path = str(tmp_path / "benchmark_report.md")
    json_path = str(tmp_path / "benchmark_metrics.json")

    results = await run_benchmark_suite(
        scenario="burst",
        num_requests=25,
        concurrency=5,
        target_url="internal",
        warmup_count=3,
        export_md=md_path,
        export_json=json_path,
        quiet=True
    )

    assert len(results) == 1
    r = results[0]
    assert r.scenario_name == "burst"
    assert r.total_requests == 25
    assert r.successful_requests == 25
    assert r.failed_requests == 0
    assert r.rps > 0
    assert r.p50_ms > 0
    assert r.hit_ratio_pct >= 80.0  # Hot burst should have high hit ratio

    # Check Markdown report
    assert os.path.exists(md_path)
    with open(md_path, "r", encoding="utf-8") as f:
        md_content = f.read()
    assert "OmniCache Production Benchmark" in md_content
    assert "P50 Latency" in md_content
    assert "flowchart TD" in md_content
    assert "Dec{\"Radix / Exact Match?\"}" in md_content or "Dec" in md_content

    # Check JSON export
    assert os.path.exists(json_path)
    with open(json_path, "r", encoding="utf-8") as f:
        json_data = json.load(f)
    assert "timestamp" in json_data
    assert len(json_data["scenarios"]) == 1
    s0 = json_data["scenarios"][0]
    assert s0["name"] == "burst"
    assert s0["total_requests"] == 25
    assert "p50_ms" in s0["latencies"]
    assert "exact_hits" in s0["tiers"]
