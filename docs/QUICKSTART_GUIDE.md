# 🚀 OmniCache AI Proxy 2.0: Developer Quickstart Guide

---

## 1. 30-Second Local Setup

### Step 1: Clone & Run
```bash
git clone https://github.com/13manmayarai-hash/omnicache-proxy.git
cd omnicache-proxy
pip install starlette uvicorn httpx
python3 main.py
```
*Proxy runs at `http://localhost:8000`.*

---

## 2. Drop-in Integrations

### Python (OpenAI SDK):
```python
from openai import OpenAI
client = OpenAI(api_key="your-key", base_url="http://localhost:8000/v1")
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "How do I optimize SQL indexes?"}]
)
print(response.choices[0].message.content)
```

### Python (Anthropic SDK / Claude):
```python
from anthropic import Anthropic
client = Anthropic(api_key="your-key", base_url="http://localhost:8000")
response = client.messages.create(
    model="claude-3-5-sonnet-20241022",
    messages=[{"role": "user", "content": "Explain binary search trees"}],
    max_tokens=1024
)
print(response.content[0].text)
```

### Claude Code CLI:
```bash
export ANTHROPIC_BASE_URL="http://localhost:8000/v1"
claude
```

### LangChain:
```python
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(model="gpt-4o", openai_api_base="http://localhost:8000/v1")
print(llm.invoke("Hello world!").content)
```

---

## 3. Telemetry & Web Dashboard
Open your browser at **`http://localhost:8000/dashboard`** to inspect live cost savings, token accounting, and privacy redactions in real-time.
