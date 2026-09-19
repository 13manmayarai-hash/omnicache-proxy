"""
OpenAPI 3.1.0 Specification and Interactive Swagger Documentation for OmniCache AI Proxy.
"""

from typing import Dict, Any

OPENAPI_SPEC: Dict[str, Any] = {
    "openapi": "3.1.0",
    "info": {
        "title": "OmniCache Enterprise AI Proxy & Radix Semantic Cache Fabric",
        "version": "3.1.0",
        "description": (
            "Production-grade, zero-trust AI proxy providing sub-millisecond Radix prefix caching, "
            "quantized vector semantic search, Git-aware tool replay, model cascading, "
            "distributed CRDT mesh sync, and enterprise compliance audit logging."
        ),
        "license": {
            "name": "FSL-1.1-MIT",
            "url": "https://github.com/13manmayarai-hash/omnicache-proxy/blob/main/LICENSE"
        },
        "contact": {
            "name": "OmniCache Engineering",
            "url": "https://github.com/13manmayarai-hash/omnicache-proxy"
        }
    },
    "servers": [
        {"url": "http://127.0.0.1:8000", "description": "Local Sidecar / Development Gateway"},
        {"url": "http://localhost:8000", "description": "Localhost Alias"}
    ],
    "paths": {
        "/v1/chat/completions": {
            "post": {
                "summary": "OpenAI-Compatible Chat Completions with Radix Semantic Interception",
                "description": "Executes or replays chat completions with sub-millisecond caching and PII scrubbing.",
                "tags": ["AI Proxy Inference"],
                "parameters": [
                    {
                        "name": "x-api-key",
                        "in": "header",
                        "required": False,
                        "schema": {"type": "string"},
                        "description": "OmniCache virtual API key"
                    },
                    {
                        "name": "x-org-id",
                        "in": "header",
                        "required": False,
                        "schema": {"type": "string", "default": "default"},
                        "description": "Tenant isolation namespace"
                    },
                    {
                        "name": "x-cache-bypass",
                        "in": "header",
                        "required": False,
                        "schema": {"type": "boolean", "default": False},
                        "description": "Force live upstream execution bypassing all cache tiers"
                    },
                    {
                        "name": "x-omnicache-model-cascade",
                        "in": "header",
                        "required": False,
                        "schema": {"type": "boolean", "default": False},
                        "description": "Opt-in to cost-arbitrage model cascading"
                    }
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["model", "messages"],
                                "properties": {
                                    "model": {"type": "string", "example": "gpt-4o"},
                                    "messages": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "required": ["role", "content"],
                                            "properties": {
                                                "role": {"type": "string", "enum": ["system", "user", "assistant", "tool"]},
                                                "content": {"type": "string"}
                                            }
                                        }
                                    },
                                    "temperature": {"type": "number", "default": 0.0},
                                    "max_tokens": {"type": "integer"}
                                }
                            }
                        }
                    }
                },
                "responses": {
                    "200": {
                        "description": "Successful completion or cache replay",
                        "headers": {
                            "X-Cache-Status": {"schema": {"type": "string", "example": "HIT_L1_RADIX"}},
                            "X-Cache-Latency-Ms": {"schema": {"type": "string", "example": "0.06"}},
                            "X-Tokens-Saved": {"schema": {"type": "string", "example": "125"}},
                            "X-Cost-Saved-USD": {"schema": {"type": "string", "example": "0.00125"}}
                        }
                    }
                }
            }
        },
        "/v1/messages": {
            "post": {
                "summary": "Anthropic-Compatible Messages API with Ephemeral Block Alignment",
                "description": "Executes or replays Anthropic Claude messages with prompt-block caching.",
                "tags": ["AI Proxy Inference"],
                "responses": {
                    "200": {"description": "Anthropic message response"}
                }
            }
        },
        "/v1/enterprise/audit/export": {
            "get": {
                "summary": "Export Enterprise Compliance Audit Trail",
                "description": "Downloads immutable compliance events in JSON, CSV (RFC 4180), or OpenTelemetry Logs format.",
                "tags": ["Enterprise Compliance & Audit"],
                "parameters": [
                    {
                        "name": "format",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string", "enum": ["json", "csv", "otel"], "default": "json"}
                    },
                    {
                        "name": "event_type",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string"}
                    },
                    {
                        "name": "limit",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "integer", "default": 500}
                    }
                ],
                "responses": {
                    "200": {"description": "Formatted compliance export"}
                }
            }
        },
        "/v1/enterprise/audit/summary": {
            "get": {
                "summary": "Enterprise Policy & Compliance KPI Rollup",
                "description": "Returns counts for PII redactions, model cascades, and budget warnings.",
                "tags": ["Enterprise Compliance & Audit"],
                "responses": {
                    "200": {"description": "KPI summary object"}
                }
            }
        },
        "/v1/enterprise/audit/events": {
            "get": {
                "summary": "List Live Compliance Audit Events",
                "tags": ["Enterprise Compliance & Audit"],
                "responses": {"200": {"description": "List of recent audit records"}}
            },
            "post": {
                "summary": "Log Custom Policy Compliance Event",
                "tags": ["Enterprise Compliance & Audit"],
                "responses": {"200": {"description": "Event recorded"}}
            }
        },
        "/v1/mesh/peers": {
            "get": {
                "summary": "List Distributed CRDT Mesh Peers",
                "description": "Returns active cluster nodes and peer health states.",
                "tags": ["Distributed CRDT Mesh"],
                "responses": {"200": {"description": "Active peer topology"}}
            },
            "post": {
                "summary": "Register Peer Mesh Node",
                "tags": ["Distributed CRDT Mesh"],
                "responses": {"200": {"description": "Peer registered"}}
            }
        },
        "/v1/cache/purge": {
            "post": {
                "summary": "Purge Cache Fabric",
                "description": "Evicts in-memory L1 entries and SQLite snapshots with optional tenant scoping.",
                "tags": ["Cache Fabric Management"],
                "responses": {"200": {"description": "Purge summary"}}
            }
        },
        "/healthz": {
            "get": {
                "summary": "Liveness & Readiness Health Probe",
                "tags": ["System Health & Telemetry"],
                "responses": {"200": {"description": "Proxy operational"}}
            }
        },
        "/metrics": {
            "get": {
                "summary": "Prometheus Metrics Exporter",
                "tags": ["System Health & Telemetry"],
                "responses": {"200": {"description": "Prometheus text format metrics"}}
            }
        }
    }
}


