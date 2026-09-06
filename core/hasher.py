"""
Composite hashing and prompt extraction utilities.
Ensures zero collisions between differing JSON schemas, system prompts, or tool definitions.
Also includes optional PII redaction utilities for enterprise privacy compliance.
"""

import hashlib
import json
import re
from typing import Dict, Any, Tuple, Optional, List

class RequestHasher:
    # Common PII Regex Patterns
    SSN_PATTERN = r"\b\d{3}-\d{2}-\d{4}\b"
    CREDIT_CARD_PATTERN = r"\b(?:\d{4}[-\s]?){3}\d{4}\b"
    EMAIL_PATTERN = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"

    @classmethod
    def redact_pii(cls, text: str) -> str:
        """
        Anonymizes sensitive tokens before hashing or embedding.
        """
        if not text:
            return ""
        text = re.sub(cls.SSN_PATTERN, "[REDACTED_SSN]", text)
        text = re.sub(cls.CREDIT_CARD_PATTERN, "[REDACTED_CC]", text)
        text = re.sub(cls.EMAIL_PATTERN, "[REDACTED_EMAIL]", text)
        return text

    @classmethod
    def normalize_system_prompt(cls, text: str) -> str:
        """
        Normalizes dynamic timestamps, dates, and session UUIDs from agent system prompts
        so that deterministic cache hashing remains stable across turns and sessions.
        """
        if not text:
            return ""
        # 1. ISO 8601 & RFC 3339 timestamps (e.g. 2026-09-05T17:15:30Z or 2026-09-05 17:15:30)
        norm = re.sub(r'\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b', '<NORMALIZED_TIMESTAMP>', text)
        # 2. Human-readable dates with day-of-week (e.g. "Current date: Saturday, September 5, 2026" or "Today is ...")
        norm = re.sub(r'(?i)(?:current\s+(?:date|time)|today(?:\'s\s+date)?):\s*[A-Za-z]+,\s*[A-Za-z]+\s+\d{1,2},?\s+\d{4}(?:[^\n]*)?', '<NORMALIZED_DATE>', norm)
        # 3. Formatted month-day-year dates (e.g. September 5, 2026)
        norm = re.sub(r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b', '<NORMALIZED_DATE>', norm)
        # 4. Standalone time strings (e.g. 05:17:30 PM UTC)
        norm = re.sub(r'\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|UTC|GMT)\b', '<NORMALIZED_TIME>', norm)
        return norm

    @classmethod
    def extract_system_and_user_prompts(cls, messages: List[Dict[str, Any]]) -> Tuple[str, str, bool]:
        """
        Extracts concatenated system prompt and last user prompt.
        Also returns a boolean indicating if multimodal/image content is detected.
        """
        system_parts = []
        user_parts = []
        is_multimodal = False
        
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            
            if isinstance(content, list):
                # Multimodal format: [{type: 'text', text: '...'}, {type: 'image_url', ...}]
                text_subparts = []
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            text_subparts.append(part.get("text", ""))
                        elif part.get("type") in ("image_url", "input_audio", "file"):
                            is_multimodal = True
                content_str = " ".join(text_subparts)
            else:
                content_str = str(content) if content is not None else ""
                
            if role == "system":
                system_parts.append(content_str)
            elif role == "user":
                user_parts.append(content_str)
                
        raw_system_prompt = "\n".join(system_parts).strip()
        system_prompt = cls.normalize_system_prompt(raw_system_prompt)
        last_user_prompt = user_parts[-1] if user_parts else ""
        return system_prompt, last_user_prompt, is_multimodal

    @classmethod
    def compute_exact_hash(cls, payload: Dict[str, Any], org_id: str = "default") -> str:
        """
        Computes a deterministic SHA-256 hash representing the exact request signature.
        Includes model, messages, temperature, response_format (schema), tools, and stop sequences.
        System prompts are automatically normalized to remove volatile timestamps/dates.
        """
        raw_messages = payload.get("messages", [])
        normalized_messages = []
        for msg in raw_messages:
            if isinstance(msg, dict) and msg.get("role") == "system":
                m_copy = dict(msg)
                content = m_copy.get("content", "")
                if isinstance(content, str):
                    m_copy["content"] = cls.normalize_system_prompt(content)
                normalized_messages.append(m_copy)
            else:
                normalized_messages.append(msg)

        normalized_data = {
            "org_id": org_id,
            "model": payload.get("model", "").strip().lower(),
            "messages": normalized_messages,
            "temperature": payload.get("temperature", 1.0),
            "response_format": payload.get("response_format", None),
            "tools": payload.get("tools", None),
            "tool_choice": payload.get("tool_choice", None),
            "stop": payload.get("stop", None)
        }
        
        # Serialize to deterministic JSON with sorted keys
        json_bytes = json.dumps(normalized_data, sort_keys=True, separators=(',', ':')).encode('utf-8')
        return hashlib.sha256(json_bytes).hexdigest()

    @classmethod
    def compute_schema_hash(cls, response_format: Optional[Dict[str, Any]]) -> str:
        """
        Computes deterministic hash for JSON Schema structured outputs.
        """
        if not response_format:
            return "no_schema"
        raw_bytes = json.dumps(response_format, sort_keys=True, separators=(',', ':')).encode('utf-8')
        return hashlib.sha256(raw_bytes).hexdigest()[:16]

    @classmethod
    def compute_tools_hash(cls, tools: Optional[List[Dict[str, Any]]]) -> str:
        """
        Computes deterministic hash for agent tool and function definitions.
        """
        if not tools:
            return "no_tools"
        raw_bytes = json.dumps(tools, sort_keys=True, separators=(',', ':')).encode('utf-8')
        return hashlib.sha256(raw_bytes).hexdigest()[:16]
