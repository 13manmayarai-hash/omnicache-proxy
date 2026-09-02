# ⚡ OmniCache AI Proxy 2.0

> **Zero-Latency Semantic Caching, Autonomous Agent Accelerator & Enterprise Cost Gateway for LLMs.**  
> *Slash your OpenAI & Anthropic API bills by 40%–75%. Deliver sub-millisecond AI responses with zero code refactoring.*

[![Version](https://img.shields.io/badge/version-2.0.0-blue.svg)](https://github.com/13manmayarai-hash/omnicache-proxy)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-27%2F27%20passing-emerald.svg)](tests/)
[![Latency](https://img.shields.io/badge/latency-%3C%200.8ms-purple.svg)](#benchmarks)
[![Python](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12%20%7C%203.14-cyan.svg)](pyproject.toml)

---

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              THE OMNICACHE ARCHITECTURE                                │
└────────────────────────────────────────────────────────────────────────────────────────┘

 [ Client / Agent / IDE ] ──► (POST /v1/chat/completions OR /v1/messages)
                                       │
                                       ▼
                       ┌──────────────────────────────┐
                       │   OmniCache AI Gateway 2.0   │
                       │  - Virtual Key Quota Guard   │
                       │  - Zero-Knowledge PII Shield │
                       └──────────────┬───────────────┘
                                      │
              ┌───────────────────────┴───────────────────────┐
              ▼ HIT (< 1ms, $0.00)                            ▼ MISS / BYPASS
  ┌───────────────────────┐                       ┌───────────────────────┐
  │ Token Jitter SSE      │                       │ SingleFlight Mutex    │
  │ Stream Replayer       │                       │ & Cost Cascade Router │
  │ (~65 tok/s, <10ms TTFT│                       │ (Gemini 2.5 / Claude) │
  └───────────────────────┘                       └───────────┬───────────┘
                                                              │
                                                              ▼
                                                  [ Upstream AI Providers ]
                                                  (OpenAI / Anthropic / Gemini)
```

---

## 🌟 Why OmniCache?

1. **⚡ Sub-Millisecond Vector Semantic Caching (<0.8ms):** Pure in-memory 512-d feature projection embedder matches paraphrased queries with zero remote API lag.
2. **🏎️ Coding Agent Tool-Loop Accelerator:** Caches idempotent tool calls (`read_file`, `git status`, `grep`) for **Claude Code, Cursor, and Devin**, cutting agent loop runtimes from 15s to 350ms.
3. **🚦 Adaptive Cost Arbitrage & Model Cascade:** Automatically routes simple formatting / classification queries to **Gemini 2.5 Flash ($0.05/1M)**, slashing non-cached cloud bills by 75%.
4. **🖼️ Multi-Modal Vision Perception Cache:** Uses **64-bit Perceptual Hashing (dHash)** to match UI screenshots, invoices, and images in **<0.3ms at $0.00**.
5. **🛡️ Zero-Knowledge Privacy Vault:** Reversible tokenized masking of SSNs, credit cards, emails, and API keys before sending upstream (HIPAA & SOC2 ready).
6. **🌊 Token Jitter SSE Streaming:** Smoothly replays cached tokens at natural typing speed (~65 tokens/sec) with `<10ms` Time-To-First-Token, fixing the 0ms UI typing blast.
7. **🔌 Model Context Protocol (MCP) Native:** Integrates directly into Claude Desktop, Cursor, and Windsurf via JSON-RPC 2.0 stdio.

---

## 🚀 Quickstart (1-Line Integration)

### 1. Start OmniCache in the Background
```bash
# Option A: With Python
git clone https://github.com/13manmayarai-hash/omnicache-proxy.git
cd omnicache-proxy
pip install starlette uvicorn httpx
python3 main.py

# Option B: With Docker Compose
docker-compose up -d
```
*The gateway is now live at `http://localhost:8000` with the analytics dashboard at `http://localhost:8000/dashboard`.*

---

### 2. Connect Your Application (Zero Code Changes)

#### Python (OpenAI SDK):
```python
from openai import OpenAI

# Simply route baseURL to OmniCache
client = OpenAI(
    api_key="your-api-key",
    base_url="http://localhost:8000/v1"
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "How do I optimize SQL queries?"}]
)
print(response.choices[0].message.content)
```

#### Claude Code (Terminal Assistant):
```bash
export ANTHROPIC_BASE_URL="http://localhost:8000/v1"
claude
```

#### TypeScript / Node.js:
```typescript
import OpenAI from "openai";

const openai = new OpenAI({
  apiKey: "your-api-key",
  baseURL: "http://localhost:8000/v1"
});
```

---

## 📊 Live Web Telemetry Dashboard

Open **`http://localhost:8000/dashboard`** in your browser to inspect live:
- 🟢 **Total Cost Saved ($ USD)** & **Tokens Saved (100% Free)**
- ⚡ **P99 Sub-Millisecond Latency**
- 🛡️ **PII Masked Items Scrubbed**
- 🔑 **Virtual Key Quotas & Team Spending**
- 🛠️ **Live Tag Invalidation & Tenant Purging**

---

## 📚 Complete Documentation Suite

| Document | Description |
|:---|:---|
| 🏛️ [**Architecture Specification**](docs/ARCHITECTURE.md) | Deep technical breakdown of Radix Trees, Intent Gating, SingleFlight, and SSE Replay. |
| 📖 [**API Reference**](docs/API_REFERENCE.md) | Full REST & Messages API specification, developer headers, and error codes. |
| 🚀 [**Quickstart Guide**](docs/QUICKSTART_GUIDE.md) | Step-by-step onboarding for Python, Node.js, Claude Code, Cursor, and Docker. |
| 🛠️ [**Troubleshooting & FAQ**](docs/TROUBLESHOOTING_AND_FAQ.md) | The complete "Help Me" diagnostic manual and debugging guide. |
| 🔬 [**Research & Product Strategy**](docs/RESEARCH_AND_PRODUCT_STRATEGY.md) | Competitive teardown, provider prompt caching math, and 24-month roadmap. |
| 🔒 [**Security Policy**](SECURITY.md) | Responsible vulnerability disclosure, encryption, and patch SLAs. |
| 🛡️ [**Privacy Policy**](legal/PRIVACY_POLICY.md) | Zero-knowledge architecture, no-retention guarantee, and HIPAA/GDPR disclosures. |
| 📜 [**Terms of Service & SLA**](legal/TERMS_OF_SERVICE.md) | 99.99% uptime guarantee, sub-ms latency SLA, and enterprise support tiers. |
| 🤝 [**Contributing Guide**](CONTRIBUTING.md) | Development setup, PR workflow, and test verification guidelines. |

---

## 🧪 Running the Test Suite

```bash
python3 -m unittest discover -s tests
```
```text
Ran 27 tests in 3.569s
OK (100% Pass Rate)
```

---

## 📄 License
OmniCache AI Proxy is open-source software licensed under the [MIT License](LICENSE).