def render_swagger_html() -> str:
    """Renders dark neo-brutalist Swagger UI page."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>OmniCache API Explorer — Interactive OpenAPI Documentation</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.11.0/swagger-ui.css" />
  <style>
    body {
      margin: 0;
      padding: 0;
      background: #030508;
      color: #e6edf3;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    .topbar { display: none !important; }
    .swagger-ui {
      filter: invert(88%) hue-rotate(180deg);
    }
    .swagger-ui .wrapper {
      max-width: 1200px;
      margin: 0 auto;
      padding: 20px;
    }
    .custom-header {
      background: #080c14;
      border-bottom: 2px solid #00f0ff;
      padding: 16px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .custom-header h1 {
      margin: 0;
      font-size: 16px;
      font-family: "JetBrains Mono", monospace;
      color: #00f0ff;
      letter-spacing: 0.5px;
    }
    .custom-header a {
      color: #39ff14;
      font-size: 12px;
      font-family: "JetBrains Mono", monospace;
      text-decoration: none;
      border: 1px solid #39ff14;
      padding: 4px 10px;
    }
  </style>
</head>
<body>
  <div class="custom-header">
    <h1>// OMNICACHE ENTERPRISE API SPECIFICATION (v3.1.0)</h1>
    <a href="/dashboard">← Back to Visual Dashboard</a>
  </div>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5.11.0/swagger-ui-bundle.js"></script>
  <script>
    window.onload = () => {
      window.ui = SwaggerUIBundle({
        url: '/openapi.json',
        dom_id: '#swagger-ui',
        deepLinking: true,
        presets: [
          SwaggerUIBundle.presets.apis,
          SwaggerUIBundle.SwaggerUIStandalonePreset
        ],
        layout: "BaseLayout"
      });
    };
  </script>
</body>
</html>
"""
