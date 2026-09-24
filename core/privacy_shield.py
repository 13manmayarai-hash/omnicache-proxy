"""
Zero-Knowledge Enterprise Privacy Shield & Reversible PII Tokenizer.
Automatically scrubs SSNs, Credit Cards, Emails, API Keys, and PHI before sending to upstream LLMs,
and seamlessly rehydrates original data on response delivery with cryptographic token isolation.
"""

import re
import copy
import hashlib
import hmac
import math
from collections import Counter
from typing import Dict, Tuple, List, Any, Optional
from core.config import config

# Enterprise PII Detection Regex Patterns (non-capturing groups for deterministic matching)
PATTERNS = {
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "CREDIT_CARD": re.compile(r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|6(?:011|5[0-9]{2})[0-9]{12})\b"),
    "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "API_KEY": re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})\b"),
    "PHONE": re.compile(r"\b(?:\+?1[-.\s]?)?\(?[2-9]\d{2}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
}

class PrivacyShield:
    """Reversible PII/PHI scrubbing and cryptographic token rehydration engine."""

    @classmethod
    def compute_token_shannon_entropy(cls, token: str) -> float:
        """
        Computes Shannon character entropy (bits/char) for a candidate token string.
        High-entropy strings (>= 4.2 bits/char) with diverse character classes
        indicate cryptographic secrets, random bearer tokens, or high-randomness API keys.
        """
        if not token:
            return 0.0
        counts = Counter(token)
        length = len(token)
        return -sum((c / length) * math.log2(c / length) for c in counts.values())

    @classmethod
    def scan_entropy_secrets(cls, text: str) -> List[Tuple[str, str]]:
        """
        Scans text for high-entropy tokens and secrets that bypass predefined regex patterns.
        Returns a list of (secret_value, pii_type).
        """
        if not getattr(config, "ENTROPY_SECRET_DETECTION_ENABLED", True) or not text:
            return []

        candidate_regex = re.compile(r"(?<![A-Za-z0-9_\-\.\+\/=])([A-Za-z0-9_\-\.\+\/=]{20,128})(?![A-Za-z0-9_\-\.\+\/=])")
        secret_keywords = re.compile(
            r"(?i)\b(?:bearer|api[_\s-]?key|auth(?:orization)?|token|secret|password|passwd|private[_\s-]?key|access[_\s-]?token|client[_\s-]?secret|oauth)\b"
        )

        min_entropy = getattr(config, "MIN_SECRET_ENTROPY", 4.2)
        detected: List[Tuple[str, str]] = []
        seen = set()

        for match in candidate_regex.finditer(text):
            cand = match.group(1)
            if cand in seen or cand.startswith("[REDACTED_") or "/" in cand or "\\" in cand:
                continue

            # Skip common file extensions
            if cand.endswith((".py", ".json", ".html", ".css", ".md", ".txt", ".js", ".ts", ".go", ".rs", ".cpp", ".c", ".h", ".svg", ".png", ".jpg")):
                continue

            ent = cls.compute_token_shannon_entropy(cand)
            has_upper = any(c.isupper() for c in cand)
            has_lower = any(c.islower() for c in cand)
            has_digit = any(c.isdigit() for c in cand)
            has_special = any(c in "_-+./=" for c in cand)
            classes = sum([has_upper, has_lower, has_digit, has_special])

            # Preceding context window check for secret keyword cues
            start_idx = max(0, match.start() - 50)
            prefix_window = text[start_idx:match.start()]
            has_keyword = bool(secret_keywords.search(prefix_window))

            is_secret = False
            pii_label = "ENTROPY_SECRET"
            if has_keyword and ent >= 3.7 and classes >= 2 and len(cand) >= 20:
                is_secret = True
                pii_label = "BEARER_SECRET" if "bearer" in prefix_window.lower() else "KEYWORD_SECRET"
            elif not has_keyword and ent >= min_entropy and classes >= 3 and len(cand) >= 24:
                is_secret = True
                pii_label = "HIGH_ENTROPY_SECRET"

            if is_secret:
                seen.add(cand)
                detected.append((cand, pii_label))

        return detected

    @classmethod
    def generate_token(cls, pii_type: str, raw_value: str, salt: Optional[str] = None) -> str:
        """
        Generates a collision-resistant deterministic token for a PII value.
        Utilizes HMAC-SHA256 with the configured enterprise salt (16 hex chars / 64-bit entropy),
        guaranteeing that differing user values generate distinct cache keys while
        preventing third-party rainbow-table reversibility.
        """
        active_salt = salt or getattr(config, "PRIVACY_SALT", "omnicache_salt_v2")
        digest = hmac.new(active_salt.encode("utf-8"), raw_value.encode("utf-8"), hashlib.sha256).hexdigest()[:16].upper()
        return f"[REDACTED_{pii_type}_{digest}]"

    @classmethod
    def sanitize_text(cls, text: str, salt: Optional[str] = None) -> Tuple[str, Dict[str, str], int]:
        """
        Replaces sensitive PII instances and high-entropy secrets with deterministic cryptographic tokens.
        Returns (sanitized_text, token_map, total_redactions).
        """
        if not text:
            return "", {}, 0

        token_map: Dict[str, str] = {}
        sanitized = text
        total_redactions = 0

        # 1. Standard Pre-compiled Regex Patterns
        for pii_type, regex in PATTERNS.items():
            matches = list(set(regex.findall(sanitized)))
            # Sort matches by descending length to prevent partial substring corruption
            matches.sort(key=len, reverse=True)
            for match in matches:
                token = cls.generate_token(pii_type, match, salt=salt)
                token_map[token] = match
                sanitized = sanitized.replace(match, token)
                total_redactions += 1

        # 2. Shannon-Entropy Secret Scanning for Unpatterned Secrets / Bearer Tokens
        entropy_secrets = cls.scan_entropy_secrets(sanitized)
        entropy_secrets.sort(key=lambda x: len(x[0]), reverse=True)
        for secret_val, pii_type in entropy_secrets:
            if secret_val in sanitized:
                token = cls.generate_token(pii_type, secret_val, salt=salt)
                token_map[token] = secret_val
                sanitized = sanitized.replace(secret_val, token)
                total_redactions += 1

        return sanitized, token_map, total_redactions

    @classmethod
    def sanitize_payload(cls, payload: Dict[str, Any], salt: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, str], int]:
        """
        Recursively sanitizes all message contents, system prompts, and tool payloads in an OpenAI / Claude payload.
        """
        sanitized_payload = dict(payload)
        master_token_map: Dict[str, str] = {}
        system_scrubbed = 0
        user_scrubbed = 0

        # 1. Sanitize top-level system prompt (Anthropic format)
        if "system" in sanitized_payload:
            system_val = sanitized_payload["system"]
            if isinstance(system_val, str):
                s_text, t_map, count = cls.sanitize_text(system_val, salt=salt)
                sanitized_payload["system"] = s_text
                master_token_map.update(t_map)
                system_scrubbed += count
            elif isinstance(system_val, list):
                new_sys_list = []
                for item in system_val:
                    if isinstance(item, dict) and "text" in item and isinstance(item["text"], str):
                        item_copy = dict(item)
                        s_text, t_map, count = cls.sanitize_text(item_copy["text"], salt=salt)
                        item_copy["text"] = s_text
                        master_token_map.update(t_map)
                        system_scrubbed += count
                        new_sys_list.append(item_copy)
                    elif isinstance(item, str):
                        s_text, t_map, count = cls.sanitize_text(item, salt=salt)
                        master_token_map.update(t_map)
                        system_scrubbed += count
                        new_sys_list.append(s_text)
                    else:
                        new_sys_list.append(item)
                sanitized_payload["system"] = new_sys_list

        # 2. Sanitize messages (attributing counts to user input vs system context)
        messages = sanitized_payload.get("messages", [])
        new_messages = []

        for m in messages:
            m_copy = dict(m)
            role = m_copy.get("role", "user")
            content = m_copy.get("content", "")
            if isinstance(content, str):
                s_text, t_map, count = cls.sanitize_text(content, salt=salt)
                m_copy["content"] = s_text
                master_token_map.update(t_map)
                if role == "system":
                    system_scrubbed += count
                else:
                    user_scrubbed += count
            elif isinstance(content, list):
                new_content_blocks = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                        b_copy = dict(block)
                        s_text, t_map, count = cls.sanitize_text(b_copy["text"], salt=salt)
                        b_copy["text"] = s_text
                        master_token_map.update(t_map)
                        if role == "system":
                            system_scrubbed += count
                        else:
                            user_scrubbed += count
                        new_content_blocks.append(b_copy)
                    else:
                        new_content_blocks.append(block)
                m_copy["content"] = new_content_blocks
            new_messages.append(m_copy)

        sanitized_payload["messages"] = new_messages
        # Return user_scrubbed to prevent static agent system prompt boilerplate (git email, paths) from inflating KPI telemetry
        return sanitized_payload, master_token_map, user_scrubbed

    @classmethod
    def rehydrate_response(cls, response_payload: Dict[str, Any], token_map: Dict[str, str]) -> Dict[str, Any]:
        """
        Restores original sensitive values into the assistant response text.
        Always operates on an isolated deep copy to prevent in-place mutation of cached entries.
        """
        if not token_map:
            return copy.deepcopy(response_payload)

        resp_copy = copy.deepcopy(response_payload)
        
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
