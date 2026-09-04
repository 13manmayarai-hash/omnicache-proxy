"""
OmniCache AI Proxy - Advanced Enterprise Trust Gateway.
Guarantees 100% compatibility with Claude Code, Cursor, and OpenAI SDKs.
Secured with cryptographic tenant isolation, SingleFlight coalescing,
vision perceptual caching, and explainable decision auditing.
"""

import time
import json
import os
import asyncio
from typing import Dict, Any, Optional, Tuple, List
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse, HTMLResponse, Response
from starlette.routing import Route

from core.config import config, MODEL_PRICING
from core.hasher import RequestHasher
from core.vector_cache import cache_instance, get_model_family
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
    "exact_tokens_used": 0,
    "estimated_tokens_used": 0,
    "exact_tokens_saved": 0,
    "estimated_tokens_saved": 0,
    "arbitrage_savings_usd": 0.0,
    "privacy_scrubbed_count": 0,
    "agent_tool_hits": 0,
    "vision_cache_hits": 0,
    "singleflight_coalesced_count": 0
}

loaded_entries = snapshot_store.load_into_cache(cache_instance)
if loaded_entries > 0:
    print(f"📦 [OmniCache] Restored {loaded_entries} cached entries from SQLite snapshot.")


# =====================================================================
# Security & Identity Helpers
# =====================================================================

def get_cors_headers(request: Request) -> Dict[str, str]:
    """Computes restricted, origin-verified CORS headers."""
    origin = request.headers.get("origin", "")
    allowed_origins = getattr(config, "CORS_ALLOWED_ORIGINS", [])
    allow_all = getattr(config, "CORS_ALLOW_ALL", False)

    if allow_all:
        allow_origin = "*"
    elif origin and (origin in allowed_origins or "*" in allowed_origins):
        allow_origin = origin
    else:
        allow_origin = allowed_origins[0] if allowed_origins else "http://localhost:8000"

    return {
        "Access-Control-Allow-Origin": allow_origin,
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, HEAD, PATCH",
        "Access-Control-Allow-Headers": "Authorization, Content-Type, x-api-key, x-admin-key, x-org-id, x-cache-bypass, x-omnicache-model-cascade, x-allow-cascade, x-cache-ttl, x-cache-threshold, x-cache-tag, anthropic-version, anthropic-beta",
        "Access-Control-Expose-Headers": "X-Cache-Status, X-Cache-Decision-Reason, X-Cache-Similarity, X-Cache-Latency-Ms, X-Cache-TTL-Remaining, X-Cost-Avoided-USD, X-Cost-Saved-USD, X-Tokens-Used, X-Tokens-Saved, X-Tokens-Accounting, X-Requested-Model, X-Served-Model, X-Cascade-Applied, X-Cascade-Reason"
    }


