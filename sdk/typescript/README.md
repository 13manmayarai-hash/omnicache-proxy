# OmniCache TypeScript & Node.js Client SDK (`omnicache-ai`)

Official TypeScript and Node.js client bindings for **OmniCache AI Proxy & Radix Semantic Cache Fabric**.

Zero-dependency, native `fetch`-based client designed for high-throughput Edge runtime, Next.js, Express, NestJS, and serverless Node.js environments.

---

## Installation

```bash
npm install omnicache-ai
```

Or using Yarn / pnpm:
```bash
yarn add omnicache-ai
# or
pnpm add omnicache-ai
```

---

## Quickstart

### 1. OpenAI-Compatible Chat Completions

```typescript
import { OmniCache } from 'omnicache-ai';

// Initialize client
const client = new OmniCache({
  baseUrl: process.env.OMNICACHE_BASE_URL || 'http://127.0.0.1:8000',
  apiKey: process.env.OMNICACHE_API_KEY,
  orgId: 'engineering_team'
});

async function run() {
  const response = await client.chat.completions.create({
    model: 'gpt-4o',
    messages: [
      { role: 'system', content: 'You are an expert TypeScript developer.' },
      { role: 'user', content: 'Explain generics in TypeScript with an example.' }
    ],
    temperature: 0.0
  });

  // Standard OpenAI response format
  console.log(response.choices[0].message.content);

  // Deep telemetry on the _cache object
  console.log('--- OmniCache Telemetry ---');
  console.log(`Status:       ${response._cache.status}`);        // HIT_EXACT, HIT_SEMANTIC, or MISS
  console.log(`Is Hit:       ${response._cache.isHit}`);         // boolean
  console.log(`Latency:      ${response._cache.latencyMs} ms`);   // Proxy turnaround time
  console.log(`Tokens Saved: ${response._cache.tokensSaved}`);   // Tokens reused
  console.log(`Cost Saved:   $${response._cache.costSavedUsd}`); // Estimated cost avoided
}

run();
```

---

### 2. Anthropic Claude Messages API

```typescript
import { OmniCache } from 'omnicache-ai';

const client = new OmniCache({ baseUrl: 'http://127.0.0.1:8000' });

async function runAnthropic() {
  const response = await client.messages.create({
    model: 'claude-3-5-sonnet-20241022',
    messages: [
      { role: 'user', content: 'Summarize the differences between TCP and UDP.' }
    ],
    max_tokens: 300
  });

  console.log(response.content[0].text);
  console.log(`Cache Status: ${response._cache.status}`);
}

runAnthropic();
```

---

## Advanced Capabilities

### Speculative Model Cascading

Opt-in to automatic heuristic downgrading for classification and short informational queries:

```typescript
const response = await client.chat.completions.create({
  model: 'gpt-4o',
  messages: [{ role: 'user', content: 'Classify sentiment: "Loved the UI design!"' }],
  cascadeOptIn: true // Allows automatic routing to fast tier (e.g., gpt-4o-mini)
});

if (response._cache.cascadeApplied) {
  console.log(`Downgraded to: ${response._cache.servedModel}`);
  console.log(`Reason:        ${response._cache.cascadeReason}`);
}
```

### Cache Bypass & Custom TTL

```typescript
// Bypass cache to guarantee cold generation
const freshResponse = await client.chat.completions.create({
  model: 'gpt-4o',
  messages: [{ role: 'user', content: 'Current stock market quotes' }],
  cacheBypass: true
});

// Cache entry with custom 10-minute TTL (600 seconds)
const tempResponse = await client.chat.completions.create({
  model: 'gpt-4o',
  messages: [{ role: 'user', content: 'Generate meeting agenda' }],
  cacheTtl: 600
});
```

### Zero-Downtime Upstream Failover

If the local OmniCache proxy is undergoing maintenance or temporarily unreachable, the SDK will automatically fail over directly to OpenAI or Anthropic using provided fallback keys:

```typescript
const client = new OmniCache({
  baseUrl: 'http://127.0.0.1:8000',
  fallbackToUpstream: true,
  openaiApiKey: process.env.OPENAI_API_KEY,
  anthropicApiKey: process.env.ANTHROPIC_API_KEY
});

// If port 8000 is down, automatically routes to https://api.openai.com
const response = await client.chat.completions.create({
  model: 'gpt-4o',
  messages: [{ role: 'user', content: 'Hello' }]
});

if (response._cache.status === 'FALLBACK_DIRECT_UPSTREAM') {
  console.warn('Proxy unreachable; request fulfilled via direct upstream fallback.');
}
```

---

## TypeScript Type Declarations

All parameters and telemetry objects are strongly typed:

```typescript
export interface CacheMetadata {
  status: string;
  isHit: boolean;
  similarity: number;
  latencyMs: number;
  tokensSaved: number;
  costSavedUsd: number;
  servedModel: string;
  cascadeApplied: boolean;
  cascadeReason: string;
  swarmHit: boolean;
  meshNode: string;
}

export type OmniCacheResponse<T> = T & {
  _cache: CacheMetadata;
};
```
