"""
Starlette ASGI Gateway for OmniCache Proxy.
Implements /v1/chat/completions with SingleFlight coalescing, Token Jitter SSE,
developer headers, cache invalidation, SQLite persistence, and real-time dashboard.
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
from server.singleflight import flight_bus
from server.stream_replayer import StreamReplayer
from server.upstream import upstream_client
from persistence.snapshot_store import snapshot_store

# Cumulative financial savings counter
METRICS_LEDGER = {
    "total_savings_usd": 0.0,
    "total_cached_prompt_tokens": 0,
    "total_cached_completion_tokens": 0
}

# Auto-load SQLite snapshot into RAM on import
loaded_entries = snapshot_store.load_into_cache(cache_instance)
if loaded_entries > 0:
    print(f"📦 [OmniCache] Restored {loaded_entries} cached entries from SQLite snapshot.")

async def handle_chat_completions(request: Request) -> Response:
    start_time = time.perf_counter()
    
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}}, status_code=400)

    # 1. Parse Developer Headers
    headers = request.headers
    bypass_cache = headers.get("x-cache-bypass", "false").lower() in ("true", "1")
    custom_ttl_str = headers.get("x-cache-ttl", None)
    custom_ttl = int(custom_ttl_str) if (custom_ttl_str and custom_ttl_str.isdigit()) else None
    custom_threshold_str = headers.get("x-cache-threshold", None)
    custom_threshold = float(custom_threshold_str) if custom_threshold_str else None
    cache_tag = headers.get("x-cache-tag", None)
    org_id = headers.get("x-org-id", "default")
    auth_header = headers.get("authorization", None)

    is_stream = bool(payload.get("stream", False))
    model = payload.get("model", "default")

    # 2. Check Cache
    if not bypass_cache:
        status, entry, similarity = cache_instance.lookup(
            payload,
            org_id=org_id,
            custom_threshold=custom_threshold
        )
    else:
        status, entry, similarity = "BYPASS", None, 0.0

    # 3. Cache HIT Handling
    if entry is not None and status in ("HIT_EXACT", "HIT_SEMANTIC"):
        latency_ms = (time.perf_counter() - start_time) * 1000
        
        # Calculate cost savings from cached usage
        usage = entry.response_payload.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 100)
        completion_tokens = usage.get("completion_tokens", 50)
        savings = upstream_client.calculate_savings(model, prompt_tokens, completion_tokens)
        
        METRICS_LEDGER["total_savings_usd"] += savings
        METRICS_LEDGER["total_cached_prompt_tokens"] += prompt_tokens
        METRICS_LEDGER["total_cached_completion_tokens"] += completion_tokens

        resp_headers = {
            "X-Cache-Status": status,
            "X-Cache-Similarity": f"{similarity:.4f}",
            "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
            "X-Cost-Saved-USD": f"{savings:.6f}",
            "Access-Control-Allow-Origin": "*"
        }

        if is_stream:
            return StreamingResponse(
                StreamReplayer.replay_cached_stream(
                    entry.response_payload,
                    stream_chunks=entry.stream_chunks,
                    tokens_per_sec=config.STREAM_REPLAY_TOKENS_PER_SEC
                ),
                media_type="text/event-stream",
                headers=resp_headers
            )
        else:
            return JSONResponse(entry.response_payload, headers=resp_headers)

    # 4. Cache MISS / BYPASS -> Forward Upstream with SingleFlight Coalescing
    exact_hash = RequestHasher.compute_exact_hash(payload, org_id=org_id)

    async def fetch_upstream_action():
        if not is_stream:
            status_code, res_data, res_headers = await upstream_client.forward_non_stream(payload, auth_header=auth_header)
            if status_code == 200:
                saved_entry = cache_instance.store(
                    payload=payload,
                    response_payload=res_data,
                    org_id=org_id,
                    tag=cache_tag,
                    custom_ttl=custom_ttl
                )
                snapshot_store.persist_entry(saved_entry)
            return res_data, None
        else:
            return None, None

    # Handle Non-Streaming with SingleFlight
    if not is_stream:
        try:
            res_data, _, is_leader = await flight_bus.execute(exact_hash, fetch_upstream_action)
            latency_ms = (time.perf_counter() - start_time) * 1000
            resp_headers = {
                "X-Cache-Status": status,
                "X-Cache-Similarity": f"{similarity:.4f}",
                "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
                "X-SingleFlight-Leader": str(is_leader),
                "Access-Control-Allow-Origin": "*"
            }
            return JSONResponse(res_data, headers=resp_headers)
        except Exception as e:
            return JSONResponse({"error": {"message": str(e), "type": "upstream_error"}}, status_code=502)

    # Handle Streaming Upstream
    status_code, upstream_resp, err_data, _ = await upstream_client.forward_stream(payload, auth_header=auth_header)
    if status_code != 200 or upstream_resp is None:
        return JSONResponse(err_data or {"error": "Upstream error"}, status_code=status_code)

    async def stream_and_record():
        recorded_chunks = []
        full_content_parts = []
        reasoning_parts = []
        role = "assistant"
        req_id = f"chatcmpl-{int(time.time()*1000)}"

        try:
            async for raw_line in upstream_resp.aiter_lines():
                if not raw_line:
                    continue
                yield f"{raw_line}\n\n"
                
                if raw_line.startswith("data: ") and raw_line != "data: [DONE]":
                    try:
                        chunk_json = json.loads(raw_line[6:])
                        recorded_chunks.append(chunk_json)
                        req_id = chunk_json.get("id", req_id)
                        choices = chunk_json.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            if "content" in delta and delta["content"]:
                                full_content_parts.append(delta["content"])
                            if "reasoning_content" in delta and delta["reasoning_content"]:
                                reasoning_parts.append(delta["reasoning_content"])
                    except Exception:
                        pass
        finally:
            await upstream_resp.aclose()

            if recorded_chunks:
                synthesized_response = {
                    "id": req_id,
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "message": {
                            "role": role,
                            "content": "".join(full_content_parts),
                            "reasoning_content": "".join(reasoning_parts) if reasoning_parts else None
                        },
                        "finish_reason": "stop"
                    }],
                    "usage": {
                        "prompt_tokens": len(str(payload.get("messages", "")).split()),
                        "completion_tokens": len("".join(full_content_parts).split()),
                        "total_tokens": len(str(payload.get("messages", "")).split()) + len("".join(full_content_parts).split())
                    }
                }
                saved_entry = cache_instance.store(
                    payload=payload,
                    response_payload=synthesized_response,
                    org_id=org_id,
                    tag=cache_tag,
                    custom_ttl=custom_ttl,
                    stream_chunks=recorded_chunks
                )
                snapshot_store.persist_entry(saved_entry)

    latency_ms = (time.perf_counter() - start_time) * 1000
    resp_headers = {
        "X-Cache-Status": status,
        "X-Cache-Similarity": f"{similarity:.4f}",
        "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
        "Access-Control-Allow-Origin": "*"
    }
    return StreamingResponse(stream_and_record(), media_type="text/event-stream", headers=resp_headers)


async def handle_purge(request: Request) -> Response:
    org_id = request.headers.get("x-org-id", "default")
    removed = cache_instance.purge_tenant(org_id)
    snapshot_store.remove_by_org(org_id)
    return JSONResponse({"status": "success", "purged_entries": removed, "org_id": org_id})

async def handle_invalidate_tag(request: Request) -> Response:
    try:
        body = await request.json()
        tag = body.get("tag")
    except Exception:
        tag = request.query_params.get("tag")
    if not tag:
        return JSONResponse({"error": "Missing 'tag' parameter"}, status_code=400)
    org_id = request.headers.get("x-org-id", None)
    removed = cache_instance.invalidate_tag(tag, org_id=org_id)
    snapshot_store.remove_by_tag(tag, org_id=org_id)
    return JSONResponse({"status": "success", "tag": tag, "invalidated_entries": removed})

async def handle_stats(request: Request) -> Response:
    org_id = request.headers.get("x-org-id", None)
    stats = cache_instance.get_stats(org_id)
    stats["financial_metrics"] = {
        "total_savings_usd": round(METRICS_LEDGER["total_savings_usd"], 4),
        "total_cached_prompt_tokens": METRICS_LEDGER["total_cached_prompt_tokens"],
        "total_cached_completion_tokens": METRICS_LEDGER["total_cached_completion_tokens"]
    }
    return JSONResponse(stats)

async def handle_models(request: Request) -> Response:
    models = list(MODEL_PRICING.keys())
    model_data = [{"id": m, "object": "model", "owned_by": "omnicache"} for m in models]
    return JSONResponse({"object": "list", "data": model_data})

async def handle_health(request: Request) -> Response:
    return JSONResponse({"status": "healthy", "service": "omnicache-proxy", "timestamp": time.time()})

async def handle_dashboard(request: Request) -> Response:
    html_path = "/root/omnicache_proxy/dashboard/index.html"
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return JSONResponse({"status": "healthy", "service": "omnicache-proxy"})


routes = [
    Route("/v1/chat/completions", handle_chat_completions, methods=["POST", "OPTIONS"]),
    Route("/v1/cache/purge", handle_purge, methods=["POST"]),
    Route("/v1/cache/invalidate-tag", handle_invalidate_tag, methods=["POST"]),
    Route("/v1/cache/stats", handle_stats, methods=["GET"]),
    Route("/v1/models", handle_models, methods=["GET"]),
    Route("/healthz", handle_health, methods=["GET"]),
    Route("/dashboard", handle_dashboard, methods=["GET"]),
    Route("/", handle_dashboard, methods=["GET"])
]

app = Starlette(routes=routes)
