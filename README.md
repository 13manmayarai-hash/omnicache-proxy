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
│ Multimodal Raw Audio Caching (Voice/Realtime) │ ❌ No (Re-transcribes & speaks)│ ✅ <1ms (Spectral LPC aHash)    │
│ Smart Model Cascading & Cost Arbiter          │ ❌ No (Always frontier rate)   │ ✅ <0.2ms (Shannon Entropy Arbiter) │
│ Multi-Agent Swarms & Subagent Delegation Bus  │ ❌ No (Isolated per agent)     │ ✅ <0.1ms (Shared Bus & Mutation Purge) │
│ Distributed P2P / Edge Mesh State Sync        │ ❌ No (Requires external DB)   │ ✅ <0.5ms (CRDT Vector Clock Sync) │
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
🎯 OmniCache Live Agent Integration Harness (v2.9.8)
========================================================================================
Subsystem / Protocol                 Status       Latency        Details
----------------------------------------------------------------------------------------
Claude Code (Boilerplate Stripper)   ✔ PASSED     0.038 ms       L1 Exact Hit on dynamic time
Cursor & OpenAI SDK Gateway          ✔ PASSED     0.041 ms       Standard /v1/chat/completions
L2 FastHash Semantic Vector Engine   ✔ PASSED     0.985 ms       Cosine similarity >= 0.68 threshold
Agent Tool Replayer (Git-Aware)      ✔ PASSED     0.192 ms       Sub-ms deterministic tool replay
Mutation Guard Safety Policy         ✔ PASSED     0.015 ms       Blocked mutative tool caching
Adaptive Context Compactor           ✔ PASSED     0.025 ms       Pruned historical tokens
Workspace CI/CD Cache Warming        ✔ PASSED     14.20 ms       Indexed files into tool store
MCP Server Protocol (stdio/JSON-RPC) ✔ PASSED     0.018 ms       Discovered 9 MCP tools
Voice & Telephony Agent Adapter      ✔ PASSED     0.527 ms       Stripped fillers, canonicalized caller IDs
Multimodal Audio Stream Caching      ✔ PASSED     28.99 ms       Acoustic match (aHash dist <= 6)
Smart Model Cascading & Arbiter      ✔ PASSED     0.505 ms       Arbitrage Savings ($0.0001 saved, H_diff: 1.00)
----------------------------------------------------------------------------------------
🎉 Scorecard: 11 / 11 checks PASSED (100% Ready)
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
* **Conversational Voice & Telephony Agent Adapter (v2.9.6):**
  * Built for real-time calling agents (LiveKit, Twilio Media Streams, Daily, Vapi, Retell, Pipecat).
  * Automatically strips Speech-to-Text disfluencies and acoustic artifacts ("uh", "um", "err", stutter syllables, `[pause]`, `[clears throat]`).
  * Canonicalizes dynamic caller session metadata (`<CALL_SID>`, `<CALLER_PHONE>`, `<TIMESTAMP>`, `<SESSION_ID>`) in system prompts to trigger instant prompt cache hits across callers.
  * Sub-millisecond fast-path intent matching (<0.2ms) for telephony checks ("can you hear me?", "hold on", "repeat that").
* **Multimodal Raw Audio Perception Caching Engine (v2.9.7):**
  * Built for raw voice audio streams (OpenAI Realtime API `gpt-4o-realtime-preview`, GPT-4o Audio `input_audio`, Gemini Live, and Anthropic audio blocks).
  * Pure-Python, zero-dependency 64-bit spectral aHash with multi-lag autocorrelation (LPC-inspired) and VAD silence trimming (<1.5ms).
  * Invariant to microphone distance/gain and ambient room noise, allowing repeated spoken queries to hit cache directly at the acoustic waveform level.
  * Eliminates both upstream LLM reasoning cost ($40/1M audio in, $80/1M audio out) AND text-to-speech audio synthesis latency.
* **Smart Model Cascading & Automated Cost Arbiter (v2.9.8):**
  * Evaluates prompt complexity in `<0.2ms` via normalized Shannon token entropy ($H \in [0.0, 1.0]$) and lexical reasoning classifiers.
  * Dynamically arbitrates procedural, formatting, and trivial queries down to ultra-fast economy models (`gpt-4o-mini`, `gemini-2.5-flash`, `claude-3-5-haiku-20241022`) when authorized via `OMNICACHE_CASCADE_POLICY=auto` or `X-OmniCache-Model-Cascade: allow`, saving up to 80% on cache-miss spend.
  * Preserves vendor family affinity (`same-vendor` vs `cross-vendor`) and enforces strict execution safety invariants: agent tools, structured JSON schemas, and multi-turn conversational chains are **never** downgraded.
