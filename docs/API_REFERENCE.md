# API Reference

OmniCache implements standard OpenAI and Anthropic REST endpoints, alongside administrative cache and telemetry routes.

---

## Core Endpoints

### 1. OpenAI Chat Completions
`POST /v1/chat/completions`

Accepts standard OpenAI-format chat completion payloads.

#### Custom Request Headers:
* `X-Cache-Bypass: true` – Forces a cache bypass and fetches a fresh response from upstream.
* `X-Org-Id: <tenant-name>` – Logical tenant identifier for multi-tenant isolation.
* `X-Cache-Tag: <tag-name>` – Assigns an invalidation tag to the cached entry.

#### Response Headers:
* `X-Cache-Status`: `HIT_EXACT`, `HIT_SEMANTIC`, `MISS`, or `BYPASS`
* `X-Cache-Latency-Ms`: Lookup latency in milliseconds
* `X-Tokens-Saved`: Total tokens saved by cache hit
* `X-Cost-Saved-USD`: Estimated USD saved based on model price card

---

### 2. Anthropic Messages
`POST /v1/messages`

Accepts standard Anthropic Messages API payloads (used by Claude Code and Anthropic SDKs). Supports both JSON responses and Server-Sent Events (`stream: true`).

---

### 3. Model List
`GET /v1/models`

Returns available upstream models.

---

## Cache Management Endpoints

### 4. Invalidate by Tag
`POST /v1/cache/invalidate-tag`

Invalidates all cached entries associated with a specific tag.

```json
{
  "tag": "schema_v1"
}
```

---

### 5. Purge Cache
`POST /v1/cache/purge`

Purges all entries for a given tenant (via `X-Org-Id` header) or globally if no tenant header is provided.

---

### 6. CSV Ledger Export
`GET /v1/cache/export`

Downloads a `.csv` file containing the current cached prompt inventory, access counts, and creation timestamps.

---

## Observability & Diagnostics

### 7. Prometheus Metrics
`GET /metrics`

Standard Prometheus text format metrics for Grafana or Datadog scrapers.
Exposes `omnicache_savings_usd`, `omnicache_tokens_saved_total`, `omnicache_exact_hits_total`, `omnicache_semantic_hits_total`, and `omnicache_hit_rate_percentage`.

---

### 8. Cache Statistics
`GET /v1/cache/stats`

Returns runtime JSON metrics including total requests, exact hits, semantic hits, hit rate percentage, and active entry counts.

---

### 9. Health Check
`GET /healthz`

Returns `{"status": "ok", "version": "2.1.3"}`.
