# Quickstart Guide

Get OmniCache accelerating your AI coding workflow in under 30 seconds.

---

## 1. Install

```bash
pip install omnicache-proxy
```

---

## 2. Zero-Config Launch (`omnicache run`)

The fastest way to use OmniCache with Claude Code or any coding assistant:

```bash
# Automatically boots background proxy, injects ANTHROPIC_BASE_URL, and tracks session savings
omnicache run claude
```

You can wrap any AI agent, IDE, or script:
```bash
omnicache run cursor .
omnicache run python agent.py
```

When you exit your session, OmniCache summarizes total tokens saved, avoided costs, and replayed tool executions.

---

## 3. Manual Configuration

If you prefer running OmniCache as a standalone daemon:

### 1. Start the Daemon
```bash
omnicache
```
The proxy listens on `http://127.0.0.1:8000`.

### 2. Configure Your Tool

#### Claude Code (Terminal CLI)
```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:8000"
claude
```

#### Python (OpenAI SDK)
```python
from openai import OpenAI

client = OpenAI(
    api_key="your-api-key",
    base_url="http://127.0.0.1:8000/v1"
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Explain binary search in Python."}]
)
print(response.choices[0].message.content)
```

#### Cursor / VS Code / Windsurf
In your editor settings under **OpenAI Base URL**, enter:
```text
http://127.0.0.1:8000/v1
```

---

## 4. Verify Savings

View live metrics in your terminal or browser:
```bash
# Check stats in terminal
omnicache stats

# Run micro-benchmark
omnicache benchmark

# Open web dashboard in browser
open http://localhost:8000/dashboard
```
