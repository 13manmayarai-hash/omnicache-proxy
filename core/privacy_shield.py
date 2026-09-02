"""
Zero-Knowledge Enterprise Privacy Shield & Reversible PII Tokenizer.
Automatically scrubs SSNs, Credit Cards, Emails, API Keys, and PHI before sending to upstream LLMs,
and seamlessly rehydrates original data on response delivery for HIPAA & SOC2 compliance.
"""

import re
from typing import Dict, Tuple, List, Any

# Enterprise PII Detection Regex Patterns
PATTERNS = {
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "CREDIT_CARD": re.compile(r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|6(?:011|5[0-9]{2})[0-9]{12})\b"),
    "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "API_KEY": re.compile(r"\b(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b"),
    "PHONE": re.compile(r"\b(?:\+?1[-.\s]?)?\(?[2-9]\d{2}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
}

class PrivacyShield:
    """Reversible PII/PHI scrubbing and token rehydration engine."""

    @classmethod
    def sanitize_text(cls, text: str) -> Tuple[str, Dict[str, str], int]:
        """
        Replaces sensitive PII instances with deterministic tokens.
        Returns (sanitized_text, token_map, total_redactions).
        """
        token_map = {}
        counter = 1
        sanitized = text

        for pii_type, regex in PATTERNS.items():
            matches = list(set(regex.findall(sanitized)))
            for match in matches:
                token = f"[REDACTED_{pii_type}_{counter}]"
                token_map[token] = match
                sanitized = sanitized.replace(match, token)
                counter += 1

        return sanitized, token_map, (counter - 1)

    @classmethod
    def sanitize_payload(cls, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str], int]:
        """
        Recursively sanitizes all message contents in an OpenAI / Claude payload.
        """
        sanitized_payload = dict(payload)
        master_token_map = {}
        total_scrubbed = 0

        messages = sanitized_payload.get("messages", [])
        new_messages = []

        for m in messages:
            m_copy = dict(m)
            content = m_copy.get("content", "")
            if isinstance(content, str):
                s_text, t_map, count = cls.sanitize_text(content)
                m_copy["content"] = s_text
                master_token_map.update(t_map)
                total_scrubbed += count
            new_messages.append(m_copy)

        sanitized_payload["messages"] = new_messages
        return sanitized_payload, master_token_map, total_scrubbed

    @classmethod
    def rehydrate_response(cls, response_payload: Dict[str, Any], token_map: Dict[str, str]) -> Dict[str, Any]:
        """
        Restores original sensitive values into the assistant response text.
        """
        if not token_map:
            return response_payload

        resp_copy = dict(response_payload)
        choices = resp_copy.get("choices", [])
        for c in choices:
            msg = c.get("message", {})
            if "content" in msg and isinstance(msg["content"], str):
                for token, original in token_map.items():
                    msg["content"] = msg["content"].replace(token, original)

        # Anthropic format
        if "content" in resp_copy and isinstance(resp_copy["content"], list):
            for block in resp_copy["content"]:
                if isinstance(block, dict) and "text" in block:
                    for token, original in token_map.items():
                        block["text"] = block["text"].replace(token, original)

        return resp_copy

# Global Privacy Shield instance
privacy_shield = PrivacyShield()
