# Architecture Overview

This document outlines the internal architecture and request lifecycle of OmniCache.

---

## 1. System Components

OmniCache is designed as an asynchronous, low-overhead HTTP proxy sitting between LLM client applications and upstream AI providers.

```text
[ Client / Coding Agent ] (Claude Code, Cursor, Python SDK)
         │
         ▼
[ Ingestion & Auth Gateway ]
  - Header inspection (OAuth bearer tokens, API keys)
  - Virtual key quotas & rate limiter
  - Optional PII token masking
         │
         ▼
[ Dual-Tier Cache Engine ]
  - L1 Exact Cache (Sorted SHA-256 trie lookup: ~0.03ms)
  - L2 Semantic Cache (512-dimension vector cosine similarity: ~0.6ms)
  - Radix Prefix Tree (conversation history caching)
  - SQLite Persistence Store (~/.omnicache/omnicache.db)
         │
    ┌────┴──────────────────────────┐
    │                               │
    ▼ (Cache HIT: < 1ms)            ▼ (Cache MISS)
[ Token Jitter SSE Replayer ]   [ SingleFlight Coalescing Mutex ]
  - Emulates ~65 tok/s stream       │ (Only 1 upstream request per prompt)
  - Zero cloud API billing          ▼
                                [ Upstream HTTP/2 Connection Pool ]
                                (Anthropic, OpenAI, Gemini)
```

---

## 2. Request Lifecycle

1. **Client Request Ingestion:**
   Incoming requests to `/v1/chat/completions` (OpenAI format) or `/v1/messages` (Anthropic format) are parsed for messages, model, temperature, tools, and response format.

2. **L1 Exact Cache Lookup:**
   A deterministic SHA-256 hash is computed across the normalized messages, tool schemas, and model parameters. If an exact match exists in memory, it is returned immediately.

3. **L2 Semantic Cache Lookup (Cosine Similarity):**
   If no exact match is found, user prompt text is projected into a 512-dimension vector embedding using an in-memory linear algebra projection. If the cosine similarity against existing cached entries exceeds `SEMANTIC_SIMILARITY_THRESHOLD` (default: `0.92`), the cached response is returned.

4. **SingleFlight Mutex (Cache MISS):**
   When a cache miss occurs, the prompt key is registered in a SingleFlight mutex table. If multiple concurrent requests for the same prompt arrive simultaneously, only the first request is forwarded upstream; other waiting callers share the resulting response once it completes.

5. **Upstream Forwarding & SQLite Persistence:**
   The upstream response is streamed back to the client while simultaneously recording chunks and token counts. On completion, the result is saved to the in-memory cache and persisted to `~/.omnicache/omnicache.db`.

---

## 3. SSE Stream Replay

Many coding assistants (such as Claude Code) require Server-Sent Events (SSE) streaming. Returning an entire cached response in a single 0ms chunk can cause buffer overflows or display anomalies in terminal UIs.

OmniCache solves this with an event replayer:
- Emulates natural token streaming cadence (~65 tokens/second with slight random jitter).
- Maintains `<10ms` Time-To-First-Token (TTFT).
- Emits standard `message_start`, `content_block_delta`, `message_delta`, and `message_stop` events.
