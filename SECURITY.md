# 🔒 Security Policy & Vulnerability Disclosure

## 1. Supported Versions
| Version | Supported |
|:---|:---:|
| 2.x.x | ✅ Yes |
| 1.x.x | ⚠️ Critical Fixes Only |

## 2. Reporting a Vulnerability
We take the security of OmniCache AI Proxy and its downstream enterprise deployments seriously. If you discover a security vulnerability:
* **Email:** `security@omnicache.ai` (or open a private vulnerability report on GitHub).
* **Response SLA:** We acknowledge all security reports within **24 hours** and provide a patch within **72 hours** for high/critical vulnerabilities.
* **Responsible Disclosure:** Please do not publicly disclose vulnerabilities until our team has released a patch.

## 3. Cryptographic Standards & Data Protection
* **In-Flight Encryption:** TLS 1.3 enforced on all external endpoints.
* **At-Rest Persistence:** SQLite snapshots support AES-256-GCM envelope encryption.
* **Zero-Knowledge Privacy:** PII/PHI scrubbing strips sensitive tokens before sending to upstream LLM APIs.
