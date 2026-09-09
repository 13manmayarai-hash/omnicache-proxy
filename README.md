<div align="center">

# ⚡ OmniCache

### The Deterministic Git-Aware Local AI Agent Acceleration Sidecar
**Never pay for the same tool call twice. Replay unchanged Claude Code & Cursor operations in `<0.1ms` with `$0.00` token spend.**

<br/>

[![PyPI version](https://img.shields.io/pypi/v/omnicache-proxy.svg?style=for-the-badge&color=10b981&logo=pypi&logoColor=white)](https://pypi.org/project/omnicache-proxy/)
[![Python 3.9 - 3.14](https://img.shields.io/badge/Python-3.9_--_3.14-38bdf8?style=for-the-badge&logo=python&logoColor=white)](https://pypi.org/project/omnicache-proxy/)
[![License: FSL-1.1-MIT](https://img.shields.io/badge/License-FSL--1.1--MIT-818cf8?style=for-the-badge)](https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/LICENSE)
[![Test Suite](https://img.shields.io/badge/Tests-206%20Passed%20(100%25)-34d399?style=for-the-badge&logo=pytest&logoColor=white)](https://github.com/13manmayarai-hash/omnicache-proxy/actions)
[![Zero Telemetry](https://img.shields.io/badge/Privacy-100%25%20Localhost%20(127.0.0.1)-f59e0b?style=for-the-badge&logo=shield&logoColor=white)](https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/SECURITY.md)

<br/>

<table>
  <tr>
    <td align="center"><a href="#-60-second-quickstart"><b>🚀 Quick Start</b></a></td>
    <td align="center"><a href="#-architecture"><b>📐 Architecture</b></a></td>
    <td align="center"><a href="#-why-omnicache-vs-native-provider-caching"><b>💡 Why OmniCache</b></a></td>
    <td align="center"><a href="#-verified-performance-benchmarks"><b>⚡ Benchmarks</b></a></td>
    <td align="center"><a href="#-drop-in-agent-support"><b>🤖 Agent Presets</b></a></td>
    <td align="center"><a href="#-minimalistic-glassmorphism-dashboard"><b>📊 Dashboard</b></a></td>
    <td align="center"><a href="#-command-cheatsheet"><b>🛠️ CLI Tools</b></a></td>
  </tr>
</table>

</div>

<br/>

---

## 💎 At a Glance

```text
┌────────────────────────┬────────────────────────┬────────────────────────┬────────────────────────┐
│      < 0.1 ms          │        $0.00           │        100%            │        100%            │
│  Tool Replay Latency   │  Cost on Repeat Reads  │  Token Savings on Hits │  Localhost Air-Gapped  │
└────────────────────────┴────────────────────────┴────────────────────────┴────────────────────────┘
```

When autonomous coding agents (**Claude Code**, **Cursor**, **Aider**, **Cline**) execute multi-turn development loops, up to **60%–80% of token burn and latency** goes into repetitive disk reads:
* Re-reading `package.json`, `tsconfig.json`, directory trees, and lint rules on every conversation turn.
* Running test suites, git diffs, or bash inspection commands where the underlying code has **not changed**.
* Incurring **1,000ms–2,500ms** remote cloud roundtrips and paying frontier input token prices for identical context.

**OmniCache** sits transparently on `127.0.0.1:8000`. It cryptographically fingerprints your local Git tree and workspace file modification times (`mtime`). If the files haven't changed, the agent receives the exact tool output or cached completion from local SQLite WAL memory in **under 0.1ms** without touching the internet. The second you edit a file or git commit, affected cache entries are **instantly evicted**.

---

## 📐 Architecture

```mermaid
flowchart TD
    subgraph Client["Developer Coding Workspace"]
        Agent["🤖 AI Coding Agent<br/>(Claude Code / Cursor / Cline / Aider)"]
    end

    subgraph Sidecar["⚡ OmniCache Local Engine (127.0.0.1:8000)"]
        Router["Transparent Interceptor<br/>(Protocol Translator)"]
        
        subgraph MemoryFabric["Deterministic Memory Subsystems"]
            GitCheck{"Git Working Tree<br/>Changed?"}
            L1["L1 Exact Trie Cache<br/>(0.06ms Lookup)"]
            L2["L2 Quantized Vector Cache<br/>(Int8 SIMD Cosine Match)"]
            ToolStore[("SQLite WAL Store<br/>Tool & File Signatures")]
            Compactor["Context Compaction<br/>(Head/Tail Pruning)"]
        end
    end

    subgraph Cloud["Remote Cloud Providers (Only on Cache Miss)"]
        Upstream["Anthropic / OpenAI / Gemini<br/>(1,000ms - 2,500ms Turn)"]
    end

    Agent -->|"ANTHROPIC_BASE_URL<br/>OPENAI_BASE_URL"| Router
    Router --> GitCheck
    GitCheck -->|"Unchanged (Hit)"| ToolStore
    GitCheck -->|"Prompt Re-evaluation"| L1
    L1 -->|"Exact Match"| ToolStore
    L1 -->|"Semantic Match"| L2
    ToolStore -->|"Replay in <0.1ms ($0.00)"| Agent
    
    L2 -->|"Cache Miss"| Compactor
    Compactor -->|"Pruned Prompt"| Upstream
    Upstream -->|"Record to SQLite WAL"| ToolStore
    ToolStore -->|"Streaming Response"| Agent
```

---

## 💡 Why OmniCache vs. Native Provider Caching?

Anthropic and OpenAI offer server-side prompt caching, which discounts prefix tokens in the cloud. However, native cloud caching leaves four critical developer gaps unaddressed:

| Capability | Native Cloud Provider Caching | OmniCache Local Sidecar |
| :--- | :--- | :--- |
| **Tool Execution Roundtrips** | ❌ **Runs on disk & calls cloud every turn** | ✅ **`<0.1ms` deterministic SQLite replay** |
| **Completion Output Token Cost** | ❌ **100% full price** ($10–$15 per 1M tokens) | ✅ **100% free ($0.00)** on cached turns |
| **Cache Lifetime** | ❌ **Ephemeral** (evicts after 5–10 min idle) | ✅ **Persistent** across sessions (SQLite WAL / Redis) |
| **Git Working-Tree Awareness** | ❌ **Zero** (no concept of git commits or mtimes) | ✅ **Deep** (SHA-256 tree validation + instant edit purge) |
| **Multi-Turn Context Growth** | ❌ **Unbounded** (context expands every turn) | ✅ **Adaptive Compaction** (prunes intermediate tool dumps) |
| **Team & Cross-Agent Sharing** | ❌ **Isolated** to a single API connection | ✅ **Shared** memory bus & CRDT P2P edge mesh |
| **Offline Air-Gapped Speed** | ❌ **Requires active internet connection** | ✅ **Runs 100% locally** on `127.0.0.1:8000` |

---

## 🚀 60-Second Quickstart

### 1. Install via PyPI
```bash
pip install omnicache-proxy
```

### 2. Verify Your Environment
```bash
omnicache doctor
```

### 3. Launch Your Coding Agent (Zero Configuration Changes)
Use the `run` execution wrapper. It automatically launches the background sidecar, exports loopback environment variables (`ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`, `LLM_BASE_URL`), and displays a session savings summary when you finish:

```bash
# Claude Code:
omnicache run claude

# Cursor IDE:
omnicache run cursor .

# Custom Python agent scripts or runners:
omnicache run -- python my_agent.py
```

When your session exits, OmniCache renders your verified telemetry ledger:
```text
╭──────────────────────────────────────────────────╮
│ ⚡ OmniCache Session Telemetry                   │
│  - Tokens Saved:       18,450 tokens             │
│  - Avoided Cost:       $0.0554 USD               │
│  - Tool Replays:            14 cached tool calls │
╰──────────────────────────────────────────────────╯
```

---

## ⚡ Verified Performance Benchmarks

Measured on standard commodity ARM/x86 hardware running Python 3.10–3.14:

```text
==================================================================================================
Subsystem                        Est. Upstream Turn   OmniCache Replay   Speedup    Benefit
==================================================================================================
L1 Exact Request Cache           ~450.00 ms (Est.)    0.0609 ms          7,390x     100% Token Savings
L2 FastHash Semantic Vector      ~450.00 ms (Est.)    1.0232 ms            439x     90%+ Cosine Replay
Agent Tool Replayer (Git-Aware)  ~1,200.00 ms (Est.)  0.3052 ms          3,931x     $0.00 Disk Thrashing
Workspace CI/CD Pre-Warming      Cold Repo Scan       2781.43 ms         2 f/s      Pre-warmed 5 files
==================================================================================================
* Est. Upstream Turn represents typical remote cloud LLM network roundtrips.
  OmniCache Replay columns represent actual locally measured micro-benchmarks on hardware.
```

Run benchmarks locally anytime:
```bash
omnicache benchmark
```

---

## 🤖 Drop-In Agent Support

OmniCache automatically writes native configurations for your preferred developer environment:

```bash
# Auto-configure Claude Code, Cursor, Cline, and OpenHands:
omnicache init

# Or preview configuration JSON / TOML snippets without touching disk:
omnicache init --show

# Target a specific assistant:
omnicache init --agent claude
omnicache init --agent cursor
omnicache init --agent cline
```

### Manual Environment Variables
If you prefer configuring your shell manually:
```bash
# Claude Code
export ANTHROPIC_BASE_URL="http://127.0.0.1:8000"

# OpenAI SDK & Cursor
export OPENAI_BASE_URL="http://127.0.0.1:8000/v1"
export OPENAI_API_BASE="http://127.0.0.1:8000/v1"
```

---

## 📊 Minimalistic Glassmorphism Dashboard

OmniCache includes an integrated developer observability dashboard styled with a modern frosted glass aesthetic:

```text
http://localhost:8000/dashboard
```

* **Live Financial & Token Telemetry:** Real-time metrics for avoided dollar spend, total tokens avoided, and cache hit rates.
* **Velocity Stream:** Real-time Chart.js spline graph tracking token savings velocity across active agent turns.
* **Resolution Doughnut:** Real-time distribution between L1 Exact hits, L2 Semantic matches, and Agent Tool replays.
* **Interactive Gateway Sandbox:** Test Claude Messages and OpenAI Chat Completions in-browser with live token accounting.
* **Workspace Git Memory:** View dirty/clean git status and pre-warm repository file caches on demand.

> [!TIP]
> Run `omnicache demo` to launch an interactive 30-second live simulation that streams realistic agent events directly to your browser dashboard.

---

## 🛠️ Command Cheatsheet

| Command | Purpose |
| :--- | :--- |
| `omnicache` | Start the background proxy daemon listening on `127.0.0.1:8000` |
| `omnicache run <cmd>` | Zero-config execution wrapper for Claude Code, Cursor, or custom agents |
| `omnicache doctor` | Validate Python runtime, SQLite WAL status, port binding, and vector engine health |
| `omnicache benchmark` | Run microsecond latency and throughput benchmarks across all cache tiers |
| `omnicache demo` | Run an automated 30-second live test streaming realistic events to the dashboard |
| `omnicache harness` | Run the complete 14-subsystem integration test scorecard (`14/14 Passed`) |
| `omnicache warm` | Pre-warm workspace repository files and tool signatures for instant replays |
| `omnicache sync` | Export, import, push, or pull cache snapshots across teammates |
| `omnicache mesh` | Discover and inspect P2P Edge Mesh peers and CRDT vector clocks |
| `omnicache stats` | Output cumulative session savings (pass `--markdown` for CI/CD summaries) |

---

## 🔒 Security & Air-Gapped Privacy

* **Strict Localhost Binding:** By default, OmniCache binds strictly to loopback (`127.0.0.1:8000`), refusing to expose unauthenticated endpoints to external networks.
* **Salted HMAC-SHA256 Tokenization:** The built-in `PrivacyShield` scrubs sensitive developer credentials (emails, API keys, private tokens) locally before optional upstream transmission.
* **Zero Remote Telemetry:** All embeddings, SQLite WAL database writes, and telemetry aggregations execute in-process. No data is phoned home to external cloud servers.
* For security disclosures and guidelines, see [SECURITY.md](SECURITY.md).

---

## 📄 Documentation

* 📖 [Architecture Deep Dive](docs/ARCHITECTURE.md)
* 🤖 [Agent Integrations Guide (Claude Code, Cursor, Cline)](docs/AGENT_INTEGRATIONS.md)
* 📡 [API Reference](docs/API_REFERENCE.md)
* 🔬 [Independent Empirical Audit Report](docs/EMPIRICAL_AUDIT_REPORT.md)
* ❓ [Troubleshooting & FAQ](docs/TROUBLESHOOTING_AND_FAQ.md)

---

## ⚖️ License

Distributed under the **Functional Source License, Version 1.1 (FSL-1.1-MIT)**.  
Copyright (c) 2026 Rajiv Prasad.

Free for developers and internal organizational use; prevents third-party commercial cloud hosting/proxy competition; converts automatically to standard **MIT License** after two years. See [LICENSE](LICENSE) for full legal terms.
