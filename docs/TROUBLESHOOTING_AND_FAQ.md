# 🛠️ OmniCache AI Proxy: Troubleshooting, Diagnostics & FAQ ("Help Me" Guide)

---

## 🔍 Common Issues & Solutions

### 1. "My prompt is returning a Cache MISS when I expected a Cache HIT"
* **Check the Temperature:** If your request has `temperature >= 0.7`, OmniCache's **Dynamic Intent Gater** automatically bypasses the cache to preserve creative diversity. To enable caching, set `temperature: 0.0` or pass header `X-Cache-Threshold: 0.90`.
* **Check Code Keywords:** If your prompt contains code (`def `, `class `, `SELECT`), OmniCache enforces a strict `0.98` threshold to prevent logic bugs. Minor wording changes might drop the similarity to `0.95`.
* **Check Tenant ID:** Ensure both requests use the same `X-Org-Id` header (default is `"default"`).

---

### 2. "Why is Claude Code / Cursor not routing through OmniCache?"
* Make sure you exported the environment variable in your active terminal:
  ```bash
  export ANTHROPIC_BASE_URL="http://localhost:8000/v1"
  export OPENAI_BASE_URL="http://localhost:8000/v1"
  ```
* Verify OmniCache is actively listening:
  ```bash
  curl http://localhost:8000/healthz
  ```

---

### 3. "How do I clear or invalidate cache entries?"
* **Purge entire tenant cache:**
  ```bash
  curl -X POST http://localhost:8000/v1/cache/purge -H "X-Org-Id: default"
  ```
* **Invalidate by domain tag:**
  ```bash
  curl -X POST http://localhost:8000/v1/cache/invalidate-tag \
    -H "Content-Type: application/json" \
    -d '{"tag": "docs-v2"}'
  ```

---

### 4. "How do I pass my real OpenAI / Anthropic API Key?"
Pass your key normally via standard SDK `api_key` or `Authorization` header:
```python
from openai import OpenAI
client = OpenAI(
    api_key="sk-proj-YOUR_ACTUAL_KEY",
    base_url="http://localhost:8000/v1"
)
```
OmniCache forwards your key securely to upstream providers only on cache misses.

---

### 5. "How does the Zero-Knowledge Privacy Shield work with HIPAA / GDPR?"
* OmniCache automatically sanitizes SSNs, credit cards, emails, and API keys before forwarding to third-party LLMs.
* The original sensitive values are temporarily held in local RAM and seamlessly restored into the response when returned to your application. Upstream LLMs never see or train on your PII.

---

### 6. "How do I run OmniCache in the background on server boot?"
* **Docker Compose:**
  ```bash
  docker-compose up -d
  ```
* **Systemd Service (Linux):**
  ```bash
  sudo cp deploy/omnicache.service /etc/systemd/system/
  sudo systemctl enable --now omnicache
  ```
