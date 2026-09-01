# ⚡ OmniCache AI Proxy
### *The Intelligent, Zero-Latency Semantic Caching & Cost Gateway for LLMs*

---

## 📖 Table of Contents
1. [Executive Summary & Core Value Proposition](#1-executive-summary--core-value-proposition)
2. [Mission & Vision](#2-mission--vision)
3. [Competitor Vulnerability Teardown (10 Fatal Flaws & OmniCache Fixes)](#3-competitor-vulnerability-teardown)
4. [Enterprise-Grade Resilience & Guardrails (6 Advanced Capabilities)](#4-enterprise-grade-resilience--guardrails)
5. [System Architecture & Dual-Tier Engine](#5-system-architecture--dual-tier-engine)
6. [Intent Gating & Dynamic Threshold Matrix](#6-intent-gating--dynamic-threshold-matrix)
7. [Specialized Protocol Handling (Streaming, Reasoning & Agentic Tools)](#7-specialized-protocol-handling)
8. [Developer Headers & Management APIs](#8-developer-headers--management-apis)
9. [Provider Pricing & Cost Savings Ledger](#9-provider-pricing--cost-savings-ledger)
10. [Codebase Architecture & File Map](#10-codebase-architecture--file-map)
11. [Build Roadmap & Milestones](#11-build-roadmap--milestones)
12. [Quickstart & Verification](#12-quickstart--verification)

---

## 1. Executive Summary & Core Value Proposition

### The Problem
AI developers and enterprises face escalating operational and financial barriers:
1. **Excessive Inference Bills:** 30%–50% of real-world LLM queries are duplicates or semantically similar variations (customer support FAQs, repeating agent loops, boilerplate code/text transformations). Calling flagship frontier models ($2.50–$30.00/1M tokens) on duplicate queries burns engineering capital.
2. **Latency Penalties:** Waiting 500ms to 5,000ms for an upstream LLM completion degrades user experience and breaks interactive real-time applications.
3. **Provider Outages & Rate Limit Bricking:** Upstream HTTP 429 rate limits or provider downtime immediately crash downstream user workflows.
4. **Cache Stampedes:** Concurrent traffic spikes on identical queries trigger simultaneous redundant upstream calls, multiplying bills.

### The OmniCache Solution
OmniCache is a **zero-friction, drop-in API proxy**. Developers make **zero application logic changes**—they only update their SDK `base_url`:

```python
# Before
from openai import OpenAI
client = OpenAI(api_key="sk-...")

# After: Instant semantic caching, sub-millisecond responses & 100% failover
client = OpenAI(
    api_key="your-upstream-key",
    base_url="http://localhost:8000/v1"
)
```

```
[ Client Application / SDK ]
            │
            ▼ (POST /v1/chat/completions)
┌─────────────────────────────────────────────────────────────┐
│                      OMNICACHE PROXY                        │
│                                                             │
│  1. Request Sanitization & PII Masking Pipeline             │
│  2. Modal Detection (Multimodal -> L1 Exact Match Only)     │
│  3. Intent Gating (Code: 0.98, Creative: Bypass, FAQ: 0.92) │
│  4. SingleFlight Mutex (In-flight Request Coalescing)       │
│                                                             │
│  ┌─────────────────────────┐   ┌─────────────────────────┐  │
│  │     L1 Exact Cache      │   │    L2 Semantic Cache    │  │
│  │ (Deterministic SHA-256) │   │ (512-d Fast Vector Sim) │  │
│  └───────────┬─────────────┘   └───────────┬─────────────┘  │
│              │ Hit (<0.1ms)                │ Hit (<1.0ms)   │
│              ▼                             ▼                │
│   [ Token Jitter SSE Engine: 65 tokens/sec replay ]         │
│   [ Warm Persistence Tier: Async SQLite Snapshot ]          │
└──────────────────────────────┬──────────────────────────────┘
                               │ Miss / Bypass
                               ▼
        ┌──────────────────────────────────────────────┐
        │       Upstream Providers (HTTP/2 Pool)       │
        │  [OpenAI]  ──►  [Anthropic]  ──►  [Gemini]   │
        └──────────────────────────────────────────────┘
```

---

## 2. Mission & Vision

* **Mission:** To dramatically cut LLM API costs and eliminate redundant inference latency down to sub-milliseconds for production AI applications through exact & semantic caching, smart routing, and resilient failover.
* **Vision:** To become the universal, transparent gateway between every software application and foundation model APIs—eliminating duplicate compute worldwide, providing total financial observability, and making AI systems resilient to provider downtime.

---

## 3. Competitor Vulnerability Teardown

During our 3-pass architectural audit, we dissected existing products (**LiteLLM, Portkey, Helicone, Cloudflare AI Gateway, GPTCache**) and isolated 10 core flaws:

| # | Competitor Flaw / Vulnerability | Why It Breaks in Production | OmniCache Engineering Fix |
|:---|:---|:---|:---|
| **1** | **Multi-Turn Context Poisoning** | Naive caches embed only the latest user message or dump the entire history, causing User B to receive User A's private context on turn 3. | **Composite System + User Partitioning:** Prompts, system instructions, and tenant IDs are bound deterministically to prevent cross-context leakage. |
| **2** | **Static Similarity Thresholds** | A fixed `0.95` threshold ruins code syntax and structured JSON output, while being too rigid for conversational text. | **Dynamic Intent Gating:** Code/math queries enforce `0.98`; JSON schemas enforce exact schema match (`1.0`); general FAQs use `0.92`. |
| **3** | **Instantaneous 0ms SSE Blasting** | Dumping cached streams in a single 0ms chunk crashes frontend typing animations and stream listeners. | **Token Jitter SSE Replayer:** Simulates smooth, natural token stream delivery (~65 tokens/sec) while keeping TTFT under 10ms. |
| **4** | **Creative Temperature Drift** | When developers set `temperature > 0.7` for creative diversity, standard caches keep returning the same output. | **Creative Bypass:** Automatic semantic cache bypass when `temperature >= 0.7` unless explicitly forced. |
| **5** | **Multimodal / Vision OOM Crashes** | Passing Base64 images into text embedding models causes memory exhaustion and server crashes. | **Modal Detector:** Image, audio, and binary payloads bypass vector embeddings and route exclusively through L1 Exact SHA-256 matching. |
| **6** | **Reasoning Model Stream Breakage** | Models like `o1`/`o3`/Claude Thinking emit separate reasoning channels and reject `temperature` parameters. | **Dual-Channel Stream Caching:** Stores both `reasoning_content` and `content` chunks, with automatic parameter sanitization. |
| **7** | **Cross-Tenant Data Leaks** | Shared vector stores risk leaking proprietary company prompts across organizations. | **Hard Tenant Isolation:** In-memory vector indexes are isolated and partitioned strictly by `org_id`. |
| **8** | **Inaccurate Cost Ledgers** | Competitor proxies ignore OpenAI/Anthropic prompt cache discounts (50%–90% off), yielding false savings data. | **Discount-Aware Pricing Registry:** Tracks input, output, and upstream `cached_input` rates across all major models. |
| **9** | **Remote Embedding Latency** | Calling external embedding APIs adds 80–150ms of network overhead per lookup. | **In-Memory 512-d Fast Embedder:** Pure local embedding engine computes vector similarities in **<1ms** with zero external dependencies. |
| **10** | **Unbounded Cache RAM Bloat** | Infinite caching leads to out-of-memory crashes and caches HTTP 4xx/5xx errors. | **Sliding TTL & LRU Eviction:** Automatic eviction of the oldest 10% when tenant quotas exceed limits; strict exclusion of error responses. |

---

## 4. Enterprise-Grade Resilience & Guardrails

To make OmniCache bulletproof in high-throughput enterprise environments, 6 critical production capabilities are built-in:

### 1. In-Flight Request Coalescing (*SingleFlight Stampede Protection*)
When sudden traffic spikes cause 50 concurrent requests for the same prompt within milliseconds, only **1 request** is forwarded upstream. The remaining 49 requests attach to the in-flight broadcast channel and stream the result concurrently with **zero redundant upstream charges**.

### 2. Client Disconnection & Backpressure Cancellation
If a client terminates an HTTP connection mid-stream (e.g. user closes browser or cancels prompt), OmniCache instantly cancels the upstream HTTP/2 connection, preventing upstream token waste.

### 3. Agentic Function & Tool Calling State Management
Supports modern agent frameworks (LangChain, AutoGen, CrewAI, LlamaIndex):
- Validates that `tools` schemas match exactly.
- Dynamically assigns unique, compliant `tool_call_id`s during cached replay to prevent agent state machine desynchronization.

### 4. Enterprise PII Redaction & Privacy Guardrails (HIPAA / GDPR / PCI)
An optional pre-cache masking engine scrubs sensitive data (SSNs, credit card numbers, auth tokens, phone numbers) before vector indexing to guarantee zero compliance exposure in cache memory.

### 5. Warm Snapshot Persistence Tier (*Zero Cold Start*)
While the hot cache operates in RAM for sub-millisecond lookups, an asynchronous background persistence worker writes snapshots to an embedded SQLite datastore. Server redeployments and restarts reload the hot vector index in **<1 second**.

### 6. Day-2 Management & Invalidation APIs
Comprehensive cache control endpoints allow engineering teams to purge or selectively invalidate cached knowledge when underlying documentation, pricing, or policies change.

---

## 5. System Architecture & Dual-Tier Engine

OmniCache operates a high-speed **Dual-Tier Cache**:

### Tier 1: L1 Exact Match Cache (Deterministic SHA-256)
- **Mechanism:** Computes a sorted, normalized SHA-256 hash over `{org_id, model, messages, temperature, schema, tools}`.
- **Latency:** **< 0.1ms**
- **Use Case:** Identical requests, automated scripts, polling agents, repeated multimodal inputs.

### Tier 2: L2 Semantic Vector Cache (512-d N-Gram Feature Projection)
- **Mechanism:** Extracts the normalized user prompt, generates an L2-normalized 512-dimensional vector using unigram, bigram, and subword n-gram feature hashing, and performs cosine similarity matching within the tenant's namespace.
- **Latency:** **< 1.0ms**
- **Use Case:** Rephrased questions (*"How to reset password?"* vs. *"I forgot my password, how do I recover it?"*).

---

## 6. Intent Gating & Dynamic Threshold Matrix

```
                          [ Incoming User Prompt ]
                                     │
                 ┌───────────────────┴───────────────────┐
                 ▼                                       ▼
     [ Schema / Structure Requested? ]           [ Temperature >= 0.7? ]
                 │ YES                                   │ YES
                 ▼                                       ▼
     Threshold: 1.0 (Exact Schema Hash)          Threshold: 1.01 (Bypass)
                 │ NO                                    │ NO
                 ├───────────────────────────────────────┤
                 ▼                                       ▼
      [ Code Syntax Detected? ]               [ Math / Logic Detected? ]
      (def, class, SELECT, ```)               (\d+ [+-*/^] \d+, solve)
                 │ YES                                   │ YES
                 ▼                                       ▼
          Threshold: 0.98                         Threshold: 0.98
                 │ NO                                    │ NO
                 └───────────────────┬───────────────────┘
                                     ▼
                   [ Conversational / FAQ Default ]
                            Threshold: 0.92
```

---

## 7. Specialized Protocol Handling

### Token Jitter SSE Replayer
When a client requests `stream: true` and a cache hit occurs:
1. Emits the initial chunk immediately (**TTFT < 10ms**).
2. Plays back subsequent chunks with calibrated micro-delays matching target velocity (`STREAM_REPLAY_TOKENS_PER_SEC = 65.0`).
3. Emits standard `data: [DONE]\n\n` termination.

### Reasoning Model Protocol (`o1`, `o3-mini`, `Claude Thinking`)
1. Filters unsupported parameters (`temperature`, `presence_penalty`, `frequency_penalty`).
2. Intercepts `delta.reasoning_content` and stores alongside `delta.content`.
3. Replays both streams faithfully on cache hit.

---

## 8. Developer Headers & Management APIs

### Request Headers
| Header | Type | Default | Description |
|:---|:---|:---|:---|
| `X-Cache-Bypass` | `boolean` | `false` | When `true`, bypasses cache lookup and forces a fresh upstream completion. |
| `X-Cache-TTL` | `integer` | `604800` | Custom time-to-live in seconds for this request entry. |
| `X-Cache-Threshold` | `float` | `0.92` | Overrides the dynamic similarity threshold for this query. |
| `X-Cache-Tag` | `string` | `None` | Assigns an invalidation tag (e.g. `docs-v2`, `pricing-q3`). |

### Diagnostic Response Headers
| Header | Description | Example |
|:---|:---|:---|
| `X-Cache-Status` | Cache outcome status | `HIT_EXACT` \| `HIT_SEMANTIC` \| `MISS` \| `BYPASS` |
| `X-Cache-Similarity` | Cosine similarity score | `0.954` |
| `X-Cache-Latency-Ms` | Total proxy lookup duration | `0.82ms` |
| `X-Cost-Saved-USD` | Instantaneous money saved | `$0.00345` |

### Management & Invalidation Endpoints
- `POST /v1/cache/purge`: Clear all cache entries for the calling tenant.
- `POST /v1/cache/invalidate-tag`: Invalidate all entries associated with a specific `X-Cache-Tag`.
- `DELETE /v1/cache/entries?pattern=regex`: Delete entries matching a regex pattern or keyword.
- `GET /v1/cache/stats`: Returns real-time metrics (hit rate, total requests, total $ saved, active entries).

---

## 9. Provider Pricing & Cost Savings Ledger

OmniCache computes instantaneous cost savings on every cache hit:

$$\text{Total Savings} = (\text{Prompt Tokens} \times \text{Price}_{\text{in}}) + (\text{Completion Tokens} \times \text{Price}_{\text{out}})$$

### Upstream Pricing Matrix (USD per 1,000,000 tokens)
| Provider | Model | Input Price | Output Price | Upstream Cached Input |
|:---|:---|:---|:---|:---|
| **OpenAI** | `gpt-4o` | $2.50 | $10.00 | $1.25 |
| **OpenAI** | `gpt-4o-mini` | $0.15 | $0.60 | $0.075 |
| **OpenAI** | `o1` | $15.00 | $60.00 | $7.50 |
| **OpenAI** | `o3-mini` | $1.10 | $4.40 | $0.55 |
| **Anthropic** | `claude-3-5-sonnet` | $3.00 | $15.00 | $0.30 |
| **Anthropic** | `claude-3-5-haiku` | $0.80 | $4.00 | $0.08 |
| **Anthropic** | `claude-3-7-sonnet` | $3.00 | $15.00 | $0.30 |
| **Google** | `gemini-2.5-flash` | $0.10 | $0.40 | $0.025 |
| **Google** | `gemini-1.5-pro` | $1.25 | $5.00 | $0.3125 |

---

## 10. Codebase Architecture & File Map

```
omnicache_proxy/
├── README.md                      # Complete Master Blueprint & Technical Specification
├── core/
│   ├── config.py                  # Proxy settings, endpoints, pricing registry & SingleFlight timeouts
│   ├── hasher.py                  # Deterministic hashing, schema hashing & PII extraction
│   ├── embeddings.py              # Sub-millisecond 512-d vector embedder & cosine similarity
│   └── vector_cache.py            # Dual-tier cache, intent gating, SingleFlight locks & LRU eviction
├── server/                        # Gateway, Proxy Routers & Protocol Handlers
│   ├── gateway.py                 # /v1/chat/completions endpoint, header parser & lifecycle controller
│   ├── singleflight.py            # Mutex-locked in-flight request coalescing bus
│   ├── stream_replayer.py         # Token jitter SSE stream broadcaster
│   ├── management.py              # Cache purge, tag invalidation & telemetry endpoints
│   └── upstream.py                # HTTP/2 connection pooling & multi-provider client
├── persistence/                   # Async warm snapshot persistence tier
│   └── snapshot_store.py          # SQLite/file background writer and cold-start index reloader
├── dashboard/                     # Web Analytics Dashboard & Metrics UI
└── tests/                         # Comprehensive unit, integration & benchmark test suite
    ├── test_embeddings.py         # Cosine similarity and speed tests
    ├── test_cache_hits.py         # Exact and semantic hit validation
    ├── test_singleflight.py       # Stampede concurrency & coalescing tests
    └── test_streaming.py          # SSE token replay and cancellation validation
```

---

## 11. Build Roadmap & Milestones

- [x] **Phase 0: Research & System Design**
  - Competitor flaw analysis & edge case audit.
  - Architecture blueprint & pricing registry.
- [x] **Phase 1: Core Caching Engine**
  - Deterministic SHA-256 hasher & prompt extractor ([`hasher.py`](core/hasher.py)).
  - In-memory 512-d Fast Semantic Embedder ([`embeddings.py`](core/embeddings.py)).
  - Dual-tier L1/L2 Vector Cache with Intent Gating ([`vector_cache.py`](core/vector_cache.py)).
- [x] **Phase 2: Gateway & Upstream Streaming Engine**
  - Async HTTP proxy server (`/v1/chat/completions`).
  - SingleFlight in-flight request deduplication.
  - Native SSE token streaming & replay engine with jitter.
  - Developer headers (`X-Cache-*`) & management APIs (`/purge`, `/invalidate-tag`).
- [x] **Phase 3: Multi-Provider Support & Failover**
  - Transparent translation for Anthropic Messages API & Gemini OpenAI-compatible endpoints.
  - Circuit breaker & automatic provider fallback.
- [x] **Phase 4: Persistence Tier & Developer Analytics UI**
  - SQLite snapshot storage for zero-downtime restarts.
  - Token savings, cache hit ratios, and latency dashboard.
- [x] **Phase 5: Benchmarking & Packaging**
  - Benchmark suite (<1ms cache hit verification under 500 concurrent users).
  - One-command startup script and Dockerfile.

---

## 12. Quickstart & Verification

Run the test suite on the core caching engine:
```bash
python3 -m unittest discover -s omnicache_proxy/core
```
