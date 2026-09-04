# OmniCache

[![PyPI version](https://img.shields.io/pypi/v/omnicache-proxy.svg)](https://pypi.org/project/omnicache-proxy/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/LICENSE)

OmniCache is a lightweight, local caching proxy for Anthropic (Claude), OpenAI (GPT), and Google (Gemini) APIs. 

When developing with AI agents (like Claude Code, Cursor, Aider, or custom LLM scripts), repeated prompts, test runs, and static file queries frequently make duplicate upstream API calls. OmniCache sits between your client and upstream providers to intercept matching requests locally in `<1ms`, saving API costs and eliminating remote network latency.

---

## Installation

```bash
pip install omnicache-proxy
```

---

## Quickstart

### 1. Start the Proxy Server

```bash
omnicache
```

By default, the proxy runs on `http://localhost:8000`. You can change the port with `--port`:

```bash
omnicache --port 8080
```

### 2. Connect Your Client

#### Claude Code (Terminal CLI)
Set the Anthropic base URL environment variable before running `claude`:

```bash
export ANTHROPIC_BASE_URL="http://localhost:8000"
claude
```

#### Python (OpenAI SDK)
Route the `base_url` parameter to the local proxy:

```python
from openai import OpenAI

client = OpenAI(
    api_key="your-api-key",
    base_url="http://localhost:8000/v1"
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "How do I reverse a linked list in Python?"}]
)
print(response.choices[0].message.content)
```

#### Cursor / VS Code / Other Tools
In your tool's model settings, set the API Base URL to `http://localhost:8000/v1`.

---

## Key Features

* **Two-Tier Cache Engine:**
  * **L1 Exact Match (Trie Hash / Redis):** Sub-0.05ms lookup for identical payloads.
  * **L2 Semantic Match (Multi-Table LSH & FAISS ANN Indexing):** Matches semantically equivalent prompts using high-speed multi-table hyperplane locality-sensitive hashing or FAISS HNSW.
  * **Pluggable Embedders:** Instant zero-dependency 512-d FastHashEmbedder or dense 384-d ONNX embeddings (`all-MiniLM-L6-v2`).
* **Horizontal Scaling & Redis Clustering:**
  * Pluggable storage adapter architecture supporting both zero-dependency standalone mode and distributed multi-worker/multi-replica clusters.
  * Atomic spend tracking (`INCRBYFLOAT`) and sliding-window Redis RPM rate limiting across all worker processes.
  * Synchronized cluster-wide Circuit Breaker & upstream model failover state.
* **Asynchronous Write-Behind Persistence:**
  * Micro-batched non-blocking worker queue writing to embedded SQLite WAL store off the critical path with zero latency impact.
  * Durable Virtual Key and spend budget ledger surviving process cold starts.
* **Remote Authenticated MCP JSON-RPC 2.0 Transport:**
  * Native `/mcp` and `/v1/mcp` endpoint enabling AI IDEs (Cursor, Claude Code, VS Code) to perform intent-gated caching, vector search, and cache invalidation over HTTP.
* **Agent Stream Replayer:** Emulates natural token-streaming for cached responses so interactive CLIs (like Claude Code) stream smoothly without terminal glitches.
* **Request Coalescing (SingleFlight):** Deduplicates concurrent in-flight requests for the same prompt, making only one upstream call.
* **Built-in CLI Utilities:**
  * `omnicache doctor`: Checks database state, port bindings, and embedder health.
  * `omnicache benchmark`: Measures P50, P95, and P99 cache lookup latencies on your machine.
  * `omnicache stats`: Prints total tokens and cost savings directly to the console.
* **Observability:**
  * Web Dashboard: `http://localhost:8000/dashboard`
  * Prometheus Metrics: `http://localhost:8000/metrics`
  * CSV Ledger Export: `http://localhost:8000/v1/cache/export`

---

## Horizontal Multi-Worker Deployment

To run OmniCache with multiple worker processes or in a clustered container environment, simply provide `REDIS_URL`:

```bash
# Multi-worker deployment with Redis distributed state
REDIS_URL="redis://127.0.0.1:6379/0" uvicorn server.gateway:app --host 127.0.0.1 --port 8000 --workers 4
```

---

## Configuration

OmniCache can be configured via command-line flags or environment variables (in your shell or a local `.env` file):

| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `HOST` | `127.0.0.1` | Host interface to listen on (local-first by default). |
| `PORT` | `8000` | Port to bind the proxy server to. |
| `REDIS_URL` | `""` | Redis connection URL (e.g. `redis://127.0.0.1:6379/0`) for multi-worker state clustering. |
| `CACHE_STORAGE_BACKEND` | `auto` | Storage engine backend: `auto`, `redis`, or `memory`. |
| `EMBEDDER_BACKEND` | `fast_hash` | Semantic embedder: `fast_hash`, `onnx`, or `auto`. |
| `ANN_INDEX_ENABLED` | `true` | Enables sub-millisecond Approximate Nearest Neighbor vector search. |
| `REQUIRE_AUTH` | `false` | When `true`, enforces valid API key registration on all requests. |
| `ADMIN_API_KEY` | `""` | Master admin secret for managing `/v1/enterprise/quotas` and data exports. |
| `PRIVACY_SALT` | `(auto-generated)` | 256-bit cryptographic salt for anonymized PII tokenization. |
| `SEMANTIC_CACHE_TTL_SECONDS` | `604800` | Default time-to-live for cache entries (7 days). |
| `SEMANTIC_SIMILARITY_THRESHOLD` | `0.92` | Minimum cosine similarity required for an L2 semantic cache hit. |
| `OMNICACHE_DB_PATH` | `~/.omnicache/omnicache.db` | Path to SQLite persistence database (WAL mode enabled). |
| `ANTHROPIC_API_KEY` | *(Optional)* | Default upstream Anthropic API key (if not passed in client headers). |
| `OPENAI_API_KEY` | *(Optional)* | Default upstream OpenAI API key (if not passed in client headers). |
| `GEMINI_API_KEY` | *(Optional)* | Default upstream Google Gemini API key. |

---

## Running Tests

Run the test suite using `pytest`:

```bash
git clone https://github.com/13manmayarai-hash/omnicache-proxy.git
cd omnicache-proxy
pip install -e .
pytest tests/ -v
```

---

## Documentation

* [API Reference](https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/docs/API_REFERENCE.md)
* [Architecture Overview](https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/docs/ARCHITECTURE.md)
* [Troubleshooting & FAQ](https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/docs/TROUBLESHOOTING_AND_FAQ.md)
* [Contributing Guidelines](https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/CONTRIBUTING.md)

---

## License

MIT License. See [LICENSE](https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/LICENSE) for details.
