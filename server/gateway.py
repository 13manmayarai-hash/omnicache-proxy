"""
OmniCache AI Proxy - Advanced Enterprise Gateway with Universal Catch-All.
Guarantees 100% compatibility with all Claude Code versions and Anthropic SDKs.
"""

import time
import json
import os
import asyncio
from typing import Dict, Any, Optional
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse, HTMLResponse, Response
from starlette.routing import Route

from core.config import config, MODEL_PRICING
from core.hasher import RequestHasher
from core.vector_cache import cache_instance
from core.radix_tree import radix_tree
from core.vision_cache import vision_cache
from core.privacy_shield import privacy_shield
from server.tool_replayer import tool_cache
from server.cascade_router import cascade_router
from server.quotas import quota_manager
from server.singleflight import flight_bus
from server.stream_replayer import StreamReplayer
from server.upstream import upstream_client
from server.translator import ProtocolTranslator
from persistence.snapshot_store import snapshot_store

METRICS_LEDGER = {
    "total_savings_usd": 0.0,
    "total_tokens_used": 0,
    "total_tokens_saved": 0,
    "total_cached_prompt_tokens": 0,
    "total_cached_completion_tokens": 0,
    "arbitrage_savings_usd": 0.0,
    "privacy_scrubbed_count": 0,
    "agent_tool_hits": 0,
    "vision_cache_hits": 0
}

loaded_entries = snapshot_store.load_into_cache(cache_instance)
if loaded_entries > 0:
    print(f"📦 [OmniCache] Restored {loaded_entries} cached entries from SQLite snapshot.")


async def handle_models(request: Request) -> Response:
    model_list = [
        {"id": "claude-3-5-sonnet-20241022", "object": "model", "type": "model", "display_name": "Claude 3.5 Sonnet"},
        {"id": "claude-3-7-sonnet-20250219", "object": "model", "type": "model", "display_name": "Claude 3.7 Sonnet"},
        {"id": "claude-3-5-haiku-20241022", "object": "model", "type": "model", "display_name": "Claude 3.5 Haiku"},
        {"id": "claude-sonnet-4-5-20250929", "object": "model", "type": "model", "display_name": "Claude Sonnet 4.5"},
        {"id": "claude-haiku-4-5-20251001", "object": "model", "type": "model", "display_name": "Claude Haiku 4.5"},
        {"id": "gpt-4o", "object": "model", "type": "model"},
        {"id": "gpt-4o-mini", "object": "model", "type": "model"},
        {"id": "gemini-2.5-flash", "object": "model", "type": "model"}
    ]
    return JSONResponse({
        "object": "list",
        "data": model_list,
        "models": model_list,
        "has_more": False,
        "first_id": model_list[0]["id"],
        "last_id": model_list[-1]["id"]
    }, headers={"Access-Control-Allow-Origin": "*"})


