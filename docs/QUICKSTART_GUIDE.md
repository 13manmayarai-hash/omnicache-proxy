# Quickstart Guide

Get OmniCache running in under a minute.

---

## 1. Install & Run

```bash
pip install omnicache-proxy
omnicache
```

The proxy is now listening on `http://localhost:8000`.

---

## 2. Configure Your Tool

### Claude Code
```bash
export ANTHROPIC_BASE_URL="http://localhost:8000"
claude
```

### Python (OpenAI SDK)
```python
from openai import OpenAI

client = OpenAI(
    api_key="your-api-key",
    base_url="http://localhost:8000/v1"
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Explain binary search in Python."}]
)
print(response.choices[0].message.content)
```

### Cursor / Windsurf
In your editor settings under **OpenAI Base URL**, enter:
```text
http://localhost:8000/v1
```

---

## 3. Verify Savings

View live metrics in your terminal or browser:
```bash
# Check stats in terminal
omnicache stats

# Open web dashboard in browser
open http://localhost:8000/dashboard
```
