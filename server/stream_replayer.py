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

    @classmethod
    async def replay_cached_anthropic_stream(
        cls,
        entry_payload: Dict[str, Any],
        stream_chunks: Optional[List[Dict[str, Any]]] = None,
        tokens_per_sec: float = 65.0
    ) -> AsyncGenerator[str, None]:
        """
        Replays cached completion as an Anthropic-compatible SSE stream.
        Emits: message_start, content_block_start, content_block_delta, content_block_stop, message_delta, message_stop.
        """
        delay_per_chunk = (1.0 / max(10.0, tokens_per_sec)) if tokens_per_sec > 0 else 0.0

        # If recorded raw stream chunks exist, replay them
        if stream_chunks and len(stream_chunks) > 0:
            for idx, chunk in enumerate(stream_chunks):
                if isinstance(chunk, str):
                    yield chunk
                else:
                    event_type = chunk.get("type", "message_delta")
                    yield f"event: {event_type}\ndata: {json.dumps(chunk, separators=(',', ':'))}\n\n"
                if idx > 0 and delay_per_chunk > 0:
                    await asyncio.sleep(delay_per_chunk)
            return

        msg_id = entry_payload.get("id", f"msg_cached_{int(time.time()*1000)}")
        model = entry_payload.get("model", "claude-3-5-sonnet-20241022")
        usage = entry_payload.get("usage", {"input_tokens": 10, "output_tokens": 10})
        content_blocks = entry_payload.get("content", [])

        # 1. Emit message_start
        msg_start = {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": usage.get("input_tokens", 10),
                    "output_tokens": 0
                }
            }
        }
        yield f"event: message_start\ndata: {json.dumps(msg_start, separators=(',', ':'))}\n\n"

        # 2. Iterate through content blocks
        if not content_blocks and "text" in entry_payload:
            content_blocks = [{"type": "text", "text": entry_payload["text"]}]

        total_output_tokens = 0
        for b_idx, block in enumerate(content_blocks):
            if isinstance(block, str):
                block = {"type": "text", "text": block}
            b_type = block.get("type", "text")

            if b_type == "text":
                text = block.get("text", "") or ""
                cb_start = {
                    "type": "content_block_start",
                    "index": b_idx,
                    "content_block": {"type": "text", "text": ""}
                }
                yield f"event: content_block_start\ndata: {json.dumps(cb_start, separators=(',', ':'))}\n\n"

                words = re.findall(r"\S+\s*|\s+", text) if text else [""]
                for w in words:
                    cb_delta = {
                        "type": "content_block_delta",
                        "index": b_idx,
                        "delta": {"type": "text_delta", "text": w}
                    }
                    yield f"event: content_block_delta\ndata: {json.dumps(cb_delta, separators=(',', ':'))}\n\n"
                    total_output_tokens += 1
                    if delay_per_chunk > 0:
                        await asyncio.sleep(delay_per_chunk)

                cb_stop = {"type": "content_block_stop", "index": b_idx}
                yield f"event: content_block_stop\ndata: {json.dumps(cb_stop, separators=(',', ':'))}\n\n"

            elif b_type == "tool_use":
                cb_start = {
                    "type": "content_block_start",
                    "index": b_idx,
                    "content_block": {
                        "type": "tool_use",
                        "id": block.get("id", f"toolu_cached_{b_idx}"),
                        "name": block.get("name", "tool"),
                        "input": {}
                    }
                }
                yield f"event: content_block_start\ndata: {json.dumps(cb_start, separators=(',', ':'))}\n\n"

                input_json = json.dumps(block.get("input", {}))
                cb_delta = {
                    "type": "content_block_delta",
                    "index": b_idx,
                    "delta": {"type": "input_json_delta", "partial_json": input_json}
                }
                yield f"event: content_block_delta\ndata: {json.dumps(cb_delta, separators=(',', ':'))}\n\n"

                cb_stop = {"type": "content_block_stop", "index": b_idx}
                yield f"event: content_block_stop\ndata: {json.dumps(cb_stop, separators=(',', ':'))}\n\n"

        # 3. Emit message_delta
        stop_reason = entry_payload.get("stop_reason", "end_turn") or "end_turn"
        msg_delta = {
            "type": "message_delta",
            "delta": {
                "stop_reason": stop_reason,
                "stop_sequence": entry_payload.get("stop_sequence", None)
            },
            "usage": {
                "output_tokens": usage.get("output_tokens", total_output_tokens or 10)
            }
        }
        yield f"event: message_delta\ndata: {json.dumps(msg_delta, separators=(',', ':'))}\n\n"

        # 4. Emit message_stop
        msg_stop = {"type": "message_stop"}
        yield f"event: message_stop\ndata: {json.dumps(msg_stop, separators=(',', ':'))}\n\n"

