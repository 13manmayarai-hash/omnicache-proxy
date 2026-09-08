# 🔒 Security Policy & Vulnerability Disclosure

## 1. Supported Versions
| Version | Supported | Status |
|:---|:---:|:---|
| 3.x.x | ✅ Yes | Active releases (FSL-1.1-MIT) |
| 2.x.x | ⚠️ Maintenance | Critical security fixes only |
| < 2.0.0 | ❌ No | End of life |

---

## 2. Reporting a Vulnerability
We take the security and integrity of OmniCache AI Proxy seriously. If you discover a security vulnerability or sensitive flaw:

* **GitHub Advisory (Recommended):** [Open a Private Vulnerability Report](https://github.com/13manmayarai-hash/omnicache-proxy/security/advisories/new) directly on GitHub.
* **Direct Contact:** Email Rajiv Prasad at `13manmayarai@gmail.com` with subject `[SECURITY] OmniCache Vulnerability Report`.
* **Response SLA:** We acknowledge all security reports within **24 hours** and aim to release a verified patch within **72 hours** for high or critical severity issues.
* **Responsible Disclosure:** Please do not publicly disclose vulnerabilities until a patch has been verified and published.

---

## 3. Data Protection & Security Architecture (Current Reality)

### Local Host Boundary & Network Transport
* **Default Loopback Binding:** OmniCache binds strictly to `127.0.0.1` (localhost) by default. It is not exposed to external network interfaces unless explicitly configured via `--host 0.0.0.0` or `OMNICACHE_HOST`.
* **In-Flight Transport:** When running locally on developer workstations, communication between coding agents and OmniCache uses local loopback HTTP. For distributed or remote network deployments, external traffic must be fronted by a TLS-terminating reverse proxy (e.g., Caddy, Nginx, Cloudflare, or AWS ALB) or configured with Uvicorn SSL certificates (`--ssl-certfile`, `--ssl-keyfile`).

### Storage at Rest
* **SQLite Snapshot Persistence:** Cache records, tool replay outputs, and vector indices are persisted locally to SQLite in WAL mode at `~/.omnicache/omnicache.db` (overrideable via `OMNICACHE_DB_PATH`).
* **Disk Encryption:** Data-at-rest protection relies on host operating-system disk encryption (e.g., Linux LUKS, macOS FileVault, Android File-Based Encryption, Windows BitLocker). OmniCache does not currently implement application-level envelope encryption.

### Upstream Credential Handling
* **Header Passthrough:** Upstream provider credentials (`Authorization: Bearer <key>` and `x-api-key`) provided by local coding tools (Claude Code, Cursor, Cline) are forwarded directly in-flight to upstream providers (Anthropic, OpenAI, Gemini). Provider API keys are **never** persisted to SQLite or written into cache tables.

### PII Redaction & Salted Tokenization
* **Privacy Shield:** When enabled, the `PrivacyShield` module performs deterministic regex-based scrubbing of sensitive patterns (SSNs, credit cards, emails, API keys, phone numbers) before requests leave the machine. Scrubbed values are replaced with HMAC-SHA256 salted tokens (`[REDACTED_<TYPE>_<HASH>]`) and rehydrated in-flight upon response arrival.
