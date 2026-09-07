"""
OmniCache Voice & Telephony Agent Adapter.
Ultra-low-latency Speech-to-Text transcript normalizer, caller session metadata
canonicalizer, and multi-turn conversational compactor for AI calling agents
(Twilio Media Streams, LiveKit Agents, Daily Bots, Vapi, Retell, Pipecat).
"""

import re
import copy
from typing import Dict, Any, List, Optional, Tuple, Union
from core.config import config

# Speech filler words and hesitations that degrade LLM cache hit rates across calls
PURE_FILLERS_REGEX = re.compile(
    r"\b(uh|um|err|erm|ah|hmm|mhm|uh-huh)\b[,.]?",
    re.IGNORECASE
)

CONVERSATIONAL_TAGS_REGEX = re.compile(
    r"(?:^|[,\s])(you know|i mean|sort of|kind of)(?=[,\s.?!;:]|$)",
    re.IGNORECASE
)

# Bracketed / parenthesized Speech-to-Text acoustic annotations
ACOUSTIC_ANNOTATIONS_REGEX = re.compile(
    r"\[(pause|clears throat|background noise|laughter|inaudible|cough|sigh|applause)\]"
    r"|\((pause|clears throat|background noise|laughter|inaudible|cough|sigh)\)",
    re.IGNORECASE
)

# Syllable stuttering / false starts (e.g. "w-what" -> "what", "I-I" -> "I", "th-the" -> "the")
STUTTER_REGEX = re.compile(
    r"\b([a-zA-Z]{1,3})-(?:\1)+([a-zA-Z]*)\b",
    re.IGNORECASE
)

# High-entropy telephony identifiers that break prefix caching across unique phone calls
TWILIO_CALL_SID_REGEX = re.compile(r"\bCA[a-f0-9]{32}\b", re.IGNORECASE)
PHONE_NUMBER_REGEX = re.compile(
    r"\+?\[REDACTED_PHONE_[A-Fa-f0-9]+\]|(?:\+?1[-. ]?)?\(?([0-9]{3})\)?[-. ]?([0-9]{3})[-. ]?([0-9]{4})\b|\+[1-9][0-9]{9,14}\b"
)
ISO_TIMESTAMP_REGEX = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
UUID_REGEX = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
SESSION_TOKEN_REGEX = re.compile(
    r"\b(?:room|call|sess|stream)_[a-zA-Z0-9_-]{6,}\b"
)

# Common short voice confirmations
VOICE_CONFIRMATIONS = {
    "ok", "okay", "yeah", "yes", "yep", "sure", "got it", "right", "uh-huh",
    "sounds good", "alright", "all right", "understood"
}