async def handle_chat_completions(request: Request) -> Response:
    if request.method == "OPTIONS":
        return Response(headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "*",
            "Access-Control-Allow-Headers": "*"
        })

    start_time = time.perf_counter()
    try:
        raw_payload: Dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}}, status_code=400)

    api_key_header = request.headers.get("authorization", "").replace("Bearer ", "").strip() or request.headers.get("x-api-key", "default")
    allowed, auth_reason, key_info = quota_manager.check_authorization(api_key_header)
    if not allowed:
        return JSONResponse({"error": {"message": auth_reason, "type": "quota_exceeded"}}, status_code=429)

    payload, pii_token_map, scrubbed_count = privacy_shield.sanitize_payload(raw_payload)
    if scrubbed_count > 0:
        METRICS_LEDGER["privacy_scrubbed_count"] += scrubbed_count

    headers = request.headers
    bypass_cache = headers.get("x-cache-bypass", "false").lower() in ("true", "1")
    allow_cascade = headers.get("x-allow-cascade", "true").lower() in ("true", "1")
    custom_ttl = int(headers.get("x-cache-ttl")) if headers.get("x-cache-ttl", "").isdigit() else None
    custom_threshold = float(headers.get("x-cache-threshold")) if headers.get("x-cache-threshold") else None
    cache_tag = headers.get("x-cache-tag", None)
    org_id = headers.get("x-org-id", "default")
    auth_header = headers.get("authorization", None)

    is_stream = bool(payload.get("stream", False))
    model = payload.get("model", "default")

    if not bypass_cache:
        status, entry, similarity = cache_instance.lookup(payload, org_id=org_id, custom_threshold=custom_threshold)
    else:
        status, entry, similarity = "BYPASS", None, 0.0

    if entry is not None and status in ("HIT_EXACT", "HIT_SEMANTIC"):
        latency_ms = (time.perf_counter() - start_time) * 1000
        usage = entry.response_payload.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 50)
        completion_tokens = usage.get("completion_tokens", 80)
        total_saved_tokens = prompt_tokens + completion_tokens
        savings = upstream_client.calculate_savings(model, prompt_tokens, completion_tokens)
        
        METRICS_LEDGER["total_savings_usd"] += savings
        METRICS_LEDGER["total_tokens_saved"] += total_saved_tokens

        resp_headers = {
            "X-Cache-Status": status,
            "X-Cache-Similarity": f"{similarity:.4f}",
            "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
            "X-Cost-Saved-USD": f"{savings:.6f}",
            "X-Tokens-Used": "0",
            "X-Tokens-Saved": str(total_saved_tokens),
            "Access-Control-Allow-Origin": "*"
        }

        rehydrated_response = privacy_shield.rehydrate_response(entry.response_payload, pii_token_map)

        if is_stream:
            return StreamingResponse(
                StreamReplayer.replay_cached_stream(rehydrated_response, stream_chunks=entry.stream_chunks, tokens_per_sec=config.STREAM_REPLAY_TOKENS_PER_SEC),
                media_type="text/event-stream",
                headers=resp_headers
            )
        else:
            return JSONResponse(rehydrated_response, headers=resp_headers)

    routed_model, route_tier, complexity = cascade_router.evaluate_route(model, payload, allow_cascade=allow_cascade)
    payload["model"] = routed_model

    if not is_stream:
        status_code, res_data, _ = await upstream_client.forward_non_stream(payload, auth_header=auth_header)
        latency_ms = (time.perf_counter() - start_time) * 1000
        if status_code == 200:
            saved_entry = cache_instance.store(payload=payload, response_payload=res_data, org_id=org_id, tag=cache_tag, custom_ttl=custom_ttl)
            snapshot_store.persist_entry(saved_entry)
            radix_tree.insert_conversation(payload.get("messages", []), res_data)
            
            usage = res_data.get("usage", {})
            tokens_used = usage.get("total_tokens", 100)
            METRICS_LEDGER["total_tokens_used"] += tokens_used

            rehydrated = privacy_shield.rehydrate_response(res_data, pii_token_map)
            return JSONResponse(rehydrated, headers={
                "X-Cache-Status": "MISS",
                "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
                "X-Tokens-Used": str(tokens_used),
                "X-Tokens-Saved": "0",
                "Access-Control-Allow-Origin": "*"
            })
        else:
            return JSONResponse(res_data, status_code=status_code)

    status_code, upstream_resp, err_data, _ = await upstream_client.forward_stream(payload, auth_header=auth_header)
    if status_code != 200 or upstream_resp is None:
        return JSONResponse(err_data or {"error": "Upstream error"}, status_code=status_code)

    async def stream_and_record():
        recorded_chunks = []
        full_content_parts = []
        try:
            async for raw_line in upstream_resp.aiter_lines():
                if not raw_line:
                    continue
                yield f"{raw_line}\n\n"
                if raw_line.startswith("data: ") and raw_line != "data: [DONE]":
                    try:
                        chunk_json = json.loads(raw_line[6:])
                        recorded_chunks.append(chunk_json)
                        choices = chunk_json.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            if "content" in delta and delta["content"]:
                                full_content_parts.append(delta["content"])
                    except Exception:
                        pass
        finally:
            await upstream_resp.aclose()
            if recorded_chunks:
                p_tok = len(str(payload.get("messages", "")).split())
                c_tok = len("".join(full_content_parts).split())
                METRICS_LEDGER["total_tokens_used"] += (p_tok + c_tok)
                synthesized = {
                    "id": f"chatcmpl-{int(time.time()*1000)}",
                    "object": "chat.completion",
                    "model": routed_model,
                    "choices": [{"message": {"role": "assistant", "content": "".join(full_content_parts)}}],
                    "usage": {"prompt_tokens": p_tok, "completion_tokens": c_tok, "total_tokens": p_tok + c_tok}
                }
                saved_entry = cache_instance.store(payload=payload, response_payload=synthesized, org_id=org_id, stream_chunks=recorded_chunks)
                snapshot_store.persist_entry(saved_entry)

    latency_ms = (time.perf_counter() - start_time) * 1000
    return StreamingResponse(stream_and_record(), media_type="text/event-stream", headers={
        "X-Cache-Status": "MISS",
        "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
        "Access-Control-Allow-Origin": "*"
    })


