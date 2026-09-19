"""
Benchmark scenario definitions and synthetic traffic generators for OmniCache Proxy.
Simulates realistic enterprise workloads: Developer Copilot, Agent Swarms, High-Burst Concurrency, and Multi-Tenant Isolation.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List
import random

@dataclass
class BenchmarkRequest:
    endpoint: str
    payload: Dict[str, Any]
    headers: Dict[str, str] = field(default_factory=dict)
    name: str = "request"
    expected_type: str = "any"  # exact, semantic, tool, miss, any

BASE_PROMPTS = [
    "Write a Python function to sort a list using quicksort with median-of-three pivot.",
    "Explain the difference between optimistic and pessimistic locking in distributed databases.",
    "How do I implement rate limiting using the sliding window log algorithm in Redis?",
    "What are the best practices for structuring microservices communication using gRPC?",
    "Convert this SQL query to use window functions to calculate a 7-day rolling average.",
    "Debug this memory leak in Node.js event emitter listener registration.",
    "Provide a Kubernetes Deployment YAML for a stateless web application with rolling updates.",
    "How does the CRDT state-based replication resolve conflicting writes across P2P nodes?",
]

REPHRASE_TEMPLATES = [
    "Please {prompt}",
    "Could you {prompt}",
    "Help me {prompt}",
    "Can you {prompt}",
    "Kindly {prompt}",
]

def generate_rephrased(prompt: str) -> str:
    template = random.choice(REPHRASE_TEMPLATES)
    lower_first = prompt[0].lower() + prompt[1:]
    return template.format(prompt=lower_first)


def get_copilot_workload(num_requests: int = 100, tenant: str = "copilot_dev") -> List[BenchmarkRequest]:
    """
    Simulates developer IDE copilot traffic:
    ~75% Exact Cache Hits, ~15% Semantic Variants, ~10% Novel Ingestions.
    """
    requests = []
    pool = BASE_PROMPTS[:5]

    for i in range(num_requests):
        rand = random.random()
        if rand < 0.75:
            # Exact match repeat
            prompt = pool[i % len(pool)]
            exp = "exact"
        elif rand < 0.90:
            # Semantic variant
            base = pool[i % len(pool)]
            prompt = generate_rephrased(base)
            exp = "semantic"
        else:
            # Novel miss
            prompt = f"Novel query {i} regarding compiler optimization passes in LLVM."
            exp = "miss"

        requests.append(BenchmarkRequest(
            endpoint="/v1/chat/completions",
            payload={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0
            },
            headers={
                "x-org-id": tenant,
                "x-dashboard-playground": "true"
            },
            name=f"copilot-{i}",
            expected_type=exp
        ))
    return requests


def get_agent_swarm_workload(num_requests: int = 100, tenant: str = "agent_swarm") -> List[BenchmarkRequest]:
    """
    Simulates multi-agent swarm execution:
    Mix of Claude Messages format and OpenAI completions with semantic queries and tool payloads.
    """
    requests = []
    pool = BASE_PROMPTS[3:]

    for i in range(num_requests):
        use_claude = (i % 2 == 0)
        base = pool[i % len(pool)]
        is_semantic = (i % 3 == 0)
        prompt = generate_rephrased(base) if is_semantic else base

        if use_claude:
            req = BenchmarkRequest(
                endpoint="/v1/messages",
                payload={
                    "model": "claude-sonnet-4-5-20250929",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1024
                },
                headers={
                    "x-org-id": tenant,
                    "x-dashboard-playground": "true"
                },
                name=f"swarm-claude-{i}",
                expected_type="semantic" if is_semantic else "exact"
            )
        else:
            req = BenchmarkRequest(
                endpoint="/v1/chat/completions",
                payload={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0
                },
                headers={
                    "x-org-id": tenant,
                    "x-dashboard-playground": "true"
                },
                name=f"swarm-openai-{i}",
                expected_type="semantic" if is_semantic else "exact"
            )
        requests.append(req)
    return requests


def get_burst_workload(num_requests: int = 200, tenant: str = "burst_tenant") -> List[BenchmarkRequest]:
    """
    Simulates high-velocity spike traffic:
    Thousands of requests hitting a small set of hot cached prompts.
    Tests lock contention, memory throughput, and singleflight deduplication.
    """
    requests = []
    hot_prompts = BASE_PROMPTS[:3]
    for i in range(num_requests):
        prompt = hot_prompts[i % len(hot_prompts)]
        requests.append(BenchmarkRequest(
            endpoint="/v1/chat/completions",
            payload={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0
            },
            headers={
                "x-org-id": tenant,
                "x-dashboard-playground": "true"
            },
            name=f"burst-{i}",
            expected_type="exact"
        ))
    return requests


def get_multi_tenant_workload(num_requests: int = 120) -> List[BenchmarkRequest]:
    """
    Simulates multi-tenant enterprise traffic split across isolated orgs.
    Verifies tenant cache isolation and multi-tenant quotas.
    """
    tenants = ["org_finance", "org_engineering", "org_datascience"]
    requests = []
    for i in range(num_requests):
        tenant = tenants[i % len(tenants)]
        prompt = BASE_PROMPTS[i % len(BASE_PROMPTS)]
        requests.append(BenchmarkRequest(
            endpoint="/v1/chat/completions",
            payload={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0
            },
            headers={
                "x-org-id": tenant,
                "x-dashboard-playground": "true"
            },
            name=f"tenant-{tenant}-{i}",
            expected_type="any"
        ))
    return requests