class TelephonyFilter:
    """
    Real-Time Voice & Telephony Agent Adapter for low-latency conversational AI pipelines.
    Normalizes transcript jitter, masks dynamic caller identifiers, and compacts multi-turn
    phone dialogue to maximize cache hits and minimize Time-To-First-Token (TTFT).
    """

    def __init__(
        self,
        strip_fillers: Optional[bool] = None,
        canonicalize_metadata: Optional[bool] = None,
        max_active_turns: Optional[int] = None,
        fast_path: Optional[bool] = None
    ):
        self.strip_fillers = strip_fillers if strip_fillers is not None else getattr(config, "VOICE_STRIP_FILLERS", True)
        self.canonicalize_metadata = canonicalize_metadata if canonicalize_metadata is not None else getattr(config, "VOICE_CANONICALIZE_METADATA", True)
        self.max_active_turns = max_active_turns if max_active_turns is not None else getattr(config, "VOICE_MAX_ACTIVE_TURNS", 8)
        self.fast_path = fast_path if fast_path is not None else getattr(config, "VOICE_FAST_PATH", True)

    def normalize_transcript(self, text: str) -> Tuple[str, int]:
        """
        Cleans speech-to-text transcript variations and acoustic disfluencies.
        Returns (normalized_text, fillers_removed_count).
        """
        if not text or not isinstance(text, str):
            return text, 0

        fillers_removed = 0
        cleaned = text

        # 1. Strip STT acoustic tags: [pause], (laughter), etc.
        ann_matches = len(ACOUSTIC_ANNOTATIONS_REGEX.findall(cleaned))
        if ann_matches > 0:
            fillers_removed += ann_matches
            cleaned = ACOUSTIC_ANNOTATIONS_REGEX.sub("", cleaned)

        # 2. Collapse stuttered syllables ("w-w-what" -> "what", "I-I" -> "I")
        def _collapse_stutter(m: re.Match) -> str:
            nonlocal fillers_removed
            fillers_removed += 1
            root = m.group(1)
            suffix = m.group(2) or ""
            return root + suffix

        cleaned = STUTTER_REGEX.sub(_collapse_stutter, cleaned)

        if self.strip_fillers:
            # 3. Strip pure fillers: uh, um, err, erm, ah, hmm
            pure_matches = len(PURE_FILLERS_REGEX.findall(cleaned))
            if pure_matches > 0:
                fillers_removed += pure_matches
                cleaned = PURE_FILLERS_REGEX.sub("", cleaned)

            # 4. Strip conversational filler tags: "you know", "i mean", "sort of", "kind of"
            tag_matches = len(CONVERSATIONAL_TAGS_REGEX.findall(cleaned))
            if tag_matches > 0:
                fillers_removed += tag_matches
                cleaned = CONVERSATIONAL_TAGS_REGEX.sub("", cleaned)

        # 5. Clean punctuation artifacts: trailing hesitation dots "...", dashes, repeated spaces
        cleaned = re.sub(r"\s*—+\s*", " ", cleaned)
        cleaned = re.sub(r"\s*--+\s*", " ", cleaned)
        cleaned = re.sub(r"\s*\.{2,}\s*", ". ", cleaned)
        cleaned = re.sub(r"\s*,{2,}\s*", ", ", cleaned)
        cleaned = re.sub(r"\s*([,.?!;:])\s*([,.?!;:])+", r"\2", cleaned)
        cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
        cleaned = re.sub(r"^[,.?!;:\s]+", "", cleaned).strip()

        # Guard against stripping entire message down to nothing
        if not cleaned:
            # Fall back to original trimmed text if stripping deleted everything
            cleaned = text.strip()
            return cleaned, 0

        # Ensure sensible capitalization of first letter
        if cleaned and cleaned[0].islower():
            cleaned = cleaned[0].upper() + cleaned[1:]

        return cleaned, fillers_removed

    def canonicalize_telephony_metadata(self, text: str) -> Tuple[str, Dict[str, str], int]:
        """
        Masks high-entropy telephony identifiers in system prompts or session context
        (Twilio Call SIDs, phone numbers, timestamps, room UUIDs).
        Returns (canonical_text, metadata_map, replacements_count).
        """
        if not text or not isinstance(text, str) or not self.canonicalize_metadata:
            return text, {}, 0

        metadata_map: Dict[str, str] = {}
        replacements = 0
        canonical = text

        # 1. Twilio Call SID (CA + 32 hex chars)
        sids = TWILIO_CALL_SID_REGEX.findall(canonical)
        for sid in sids:
            replacements += 1
            metadata_map[f"<CALL_SID_{replacements}>"] = sid
            canonical = canonical.replace(sid, "<CALL_SID>")

        # 2. Phone Numbers
        phone_matches = PHONE_NUMBER_REGEX.finditer(canonical)
        found_phones = [m.group(0) for m in phone_matches]
        for phone in found_phones:
            replacements += 1
            metadata_map[f"<PHONE_NUM_{replacements}>"] = phone
            canonical = canonical.replace(phone, "<CALLER_PHONE>")

        # 3. ISO Timestamps
        ts_matches = ISO_TIMESTAMP_REGEX.findall(canonical)
        for ts in ts_matches:
            replacements += 1
            metadata_map[f"<TIMESTAMP_{replacements}>"] = ts
            canonical = canonical.replace(ts, "<TIMESTAMP>")

        # 4. UUIDs
        uuid_matches = UUID_REGEX.findall(canonical)
        for uid in uuid_matches:
            replacements += 1
            metadata_map[f"<SESSION_UUID_{replacements}>"] = uid
            canonical = canonical.replace(uid, "<SESSION_UUID>")

        # 5. LiveKit / Telephony Session & Room IDs
        room_matches = SESSION_TOKEN_REGEX.findall(canonical)
        for r_id in room_matches:
            replacements += 1
            metadata_map[f"<SESSION_ID_{replacements}>"] = r_id
            canonical = canonical.replace(r_id, "<SESSION_ID>")

        return canonical, metadata_map, replacements

    def compact_voice_history(
        self,
        messages: List[Dict[str, Any]],
        max_active_turns: Optional[int] = None
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Compacts historical turns in long-running phone calls.
        Preserves the latest active dialogue window and system instructions,
        while consolidating earlier repetitive single-word acknowledgments.
        Returns (compacted_messages, tokens_saved).
        """
        if not messages or not isinstance(messages, list):
            return messages, 0

        max_turns = max_active_turns or self.max_active_turns
        if len(messages) <= max_turns:
            return messages, 0

        compacted_msgs: List[Dict[str, Any]] = []
        tokens_saved = 0
        cutoff_idx = len(messages) - max_turns

        for idx, msg in enumerate(messages):
            if not isinstance(msg, dict):
                compacted_msgs.append(msg)
                continue

            # Leave recent conversational window intact
            if idx >= cutoff_idx or msg.get("role") in ("system", "developer"):
                compacted_msgs.append(copy.deepcopy(msg))
                continue

            role = msg.get("role")
            content = msg.get("content")

            # Check for historical user voice confirmations
            if role == "user" and isinstance(content, str):
                normalized = content.strip().lower().rstrip(".,!?")
                if normalized in VOICE_CONFIRMATIONS:
                    compact_content = "[Caller acknowledged]"
                    new_msg = copy.deepcopy(msg)
                    new_msg["content"] = compact_content
                    compacted_msgs.append(new_msg)
                    tokens_saved += 1
                    continue

            # Bulky historical assistant turns (> 80 chars) prior to cutoff
            if role == "assistant" and isinstance(content, str) and len(content) > 80:
                # Keep first sentence for dialogue grounding, compact remainder
                sentences = re.split(r"(?<=[.?!])\s+", content)
                if len(sentences) > 1:
                    lead = sentences[0]
                    compact_content = f"{lead} [Historical dialogue summarized for voice latency]"
                    orig_tokens = int(len(content.split()) * 1.3)
                    new_tokens = int(len(compact_content.split()) * 1.3)
                    saved = max(1, orig_tokens - new_tokens)
                    tokens_saved += saved
                    new_msg = copy.deepcopy(msg)
                    new_msg["content"] = compact_content
                    compacted_msgs.append(new_msg)
                    continue

            compacted_msgs.append(copy.deepcopy(msg))

        return compacted_msgs, tokens_saved

    def match_fast_path_intent(self, user_text: str, conversation_history: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
        """
        Fast-path sub-millisecond intent matcher for standard conversational telephony checks.
        Instantly replies to line health checks ('can you hear me?'), hold requests ('hold on'),
        or clarification queries without waiting for upstream LLM.
        """
        if not self.fast_path or not user_text:
            return None

        clean = user_text.lower().strip().rstrip("?.!,")
        # Strip politeness fillers ("please", "pls")
        clean = re.sub(r"\b(please|pls)\b", "", clean).strip()
        clean = re.sub(r"\s+", " ", clean).strip()

        # 1. Line check / latency test
        if any(clean.startswith(p) for p in ("are you there", "can you hear me", "are you still there", "is anyone there")) or clean == "hello":
            return "Yes, I can hear you clearly. How can I assist you today?"

        # 2. Hold / Pause requests
        if any(clean.startswith(p) for p in ("hold on", "wait a minute", "just a minute", "one second", "give me a second", "wait a second", "just a second")):
            return "Take your time, I'm right here whenever you're ready."

        # 3. Repeat requests ("repeat that please", "what did you say")
        if any(clean.startswith(p) for p in ("repeat that", "what did you say", "could you repeat that", "pardon", "what was that", "say that again")):
            if conversation_history:
                # Find last assistant message
                for msg in reversed(conversation_history):
                    if isinstance(msg, dict) and msg.get("role") == "assistant":
                        c = msg.get("content")
                        if isinstance(c, str) and c.strip() and not c.startswith("["):
                            return f"I said: {c}"
                        elif isinstance(c, list):
                            for blk in c:
                                if isinstance(blk, dict) and blk.get("type") == "text":
                                    return f"I said: {blk.get('text', '')}"
            return "I apologize, could you please tell me what you'd like me to review?"

        return None

    def process_telephony_payload(
        self,
        payload: Dict[str, Any],
        is_voice_mode: bool = False
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Primary gateway pipeline method for voice / telephony requests.
        Handles both OpenAI (/v1/chat/completions) and Anthropic (/v1/messages) payloads.
        Returns (modified_payload, stats_dict).
        """
        stats = {
            "fillers_removed": 0,
            "metadata_canonicalized": 0,
            "turns_compacted": 0,
            "tokens_saved": 0,
            "is_voice_mode": is_voice_mode,
            "fast_path_reply": None
        }

        if not is_voice_mode and not getattr(config, "VOICE_ADAPTER_ENABLED", True):
            return payload, stats

        new_payload = copy.deepcopy(payload)

        # 1. Process System Prompt (OpenAI: system message, Anthropic: system key)
        system_text = new_payload.get("system")
        if isinstance(system_text, str) and system_text:
            canon_sys, _, canon_cnt = self.canonicalize_telephony_metadata(system_text)
            if canon_cnt > 0:
                new_payload["system"] = canon_sys
                stats["metadata_canonicalized"] += canon_cnt
        elif isinstance(system_text, list):
            # Anthropic multi-block system prompt
            new_sys_list = []
            for blk in system_text:
                if isinstance(blk, dict) and blk.get("type") == "text" and isinstance(blk.get("text"), str):
                    canon_txt, _, canon_cnt = self.canonicalize_telephony_metadata(blk["text"])
                    if canon_cnt > 0:
                        stats["metadata_canonicalized"] += canon_cnt
                        new_blk = dict(blk)
                        new_blk["text"] = canon_txt
                        new_sys_list.append(new_blk)
                    else:
                        new_sys_list.append(blk)
                else:
                    new_sys_list.append(blk)
            new_payload["system"] = new_sys_list

        messages = new_payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return new_payload, stats

        # 2. Canonicalize metadata in system/developer messages within messages array
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") in ("system", "developer"):
                c = msg.get("content")
                if isinstance(c, str):
                    canon_c, _, canon_cnt = self.canonicalize_telephony_metadata(c)
                    if canon_cnt > 0:
                        msg["content"] = canon_c
                        stats["metadata_canonicalized"] += canon_cnt

        # 3. Compact historical turns if conversation exceeds active window
        compacted_msgs, turns_saved = self.compact_voice_history(messages)
        if turns_saved > 0:
            new_payload["messages"] = compacted_msgs
            stats["tokens_saved"] += turns_saved
            stats["turns_compacted"] = 1
            messages = compacted_msgs

        # 4. Normalize Speech-to-Text transcript in user messages
        last_user_text = None
        for msg in messages:
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue

            content = msg.get("content")
            # OpenAI / standard text string
            if isinstance(content, str):
                cleaned, removed = self.normalize_transcript(content)
                msg["content"] = cleaned
                if removed > 0:
                    stats["fillers_removed"] += removed
                    # Estimate tokens saved from stripped filler words
                    tokens_saved_estimate = max(1, int(removed * 1.2))
                    stats["tokens_saved"] += tokens_saved_estimate
                last_user_text = cleaned or content

            # Anthropic content blocks: [{"type": "text", "text": "..."}]
            elif isinstance(content, list):
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        txt = blk.get("text", "")
                        cleaned, removed = self.normalize_transcript(txt)
                        blk["text"] = cleaned
                        if removed > 0:
                            stats["fillers_removed"] += removed
                            tokens_saved_estimate = max(1, int(removed * 1.2))
                            stats["tokens_saved"] += tokens_saved_estimate
                        last_user_text = cleaned or txt

        # 5. Fast-Path Intent Check on the final user utterance
        if last_user_text and self.fast_path:
            fp_reply = self.match_fast_path_intent(last_user_text, messages[:-1])
            if fp_reply:
                stats["fast_path_reply"] = fp_reply

        return new_payload, stats


# Global Singleton Filter Instance
telephony_filter = TelephonyFilter()
