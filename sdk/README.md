# OmniCache SDK Ecosystem

Official client libraries for **OmniCache AI Proxy & Radix Semantic Cache Fabric**.

OmniCache provides drop-in client bindings for Python and TypeScript / JavaScript, enabling seamless multi-tier semantic caching, speculative model cascading, zero-trust PII redaction, and sub-millisecond response replays across OpenAI and Anthropic LLM endpoints.

---

## Supported SDKs

| Language | Package | Version | Documentation |
| :--- | :--- | :--- | :--- |
| **Python** (3.9+) | `omnicache-client` | `v3.1.0` | [Python SDK Guide](file:///root/omnicache_proxy/sdk/python/README.md) |
| **TypeScript / Node.js** | `omnicache-ai` | `v3.1.0` | [TypeScript SDK Guide](file:///root/omnicache_proxy/sdk/typescript/README.md) |

---

## Core Capabilities

```
                  ┌─────────────────────────────────────────┐
                  │          OmniCache Client SDK           │
                  │   (Python / TypeScript / JavaScript)    │
                  └────────────────────┬────────────────────┘
                                       │
                         Dual Protocol Routing
                                       ▼
                  ┌────────────────────┬────────────────────┐
                  │                    │                    │
                  ▼                    ▼                    ▼
          OpenAI Compatible    Anthropic Compatible   Custom Headers
          (/v1/chat/completions) (/v1/messages)       (Bypass, TTL, Org)
                  │                    │                    │
                  └────────────────────┼────────────────────┘
                                       │
                                       ▼
                  ┌─────────────────────────────────────────┐
                  │            OmniCache Gateway            │
                  │  L1 Radix Trie  •  L2 Quantized Vector  │
                  │  Model Cascade  •  Zero-Trust PII Scrub │
                  │  CRDT Mesh Bus  •  OTel Audit Ledger    │
                  └─────────────────────────────────────────┘
```

* **⚡ Sub-Millisecond L1 Replays:** Automatic deterministic Radix cache matching for exact and multi-turn prefix requests.
* **🧠 L2 Quantized Semantic Match:** Vector similarity matching across rephrased queries and semantic near-duplicates.
* **📉 Speculative Model Cascading:** Automatic complexity evaluation and cost arbitrage (e.g., routing simple classification from expensive frontier models to high-throughput lightweight models).
* **🔒 Zero-Trust PII Redaction:** Automatic redaction of sensitive identifiers (emails, credit cards, SSNs, phone numbers) before persistence.
* **🛡️ Zero-Downtime Fallback:** Automatic direct upstream failover if the local cache proxy becomes unreachable.
* **📊 Deep Telemetry Introspection:** Every response contains rich metadata: `is_hit`, `similarity`, `latency_ms`, `tokens_saved`, and `cost_saved_usd`.

---

## Quickstart Comparison

### Python
```python
from core.client import OmniCacheClient

client = OmniCacheClient(base_url="http://127.0.0.1:8000")

# Drop-in OpenAI chat completion
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Explain binary search"}]
)

print(f"Status: {response.cache.status} | Saved: {response.cache.tokens_saved} tokens")
```

### TypeScript / Node.js
```typescript
import { OmniCache } from 'omnicache-ai';

const client = new OmniCache({ baseUrl: 'http://127.0.0.1:8000' });

// Drop-in OpenAI chat completion
const response = await client.chat.completions.create({
  model: 'gpt-4o',
  messages: [{ role: 'user', content: 'Explain binary search' }]
});

console.log(`Status: ${response._cache.status} | Saved: ${response._cache.tokensSaved} tokens`);
```

---

## Configuration Reference

Both SDKs accept consistent configuration parameters:

| Parameter | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `baseUrl` | `OMNICACHE_BASE_URL` | `http://127.0.0.1:8000` | Gateway endpoint URL |
| `apiKey` | `OMNICACHE_API_KEY` | `""` | Tenant authentication key |
| `orgId` | `OMNICACHE_ORG_ID` | `"default"` | Multi-tenant organization namespace |
| `fallbackToUpstream` | `OMNICACHE_FALLBACK` | `True` / `true` | Failover to direct provider on proxy outage |
| `openaiApiKey` | `OPENAI_API_KEY` | `""` | Direct OpenAI key for emergency fallback |
| `anthropicApiKey` | `ANTHROPIC_API_KEY` | `""` | Direct Anthropic key for emergency fallback |
| `timeout` | `OMNICACHE_TIMEOUT` | `30.0`s / `30000`ms | Request timeout in seconds (Python) / ms (TS) |

---

## Repository Structure

```
sdk/
├── README.md                  # This guide
├── python/
│   ├── README.md              # Python SDK documentation
│   ├── pyproject.toml         # Packaging configuration
│   ├── omnicache/
│   │   └── __init__.py        # Exported classes and version
│   └── examples/
│       ├── quickstart_openai.py
│       ├── quickstart_anthropic.py
│       └── model_cascade.py
└── typescript/
    ├── README.md              # TypeScript SDK documentation
    ├── package.json           # Node.js npm package definition
    ├── tsconfig.json          # TypeScript compiler configuration
    └── src/
        └── index.ts           # Core TypeScript implementation
```
