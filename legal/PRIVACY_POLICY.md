# 🛡️ OmniCache AI Proxy: Enterprise Privacy Policy & Zero-Knowledge Architecture

**Effective Date:** September 2, 2026  
**Compliance Standards:** HIPAA, GDPR, SOC2 Type II, California Consumer Privacy Act (CCPA)

---

## 1. Zero-Knowledge Architecture
OmniCache is designed from the ground up as a **Zero-Knowledge Infrastructure Gateway**:
* **No Plaintext Training Data:** Prompts and completions processed by OmniCache are never sold, shared, or used to train third-party foundation models.
* **On-Premise / Self-Hosted Privacy:** In self-hosted deployments, 100% of data remains entirely within the customer's private VPC / local server.

## 2. In-Flight PII / PHI Sanitization
The built-in **Zero-Knowledge Privacy Shield** automatically redacts Personally Identifiable Information (SSNs, credit cards, emails, medical IDs, and API secrets) using reversible tokenization before payloads are forwarded to external LLM providers.

## 3. Data Retention & Cache Purging
Customers maintain full sovereignty over cached data:
* **Immediate Invalidation:** Cache entries can be purged instantly via `/v1/cache/purge` or `/v1/cache/invalidate-tag`.
* **Configurable TTLs:** Granular time-to-live settings ensure automatic data expiration after predefined retention periods.
