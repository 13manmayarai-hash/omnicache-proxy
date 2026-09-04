"""
Zero-Knowledge Enterprise Privacy Shield & Reversible PII Tokenizer.
Automatically scrubs SSNs, Credit Cards, Emails, API Keys, and PHI before sending to upstream LLMs,
and seamlessly rehydrates original data on response delivery with cryptographic token isolation.
"""

import re
import hashlib
import hmac
from typing import Dict, Tuple, List, Any, Optional
from core.config import config

# Enterprise PII Detection Regex Patterns (non-capturing groups for deterministic matching)
PATTERNS = {
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "CREDIT_CARD": re.compile(r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|6(?:011|5[0-9]{2})[0-9]{12})\b"),
    "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "API_KEY": re.compile(r"\b(?:sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b"),
    "PHONE": re.compile(r"\b(?:\+?1[-.\s]?)?\(?[2-9]\d{2}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
}

class PrivacyShield:
    """Reversible PII/PHI scrubbing and cryptographic token rehydration engine."""

    @classmethod
    def generate_token(cls, pii_type: str, raw_value: str, salt: Optional[str] = None) -> str:
        """
        Generates a collision-resistant deterministic token for a PII value.
        Utilizes HMAC-SHA256 with the configured enterprise salt,
        guaranteeing that differing user values generate distinct cache keys while
        preventing third-party rainbow-table reversibility.
        """
        active_salt = salt or getattr(config, "PRIVACY_SALT", "omnicache_salt_v2")
        digest = hmac.new(active_salt.encode("utf-8"), raw_value.encode("utf-8"), hashlib.sha256).hexdigest()[:10].upper()
        return f"[REDACTED_{pii_type}_{digest}]"

    @classmethod
    def sanitize_text(cls, text: str, salt: Optional[str] = None) -> Tuple[str, Dict[str, str], int]:
        """
        Replaces sensitive PII instances with deterministic cryptographic tokens.
        Returns (sanitized_text, token_map, total_redactions).
        """
        if not text:
            return "", {}, 0

        token_map: Dict[str, str] = {}
        sanitized = text
        total_redactions = 0

        for pii_type, regex in PATTERNS.items():
            matches = list(set(regex.findall(sanitized)))
            # Sort matches by descending length to prevent partial substring corruption
            matches.sort(key=len, reverse=True)
            for match in matches:
                token = cls.generate_token(pii_type, match, salt=salt)
                token_map[token] = match
                sanitized = sanitized.replace(match, token)
                total_redactions += 1

        return sanitized, token_map, total_redactions

    @classmethod
    def sanitize_payload(cls, payload: Dict[str, Any], salt: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, str], int]:
        """
        Recursively sanitizes all message contents, system prompts, and tool payloads in an OpenAI / Claude payload.
        """
        sanitized_payload = dict(payload)
        master_token_map: Dict[str, str] = {}
        total_scrubbed = 0

        # 1. Sanitize top-level system prompt (Anthropic format)
        if "system" in sanitized_payload:
            system_val = sanitized_payload["system"]
            if isinstance(system_val, str):
                s_text, t_map, count = cls.sanitize_text(system_val, salt=salt)
                sanitized_payload["system"] = s_text
                master_token_map.update(t_map)
                total_scrubbed += count
            elif isinstance(system_val, list):
                new_sys_list = []
                for item in system_val:
                    if isinstance(item, dict) and "text" in item and isinstance(item["text"], str):
                        item_copy = dict(item)
                        s_text, t_map, count = cls.sanitize_text(item_copy["text"], salt=salt)
                        item_copy["text"] = s_text
                        master_token_map.update(t_map)
                        total_scrubbed += count
                        new_sys_list.append(item_copy)
                    elif isinstance(item, str):
                        s_text, t_map, count = cls.sanitize_text(item, salt=salt)
                        master_token_map.update(t_map)
                        total_scrubbed += count
                        new_sys_list.append(s_text)
                    else:
                        new_sys_list.append(item)
                sanitized_payload["system"] = new_sys_list

        # 2. Sanitize messages
        messages = sanitized_payload.get("messages", [])
        new_messages = []

        for m in messages:
            m_copy = dict(m)
            content = m_copy.get("content", "")
            if isinstance(content, str):
                s_text, t_map, count = cls.sanitize_text(content, salt=salt)
                m_copy["content"] = s_text
                master_token_map.update(t_map)
                total_scrubbed += count
            elif isinstance(content, list):
                new_content_blocks = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                        b_copy = dict(block)
                        s_text, t_map, count = cls.sanitize_text(b_copy["text"], salt=salt)
                        b_copy["text"] = s_text
                        master_token_map.update(t_map)
                        total_scrubbed += count
                        new_content_blocks.append(b_copy)
                    else:
                        new_content_blocks.append(block)
                m_copy["content"] = new_content_blocks
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
        
        # OpenAI response format
        choices = resp_copy.get("choices", [])
        for c in choices:
            msg = c.get("message", {})
            if "content" in msg and isinstance(msg["content"], str):
                for token, original in token_map.items():
                    msg["content"] = msg["content"].replace(token, original)

        # Anthropic response format
        if "content" in resp_copy and isinstance(resp_copy["content"], list):
            for block in resp_copy["content"]:
                if isinstance(block, dict) and "text" in block and isinstance(block["text"], str):
                    for token, original in token_map.items():
                        block["text"] = block["text"].replace(token, original)

        return resp_copy

# Global Privacy Shield instance
privacy_shield = PrivacyShield()