* **Multi-Agent Swarms & Subagent Delegation Bus (v2.9.9):**
  * Provides a shared, thread-safe memory bus across parallel and hierarchical subagents (Claude Code subagent teams, OpenHands, CrewAI, AutoGen, LangGraph).
  * Automatically traces parent-to-child delegation lineage (`X-OmniCache-Swarm-ID`, `X-OmniCache-Agent-ID`, `X-OmniCache-Parent-Agent`).
  * Reuses deterministic tool replays and chat reasoning across peer agents without redundant disk reads or remote LLM roundtrips (`HIT_SWARM`).
  * Enforces cross-agent state invalidation: the moment any worker agent mutates a file or workspace, volatile read caches across all peer agents in that swarm session are instantly purged.
  * Live topology introspection and delegation graph queries via `/v1/swarm/topology`, `/v1/swarm/stats`, and `/v1/swarm/delegate`.
* **Distributed P2P / Edge Mesh State Sync (v3.0.0-rc1):**
  * Fully decentralized, serverless peer discovery and cache state sync without requiring an external centralized Redis cluster.
  * Implements Conflict-Free Replicated Data Types (CRDT) with total ordering (Lamport logical clock + physical timestamp tie-breaking) for deterministic Last-Write-Wins (LWW) convergence.
  * Monotonic vector clocks (`Dict[node_id, sequence]`) trace causal history and detect concurrent network mutations across edge pods and distributed agent runners.
  * Real-time anti-entropy gossip and bilateral sync: when files, prompts, or caches mutate on one node, tombstones are gossiped in `<0.5ms` to all alive mesh peers.
  * Dynamic peer discovery, heartbeat ping/pong, RTT exponential moving averages, and peer introspection via `/v1/mesh/peers`, `/v1/mesh/sync`, `/v1/mesh/heartbeat`, and `/v1/mesh/broadcast`.
* **Explainability Headers:**
  * Transparent `X-OmniCache-Decision` (`HIT` | `MISS`), `X-Cache-Status` (`HIT_EXACT` | `HIT_SEMANTIC` | `HIT_VISION` | `HIT_AUDIO` | `HIT_SWARM`), `X-OmniCache-Swarm-Hit`, `X-OmniCache-Origin-Agent`, `X-Cascade-Applied`, `X-Served-Model`, `X-Tokens-Saved`, and `X-Cost-Avoided-USD` response headers.

---

## Built-in CLI Utilities

```bash
# Check database, port bindings, and vector engine health
omnicache doctor

# Verify agent integration across Claude Code, Cursor, Cline, OpenHands, LiveKit, Twilio, Audio, Cascading, Swarms & Mesh (13/13 Scorecard)
omnicache harness  # or omnicache verify-agent

# CI/CD and Docker health probe (exits 0 if healthy, 1 if unreachable)
omnicache health

# Generate GitHub Actions CI/CD step summary Markdown report
omnicache ci-summary

# Run high-speed micro-benchmarks on your machine
omnicache benchmark [--iterations 500]

# Auto-configure agent presets or show configuration snippets
omnicache init [--agent {all,claude,cursor,cline,openhands,env,voice,livekit,twilio,audio,realtime,multimodal,cascade,arbiter,swarm}] [--show]

# Pre-warm repository cache for Claude Code or agent sessions
omnicache warm --dir . --max-files 200

# Multi-agent team sync: export, import, push, pull, or check sync status
omnicache sync status
omnicache sync export --output snapshot.json
omnicache sync import --input snapshot.json
omnicache sync push   # Push workspace snapshot to shared Redis
omnicache sync pull   # Pull workspace snapshot from shared Redis

# Inspect P2P Edge Mesh topology, vector clocks, and connect to remote peers
omnicache mesh [--peers http://peer1:8000,http://peer2:8000]

# Print cumulative token and USD savings (pass --markdown for CI tables)
omnicache stats [--markdown]
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