async def handle_anthropic_messages(request: Request) -> Response:
    """
    Anthropic Messages API Handler with full Streaming SSE support for Claude Code.
    """
    if request.method == "OPTIONS":
        return Response(headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "*",
            "Access-Control-Allow-Headers": "*"
        })
    elif request.method == "GET":
        return await handle_models(request)

    start_time = time.perf_counter()
    try:
        raw_payload: Dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid JSON"}}, status_code=400)

    anthropic_payload, pii_token_map, scrubbed_count = privacy_shield.sanitize_payload(raw_payload)
    if scrubbed_count > 0:
        METRICS_LEDGER["privacy_scrubbed_count"] += scrubbed_count

    headers = request.headers
    org_id = headers.get("x-org-id", "default")
    model = anthropic_payload.get("model", "claude-3-5-sonnet-20241022")
    is_stream = bool(anthropic_payload.get("stream", False))

    messages = []
    if "system" in anthropic_payload and anthropic_payload["system"]:
        messages.append({"role": "system", "content": anthropic_payload["system"]})
    for m in anthropic_payload.get("messages", []):
        content = m.get("content", "")
        if isinstance(content, list):
            text_blocks = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            content_str = " ".join(text_blocks)
        else:
            content_str = str(content)
        messages.append({"role": m.get("role", "user"), "content": content_str})

    normalized_payload = {
        "model": model,
        "messages": messages,
        "temperature": anthropic_payload.get("temperature", 0.0),
        "tools": anthropic_payload.get("tools", None)
    }

    status, entry, similarity = cache_instance.lookup(normalized_payload, org_id=org_id)

    # 1. Anthropic Cache HIT (Streaming or JSON)
    if entry is not None and status in ("HIT_EXACT", "HIT_SEMANTIC"):
        latency_ms = (time.perf_counter() - start_time) * 1000
        content = entry.response_payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = entry.response_payload.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 35)
        completion_tokens = usage.get("completion_tokens", 65)
        total_saved_tokens = prompt_tokens + completion_tokens
        savings = upstream_client.calculate_savings(model, prompt_tokens, completion_tokens)
        
        METRICS_LEDGER["total_savings_usd"] += savings
        METRICS_LEDGER["total_tokens_saved"] += total_saved_tokens

        resp_headers = {
            "X-Cache-Status": status,
            "X-Cache-Similarity": f"{similarity:.4f}",
            "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
            "X-Cost-Saved-USD": f"{savings:.6f}",
            "X-Tokens-Used": "0",
            "X-Tokens-Saved": str(total_saved_tokens),
            "Access-Control-Allow-Origin": "*"
        }

        if is_stream:
            async def stream_cached_anthropic():
                msg_id = f"msg_cached_{int(time.time()*1000)}"
                yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': {'id': msg_id, 'type': 'message', 'role': 'assistant', 'model': model, 'content': [], 'stop_reason': None, 'usage': {'input_tokens': prompt_tokens, 'output_tokens': 1}}})}\n\n"
                yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
                
                words = content.split(" ")
                for i, word in enumerate(words):
                    chunk_text = word + (" " if i < len(words) - 1 else "")
                    yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': chunk_text}})}\n\n"
                    await asyncio.sleep(0.008)

                yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
                yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': completion_tokens}})}\n\n"
                yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"

            return StreamingResponse(stream_cached_anthropic(), media_type="text/event-stream", headers=resp_headers)
        else:
            anthropic_response = {
                "id": f"msg_cached_{int(time.time()*1000)}",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [{"type": "text", "text": content}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": prompt_tokens, "output_tokens": completion_tokens}
            }
            rehydrated = privacy_shield.rehydrate_response(anthropic_response, pii_token_map)
            return JSONResponse(rehydrated, headers=resp_headers)

    # 2. Anthropic Cache MISS -> Forward Upstream
    if is_stream:
        status_code, stream_resp, err_data = await upstream_client.forward_anthropic_stream(anthropic_payload, incoming_headers=dict(request.headers))
        if status_code != 200 or stream_resp is None:
            return JSONResponse(err_data or {"error": "Upstream error"}, status_code=status_code)

        async def stream_and_record_anthropic():
            full_text_accum = []
            try:
                async for raw_line in stream_resp.aiter_lines():
                    if not raw_line:
                        continue
                    yield f"{raw_line}\n\n"
                    if raw_line.startswith("data: "):
                        try:
                            data_obj = json.loads(raw_line[6:])
                            if data_obj.get("type") == "content_block_delta":
                                delta_text = data_obj.get("delta", {}).get("text", "")
                                if delta_text:
                                    full_text_accum.append(delta_text)
                        except Exception:
                            pass
            finally:
                await stream_resp.aclose()
                if full_text_accum:
                    full_text = "".join(full_text_accum)
                    p_tok = len(str(messages).split())
                    c_tok = len(full_text.split())
                    METRICS_LEDGER["total_tokens_used"] += (p_tok + c_tok)
                    
                    cacheable_res_payload = {
                        "id": f"msg_{int(time.time()*1000)}",
                        "object": "chat.completion",
                        "choices": [{"message": {"role": "assistant", "content": full_text}}],
                        "usage": {"prompt_tokens": p_tok, "completion_tokens": c_tok, "total_tokens": p_tok + c_tok}
                    }
                    saved_entry = cache_instance.store(payload=normalized_payload, response_payload=cacheable_res_payload, org_id=org_id)
                    snapshot_store.persist_entry(saved_entry)

        latency_ms = (time.perf_counter() - start_time) * 1000
        return StreamingResponse(stream_and_record_anthropic(), media_type="text/event-stream", headers={
            "X-Cache-Status": "MISS",
            "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
            "Access-Control-Allow-Origin": "*"
        })

    # Non-streaming forward
    status_code, anthropic_res, _ = await upstream_client.forward_anthropic_messages(anthropic_payload, incoming_headers=dict(request.headers))
    latency_ms = (time.perf_counter() - start_time) * 1000
    if status_code == 200:
        usage = anthropic_res.get("usage", {})
        p_tok = usage.get("input_tokens", 35)
        c_tok = usage.get("output_tokens", 65)
        tokens_used = p_tok + c_tok
        METRICS_LEDGER["total_tokens_used"] += tokens_used

        content_blocks = anthropic_res.get("content", [])
        full_content = "\n".join([b.get("text", "") for b in content_blocks if isinstance(b, dict) and b.get("type") == "text"])

        cacheable_res_payload = {
            "id": anthropic_res.get("id", f"msg_{int(time.time()*1000)}"),
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": full_content}}],
            "usage": {"prompt_tokens": p_tok, "completion_tokens": c_tok, "total_tokens": tokens_used}
        }
        saved_entry = cache_instance.store(payload=normalized_payload, response_payload=cacheable_res_payload, org_id=org_id)
        snapshot_store.persist_entry(saved_entry)

        rehydrated = privacy_shield.rehydrate_response(anthropic_res, pii_token_map)
        return JSONResponse(rehydrated, headers={
            "X-Cache-Status": "MISS",
            "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
            "X-Tokens-Used": str(tokens_used),
            "X-Tokens-Saved": "0",
            "Access-Control-Allow-Origin": "*"
        })
    else:
        return JSONResponse(anthropic_res, status_code=status_code)


async def handle_anthropic_count_tokens(request: Request) -> Response:
    if request.method == "OPTIONS":
        return Response(headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "*",
            "Access-Control-Allow-Headers": "*"
        })
    try:
        body = await request.json()
        messages = body.get("messages", [])
        system = body.get("system", "")
        full_text = str(system) + " " + " ".join([str(m.get("content", "")) for m in messages])
        est_tokens = max(1, int(len(full_text.split()) * 1.3))
        return JSONResponse({"input_tokens": est_tokens}, headers={"Access-Control-Allow-Origin": "*"})
    except Exception:
        return JSONResponse({"input_tokens": 50}, headers={"Access-Control-Allow-Origin": "*"})


