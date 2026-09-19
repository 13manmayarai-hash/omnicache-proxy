/**
 * OmniCache Official TypeScript / JavaScript Client SDK.
 * Drop-in wrapper with automatic upstream fallback, semantic cache headers, and telemetry introspection.
 */

export interface OmniCacheOptions {
  baseUrl?: string;
  apiKey?: string;
  orgId?: string;
  fallbackToUpstream?: boolean;
  openaiApiKey?: string;
  anthropicApiKey?: string;
  timeoutMs?: number;
}

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

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string;
  name?: string;
  [key: string]: any;
}

export interface ChatCompletionParams {
  model: string;
  messages: ChatMessage[];
  temperature?: number;
  max_tokens?: number;
  stream?: boolean;
  cacheBypass?: boolean;
  cacheTtl?: number;
  cascadeOptIn?: boolean;
  extraHeaders?: Record<string, string>;
  [key: string]: any;
}

export interface AnthropicMessageParams {
  model: string;
  messages: Array<{ role: 'user' | 'assistant'; content: string | any[] }>;
  system?: string;
  max_tokens?: number;
  stream?: boolean;
  cacheBypass?: boolean;
  cacheTtl?: number;
  cascadeOptIn?: boolean;
  extraHeaders?: Record<string, string>;
  [key: string]: any;
}

function parseCacheHeaders(headers: Headers): CacheMetadata {
  const status = (headers.get('x-cache-status') || 'MISS').toUpperCase();
  const isHit = status.startsWith('HIT');
  return {
    status,
    isHit,
    similarity: parseFloat(headers.get('x-cache-similarity') || '0.0'),
    latencyMs: parseFloat(headers.get('x-cache-latency-ms') || '0.0'),
    tokensSaved: parseInt(headers.get('x-tokens-saved') || '0', 10),
    costSavedUsd: parseFloat(headers.get('x-cost-saved-usd') || '0.0'),
    servedModel: headers.get('x-served-model') || '',
    cascadeApplied: (headers.get('x-cascade-applied') || 'false').toLowerCase() === 'true',
    cascadeReason: headers.get('x-cascade-reason') || '',
    swarmHit: (headers.get('x-omnicache-swarm-hit') || 'false').toLowerCase() === 'true',
    meshNode: headers.get('x-omnicache-mesh-node') || ''
  };
}

export class OmniCache {
  private baseUrl: string;
  private apiKey: string;
  private orgId: string;
  private fallbackToUpstream: boolean;
  private openaiApiKey: string;
  private anthropicApiKey: string;
  private timeoutMs: number;

  public chat: {
    completions: {
      create: <T = any>(params: ChatCompletionParams) => Promise<OmniCacheResponse<T>>;
    };
  };

  public messages: {
    create: <T = any>(params: AnthropicMessageParams) => Promise<OmniCacheResponse<T>>;
  };

  constructor(options: OmniCacheOptions = {}) {
    this.baseUrl = (options.baseUrl || process.env.OMNICACHE_BASE_URL || 'http://127.0.0.1:8000').replace(/\/+$/, '');
    this.apiKey = options.apiKey || process.env.OMNICACHE_API_KEY || '';
    this.orgId = options.orgId || process.env.OMNICACHE_ORG_ID || 'default';
    this.fallbackToUpstream = options.fallbackToUpstream !== false;
    this.openaiApiKey = options.openaiApiKey || process.env.OPENAI_API_KEY || '';
    this.anthropicApiKey = options.anthropicApiKey || process.env.ANTHROPIC_API_KEY || '';
    this.timeoutMs = options.timeoutMs || 30000;

    this.chat = {
      completions: {
        create: this.chatCompletion.bind(this)
      }
    };

    this.messages = {
      create: this.anthropicMessage.bind(this)
    };
  }

  public async isHealthy(): Promise<boolean> {
    try {
      const res = await fetch(`${this.baseUrl}/healthz`, {
        signal: AbortSignal.timeout(3000)
      });
      return res.ok;
    } catch {
      return false;
    }
  }

