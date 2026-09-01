"""
Multi-Provider Protocol Translator.
Normalizes OpenAI Chat format <-> Anthropic Messages API format and Gemini REST format.
"""

from typing import Dict, Any, List, Tuple, Optional
import json

class ProtocolTranslator:
    # Model fallback hierarchy
    FALLBACK_MAP = {
        "gpt-4o": ["claude-3-5-sonnet-20241022", "gemini-2.5-flash"],
        "gpt-4o-mini": ["gemini-2.5-flash", "claude-3-5-haiku-20241022"],
        "o1": ["claude-3-7-sonnet", "gemini-1.5-pro"],
        "o3-mini": ["gemini-2.5-flash", "claude-3-5-haiku-20241022"],
        "claude-3-5-sonnet": ["gpt-4o", "gemini-2.5-flash"],
        "gemini-2.5-flash": ["gpt-4o-mini", "claude-3-5-haiku-20241022"]
    }

    @staticmethod
    def openai_to_anthropic_payload(openai_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Translates standard OpenAI Chat Completion request into Anthropic Messages API format.
        """
        messages = openai_payload.get("messages", [])
        system_prompt = ""
        anthropic_messages = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            
            if role == "system":
                if system_prompt:
                    system_prompt += "\n" + str(content)
                else:
                    system_prompt = str(content)
            elif role in ("user", "assistant"):
                anthropic_messages.append({
                    "role": role,
                    "content": content
                })

        # Anthropic requires max_tokens
        max_tokens = openai_payload.get("max_tokens") or openai_payload.get("max_completion_tokens") or 4096

        anthropic_payload: Dict[str, Any] = {
            "model": openai_payload.get("model", "claude-3-5-sonnet-20241022"),
            "messages": anthropic_messages,
            "max_tokens": max_tokens
        }

        if system_prompt:
            anthropic_payload["system"] = system_prompt

        if "temperature" in openai_payload and openai_payload["temperature"] is not None:
            anthropic_payload["temperature"] = openai_payload["temperature"]

        # Tools translation if present
        if "tools" in openai_payload and openai_payload["tools"]:
            anthropic_tools = []
            for t in openai_payload["tools"]:
                if t.get("type") == "function":
                    fn = t.get("function", {})
                    anthropic_tools.append({
                        "name": fn.get("name", "function"),
                        "description": fn.get("description", ""),
                        "input_schema": fn.get("parameters", {"type": "object", "properties": {}})
                    })
            if anthropic_tools:
                anthropic_payload["tools"] = anthropic_tools

        return anthropic_payload

    @staticmethod
    def anthropic_to_openai_response(anthropic_res: Dict[str, Any], original_model: str) -> Dict[str, Any]:
        """
        Translates Anthropic Messages API response into standard OpenAI Chat Completion format.
        """
        content_text = ""
        tool_calls = []

        for block in anthropic_res.get("content", []):
            if block.get("type") == "text":
                content_text += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", "call_anthropic"),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {}))
                    }
                })

        usage = anthropic_res.get("usage", {})
        prompt_tokens = usage.get("input_tokens", 0)
        completion_tokens = usage.get("output_tokens", 0)

        message: Dict[str, Any] = {
            "role": "assistant",
            "content": content_text if content_text else None
        }
        if tool_calls:
            message["tool_calls"] = tool_calls

        stop_reason = anthropic_res.get("stop_reason", "stop")
        finish_reason = "tool_calls" if stop_reason == "tool_use" else "stop"

        return {
            "id": anthropic_res.get("id", "chatcmpl-from-anthropic"),
            "object": "chat.completion",
            "created": int(anthropic_res.get("created_at", 0)) or 1700000000,
            "model": original_model,
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": finish_reason
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens
            }
        }