async def handle_catchall(request: Request) -> Response:
    """Universal fallback handler for any Claude Code auth/telemetry/meta routes."""
    if request.method == "OPTIONS":
        return Response(headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "*",
            "Access-Control-Allow-Headers": "*"
        })
    
    path = request.url.path
    if "models" in path:
        return await handle_models(request)
    elif "messages" in path:
        return await handle_anthropic_messages(request)
    elif "count_tokens" in path:
        return await handle_anthropic_count_tokens(request)
    
    # Return healthy 200 OK for any telemetry/auth pings
    return JSONResponse({
        "status": "ok",
        "authenticated": True,
        "path": path,
        "message": "OmniCache Universal Gateway OK"
    }, headers={"Access-Control-Allow-Origin": "*"})


async def handle_tool_replay(request: Request) -> Response:
    try:
        body = await request.json()
        tool_name = body.get("tool_name")
        arguments = body.get("arguments", {})
        env_fp = body.get("workspace_fingerprint", "default")
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    is_hit, cached_out, tool_key = tool_cache.lookup_tool_call(tool_name, arguments, env_fp)
    if is_hit:
        METRICS_LEDGER["agent_tool_hits"] += 1
        return JSONResponse({"cached": True, "output": cached_out, "key": tool_key})

    if "output" in body:
        tool_cache.store_tool_call(tool_name, arguments, body["output"], env_fp)
        return JSONResponse({"stored": True, "key": tool_key})

    return JSONResponse({"cached": False, "key": tool_key})


