"""
Token Jitter SSE Stream Broadcaster and Replay Engine.
Provides smooth, natural streaming replay (~65 tokens/sec) for cached completions with <10ms TTFT.
"""

import asyncio
import json
import time
import re
from typing import AsyncGenerator, Dict, Any, List, Optional
from core.config import config

class StreamReplayer:
    @staticmethod
    def format_sse_chunk(chunk_dict: Dict[str, Any]) -> str:
        """Formats a chunk dictionary into standard OpenAI SSE format."""
        return f"data: {json.dumps(chunk_dict, separators=(',', ':'))}\n\n"

    @classmethod
    async def replay_cached_stream(
        cls,
        entry_payload: Dict[str, Any],
        stream_chunks: Optional[List[Dict[str, Any]]] = None,
        tokens_per_sec: float = 65.0
    ) -> AsyncGenerator[str, None]:
        """
        Replays cached completion as an OpenAI-compatible SSE stream.
        """
        delay_per_chunk = 1.0 / max(10.0, tokens_per_sec)

        # 1. If we have recorded raw stream chunks, replay them
        if stream_chunks and len(stream_chunks) > 0:
            for idx, chunk in enumerate(stream_chunks):
                yield cls.format_sse_chunk(chunk)
                if idx > 0 and delay_per_chunk > 0:
                    await asyncio.sleep(delay_per_chunk)
            yield "data: [DONE]\n\n"
            return

        # 2. Synthesize stream from non-stream response_payload
        choices = entry_payload.get("choices", [])
        if not choices:
            yield "data: [DONE]\n\n"
            return

        first_choice = choices[0]
        message = first_choice.get("message", {})
        full_content = message.get("content", "") or ""
        reasoning_content = message.get("reasoning_content", "") or ""
        tool_calls = message.get("tool_calls", None)
        model = entry_payload.get("model", "omnicache-model")
        req_id = entry_payload.get("id", f"chatcmpl-cached-{int(time.time()*1000)}")

        # A. Emit initial role chunk immediately (<10ms TTFT)
        initial_chunk = {
            "id": req_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant", "content": ""},
                "finish_reason": None
            }]
        }
        yield cls.format_sse_chunk(initial_chunk)

        # B. If reasoning tokens exist (e.g. o1/o3/thinking), stream reasoning first
        if reasoning_content:
            reasoning_words = re.findall(r"\S+|\s+", reasoning_content)
            for w in reasoning_words:
                r_chunk = {
                    "id": req_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"reasoning_content": w},
                        "finish_reason": None
                    }]
                }
                yield cls.format_sse_chunk(r_chunk)
                await asyncio.sleep(delay_per_chunk)

        # C. Stream main content tokens
        if full_content:
            content_tokens = re.findall(r"\S+\s*|\s+", full_content)
            for tok in content_tokens:
                c_chunk = {
                    "id": req_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": tok},
                        "finish_reason": None
                    }]
                }
                yield cls.format_sse_chunk(c_chunk)
                await asyncio.sleep(delay_per_chunk)

        # D. Stream tool calls if present
        if tool_calls:
            t_chunk = {
                "id": req_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"tool_calls": tool_calls},
                    "finish_reason": None
                }]
            }
            yield cls.format_sse_chunk(t_chunk)

        # E. Emit final finish chunk
        final_chunk = {
            "id": req_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": first_choice.get("finish_reason", "stop")
            }]
        }
        yield cls.format_sse_chunk(final_chunk)
        yield "data: [DONE]\n\n"
