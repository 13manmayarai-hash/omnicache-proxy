# Privacy Policy for OmniCache

**Effective Date:** September 13, 2026  
**Last Updated:** September 13, 2026  
**Maintainer:** Rajiv Prasad (<13manmayarai@gmail.com>)  
**Project:** OmniCache AI Proxy & MCP Server ([https://github.com/13manmayarai-hash/omnicache-proxy](https://github.com/13manmayarai-hash/omnicache-proxy))

---

## 1. Overview & Core Privacy Commitment

OmniCache is an open-source, local-first acceleration sidecar and Model Context Protocol (MCP) server for AI coding agents.

**Our Core Privacy Principle:**  
OmniCache is strictly an **in-process, local-first sidecar**. It operates on `127.0.0.1` by default and **does not phone home, collect telemetry, track usage analytics, or exfiltrate prompts, completions, or code to any third-party or proprietary server**.

---

## 2. What Data OmniCache Processes & Stores

When you use OmniCache as an AI proxy or MCP server, the software processes and persists the following operational data solely within your local environment:

### A. Semantic Cache & Vector Memory
* **User Prompts & Queries**: Cached to detect exact or semantically equivalent requests.
* **LLM Completions & Responses**: Stored locally to serve subsequent cache hits with 0ms latency and 0 API cost.
* **Embeddings**: In-memory and local vector representations generated using pure-Python quantized embeddings or lightweight embedding models.

### B. Deterministic Tool Execution Memory
* **Tool Names & Arguments**: e.g., `read_file`, `git_status`, `grep_search`.
* **Tool Outputs**: Execution outputs from idempotent operations (e.g. file contents or git status).
* **Git Fingerprints & Modification Times (`mtime`)**: Cryptographic SHA-256 hashes of workspace state used solely to invalidate cached outputs whenever underlying files change.

### C. Enterprise Audit Logs (Optional / Configurable)
* When audit logging is enabled (`OMNICACHE_AUDIT_LOG_PATH`), structured events recording tool names, execution duration (ms), tenant ID, and status are written locally to `~/.omnicache/mcp_audit.jsonl`.

---

## 3. Storage Location & Security

* **Local Storage Only:** All persistent entries are stored locally on your device in a SQLite WAL database (`~/.omnicache/omnicache.db`) or user-configured directory.
* **No Remote Telemetry:** OmniCache transmits **zero** operational telemetry or diagnostic payloads to OmniCache maintainers or external analytics platforms.
* **PrivacyShield (PII & Secret Sanitization):** OmniCache includes an integrated `PrivacyShield` engine that detects and masks sensitive items (such as API keys, OAuth session tokens, email addresses, and secret credentials) using salted HMAC-SHA256 tokenization before upstream forwarding or local caching.

---

## 4. Upstream Provider Credentials & Transmission

* OmniCache connects **only** to the upstream LLM providers you explicitly configure (e.g., Anthropic Claude, OpenAI, Google Gemini).
* Requests forwarded upstream carry **your own** credentials (API keys or OAuth bearer tokens). OmniCache never wraps, intercepts, or retains credentials outside of the active runtime process.

---

## 5. Data Retention & User Deletion Rights

You have complete, unconstrained control over all stored data at all times:

1. **Selective Invalidation via MCP:**
   Call the `omnicache_invalidate` MCP tool with a tag or tenant ID to instantly purge specific cache entries.
2. **Purge via CLI:**
   Run `omnicache sync --purge` or delete the local database directly.
3. **Complete Physical Deletion:**
   Deleting the `~/.omnicache/` directory permanently and irreversibly erases all cached vectors, tool signatures, and audit logs.

---

## 6. Open Source Verification

OmniCache is open-source under the FSL-1.1-MIT license. The entire codebase, network layer, and storage logic can be independently inspected, audited, and verified at [https://github.com/13manmayarai-hash/omnicache-proxy](https://github.com/13manmayarai-hash/omnicache-proxy).

---

## 7. Contact & Inquiries

For questions regarding this Privacy Policy or security inquiries:
* **Maintainer:** Rajiv Prasad
* **Email:** [13manmayarai@gmail.com](mailto:13manmayarai@gmail.com)
* **Security Advisories:** [GitHub Security Advisories](https://github.com/13manmayarai-hash/omnicache-proxy/security/advisories)