  private buildHeaders(
    cacheBypass?: boolean,
    cacheTtl?: number,
    cascadeOptIn?: boolean,
    extra?: Record<string, string>
  ): Record<string, string> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'x-org-id': this.orgId,
      ...(extra || {})
    };

    if (this.apiKey) {
      headers['Authorization'] = `Bearer ${this.apiKey}`;
      headers['x-api-key'] = this.apiKey;
    }

    if (cacheBypass) {
      headers['x-cache-bypass'] = 'true';
    }

    if (cacheTtl !== undefined) {
      headers['x-cache-ttl'] = String(cacheTtl);
    }

    if (cascadeOptIn !== undefined) {
      headers['x-omnicache-model-cascade'] = cascadeOptIn ? 'true' : 'false';
    }

    return headers;
  }

  public async chatCompletion<T = any>(params: ChatCompletionParams): Promise<OmniCacheResponse<T>> {
    const { cacheBypass, cacheTtl, cascadeOptIn, extraHeaders, ...payload } = params;
    const headers = this.buildHeaders(cacheBypass, cacheTtl, cascadeOptIn, extraHeaders);

    try {
      const res = await fetch(`${this.baseUrl}/v1/chat/completions`, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(this.timeoutMs)
      });

      if (!res.ok) {
        throw new Error(`OmniCache HTTP ${res.status}: ${await res.text()}`);
      }

      const data = await res.json();
      const meta = parseCacheHeaders(res.headers);
      return Object.assign(data, { _cache: meta });
    } catch (err: any) {
      if (this.fallbackToUpstream && this.openaiApiKey) {
        return this.fallbackOpenAI<T>(payload);
      }
      throw err;
    }
  }

  public async anthropicMessage<T = any>(params: AnthropicMessageParams): Promise<OmniCacheResponse<T>> {
    const { cacheBypass, cacheTtl, cascadeOptIn, extraHeaders, ...payload } = params;
    const headers = this.buildHeaders(cacheBypass, cacheTtl, cascadeOptIn, extraHeaders);

    try {
      const res = await fetch(`${this.baseUrl}/v1/messages`, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(this.timeoutMs)
      });

      if (!res.ok) {
        throw new Error(`OmniCache HTTP ${res.status}: ${await res.text()}`);
      }

      const data = await res.json();
      const meta = parseCacheHeaders(res.headers);
      return Object.assign(data, { _cache: meta });
    } catch (err: any) {
      if (this.fallbackToUpstream && this.anthropicApiKey) {
        return this.fallbackAnthropic<T>(payload);
      }
      throw err;
    }
  }

  private async fallbackOpenAI<T>(payload: any): Promise<OmniCacheResponse<T>> {
    const res = await fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${this.openaiApiKey}`
      },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(this.timeoutMs)
    });

    const data = await res.json();
    const fallbackMeta: CacheMetadata = {
      status: 'FALLBACK_DIRECT_UPSTREAM',
      isHit: false,
      similarity: 0,
      latencyMs: 0,
      tokensSaved: 0,
      costSavedUsd: 0,
      servedModel: payload.model || '',
      cascadeApplied: false,
      cascadeReason: 'proxy_offline',
      swarmHit: false,
      meshNode: ''
    };
    return Object.assign(data, { _cache: fallbackMeta });
  }

  private async fallbackAnthropic<T>(payload: any): Promise<OmniCacheResponse<T>> {
    const res = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': this.anthropicApiKey,
        'anthropic-version': '2023-06-01'
      },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(this.timeoutMs)
    });

    const data = await res.json();
    const fallbackMeta: CacheMetadata = {
      status: 'FALLBACK_DIRECT_UPSTREAM',
      isHit: false,
      similarity: 0,
      latencyMs: 0,
      tokensSaved: 0,
      costSavedUsd: 0,
      servedModel: payload.model || '',
      cascadeApplied: false,
      cascadeReason: 'proxy_offline',
      swarmHit: false,
      meshNode: ''
    };
    return Object.assign(data, { _cache: fallbackMeta });
  }
}

export default OmniCache;
