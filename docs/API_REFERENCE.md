# 📖 OmniCache AI Proxy 2.0: API Reference & Developer Specification

---

## 1. Gateway Endpoints

### 1.1 `POST /v1/chat/completions` (OpenAI Drop-In Route)
Accepts standard OpenAI chat completion JSON payloads.

#### Developer Control Headers:
| Header Name | Type | Description | Default |
|:---|:---|:---|:---|
| `Authorization` | String | Upstream API key (`Bearer sk-...`) or Virtual Key | `None` |
| `X-Org-Id` | String | Tenant / Workspace identifier for vector isolation | `"default"` |
| `X-Cache-Bypass` | Boolean | Force bypass cache and call upstream directly | `false` |
| `X-Cache-Threshold` | Float | Override similarity threshold (e.g. `0.95`) | Dynamic |
| `X-Cache-TTL` | Integer | Custom cache TTL in seconds | `604800` (7 days) |
| `X-Cache-Tag` | String | Domain tag for bulk invalidation (e.g. `docs-v2`) | `None` |
| `X-Allow-Cascade` | Boolean | Enable automatic cost-arbitrage model cascade | `true` |

#### Output Telemetry Headers:
```http
X-Cache-Status: HIT_SEMANTIC
X-Cache-Similarity: 0.9842
X-Cache-Latency-Ms: 0.74
X-Cost-Saved-USD: 0.003500
X-Tokens-Saved: 140
X-Tokens-Used: 0
X-Routed-Model: gpt-4o
```

---

### 1.2 `POST /v1/messages` (Anthropic Messages API / Claude Code)
Accepts native Anthropic Messages API payloads:
```json
{
  "model": "claude-3-5-sonnet-20241022",
  "messages": [{"role": "user", "content": "How do I optimize SQL indexes?"}],
  "max_tokens": 1024,
  "system": "You are a database architect."
}
```

---

### 1.3 `POST /v1/agent/tool-replay` (Agent Tool Accelerator)
Caches and replays deterministic tool execution outputs (`read_file`, `git_diff`, `grep`).
```json
// Request
{
  "tool_name": "read_file",
  "arguments": {"filepath": "server.py"},
  "workspace_fingerprint": "repo_v1",
  "output": "import starlette..." // optional: provided to store
}

// Response (on hit)
{
  "cached": true,
  "output": "import starlette...",
  "key": "a1b2c3..."
}
```

---

### 1.4 `GET /v1/enterprise/quotas` (Team Spending & Quotas)
Returns real-time spending and budget utilization across all virtual keys:
```json
{
  "team_fintech": {
    "team_name": "Fintech Core",
    "monthly_budget_usd": 500.0,
    "current_spend_usd": 42.15,
    "budget_used_pct": 8.43,
    "active_rpm": 14
  }
}
```

---

### 1.5 `POST /v1/cache/invalidate-tag` (Domain Purge)
Purges all cached entries matching a domain tag:
```json
{ "tag": "release-v2.1" }
```

---

### 1.6 `GET /v1/cache/stats` (Telemetry Dashboard API)
Returns real-time global cache metrics:
```json
{
  "total_requests": 1420,
  "exact_hits": 450,
  "semantic_hits": 680,
  "misses": 290,
  "hit_rate_percentage": 79.58,
  "financial_metrics": {
    "total_savings_usd": 18.4250,
    "total_tokens_saved": 842000,
    "total_tokens_used": 120500,
    "privacy_scrubbed_count": 48,
    "agent_tool_hits": 312
  }
}
```

---

### 1.7 `GET /healthz` (Health Check)
```json
{
  "status": "healthy",
  "service": "omnicache-proxy",
  "version": "2.0.0",
  "features": ["radix_tree", "agent_tool_replay", "cost_cascade", "vision_cache", "privacy_shield", "virtual_quotas"]
}
```
