# OmniCache

[![PyPI version](https://img.shields.io/pypi/v/omnicache-proxy.svg)](https://pypi.org/project/omnicache-proxy/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/LICENSE)

**OmniCache is a local acceleration sidecar for AI coding agents (Claude Code, Cursor, Aider, and custom LLM workflows).**

It sits between your coding assistant and upstream LLM providers (Anthropic, OpenAI, Gemini) to eliminate redundant tool executions, stream terminal tokens smoothly, and share cached knowledge across developer sessions.

---

## Why OmniCache?

### How OmniCache Complements Native Anthropic Prompt Caching

Anthropic’s native prompt caching is great at discounting prefix tokens within a single active conversation. However, it structurally leaves two major gaps open in real-world coding agent loops:

```text
┌───────────────────────────────────────────────┬───────────────────────────────┬─────────────────────────────────┐
│ Capability                                    │ Native Provider Caching       │ OmniCache Acceleration Sidecar  │
├───────────────────────────────────────────────┼───────────────────────────────┼─────────────────────────────────┤
│ In-Session Prefix Input Token Discount        │ ✅ 90% (Anthropic ephemeral)  │ ✅ Supported (Passthrough)      │
│ Redundant Disk Tool Replay (git/grep/read)    │ ❌ No (Hits disk & LLM every turn) │ ✅ <0.3ms (Git-state hashed)    │
│ Cross-Session Memory (New CLI sessions)       │ ❌ 0% (Expires in 5 minutes)   │ ✅ Persistent (SQLite / Redis)  │
│ Cross-Teammate Knowledge Sharing              │ ❌ 0% (Isolated per session)  │ ✅ Shared Team Redis Store      │
│ Terminal SSE Stream Jitter Replay             │ ❌ No                         │ ✅ ~65 tok/s (Glitch-free CLI)  │
│ Multi-Modal Visual Deduplication (Screenshots) │ ❌ No (Re-uploads megabytes)   │ ✅ Perceptual dHash Match       │
└───────────────────────────────────────────────┴───────────────────────────────┴─────────────────────────────────┘
```

1. **Tool-Call Acceleration:** When Claude Code repeatedly calls `git_status`, `grep_search`, or `read_file`, native caching still runs the tool on disk and pays for the network roundtrip. OmniCache cryptographically hashes your Git working tree state (`HEAD` commit + `git status --porcelain`). If files haven't changed, tool calls return in **`<0.3ms`** with **$0.00** spent. The moment you edit a file, the cache instantly invalidates.
2. **Persistent Cross-Session & Team Memory:** Native prompt cache is ephemeral (5-minute TTL). OmniCache stores answers in an embedded SQLite WAL database or shared Redis, so opening a new session or having a teammate ask a similar architecture question reuses existing answers.
3. **Smooth CLI Stream Replaying:** Returning a 4,000-token cached completion instantaneously in 0ms can cause buffer overflows and terminal glitches in interactive CLIs. OmniCache emulates natural token-streaming (~65 tokens/sec with subtle stochastic jitter).

---

## Installation

```bash
pip install omnicache-proxy
```

---

## Zero-Config Quickstart (`omnicache run`)

The easiest way to use OmniCache is the zero-config `run` wrapper. It automatically launches the background proxy, injects provider environment variables (`ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`), and displays a session savings ledger when finished:

### 1. Launch Claude Code
```bash
omnicache run claude
```

### 2. Launch Cursor / VS Code / Other Agent Scripts
```bash
omnicache run cursor .
# or custom Python agent scripts:
omnicache run python my_coding_agent.py
```

When you exit your session, OmniCache outputs a clean summary:
```text
╭──────────────────────────────────────────────────╮
│ ⚡ OmniCache Session Telemetry                   │
│  - Tokens Saved:       1,840 tokens              │
│  - Avoided Cost:    $ 0.0142 USD                 │
│  - Tool Replays:          14 cached tool calls   │
╰──────────────────────────────────────────────────╯
```

---

## Manual Quickstart

### 1. Start the Background Daemon
```bash
omnicache
```
By default, the proxy runs on `http://127.0.0.1:8000`.

### 2. Configure Your Client Manually

#### Claude Code (Terminal CLI)
```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:8000"
claude
```

#### Python (OpenAI SDK)
```python
from openai import OpenAI

client = OpenAI(
    api_key="your-api-key",
    base_url="http://127.0.0.1:8000/v1"
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "How do I configure CORS headers in FastAPI?"}]
)
print(response.choices[0].message.content)
```

---

## Key Features

* **Deterministic Git-Aware Tool Replay:**
  * Intercepts and caches idempotent agent tools (`git_status`, `git_diff`, `read_file`, `grep_search`, `list_dir`).
  * Cryptographically fingerprinted against `git rev-parse HEAD` and `git status --porcelain`.
  * Modifying files or changing branches instantly invalidates stale results with zero false positives.
* **Dual-Tier Cache Engine:**
  * **L1 Exact Match (Trie Hash / Redis):** Sub-0.05ms lookup for identical request payloads.
  * **L2 FastHash Semantic Match:** In-memory 512-d hyperplane locality-sensitive hashing for syntactically varied queries without external vector DB dependencies.
* **Stream Replayer with Terminal Jitter:**
  * Delivers cached SSE streams with natural human-like cadence (~65 tok/s) and `<10ms` Time-To-First-Token (TTFT) for seamless CLI rendering.
* **Model Context Protocol (MCP) Remote Server:**
  * Native `/mcp` JSON-RPC 2.0 endpoint allowing Claude Code, Cursor, and IDEs to discover and invoke `omnicache_replay_tool` and `omnicache_record_tool`.
* **SingleFlight Request Coalescing:**
  * Deduplicates concurrent in-flight requests for identical prompts, forwarding only one upstream call.
* **Horizontal Scaling with Redis:**
  * Connect to Redis (`REDIS_URL="redis://127.0.0.1:6379/0"`) for shared team memory and multi-worker clusters.
* **Explainability Headers:**
  * Transparent `X-OmniCache-Decision` (`HIT` | `MISS`), `X-Tokens-Saved`, and `X-Cost-Avoided-USD` response headers.

---

## Built-in CLI Utilities

```bash
# Check database, port bindings, and vector engine health
omnicache doctor

# Run high-speed micro-benchmarks on your machine
omnicache benchmark

# Print cumulative token and USD savings
omnicache stats
```

---

## Observability & Diagnostics

* **Web Dashboard:** `http://localhost:8000/dashboard`
* **Prometheus Metrics:** `http://localhost:8000/metrics`
* **Cache Statistics:** `http://localhost:8000/v1/cache/stats`
* **CSV Export:** `http://localhost:8000/v1/cache/export`

---

## Documentation

* [API Reference](https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/docs/API_REFERENCE.md)
* [Architecture Overview](https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/docs/ARCHITECTURE.md)
* [Quickstart Guide](https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/docs/QUICKSTART_GUIDE.md)
* [Troubleshooting & FAQ](https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/docs/TROUBLESHOOTING_AND_FAQ.md)

---

## License

MIT License. See [LICENSE](https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/LICENSE) for details.
