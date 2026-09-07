# AI Coding Agent Integration Guide

OmniCache provides zero-config drop-in proxy acceleration, Git-aware tool replay, and context compaction for modern AI coding agents.

This guide provides step-by-step configurations for **Claude Code**, **Cursor IDE**, **Cline**, and **OpenHands**.

---

## ⚡ Quick Start: Automated Setup

OmniCache can automatically generate and write configuration files for all your agents in one command:

```bash
# Configure all detected agents (Claude Code, Cursor, Cline, OpenHands)
omnicache init

# Or preview configurations without writing to disk
omnicache init --show

# Target a specific agent
omnicache init --agent claude
omnicache init --agent cursor
omnicache init --agent cline
omnicache init --agent openhands
```

To verify your local agent acceleration pipeline across all protocols:
```bash
omnicache harness
```

---

## 1. Claude Code (Anthropic CLI)

OmniCache features an automated **Boilerplate Stripper** that normalizes dynamic timestamps, dates, and session metadata from Claude Code's system prompts, delivering **L1 Exact Hits (0 ms latency)** across recurring turns.

### Option A: Zero-Config Launcher (Recommended)
```bash
omnicache run claude
```
`omnicache run` automatically:
1. Boots the OmniCache daemon in the background (if not already running).
2. Injects `ANTHROPIC_BASE_URL="http://127.0.0.1:8000"`.
3. Pre-warms repository tool signatures.
4. Prints an ASCII session telemetry summary of saved tokens and avoided costs on exit.

### Option B: Global Shell Export
Add to your `~/.bashrc`, `~/.zshrc`, or shell profile:
```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:8000"
```
Run `claude` normally:
```bash
claude
```

### Option C: Model Context Protocol (MCP) Setup
To allow Claude Code to query and manage OmniCache via MCP tools, ensure `~/.claude.json` or `~/.claude/settings.json` contains:
```json
{
  "mcpServers": {
    "omnicache": {
      "command": "python",
      "args": ["-m", "mcp.server"],
      "env": {
        "OMNICACHE_PORT": "8000"
      }
    }
  }
}
```

---

## 2. Cursor IDE

Accelerate Cursor Composer, Agent, and Chat with L1 exact trie lookups and git-aware tool replay.

### Option A: Zero-Config Launcher
```bash
omnicache run cursor .
```

### Option B: Custom OpenAI Base URL
In Cursor:
1. Open **Cursor Settings** (`Ctrl+,` or `Cmd+,`).
2. Navigate to **Models** > **OpenAI API Key**.
3. Toggle **Override OpenAI Base URL** and enter:
   ```text
   http://127.0.0.1:8000/v1
   ```
4. Enter any string (e.g. `sk-omnicache` or your real key) into the API Key field.

### Option C: Cursor MCP Server
Create or update `.cursor/mcp.json` in your project root or `~/.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "omnicache": {
      "command": "python",
      "args": ["-m", "mcp.server"],
      "env": {
        "OMNICACHE_PORT": "8000"
      }
    }
  }
}
```

---

## 3. Cline (VS Code & Cursor Extension)

Cline is an autonomous coding agent extension for VS Code and Cursor.

### Option A: Custom Provider Setup
1. In Cline's extension panel, click the **Settings (Gear Icon)**.
2. Under **API Provider**, select **OpenAI Compatible** or **Anthropic**:
   - For **OpenAI Compatible**:
     - **Base URL**: `http://127.0.0.1:8000/v1`
     - **API Key**: Real API key or `test-key` (if upstream keys configured in proxy)
     - **Model ID**: `gpt-4o`, `claude-3-5-sonnet`, etc.
   - For **Anthropic**:
     - **Base URL**: `http://127.0.0.1:8000`
3. Click **Done**.

### Option B: Cline MCP Server Configuration
OmniCache installs into Cline's global settings:
- Linux: `~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`
- macOS: `~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`
- Project-level: `.vscode/cline_mcp_settings.json`

Configuration snippet:
```json
{
  "mcpServers": {
    "omnicache": {
      "command": "python",
      "args": ["-m", "mcp.server"],
      "env": {
        "OMNICACHE_PORT": "8000"
      },
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

---

## 4. OpenHands (Autonomous Software Agent)

OpenHands (formerly OpenDevin) can route all LLM requests through OmniCache to avoid redundant thinking loops and duplicate code scans.

### Option A: Local CLI (`config.toml`)
Create `config.toml` in your repository or `~/.openhands/config.toml`:
```toml
[llm]
model = "anthropic/claude-3-5-sonnet-20241022"
base_url = "http://127.0.0.1:8000"
api_key = "dummy"

# Or for OpenAI models:
# model = "openai/gpt-4o"
# base_url = "http://127.0.0.1:8000/v1"
```
Launch OpenHands:
```bash
omnicache run openhands
```

### Option B: Docker Container
When running OpenHands in Docker, forward requests to the host machine:
```bash
docker run -it \
    -e LLM_BASE_URL="http://host.docker.internal:8000/v1" \
    -e LLM_API_KEY="dummy" \
    -e LLM_MODEL="anthropic/claude-3-5-sonnet-20241022" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -p 3000:3000 \
    ghcr.io/all-hands-ai/openhands:latest
```

---

## 5. Other AI Assistants (Aider, Continue, Windsurf)

You can wrap any assistant with `omnicache run`:
```bash
# Aider CLI
omnicache run aider --model sonnet

# Python custom agent scripts
omnicache run python my_agent.py
```
Or source the helper script generated by `omnicache init`:
```bash
source ~/.omnicache/env.sh
```

---

## 6. Pre-Warming & Team Sync

Maximize cache hits before running any agent turn:

1. **Pre-warm workspace tool cache**:
   ```bash
   omnicache warm
   ```
   Pre-records repository file contents, hashes, and git tree state into the local SQLite store. The agent's very first tool queries (`view_file`, `cat`, `grep`) hit the cache in **~0.2 ms**.

2. **Export cache snapshot for team / CI**:
   ```bash
   omnicache sync export -o team-cache.json
   ```

3. **Import cache snapshot on a teammate's machine**:
   ```bash
   omnicache sync import -i team-cache.json
   ```

4. **Verify acceleration health**:
   ```bash
   omnicache harness
   ```