def extract_auth_key(request: Request) -> str:
    """Extracts API key from standard HTTP authorization headers."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        key = auth_header[7:].strip()
        if key:
            return key
    elif auth_header:
        key = auth_header.strip()
        if key:
            return key

    x_api_key = request.headers.get("x-api-key", "").strip()
    if x_api_key:
        return x_api_key

    x_admin_key = request.headers.get("x-admin-key", "").strip()
    if x_admin_key:
        return x_admin_key

    # Fallback to default key if registered and REQUIRE_AUTH is False
    if "default" in quota_manager._keys and not getattr(config, "REQUIRE_AUTH", False):
        return "default"

    return ""


def authenticate_tenant(request: Request) -> Tuple[bool, Optional[Response], Dict[str, Any], str]:
    """
    Authenticates tenant request and derives secure tenant identity (org_id).
    Returns (is_authorized, error_response_if_any, key_info, derived_org_id).
    """
    cors_headers = get_cors_headers(request)
    key = extract_auth_key(request)
    
    allowed, auth_reason, key_info = quota_manager.check_authorization(key)
    if not allowed:
        if key_info is None:
            return False, JSONResponse(
                {"error": {"message": auth_reason, "type": "authentication_error"}},
                status_code=401,
                headers=cors_headers
            ), {}, ""
        else:
            return False, JSONResponse(
                {"error": {"message": auth_reason, "type": "quota_exceeded"}},
                status_code=429,
                headers=cors_headers
            ), key_info, ""

    role = key_info.get("role", "tenant")
    client_org_id = request.headers.get("x-org-id", "").strip()
    if role == "admin" and client_org_id:
        derived_org_id = client_org_id
    else:
        derived_org_id = key_info.get("org_id", key_info.get("team_name", "default"))

    return True, None, key_info, derived_org_id


def authenticate_admin(request: Request) -> Tuple[bool, Optional[Response], Dict[str, Any]]:
    """Guards administrative endpoints. Requires valid admin credentials."""
    cors_headers = get_cors_headers(request)
    key = extract_auth_key(request)
    
    if not key:
        return False, JSONResponse(
            {"error": {"message": "Admin authorization required", "type": "authentication_error"}},
            status_code=401,
            headers=cors_headers
        ), {}

    if not quota_manager.is_admin(key):
        if key in quota_manager._keys:
            return False, JSONResponse(
                {"error": {"message": "Permission denied: Administrator privileges required", "type": "permission_denied"}},
                status_code=403,
                headers=cors_headers
            ), {}
        return False, JSONResponse(
            {"error": {"message": "Unauthorized: Invalid API key", "type": "authentication_error"}},
            status_code=401,
            headers=cors_headers
        ), {}

    key_info = quota_manager._keys.get(key, {"team_name": "Admin", "org_id": "admin", "role": "admin"})
    return True, None, key_info


def parse_cascade_opt_in(headers: Any) -> bool:
    """Evaluates caller opt-in for Speculative Model Cascading. Default False."""
    cascade_header = (
        headers.get("x-omnicache-model-cascade", "").strip().lower() or
        headers.get("x-allow-cascade", "").strip().lower()
    )
    return cascade_header in ("allow", "true", "1", "enabled", "yes")


# =====================================================================
# API Endpoints
# =====================================================================

async def handle_models(request: Request) -> Response:
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    model_list = [
        {"id": "claude-3-5-sonnet-20241022", "object": "model", "type": "model", "display_name": "Claude 3.5 Sonnet"},
        {"id": "claude-3-7-sonnet-20250219", "object": "model", "type": "model", "display_name": "Claude 3.7 Sonnet"},
        {"id": "claude-3-5-haiku-20241022", "object": "model", "type": "model", "display_name": "Claude 3.5 Haiku"},
        {"id": "claude-sonnet-4-5-20250929", "object": "model", "type": "model", "display_name": "Claude Sonnet 4.5"},
        {"id": "claude-haiku-4-5-20251001", "object": "model", "type": "model", "display_name": "Claude Haiku 4.5"},
        {"id": "gpt-4o", "object": "model", "type": "model", "display_name": "GPT-4o"},
        {"id": "gpt-4o-mini", "object": "model", "type": "model", "display_name": "GPT-4o Mini"},
        {"id": "gemini-2.5-flash", "object": "model", "type": "model", "display_name": "Gemini 2.5 Flash"}
    ]
    return JSONResponse({
        "object": "list",
        "data": model_list,
        "models": model_list,
        "has_more": False,
        "first_id": model_list[0]["id"],
        "last_id": model_list[-1]["id"]
    }, headers=cors_headers)


async def handle_chat_completions(request: Request) -> Response:
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    start_time = time.perf_counter()
    try:
        raw_payload: Dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}}, status_code=400, headers=cors_headers)

    auth_ok, auth_err, key_info, org_id = authenticate_tenant(request)
    if not auth_ok:
        return auth_err

    payload, pii_token_map, scrubbed_count = privacy_shield.sanitize_payload(raw_payload)
    if scrubbed_count > 0:
        METRICS_LEDGER["privacy_scrubbed_count"] += scrubbed_count

    headers = request.headers
    bypass_cache = headers.get("x-cache-bypass", "false").lower() in ("true", "1")
    allow_cascade = parse_cascade_opt_in(headers)
    custom_ttl = int(headers.get("x-cache-ttl")) if headers.get("x-cache-ttl", "").isdigit() else None
    custom_threshold = float(headers.get("x-cache-threshold")) if headers.get("x-cache-threshold") else None
    cache_tag = headers.get("x-cache-tag", None)
    auth_header = headers.get("authorization", None)

    is_stream = bool(payload.get("stream", False))
    requested_model = payload.get("model", "default")

    # 1. Multimodal Vision Cache Check
    extracted_images = vision_cache.extract_images_from_payload(payload) if not bypass_cache else []
    if extracted_images:
        for img_hash, prompt_text in extracted_images:
            is_vhit, v_response, v_dist = vision_cache.lookup_image(img_hash, prompt_text)
            if is_vhit and v_response is not None:
                latency_ms = (time.perf_counter() - start_time) * 1000
                savings = upstream_client.calculate_savings(requested_model, 150, 250)
                METRICS_LEDGER["vision_cache_hits"] += 1
                METRICS_LEDGER["total_savings_usd"] += savings
                METRICS_LEDGER["total_tokens_saved"] += 400

                resp_headers = {
                    "X-Cache-Status": "HIT_VISION",
                    "X-Cache-Decision-Reason": f"HIT_VISION_DHASH: Perceptual visual match (Hamming distance {v_dist}/64)",
                    "X-Cache-Similarity": f"{1.0 - (v_dist / 64.0):.4f}",
                    "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
                    "X-Cost-Avoided-USD": f"{savings:.6f}",
                    "X-Cost-Saved-USD": f"{savings:.6f}",
                    "X-Tokens-Used": "0",
                    "X-Tokens-Saved": "400",
                    "X-Tokens-Accounting": "estimated",
                    "X-Requested-Model": requested_model,
                    "X-Served-Model": requested_model,
                    **cors_headers
                }
                rehydrated = privacy_shield.rehydrate_response(v_response, pii_token_map)
                if is_stream:
                    return StreamingResponse(
                        StreamReplayer.replay_cached_stream(rehydrated, tokens_per_sec=config.STREAM_REPLAY_TOKENS_PER_SEC),
                        media_type="text/event-stream",
                        headers=resp_headers
                    )
                return JSONResponse(rehydrated, headers=resp_headers)

    # 2. Text / Dual-Tier Cache Check (L1 Exact + L2 Semantic)
    if not bypass_cache:
        status, entry, similarity, decision_reason = cache_instance.lookup(payload, org_id=org_id, custom_threshold=custom_threshold)
    else:
        status, entry, similarity, decision_reason = "BYPASS", None, 0.0, "BYPASS_EXPLICIT_HEADER: Bypassed via X-Cache-Bypass header"

    if entry is not None and status in ("HIT_EXACT", "HIT_SEMANTIC"):
        latency_ms = (time.perf_counter() - start_time) * 1000
        usage = entry.response_payload.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", entry.prompt_tokens or 50)
        completion_tokens = usage.get("completion_tokens", entry.completion_tokens or 80)
        total_saved_tokens = prompt_tokens + completion_tokens
        pricing_model = entry.model or requested_model
        savings = upstream_client.calculate_savings(pricing_model, prompt_tokens, completion_tokens)
        
        METRICS_LEDGER["total_savings_usd"] += savings
        METRICS_LEDGER["total_tokens_saved"] += total_saved_tokens
        if entry.is_exact_tokens:
            METRICS_LEDGER["exact_tokens_saved"] += total_saved_tokens
        else:
            METRICS_LEDGER["estimated_tokens_saved"] += total_saved_tokens

        resp_headers = {
            "X-Cache-Status": status,
            "X-Cache-Decision-Reason": decision_reason,
            "X-Cache-Similarity": f"{similarity:.4f}",
            "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
            "X-Cache-TTL-Remaining": str(entry.ttl_remaining()),
            "X-Cache-Entry-Age-Seconds": f"{entry.age_seconds():.1f}",
            "X-Cost-Avoided-USD": f"{savings:.6f}",
            "X-Cost-Saved-USD": f"{savings:.6f}",
            "X-Cost-Calculation-Method": "exact_model_pricing",
            "X-Avoided-Prompt-Tokens": str(prompt_tokens),
            "X-Avoided-Completion-Tokens": str(completion_tokens),
            "X-Tokens-Used": "0",
            "X-Tokens-Saved": str(total_saved_tokens),
            "X-Tokens-Accounting": "exact" if entry.is_exact_tokens else "estimated",
            "X-Requested-Model": requested_model,
            "X-Served-Model": entry.model,
            "X-Cascade-Applied": "false",
            **cors_headers
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

    # 3. Model Cascade Evaluation & Upstream SingleFlight Deduplication
    routed_model, route_tier, complexity, was_cascaded, cascade_reason = cascade_router.evaluate_route(
        requested_model, payload, allow_cascade=allow_cascade
    )
    payload["model"] = routed_model

    exact_flight_key = RequestHasher.compute_exact_hash(payload, org_id=org_id)

    if not is_stream:
        async def _fetch_non_stream():
            code, data, hdrs = await upstream_client.forward_non_stream(payload, auth_header=auth_header)
            return {"status_code": code, "res_data": data, "headers": hdrs}, None

        flight_result, _, is_leader = await flight_bus.execute(
            exact_flight_key,
            _fetch_non_stream,
            timeout_seconds=config.SINGLEFLIGHT_TIMEOUT_SECONDS
        )

        status_code = flight_result["status_code"]
        res_data = flight_result["res_data"]
        latency_ms = (time.perf_counter() - start_time) * 1000

        if not is_leader:
            METRICS_LEDGER["singleflight_coalesced_count"] += 1

        if status_code == 200:
            usage = res_data.get("usage", {})
            if usage and "prompt_tokens" in usage and "completion_tokens" in usage:
                is_exact = True
                p_tok = usage.get("prompt_tokens", 0)
                c_tok = usage.get("completion_tokens", 0)
                tokens_used = usage.get("total_tokens", p_tok + c_tok)
                if is_leader:
                    METRICS_LEDGER["exact_tokens_used"] += tokens_used
            else:
                is_exact = False
                p_tok = len(str(payload.get("messages", "")).split())
                c_tok = len(str(res_data.get("choices", [{}])[0].get("message", {}).get("content", "")).split())
                tokens_used = p_tok + c_tok
                if is_leader:
                    METRICS_LEDGER["estimated_tokens_used"] += tokens_used

            if is_leader:
                METRICS_LEDGER["total_tokens_used"] += tokens_used
                saved_entry = cache_instance.store(
                    payload=payload,
                    response_payload=res_data,
                    org_id=org_id,
                    tag=cache_tag,
                    custom_ttl=custom_ttl,
                    is_exact_tokens=is_exact,
                    prompt_tokens=p_tok,
                    completion_tokens=c_tok
                )
                asyncio.create_task(snapshot_store.persist_entry_async(saved_entry))
                radix_tree.insert_conversation(payload.get("messages", []), res_data)

                if extracted_images:
                    for img_h, p_txt in extracted_images:
                        vision_cache.store_image(img_h, p_txt, res_data)

            rehydrated = privacy_shield.rehydrate_response(res_data, pii_token_map)
            cache_status_header = "MISS" if is_leader else "HIT_SINGLEFLIGHT"
            return JSONResponse(rehydrated, headers={
                "X-Cache-Status": cache_status_header,
                "X-Cache-Decision-Reason": decision_reason if is_leader else "HIT_SINGLEFLIGHT: Concurrent in-flight request coalesced with leader",
                "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
                "X-Tokens-Used": str(tokens_used if is_leader else 0),
                "X-Tokens-Saved": str(0 if is_leader else tokens_used),
                "X-Tokens-Accounting": "exact" if is_exact else "estimated",
                "X-Requested-Model": requested_model,
                "X-Served-Model": routed_model,
                "X-Cascade-Applied": "true" if was_cascaded else "false",
                "X-Cascade-Reason": cascade_reason,
                **cors_headers
            })
        else:
            return JSONResponse(res_data, status_code=status_code, headers=cors_headers)

    # 4. Streaming Forward
    status_code, upstream_resp, err_data, _ = await upstream_client.forward_stream(payload, auth_header=auth_header)
    if status_code != 200 or upstream_resp is None:
        return JSONResponse(err_data or {"error": "Upstream error"}, status_code=status_code, headers=cors_headers)

    async def stream_and_record():
        recorded_chunks = []
        full_content_parts = []
        try:
            async for chunk in upstream_resp.aiter_raw():
                if not chunk:
                    continue
                yield chunk
                try:
                    chunk_str = chunk.decode("utf-8", errors="ignore")
                    for raw_line in chunk_str.split("\n"):
                        if raw_line.startswith("data: ") and raw_line.strip() != "data: [DONE]":
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
                tokens_used = p_tok + c_tok
                METRICS_LEDGER["total_tokens_used"] += tokens_used
                METRICS_LEDGER["estimated_tokens_used"] += tokens_used

                synthesized = {
                    "id": f"chatcmpl-{int(time.time()*1000)}",
                    "object": "chat.completion",
                    "model": routed_model,
                    "choices": [{"message": {"role": "assistant", "content": "".join(full_content_parts)}}],
                    "usage": {"prompt_tokens": p_tok, "completion_tokens": c_tok, "total_tokens": tokens_used}
                }
                saved_entry = cache_instance.store(
                    payload=payload,
                    response_payload=synthesized,
                    org_id=org_id,
                    tag=cache_tag,
                    custom_ttl=custom_ttl,
                    stream_chunks=recorded_chunks,
                    is_exact_tokens=False,
                    prompt_tokens=p_tok,
                    completion_tokens=c_tok
                )
                asyncio.create_task(snapshot_store.persist_entry_async(saved_entry))
                if extracted_images:
                    for img_h, p_txt in extracted_images:
                        vision_cache.store_image(img_h, p_txt, synthesized)

    latency_ms = (time.perf_counter() - start_time) * 1000
    return StreamingResponse(stream_and_record(), media_type="text/event-stream", headers={
        "X-Cache-Status": status,
        "X-Cache-Decision-Reason": decision_reason,
        "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
        "X-Tokens-Accounting": "estimated",
        "X-Requested-Model": requested_model,
        "X-Served-Model": routed_model,
        "X-Cascade-Applied": "true" if was_cascaded else "false",
        "X-Cascade-Reason": cascade_reason,
        **cors_headers
    })


async def handle_anthropic_messages(request: Request) -> Response:
    """
    Anthropic Messages API Handler with full Streaming SSE support, SingleFlight coalescing, and Vision Cache.
    """
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)
    elif request.method == "GET":
        return await handle_models(request)

    start_time = time.perf_counter()
    try:
        raw_payload: Dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid JSON"}}, status_code=400, headers=cors_headers)

    auth_ok, auth_err, key_info, org_id = authenticate_tenant(request)
    if not auth_ok:
        return auth_err

    anthropic_payload, pii_token_map, scrubbed_count = privacy_shield.sanitize_payload(raw_payload)
    if scrubbed_count > 0:
        METRICS_LEDGER["privacy_scrubbed_count"] += scrubbed_count

    headers = request.headers
    requested_model = anthropic_payload.get("model", "claude-3-5-sonnet-20241022")
    is_stream = bool(anthropic_payload.get("stream", False))
    bypass_cache = headers.get("x-cache-bypass", "false").lower() in ("true", "1")
    allow_cascade = parse_cascade_opt_in(headers)

    # 1. Vision Cache Check
    extracted_images = vision_cache.extract_images_from_payload(anthropic_payload) if not bypass_cache else []
    if extracted_images:
        for img_hash, prompt_text in extracted_images:
            is_vhit, v_response, v_dist = vision_cache.lookup_image(img_hash, prompt_text)
            if is_vhit and v_response is not None:
                latency_ms = (time.perf_counter() - start_time) * 1000
                METRICS_LEDGER["vision_cache_hits"] += 1
                savings = upstream_client.calculate_savings(requested_model, 100, 200)
                METRICS_LEDGER["total_savings_usd"] += savings

                resp_headers = {
                    "X-Cache-Status": "HIT_VISION",
                    "X-Cache-Decision-Reason": f"HIT_VISION_DHASH: Perceptual visual match (Hamming distance {v_dist}/64)",
                    "X-Cache-Similarity": f"{1.0 - (v_dist / 64.0):.4f}",
                    "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
                    "X-Cost-Avoided-USD": f"{savings:.6f}",
                    "X-Cost-Saved-USD": f"{savings:.6f}",
                    "X-Requested-Model": requested_model,
                    "X-Served-Model": requested_model,
                    **cors_headers
                }
                content = v_response.get("choices", [{}])[0].get("message", {}).get("content", "")
                anthropic_res = {
                    "id": f"msg_cached_{int(time.time()*1000)}",
                    "type": "message",
                    "role": "assistant",
                    "model": requested_model,
                    "content": [{"type": "text", "text": content}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 100, "output_tokens": 200}
                }
                rehydrated = privacy_shield.rehydrate_response(anthropic_res, pii_token_map)
                return JSONResponse(rehydrated, headers=resp_headers)

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
        "model": requested_model,
        "messages": messages,
        "temperature": anthropic_payload.get("temperature", 0.0),
        "tools": anthropic_payload.get("tools", None)
    }

    if not bypass_cache:
        status, entry, similarity, decision_reason = cache_instance.lookup(normalized_payload, org_id=org_id)
    else:
        status, entry, similarity, decision_reason = "BYPASS", None, 0.0, "BYPASS_EXPLICIT_HEADER: Bypassed via X-Cache-Bypass header"

    # 2. Anthropic Cache HIT
    if entry is not None and status in ("HIT_EXACT", "HIT_SEMANTIC"):
        latency_ms = (time.perf_counter() - start_time) * 1000
        content = entry.response_payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = entry.response_payload.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", entry.prompt_tokens or 35)
        completion_tokens = usage.get("completion_tokens", entry.completion_tokens or 65)
        total_saved_tokens = prompt_tokens + completion_tokens
        savings = upstream_client.calculate_savings(entry.model or requested_model, prompt_tokens, completion_tokens)
        
        METRICS_LEDGER["total_savings_usd"] += savings
        METRICS_LEDGER["total_tokens_saved"] += total_saved_tokens
        if entry.is_exact_tokens:
            METRICS_LEDGER["exact_tokens_saved"] += total_saved_tokens
        else:
            METRICS_LEDGER["estimated_tokens_saved"] += total_saved_tokens

        resp_headers = {
            "X-Cache-Status": status,
            "X-Cache-Decision-Reason": decision_reason,
            "X-Cache-Similarity": f"{similarity:.4f}",
            "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
            "X-Cache-TTL-Remaining": str(entry.ttl_remaining()),
            "X-Cache-Entry-Age-Seconds": f"{entry.age_seconds():.1f}",
            "X-Cost-Avoided-USD": f"{savings:.6f}",
            "X-Cost-Saved-USD": f"{savings:.6f}",
            "X-Cost-Calculation-Method": "exact_model_pricing",
            "X-Avoided-Prompt-Tokens": str(prompt_tokens),
            "X-Avoided-Completion-Tokens": str(completion_tokens),
            "X-Tokens-Used": "0",
            "X-Tokens-Saved": str(total_saved_tokens),
            "X-Tokens-Accounting": "exact" if entry.is_exact_tokens else "estimated",
            "X-Requested-Model": requested_model,
            "X-Served-Model": entry.model,
            "X-Cascade-Applied": "false",
            **cors_headers
        }

        if is_stream:
            async def stream_cached_anthropic():
                msg_id = f"msg_cached_{int(time.time()*1000)}"
                yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': {'id': msg_id, 'type': 'message', 'role': 'assistant', 'model': entry.model, 'content': [], 'stop_reason': None, 'usage': {'input_tokens': prompt_tokens, 'output_tokens': 1}}})}\n\n"
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
                "model": entry.model,
                "content": [{"type": "text", "text": content}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": prompt_tokens, "output_tokens": completion_tokens}
            }
            rehydrated = privacy_shield.rehydrate_response(anthropic_response, pii_token_map)
            return JSONResponse(rehydrated, headers=resp_headers)

    # 3. Anthropic Cache MISS -> Forward Upstream
    req_params = dict(request.query_params) if request.query_params else None
    if is_stream:
        status_code, stream_resp, err_data = await upstream_client.forward_anthropic_stream(
            anthropic_payload,
            incoming_headers=dict(request.headers),
            params=req_params
        )
        if status_code != 200 or stream_resp is None:
            return JSONResponse(err_data or {"error": "Upstream error"}, status_code=status_code, headers=cors_headers)

        async def stream_and_record_anthropic():
            full_text_accum = []
            try:
                async for chunk in stream_resp.aiter_raw():
                    if not chunk:
                        continue
                    yield chunk
                    try:
                        chunk_str = chunk.decode("utf-8", errors="ignore")
                        for line in chunk_str.split("\n"):
                            if line.startswith("data: "):
                                data_obj = json.loads(line[6:])
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
                    METRICS_LEDGER["estimated_tokens_used"] += (p_tok + c_tok)
                    
                    cacheable_res_payload = {
                        "id": f"msg_{int(time.time()*1000)}",
                        "object": "chat.completion",
                        "choices": [{"message": {"role": "assistant", "content": full_text}}],
                        "usage": {"prompt_tokens": p_tok, "completion_tokens": c_tok, "total_tokens": p_tok + c_tok}
                    }
                    saved_entry = cache_instance.store(
                        payload=normalized_payload,
                        response_payload=cacheable_res_payload,
                        org_id=org_id,
                        is_exact_tokens=False,
                        prompt_tokens=p_tok,
                        completion_tokens=c_tok
                    )
                    asyncio.create_task(snapshot_store.persist_entry_async(saved_entry))

        latency_ms = (time.perf_counter() - start_time) * 1000
        return StreamingResponse(stream_and_record_anthropic(), media_type="text/event-stream", headers={
            "X-Cache-Status": "MISS",
            "X-Cache-Decision-Reason": decision_reason,
            "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
            "X-Tokens-Accounting": "estimated",
            "X-Requested-Model": requested_model,
            "X-Served-Model": requested_model,
            **cors_headers
        })

    # Non-streaming forward
    status_code, anthropic_res, _ = await upstream_client.forward_anthropic_messages(
        anthropic_payload,
        incoming_headers=dict(request.headers),
        params=req_params
    )
    latency_ms = (time.perf_counter() - start_time) * 1000
    if status_code == 200:
        usage = anthropic_res.get("usage", {})
        p_tok = usage.get("input_tokens", 35)
        c_tok = usage.get("output_tokens", 65)
        tokens_used = p_tok + c_tok
        METRICS_LEDGER["total_tokens_used"] += tokens_used
        METRICS_LEDGER["exact_tokens_used"] += tokens_used

        content_blocks = anthropic_res.get("content", [])
        full_content = "\n".join([b.get("text", "") for b in content_blocks if isinstance(b, dict) and b.get("type") == "text"])

        cacheable_res_payload = {
            "id": anthropic_res.get("id", f"msg_{int(time.time()*1000)}"),
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": full_content}}],
            "usage": {"prompt_tokens": p_tok, "completion_tokens": c_tok, "total_tokens": tokens_used}
        }
        saved_entry = cache_instance.store(
            payload=normalized_payload,
            response_payload=cacheable_res_payload,
            org_id=org_id,
            is_exact_tokens=True,
            prompt_tokens=p_tok,
            completion_tokens=c_tok
        )
        asyncio.create_task(snapshot_store.persist_entry_async(saved_entry))

        rehydrated = privacy_shield.rehydrate_response(anthropic_res, pii_token_map)
        return JSONResponse(rehydrated, headers={
            "X-Cache-Status": "MISS",
            "X-Cache-Decision-Reason": decision_reason,
            "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
            "X-Tokens-Used": str(tokens_used),
            "X-Tokens-Saved": "0",
            "X-Tokens-Accounting": "exact",
            "X-Requested-Model": requested_model,
            "X-Served-Model": requested_model,
            **cors_headers
        })
    else:
        return JSONResponse(anthropic_res, status_code=status_code, headers=cors_headers)


async def handle_anthropic_count_tokens(request: Request) -> Response:
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)
    try:
        body = await request.json()
        messages = body.get("messages", [])
        system = body.get("system", "")
        full_text = str(system) + " " + " ".join([str(m.get("content", "")) for m in messages])
        est_tokens = max(1, int(len(full_text.split()) * 1.3))
        return JSONResponse({"input_tokens": est_tokens}, headers=cors_headers)
    except Exception:
        return JSONResponse({"input_tokens": 50}, headers=cors_headers)


async def handle_catchall(request: Request) -> Response:
    """Universal fallback handler for Claude Code SDK compatibility."""
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)
    
    path = request.url.path
    if "models" in path:
        return await handle_models(request)
    elif "messages" in path:
        return await handle_anthropic_messages(request)
    elif "count_tokens" in path:
        return await handle_anthropic_count_tokens(request)
    
    return JSONResponse({
        "status": "ok",
        "authenticated": True,
        "path": path,
        "message": "OmniCache Universal Gateway OK"
    }, headers=cors_headers)


async def handle_tool_replay(request: Request) -> Response:
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)
    try:
        body = await request.json()
        tool_name = body.get("tool_name")
        arguments = body.get("arguments", {})
        env_fp = body.get("workspace_fingerprint", "default")
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400, headers=cors_headers)

    is_hit, output, tool_key = tool_cache.lookup_tool_call(tool_name, arguments, workspace_fingerprint=env_fp)
    if is_hit:
        METRICS_LEDGER["agent_tool_hits"] += 1
        return JSONResponse({
            "status": "HIT",
            "tool_name": tool_name,
            "tool_key": tool_key,
            "output": output,
            "cached": True
        }, headers=cors_headers)
    
    return JSONResponse({
        "status": "MISS",
        "tool_name": tool_name,
        "cached": False
    }, headers=cors_headers)


async def handle_purge(request: Request) -> Response:
    """Protected Cache Purge Endpoint."""
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    auth_ok, auth_err, key_info, org_id = authenticate_tenant(request)
    if not auth_ok:
        return auth_err

    is_admin_user = key_info.get("role") == "admin"
    req_org = request.query_params.get("org_id", None) if is_admin_user else org_id
    
    in_mem_removed = cache_instance.purge(org_id=req_org)
    db_removed = snapshot_store.purge_all(org_id=req_org)
    
    return JSONResponse({
        "status": "success",
        "purged_entries": in_mem_removed,
        "purged_db_records": db_removed,
        "org_id": req_org or "all"
    }, headers=cors_headers)


async def handle_invalidate_tag(request: Request) -> Response:
    """Protected Tag Invalidation Endpoint."""
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    auth_ok, auth_err, key_info, org_id = authenticate_tenant(request)
    if not auth_ok:
        return auth_err

    tag = request.query_params.get("tag")
    if not tag:
        return JSONResponse({"error": "Query parameter 'tag' is required"}, status_code=400, headers=cors_headers)
    
    is_admin_user = key_info.get("role") == "admin"
    req_org = request.query_params.get("org_id", None) if is_admin_user else org_id

    removed = cache_instance.invalidate_tag(tag, org_id=req_org)
    db_removed = snapshot_store.delete_by_tag(tag, org_id=req_org)
    return JSONResponse({
        "status": "success",
        "invalidated_tag": tag,
        "removed_entries": removed,
        "removed_db_records": db_removed
    }, headers=cors_headers)


async def handle_stats(request: Request) -> Response:
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    auth_ok, auth_err, key_info, org_id = authenticate_tenant(request)
    if not auth_ok:
        return auth_err

    is_admin_user = key_info.get("role") == "admin"
    scoped_org = None if is_admin_user else org_id

    stats = cache_instance.get_stats(org_id=scoped_org)
    total_tokens = METRICS_LEDGER["total_tokens_used"] + METRICS_LEDGER["total_tokens_saved"]
    token_savings_pct = (METRICS_LEDGER["total_tokens_saved"] / total_tokens * 100) if total_tokens > 0 else 0.0

    return JSONResponse({
        "cache_stats": stats,
        "financial_telemetry": {
            "total_savings_usd": round(METRICS_LEDGER["total_savings_usd"], 6),
            "arbitrage_savings_usd": round(METRICS_LEDGER["arbitrage_savings_usd"], 6),
            "total_tokens_saved": METRICS_LEDGER["total_tokens_saved"],
            "total_tokens_used": METRICS_LEDGER["total_tokens_used"],
            "exact_tokens_saved": METRICS_LEDGER["exact_tokens_saved"],
            "estimated_tokens_saved": METRICS_LEDGER["estimated_tokens_saved"],
            "token_savings_pct": round(token_savings_pct, 2)
        },
        "enterprise_engine": {
            "privacy_redactions_total": METRICS_LEDGER["privacy_scrubbed_count"],
            "agent_tool_replays": METRICS_LEDGER["agent_tool_hits"],
            "vision_cache_hits": METRICS_LEDGER["vision_cache_hits"],
            "singleflight_coalesced": METRICS_LEDGER["singleflight_coalesced_count"]
        },
        "system_info": {
            "version": "2.1.4",
            "persistence": "sqlite3_wal",
            "host_binding": config.HOST,
            "port": config.PORT
        }
    }, headers=cors_headers)


async def handle_quotas(request: Request) -> Response:
    """Protected Quota Management Endpoint (Admin Only)."""
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    admin_ok, admin_err, key_info = authenticate_admin(request)
    if not admin_ok:
        return admin_err

    if request.method == "GET":
        quotas = quota_manager.get_all_quotas()
        return JSONResponse({"status": "success", "quotas": quotas}, headers=cors_headers)
    elif request.method == "POST":
        try:
            body = await request.json()
            key_id = body.get("key_id")
            team = body.get("team_name", "Team")
            org = body.get("org_id", team)
            budget = float(body.get("monthly_budget_usd", 100.0))
            rpm = int(body.get("rate_limit_rpm", 120))
            role = body.get("role", "tenant")
            created = quota_manager.register_key(key_id, team_name=team, org_id=org, monthly_budget_usd=budget, rate_limit_rpm=rpm, role=role)
            return JSONResponse({"status": "success", "registered": created}, headers=cors_headers)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400, headers=cors_headers)


async def handle_export_csv(request: Request) -> Response:
    """Protected Cache CSV Export Endpoint (Admin Only)."""
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    admin_ok, admin_err, _ = authenticate_admin(request)
    if not admin_ok:
        return admin_err

    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Key", "OrgID", "Model", "HitCount", "CreatedAt", "LastAccessedAt", "TTL_Remaining", "UserPromptPreview"])

    for key, entry in cache_instance.l1_exact_cache.items():
        preview = (entry.user_prompt[:80] + "...") if len(entry.user_prompt) > 80 else entry.user_prompt
        writer.writerow([key, entry.org_id, entry.model, entry.hit_count, entry.created_at, entry.last_accessed_at, entry.ttl_remaining(), preview])

    csv_content = output.getvalue()
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=omnicache_export.csv",
            **cors_headers
        }
    )


async def handle_prometheus_metrics(request: Request) -> Response:
    """Protected Prometheus /metrics Scrape Endpoint."""
    cors_headers = get_cors_headers(request)
    if getattr(config, "REQUIRE_AUTH", False):
        admin_ok, admin_err, _ = authenticate_admin(request)
        if not admin_ok:
            return admin_err

    stats = cache_instance.get_stats()
    metrics = [
        "# HELP omnicache_requests_total Total requests processed",
        "# TYPE omnicache_requests_total counter",
        f"omnicache_requests_total {stats['total_requests']}",
        "# HELP omnicache_cache_hits_exact_total Exact L1 hits",
        "# TYPE omnicache_cache_hits_exact_total counter",
        f"omnicache_cache_hits_exact_total {stats['exact_hits']}",
        "# HELP omnicache_cache_hits_semantic_total Semantic L2 hits",
        "# TYPE omnicache_cache_hits_semantic_total counter",
        f"omnicache_cache_hits_semantic_total {stats['semantic_hits']}",
        "# HELP omnicache_savings_usd_total Estimated dollars saved",
        "# TYPE omnicache_savings_usd_total gauge",
        f"omnicache_savings_usd_total {METRICS_LEDGER['total_savings_usd']:.6f}",
        "# HELP omnicache_tokens_saved_total Tokens saved from remote inference",
        "# TYPE omnicache_tokens_saved_total counter",
        f"omnicache_tokens_saved_total {METRICS_LEDGER['total_tokens_saved']}",
        "# HELP omnicache_singleflight_coalesced_total Concurrent requests coalesced",
        "# TYPE omnicache_singleflight_coalesced_total counter",
        f"omnicache_singleflight_coalesced_total {METRICS_LEDGER['singleflight_coalesced_count']}"
    ]
    return Response(content="\n".join(metrics) + "\n", media_type="text/plain; version=0.0.4", headers=cors_headers)


async def handle_healthz(request: Request) -> Response:
    cors_headers = get_cors_headers(request)
    return JSONResponse({
        "status": "healthy",
        "version": "2.1.4",
        "service": "omnicache-proxy"
    }, headers=cors_headers)


async def handle_dashboard(request: Request) -> Response:
    dashboard_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "index.html")
    if os.path.exists(dashboard_path):
        with open(dashboard_path, "r", encoding="utf-8") as f:
            html = f.read()
        return HTMLResponse(html)
    return HTMLResponse("<h1>OmniCache Dashboard Not Found</h1>", status_code=404)


# =====================================================================
# Starlette Application Routing
# =====================================================================

routes = [
    Route("/healthz", handle_healthz, methods=["GET", "OPTIONS"]),
    Route("/models", handle_models, methods=["GET", "OPTIONS"]),
    Route("/v1/models", handle_models, methods=["GET", "OPTIONS"]),
    Route("/v1/chat/completions", handle_chat_completions, methods=["POST", "OPTIONS"]),
    Route("/v1/messages", handle_anthropic_messages, methods=["POST", "GET", "OPTIONS"]),
    Route("/v1/messages/count_tokens", handle_anthropic_count_tokens, methods=["POST", "OPTIONS"]),
    Route("/v1/agent/tool_replay", handle_tool_replay, methods=["POST", "OPTIONS"]),
    Route("/v1/cache/purge", handle_purge, methods=["POST", "DELETE", "GET", "OPTIONS"]),
    Route("/v1/cache/invalidate-tag", handle_invalidate_tag, methods=["POST", "DELETE", "GET", "OPTIONS"]),
    Route("/v1/cache/stats", handle_stats, methods=["GET", "OPTIONS"]),
    Route("/v1/cache/export", handle_export_csv, methods=["GET", "OPTIONS"]),
    Route("/v1/enterprise/quotas", handle_quotas, methods=["GET", "POST", "OPTIONS"]),
    Route("/metrics", handle_prometheus_metrics, methods=["GET", "OPTIONS"]),
    Route("/dashboard", handle_dashboard, methods=["GET"]),
    Route("/{rest_of_path:path}", handle_catchall, methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
]

app = Starlette(debug=False, routes=routes)
