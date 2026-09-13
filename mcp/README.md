# OmniCache Model Context Protocol (MCP) Server

OmniCache provides a native **Model Context Protocol (MCP)** server enabling AI coding assistants, IDEs, and autonomous agents (Claude Desktop, Claude Code, Cursor, Windsurf, Cline, Antigravity, OpenHands) to interface directly with OmniCache's sub-millisecond semantic vector memory, deterministic tool-call replayer, and cost telemetry.

---

## 🚀 Quick Setup

### 1. Claude Desktop (`claude_desktop_config.json`)

Add the following to:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "omnicache": {
      "command": "omnicache",
      "args": ["mcp"]
    }
  }
}
```

*Direct Python alternative:*
```json
{
  "mcpServers": {
    "omnicache": {
      "command": "python3",
      "args": ["-m", "mcp.server"],
      "cwd": "/path/to/omnicache_proxy"
    }
  }
}
```

---

### 2. Cursor IDE (`.cursor/mcp.json`)

Add to your project root or user config (`~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "omnicache": {
      "command": "omnicache",
      "args": ["mcp"]
    }
  }
}
```

---

### 3. Claude Code (`~/.claude.json`)

Configure Claude Code via command line or config file:

```bash
claude mcp add omnicache -- omnicache mcp
```

Or in `~/.claude.json`:
```json
{
  "mcpServers": {
    "omnicache": {
      "command": "omnicache",
      "args": ["mcp"]
    }
  }
}
```

---

### 4. Antigravity (`~/.gemini/config/mcp_config.json`)

```json
{
  "mcpServers": {
    "omnicache": {
      "command": "omnicache",
      "args": ["mcp"]
    }
  }
}
```

---

### 5. Streamable HTTP & Remote SSE Gateway (Anthropic Connectors Compliant)

When the OmniCache daemon is running (`omnicache` or `omnicache start -p 8000`), the MCP endpoint fully supports the standard Streamable HTTP transport:

- **Streamable HTTP / SSE Endpoint**: `GET /mcp` or `GET /v1/mcp` with `Accept: text/event-stream` initiates a persistent Server-Sent Events channel with automatic `Mcp-Session-Id` generation and keepalives.
- **JSON-RPC 2.0 Endpoint**: `POST /mcp` or `POST /v1/mcp` accepts standard MCP JSON-RPC 2.0 payloads with `Mcp-Session-Id` and `MCP-Protocol-Version: 2024-11-05`.
- **OAuth 2.0 Discovery**: Endpoints `/.well-known/oauth-authorization-server` (RFC 8414) and `/.well-known/oauth-protected-resource` (RFC 9728) provide automatic connector discovery.
- **Session Lifecycle**: Send `DELETE /mcp` with `Mcp-Session-Id` header to close an active session.
- **Authentication**: Supports standard OAuth Bearer tokens via `/oauth/token` or tenant API keys.

---

## 🛠️ Provided Tools & Behavioral Annotations

| Tool | Purpose | Annotations | Key Parameters |
| :--- | :--- | :--- | :--- |
| `omnicache_query` | Performs intent-gated semantic cache lookup (<1ms latency), returning cached answer if similarity exceeds threshold. | `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true` | `prompt` (required), `model`, `org_id`, `threshold` |
| `omnicache_store` | Explicitly saves high-value answers, documentation, or code into vector memory with automatic PII sanitization. | `readOnlyHint: false`, `destructiveHint: false`, `idempotentHint: false` | `prompt` (required), `answer` (required), `model`, `tag`, `org_id` |
| `omnicache_search` | Sub-millisecond vector similarity search across all cached prompts, solutions, and knowledge entries. | `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true` | `query` (required), `org_id`, `top_k` |
| `omnicache_replay_tool` | Looks up cached deterministic tool execution outputs (`read_file`, `git_status`, `grep`) with workspace state validation. | `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true` | `tool_name` (required), `arguments`, `workspace_fingerprint`, `workspace_state` |
| `omnicache_record_tool` | Records and caches tool outputs with automated PII scrubbing for subsequent instant 0ms replays. | `readOnlyHint: false`, `destructiveHint: false`, `idempotentHint: false` | `tool_name` (required), `output` (required), `arguments`, `ttl_seconds` |
| `omnicache_invalidate` | Invalidates cached knowledge by tag, tenant ID, or domain. Purges SQLite snapshots irreversibly. | `readOnlyHint: false`, `destructiveHint: true`, `idempotentHint: false` | `tag`, `org_id` |
| `omnicache_stats` | Returns real-time telemetry: hit ratios, total queries, tokens saved, and cost saved in USD. | `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true` | `org_id` |
| `omnicache_health` | Performs enterprise readiness check: verifies SQLite persistence, vector memory, and tool replayer. | `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true` | None |

---

## 💬 Example Use Cases & Prompts

The following 3 prompts demonstrate core functionality via the MCP connector in Claude Desktop, Claude Code, or any MCP client:

### Example 1: Sub-Millisecond Semantic Query (Zero Token Cost)
* **User Prompt:** `"Check OmniCache to see if we have an optimized SQLite WAL connection pool implementation in Python."`
* **MCP Tool Invoked:** `omnicache_query`
* **Sample Payload:**
  ```json
  {
    "prompt": "How do I configure an optimized SQLite WAL connection pool with high concurrency in Python?",
    "model": "claude-3-5-sonnet-20241022",
    "threshold": 0.85
  }
  ```
* **Result:** Instant semantic cache hit returning the tested solution in <1ms without calling upstream LLM APIs.

### Example 2: Vector Semantic Search Across Knowledge Base
* **User Prompt:** `"Search our cached knowledge base for solutions related to OAuth 2.0 token expiration and refresh token rotation."`
* **MCP Tool Invoked:** `omnicache_search`
* **Sample Payload:**
  ```json
  {
    "query": "OAuth 2.0 authorization code grant refresh token rotation",
    "top_k": 3
  }
  ```
* **Result:** Returns top 3 closest semantic vectors scored by cosine similarity with prompt excerpts.

### Example 3: Storing Architectural Decisions with Automatic PII Scrubbing
* **User Prompt:** `"Store our standard API rate limiting configuration in OmniCache under the tag 'networking'."`
* **MCP Tool Invoked:** `omnicache_store`
* **Sample Payload:**
  ```json
  {
    "prompt": "What is our company's standard API rate limiting architecture?",
    "answer": "Use token bucket algorithm backed by Redis with atomic Lua scripts. Set default limits to 1000 requests/minute per tenant.",
    "tag": "networking",
    "model": "claude-3-5-sonnet-20241022"
  }
  ```
* **Result:** Content is scrubbed for PII via `PrivacyShield`, embedded with `FastSemanticEmbedder`, and persisted to local SQLite WAL snapshot storage.

---

## 🏢 Enterprise Multi-Tenancy & Audit Logging

- **Tenant Isolation**: Set the `OMNICACHE_ORG_ID` environment variable or pass `org_id` in tool calls to isolate cached vectors per project or team.
- **Audit Logging**: Structured JSONL audit events recording timestamp, tool name, tenant ID, duration, and status are written automatically to `~/.omnicache/mcp_audit.jsonl` (or configure a custom path via `OMNICACHE_AUDIT_LOG_PATH`).
- **Privacy & Security**: OmniCache persists all vectors strictly locally to SQLite (`~/.omnicache/omnicache.db`). Zero prompts, tool outputs, or code are sent to external telemetry. See [PRIVACY.md](../PRIVACY.md) and [SECURITY.md](../SECURITY.md).

---

## 📦 1-Click Install via Smithery

Install directly to Claude Desktop with one command:
```bash
npx -y @smithery/cli install omnicache --client claude
```

---

## 🧪 Testing the MCP Server

You can verify the MCP server directly using stdio:

```bash
# Test initialization
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | omnicache mcp

# List all available tools with annotations
echo '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | omnicache mcp

# Check cache telemetry
echo '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"omnicache_stats","arguments":{}}}' | omnicache mcp
```
