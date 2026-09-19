# OmniCache Python Client SDK (`omnicache-client`)

Official Python client bindings for **OmniCache AI Proxy & Semantic Multi-Tier Cache Fabric**.

Drop-in replacement for OpenAI and Anthropic SDK callers that automatically delivers:
* **Sub-millisecond L1 Radix replays** on exact and prefix prompt turns.
* **L2 Quantized cosine similarity matching** for paraphrases and semantic variants.
* **Speculative model cascading** for cost and latency arbitrage.
* **Zero-trust PII sanitization** before telemetry or cache storage.
* **Automatic direct upstream failover** during local maintenance or outages.

---

## Installation

```bash
pip install omnicache-client
```

Or install from source:
```bash
cd sdk/python
pip install -e .
```

---

## Quickstart

### 1. OpenAI-Compatible Chat Completions

```python
from core.client import OmniCacheClient

# Connect to local or remote OmniCache gateway
client = OmniCacheClient(
    base_url="http://127.0.0.1:8000",
    api_key="your_omnicache_api_key",
    org_id="engineering_team"
)

# Call chat completions exactly like the official OpenAI SDK
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "system", "content": "You are an expert Python engineer."},
        {"role": "user", "content": "Write a thread-safe LRU cache in Python."}
    ],
    temperature=0.0
)

# Standard OpenAI completion payload access
print(response["choices"][0]["message"]["content"])

# Inspect OmniCache telemetry directly on the response object
print("--- Cache Telemetry ---")
print(f"Status:        {response.cache.status}")           # HIT_EXACT, HIT_SEMANTIC, or MISS
print(f"Is Hit:        {response.cache.is_hit()}")         # True / False
print(f"Latency:       {response.cache.latency_ms:.2f} ms") # In-memory replay latency
print(f"Tokens Saved:  {response.cache.tokens_saved}")      # Prompt + completion tokens
print(f"Cost Saved:    ${response.cache.cost_saved_usd:.6f}") # Estimated cost avoidance
```

---

### 2. Anthropic-Compatible Messages API

```python
from core.client import OmniCacheClient

client = OmniCacheClient(base_url="http://127.0.0.1:8000")

# Call Anthropic Claude messages
response = client.messages.create(
    model="claude-3-5-sonnet-20241022",
    messages=[
        {"role": "user", "content": "Explain the CAP theorem in distributed systems."}
    ],
    max_tokens=256
)

print(response["content"][0]["text"])
print(f"Cache Status: {response.cache.status}")
```

---

### 3. Asynchronous Execution (`async` / `await`)

Both OpenAI and Anthropic endpoints support asynchronous non-blocking calls via `acreate()`:

```python
import asyncio
from core.client import OmniCacheClient

async def main():
    client = OmniCacheClient(base_url="http://127.0.0.1:8000")

    # Async OpenAI call
    openai_res = await client.chat.completions.acreate(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Fast sorting algorithms"}]
    )
    print("OpenAI status:", openai_res.cache.status)

    # Async Anthropic call
    claude_res = await client.messages.acreate(
        model="claude-3-5-sonnet-20241022",
        messages=[{"role": "user", "content": "Fast sorting algorithms"}]
    )
    print("Claude status:", claude_res.cache.status)

asyncio.run(main())
```

---

## Advanced Capabilities

### Speculative Model Cascading & Cost Arbitrage

Enable automatic complexity estimation and down-routing to fast classifier tiers:

```python
response = client.chat.completions.create(
    model="gpt-4o", # Expensive tier requested
    messages=[
        {"role": "user", "content": "Classify sentiment: 'The battery lasts 2 days!'"}
    ],
    cascade_opt_in=True # Automatically downgrades simple prompts to fast tiers (e.g., gpt-4o-mini)
)

if response.cache.cascade_applied:
    print(f"Downgraded to: {response.cache.served_model}")
    print(f"Reason: {response.cache.cascade_reason}")
```

### Cache Bypass & Custom TTL

Control caching behavior per request using dedicated parameters:

```python
# Force cold execution bypassing existing cache entries
fresh_response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Real-time market price check"}],
    cache_bypass=True
)

# Store cache entry with a custom 1-hour TTL (3600 seconds)
temp_response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Daily summary"}],
    cache_ttl=3600
)
```

### Multi-Tenant Organization Namespaces

Isolate cache keys and quotas between teams or enterprise customers:

```python
client_team_a = OmniCacheClient(base_url="http://127.0.0.1:8000", org_id="team_finance")
client_team_b = OmniCacheClient(base_url="http://127.0.0.1:8000", org_id="team_marketing")
```

---

## Cache Telemetry Reference (`response.cache`)

Every response returned by `OmniCacheClient` contains a `.cache` property:

| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `str` | `HIT_EXACT`, `HIT_SEMANTIC`, `HIT_L1_RADIX`, `HIT_SWARM`, `MISS`, or `BYPASS` |
| `is_hit()` | `bool` | Convenience method (`True` if response was served from any cache tier) |
| `similarity` | `float` | Cosine similarity score (1.0 for exact matches, ~0.75–0.99 for semantic hits) |
| `latency_ms` | `float` | Proxy resolution latency in milliseconds |
| `tokens_saved` | `int` | Number of tokens avoided through cache reuse |
| `cost_saved_usd` | `float` | Avoided cost in USD based on official provider token pricing |
| `served_model` | `str` | Model identifier that actually generated the response (tracks cascade routing) |
| `cascade_applied` | `bool` | Whether automatic model cascade down-routing occurred |
| `cascade_reason` | `str` | Reason provided by the cascade heuristic classifier |
| `swarm_hit` | `bool` | Whether response was resolved from a peer agent in a multi-agent swarm |
| `mesh_node` | `str` | CRDT mesh node identifier that fulfilled the request |

---

## In-Memory ASGI Test Support

For unit tests and local integrations, pass your Starlette/FastAPI application directly to `OmniCacheClient` to execute requests in-process without opening external network sockets:

```python
from server.gateway import app
from core.client import OmniCacheClient

# In-process ASGI client (zero network latency)
client = OmniCacheClient(base_url="http://testserver", app=app)
assert client.is_healthy() is True
```
