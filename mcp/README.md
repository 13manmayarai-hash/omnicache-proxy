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

### 5. Remote HTTP / SSE Gateway

When the OmniCache daemon is running (`omnicache` or `omnicache start -p 8000`), the MCP endpoints are available over HTTP and Server-Sent Events (SSE):

- **JSON-RPC 2.0 HTTP Endpoint**: `POST http://localhost:8000/mcp` or `POST http://localhost:8000/v1/mcp`
- **SSE Stream Endpoint**: `GET http://localhost:8000/mcp` or `GET http://localhost:8000/v1/mcp`
- **Header Authentication**: `Authorization: Bearer <ADMIN_API_KEY>` (when `ADMIN_API_KEY` is configured)

---

## 🛠️ Provided Tools

| Tool | Purpose | Key Parameters |
| :--- | :--- | :--- |
| `omnicache_query` | Performs intent-gated semantic cache lookup (<1ms latency), returning cached answer if similarity exceeds threshold. | `prompt` (required), `model`, `org_id`, `threshold` |
| `omnicache_store` | Explicitly saves high-value answers, documentation, or code into the vector memory for future instant retrieval. | `prompt` (required), `answer` (required), `model`, `tag`, `org_id` |
| `omnicache_search` | Sub-millisecond vector similarity search across all cached prompts, solutions, and knowledge entries. | `query` (required), `org_id`, `top_k` |
| `omnicache_replay_tool` | Looks up cached deterministic tool execution outputs (`read_file`, `git_status`, `grep`) with workspace state validation. | `tool_name` (required), `arguments`, `workspace_fingerprint`, `workspace_state` |
| `omnicache_record_tool` | Records and caches tool outputs for subsequent instant 0ms replays. | `tool_name` (required), `output` (required), `arguments`, `ttl_seconds` |
| `omnicache_invalidate` | Invalidates cached knowledge by tag, tenant ID, or domain. | `tag`, `org_id` |
| `omnicache_stats` | Returns real-time telemetry: hit ratios, total queries, tokens saved, and cost saved in USD. | `org_id` |

---

## 🧪 Testing the MCP Server

You can verify the MCP server directly using stdio:

```bash
# Test initialization
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | omnicache mcp

# List all available tools
echo '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | omnicache mcp

# Check cache telemetry
echo '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"omnicache_stats","arguments":{}}}' | omnicache mcp
```
