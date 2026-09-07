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
│ Context Window Compaction (Deep Agent Loops)  │ ❌ No (Unbounded token growth)│ ✅ Adaptive Head/Tail Pruning   │
│ Cross-Session Memory (New CLI sessions)       │ ❌ 0% (Expires in 5 minutes)   │ ✅ Persistent (SQLite / Redis)  │
│ Cross-Teammate Knowledge Sharing              │ ❌ 0% (Isolated per session)  │ ✅ Shared Team Redis Store      │
│ Terminal SSE Stream Jitter Replay             │ ❌ No                         │ ✅ ~65 tok/s (Glitch-free CLI)  │
│ Multi-Modal Visual Deduplication (Screenshots) │ ❌ No (Re-uploads megabytes)   │ ✅ Perceptual dHash Match       │
└───────────────────────────────────────────────┴───────────────────────────────┴─────────────────────────────────┘
```

1. **Tool-Call Acceleration:** When Claude Code repeatedly calls `git_status`, `grep_search`, or `read_file`, native caching still runs the tool on disk and pays for the network roundtrip. OmniCache cryptographically hashes your Git working tree state (`HEAD` commit + `git status --porcelain`). If files haven't changed, tool calls return in **`<0.3ms`** with **$0.00** spent. The moment you edit a file, the cache instantly invalidates.
2. **Persistent Cross-Session & Team Memory:** Native prompt cache is ephemeral (5-minute TTL). OmniCache stores answers in an embedded SQLite WAL database or shared Redis, so opening a new session or having a teammate ask a similar architecture question reuses existing answers.
3. **Smooth CLI Stream Replaying:** Returning a 4,000-token cached completion instantaneously in 0ms can cause buffer overflows and terminal glitches in interactive CLIs. OmniCache emulates natural token-streaming (~65 tokens/sec with subtle stochastic jitter).
4. **Adaptive Context Compaction & Token Pruning:** Deep multi-turn agent conversations (15–30+ turns) accumulate bulky file dumps and historical search outputs. OmniCache maintains an active lookback horizon while safely pruning intermediate lines of older tool results (preserving head/tail syntax and full archive in cache), slashing historical prompt tokens by 60–90%.

---

## Installation

### Standard (PyPI)
```bash
pip install omnicache-proxy
```

### Android & Edge (Termux 1-Line Setup)
```bash
curl -fsSL https://raw.githubusercontent.com/13manmayarai-hash/omnicache-proxy/main/scripts/install_termux.sh | bash
```

### Docker & Redis Cluster
```bash
docker compose up -d
```

---

## Zero-Config Quickstart (`omnicache run`)

The easiest way to use OmniCache is the zero-config `run` wrapper. It automatically launches the background proxy, injects provider environment variables (`ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`, `LLM_BASE_URL`), and displays a session savings ledger when finished:

### 1. Launch Claude Code
```bash
omnicache run claude
```

### 2. Launch Cursor IDE / OpenHands / Cline / Aider
```bash
omnicache run cursor .
omnicache run openhands
omnicache run aider
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

## 🤖 Drop-In Agent Setup (`omnicache init`)

OmniCache automatically configures presets for all your favorite AI coding tools:

```bash
# Auto-configure Claude Code, Cursor, Cline, and OpenHands
omnicache init

# Or preview configuration JSON / TOML snippets without touching disk
omnicache init --show

# Target a specific assistant
omnicache init --agent claude
omnicache init --agent cursor
omnicache init --agent cline
omnicache init --agent openhands
```

### Verify Agent Acceleration (`omnicache harness`)
Run the built-in end-to-end verification harness to guarantee sub-millisecond response across all agent protocols:

```text
========================================================================================
🎯 OmniCache Live Agent Integration Harness
========================================================================================
Subsystem / Protocol                 Status       Latency        Details
----------------------------------------------------------------------------------------
Claude Code (Boilerplate Stripper)   ✔ PASSED     0.038 ms       L1 Exact Hit on dynamic time
Cursor & OpenAI SDK Gateway          ✔ PASSED     0.041 ms       Standard /v1/chat/completions
L2 FastHash Semantic Vector Engine   ✔ PASSED     0.985 ms       Cosine similarity >= 0.68 threshold
Agent Tool Replayer (Git-Aware)      ✔ PASSED     0.192 ms       Sub-ms deterministic tool replay
Mutation Guard Safety Policy         ✔ PASSED     0.015 ms       Blocked mutative tool caching
Workspace CI/CD Cache Warming        ✔ PASSED     14.20 ms       Indexed files into tool store
MCP Server Protocol (stdio/JSON-RPC) ✔ PASSED     0.018 ms       Discovered 9 MCP tools
----------------------------------------------------------------------------------------
🎉 Scorecard: 7 / 7 checks PASSED (100% Ready)
========================================================================================
```

---

## ⚡ Live Performance Benchmarks (`omnicache benchmark`)

OmniCache includes an automated multi-subsystem benchmarking engine:

```text
------------------------------------------------------------------------------------------
Engine Subsystem                 Cold Turn        OmniCache Replay   Speedup    Benefit
------------------------------------------------------------------------------------------
L1 Exact Request Cache           ~450.00 ms       0.0348 ms          12,914x    100% Token Savings (505 tok)
L2 FastHash Semantic Vector      ~450.00 ms       0.9954 ms          452x       90%+ Cosine Replay
Agent Tool Replayer (Business)   ~1,200.00 ms     0.2044 ms          5,870x     $0.00 Disk Thrashing
Workspace CI/CD Pre-Warming      Cold Repo Scan   2714.74 ms         7 f/s      Pre-warmed 20 files
==========================================================================================
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
* **Configurable Business Tool Policies & Mutation Guard:**
  * Define per-tool dynamic TTLs (`tool_policies_records`), auto-detect idempotent prefixes (`read`, `view`, `get`, `query`, `check`), and strictly block non-idempotent mutation tools (`write`, `delete`, `pay`, `charge`, `execute`).
* **Multi-Agent Workspace Sync & CI/CD Cache Warming:**
  * Pre-warm workspace repository structures, files, git status, and diffs during CI/CD before coding agent loops run (`omnicache warm`).
  * Export, import, and sync cache snapshots across team members and multi-agent sessions via portable JSON archives or Redis (`omnicache sync`).
* **Explainability Headers:**
  * Transparent `X-OmniCache-Decision` (`HIT` | `MISS`), `X-Tokens-Saved`, and `X-Cost-Avoided-USD` response headers.

---

## Built-in CLI Utilities

```bash
# Check database, port bindings, and vector engine health
omnicache doctor

# Verify agent integration across Claude Code, Cursor, Cline, OpenHands
omnicache harness

# Run high-speed micro-benchmarks on your machine
omnicache benchmark [--iterations 500]

# Auto-configure agent presets or show configuration snippets
omnicache init [--agent {all,claude,cursor,cline,openhands,env}] [--show]

# Pre-warm repository cache for Claude Code or agent sessions
omnicache warm --dir . --max-files 200

# Multi-agent team sync: export, import, push, pull, or check sync status
omnicache sync status
omnicache sync export --output snapshot.json
omnicache sync import --input snapshot.json
omnicache sync push   # Push workspace snapshot to shared Redis
omnicache sync pull   # Pull workspace snapshot from shared Redis

# Print cumulative token and USD savings
omnicache stats
```

---

## Observability & Diagnostics

* **Web Dashboard & Visualizer:** `http://localhost:8000/dashboard` (features real-time savings velocity timeline, resolution distribution charts, and Workspace Sync & Tool Policies management)
* **Prometheus Metrics:** `http://localhost:8000/metrics`
* **Cache Statistics:** `http://localhost:8000/v1/cache/stats`
* **CSV Export:** `http://localhost:8000/v1/cache/export`

---

## Documentation

* [AI Coding Agent Integrations (Claude Code, Cursor, Cline, OpenHands)](https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/docs/AGENT_INTEGRATIONS.md)
* [API Reference](https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/docs/API_REFERENCE.md)
* [Architecture Overview](https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/docs/ARCHITECTURE.md)
* [Quickstart Guide](https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/docs/QUICKSTART_GUIDE.md)
* [Troubleshooting & FAQ](https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/docs/TROUBLESHOOTING_AND_FAQ.md)

---

## License

MIT License. See [LICENSE](https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/LICENSE) for details.
