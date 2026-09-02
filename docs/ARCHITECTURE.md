# 🏛️ OmniCache AI Proxy 2.0: Deep Technical Architecture Specification

---

## 1. System Overview & Component Hierarchy

OmniCache AI Proxy is an asynchronous, high-throughput, sub-millisecond AI gateway built to sit seamlessly between client applications and foundation model providers (OpenAI, Anthropic, Google Gemini, Ollama, Groq).

```
                             [ Client Applications & Coding Agents ]
                           (OpenAI SDK, Anthropic SDK, Cursor, Claude)
                                               │
                                               ▼
                      ┌──────────────────────────────────────────────────┐
                      │            Layer 1: Security & Auth Gate         │
                      │  - Virtual Key Manager (Rate limits & Quotas)    │
                      │  - Zero-Knowledge PII Masking Tokenizer          │
                      └────────────────────────┬─────────────────────────┘
                                               │
                                               ▼
                      ┌──────────────────────────────────────────────────┐
                      │          Layer 2: Dual-Tier Cache Engine         │
                      │  - L1 Exact Hash (Sorted SHA-256 <0.1ms)         │
                      │  - L2 Semantic Vector Cache (512-d Cosine <0.8ms)│
                      │  - Radix Prefix Tree (Multi-Turn Agent Dialogues)│
                      │  - Multi-Modal Vision Cache (64-bit dHash/pHash) │
                      │  - Deterministic Agent Tool Output Replayer      │
                      └────────────────────────┬─────────────────────────┘
                                               │
                        ┌──────────────────────┴──────────────────────┐
                        ▼ HIT (<1ms)                                  ▼ MISS / BYPASS
             ┌─────────────────────┐                       ┌─────────────────────┐
             │ Token Jitter SSE    │                       │ SingleFlight Bus    │
             │ Streaming Replayer  │                       │ In-flight Mutex Lock│
             │ (~65 tok/s, <10ms)  │                       └──────────┬──────────┘
             └──────────┬──────────┘                                  │ Leader Only
                        │                                             ▼
                        │                                  ┌─────────────────────┐
                        │                                  │ Adaptive Cascade    │
                        │                                  │ Model Router        │
                        │                                  │ (<0.2ms Classifier) │
                        │                                  └──────────┬──────────┘
                        │                                             │
                        │                                             ▼
                        │                                  ┌─────────────────────┐
                        │                                  │ Upstream HTTP/2     │
                        │                                  │ Connection Pool     │
                        │                                  │ & Circuit Breaker   │
                        │                                  └──────────┬──────────┘
                        │                                             │
                        │                                             ▼
                        │                                  ┌─────────────────────┐
                        │                                  │ SQLite Persistence  │
                        │                                  │ Async Warm Snapshot │
                        │                                  └──────────┬──────────┘
                        │                                             │
                        └──────────────────────┬──────────────────────┘
                                               ▼
                               [ Client Response Delivery ]
```

---

## 2. Core Subsystems

### 2.1 Fast In-Memory 512-Dimensional Vector Embedder (`core/embeddings.py`)
- **Mathematical Foundation:** Feature projection over subword n-grams, content bigrams, and lexical token sets with L2 normalization.
- **Latency Profile:** `< 0.5 ms` per 1,000 words. Zero GPU requirement; runs 100% in CPU cache with zero remote API overhead.
- **Cosine Similarity:** Computed via vectorized dot product:
  $$\text{Similarity}(u, v) = \frac{u \cdot v}{\|u\|_2 \|v\|_2} = u \cdot v \quad (\text{since } \|u\|=\|v\|=1)$$

### 2.2 Dynamic Intent Gating Matrix (`core/vector_cache.py`)
To prevent semantic hallucinations, OmniCache applies an adaptive similarity threshold based on prompt semantics:

| Intent Category | Detected Triggers | Enforced Threshold ($\tau$) | Rationale |
|:---|:---|:---:|:---|
| **JSON Schema / Function Tools** | `tools`, `response_format: json_object` | **`1.00` (Exact Schema Hash)** | Guarantees zero parsing errors in downstream application code. |
| **Code & Algorithms** | `def `, `class `, `SELECT `, ```` ` | **`0.98`** | Prevents subtle logic inversions (e.g. ascending vs descending sort). |
| **Math & Calculation** | Numbers, formulas, `calc`, `evaluate` | **`0.98`** | Ensures exact numerical answers. |
| **Creative Generation** | `temperature >= 0.7` | **`1.01` (Auto-Bypass)** | Preserves generative diversity on high-temperature prompts. |
| **General FAQ / Conversational**| Default conversational prose | **`0.92`** | Captures natural language synonyms and paraphrasings. |

### 2.3 Radix Prefix-Tree Engine (`core/radix_tree.py`)
- Represents multi-turn conversations as nodes in a prefix tree.
- Enables instant branching on turn $N$ of an autonomous agent dialogue.
- Aligns tokens to 1024-token boundaries, injecting ephemeral cache directives to unlock 90% downstream provider prompt caching discounts.

### 2.4 Token Jitter SSE Replayer (`server/stream_replayer.py`)
- Calibrated Poisson-distributed inter-token delays ($\lambda = 65\text{ tokens/sec}$).
- Preserves distinct reasoning channels (`delta.reasoning_content` for `o1`/`o3` and Claude Thinking).
- Guarantees Time-To-First-Token (TTFT) `< 10ms`.

### 2.5 SingleFlight Concurrency Bus (`server/singleflight.py`)
- Async mutex lock on `exact_hash`.
- When 100 concurrent requests arrive for the same cold prompt, only **1 leader request** is sent upstream. The remaining 99 await the shared leader future, preventing API rate-limit stampedes.

### 2.6 Zero-Knowledge Privacy Vault (`core/privacy_shield.py`)
- Reversible tokenized masking of SSNs, credit cards, emails, API keys, and PHI before sending to upstream LLMs.
- Seamless rehydration on response delivery ensures zero raw PII is exposed to third-party model providers.