async def handle_quotas(request: Request) -> Response:
    return JSONResponse(quota_manager.get_all_quotas(), headers={"Access-Control-Allow-Origin": "*"})


async def handle_stats(request: Request) -> Response:
    org_id = request.headers.get("x-org-id", None)
    stats = cache_instance.get_stats(org_id)
    stats["financial_metrics"] = {
        "total_savings_usd": round(METRICS_LEDGER["total_savings_usd"], 4),
        "total_tokens_used": METRICS_LEDGER["total_tokens_used"],
        "total_tokens_saved": METRICS_LEDGER["total_tokens_saved"],
        "arbitrage_savings_usd": round(METRICS_LEDGER["arbitrage_savings_usd"], 4),
        "privacy_scrubbed_count": METRICS_LEDGER["privacy_scrubbed_count"],
        "agent_tool_hits": METRICS_LEDGER["agent_tool_hits"] + tool_cache.tool_hits,
        "vision_cache_hits": METRICS_LEDGER["vision_cache_hits"] + vision_cache.vision_hits
    }
    return JSONResponse(stats, headers={"Access-Control-Allow-Origin": "*"})


async def handle_health(request: Request) -> Response:
    return JSONResponse({"status": "healthy", "service": "omnicache-proxy", "version": "2.0.0"}, headers={"Access-Control-Allow-Origin": "*"})


async def handle_dashboard(request: Request) -> Response:
    cur_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(os.path.dirname(cur_dir), "dashboard", "index.html"),
        os.path.join(cur_dir, "dashboard", "index.html"),
        os.path.join(os.getcwd(), "dashboard", "index.html"),
        "/root/omnicache_proxy/dashboard/index.html"
    ]
    for html_path in candidates:
        if os.path.exists(html_path):
            try:
                with open(html_path, "r", encoding="utf-8") as f:
                    return HTMLResponse(f.read())
            except Exception:
                pass
    return HTMLResponse("<h1>OmniCache AI Proxy Active</h1><p>Visit /v1/cache/stats for metrics.</p>")


routes = [
    Route("/v1/models", handle_models, methods=["GET", "POST", "OPTIONS", "HEAD"]),
    Route("/models", handle_models, methods=["GET", "POST", "OPTIONS", "HEAD"]),
    Route("/v1/chat/completions", handle_chat_completions, methods=["POST", "GET", "OPTIONS", "HEAD"]),
    Route("/chat/completions", handle_chat_completions, methods=["POST", "GET", "OPTIONS", "HEAD"]),
    Route("/v1/messages", handle_anthropic_messages, methods=["POST", "GET", "OPTIONS", "HEAD"]),
    Route("/messages", handle_anthropic_messages, methods=["POST", "GET", "OPTIONS", "HEAD"]),
    Route("/v1/messages/count_tokens", handle_anthropic_count_tokens, methods=["POST", "GET", "OPTIONS", "HEAD"]),
    Route("/messages/count_tokens", handle_anthropic_count_tokens, methods=["POST", "GET", "OPTIONS", "HEAD"]),
    Route("/v1/agent/tool-replay", handle_tool_replay, methods=["POST", "OPTIONS"]),
    Route("/v1/enterprise/quotas", handle_quotas, methods=["GET", "OPTIONS"]),
    Route("/v1/cache/stats", handle_stats, methods=["GET", "OPTIONS"]),
    Route("/healthz", handle_health, methods=["GET", "OPTIONS"]),
    Route("/dashboard", handle_dashboard, methods=["GET", "OPTIONS"]),
    Route("/", handle_dashboard, methods=["GET", "OPTIONS"]),
    Route("/{path:path}", handle_catchall, methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
]

app = Starlette(routes=routes)
