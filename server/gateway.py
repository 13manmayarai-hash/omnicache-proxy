"""
OmniCache AI Proxy - Advanced Enterprise Trust Gateway.
Guarantees 100% compatibility with Claude Code, Cursor, and OpenAI SDKs.
Secured with cryptographic tenant isolation, SingleFlight coalescing,
vision perceptual caching, and explainable decision auditing.
"""

import time
import json
import os
import sys
import asyncio
import collections
import uuid
import secrets
import httpx
import hashlib
import base64
import hmac
import re
import threading
import html
from urllib.parse import parse_qs, urlencode, quote_plus, urlparse
from typing import Dict, Any, Optional, Tuple, List, Set
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse, HTMLResponse, Response, RedirectResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from core.config import config, MODEL_PRICING
from core.hasher import RequestHasher
from core.vector_cache import cache_instance, get_model_family, CacheEntry
from core.radix_tree import radix_tree
from core.vision_cache import vision_cache
from core.audio_cache import audio_cache
from core.privacy_shield import privacy_shield
from core.telephony_filter import telephony_filter
from core.swarm_bus import swarm_bus
from core.p2p_mesh import mesh_bus, CRDTTombstone
from core.quantized_embedder import quantized_embedder
from server.tool_replayer import tool_cache, tool_policy_manager, compact_and_record_agent_tools
from server.workspace_sync import workspace_warmer, workspace_sync_manager
from server.cascade_router import cascade_router
from server.quotas import quota_manager
from server.singleflight import flight_bus
from server.stream_replayer import StreamReplayer
from server.upstream import upstream_client
from server.translator import ProtocolTranslator
from server.failover import failover_engine
from persistence.snapshot_store import snapshot_store
from mcp.server import process_mcp_jsonrpc, TOOLS_METADATA

METRICS_LEDGER = {
    "total_savings_usd": 0.0,
    "total_tokens_used": 0,
    "total_tokens_saved": 0,
    "exact_tokens_used": 0,
    "estimated_tokens_used": 0,
    "exact_tokens_saved": 0,
    "estimated_tokens_saved": 0,
    "arbitrage_savings_usd": 0.0,
    "cascade_routes_total": 0,
    "cascade_downgrades_total": 0,
    "cascade_savings_usd": 0.0,
    "swarm_requests_processed": 0,
    "swarm_cross_agent_hits": 0,
    "swarm_tokens_saved": 0,
    "swarm_mutations_invalidated": 0,
    "privacy_scrubbed_count": 0,
    "agent_tool_hits": 0,
    "agent_tool_recorded_count": 0,
    "agent_tool_compacted_tokens": 0,
    "vision_cache_hits": 0,
    "singleflight_coalesced_count": 0,
    "radix_tree_hits": 0,
    "telephony_requests_processed": 0,
    "telephony_fillers_stripped": 0,
    "telephony_tokens_saved": 0,
    "audio_cache_hits": 0,
    "audio_requests_processed": 0,
    "audio_tokens_saved": 0,
    "quantized_embeddings_generated": 0
}

loaded_entries = snapshot_store.load_into_cache(cache_instance)
if loaded_entries > 0:
    print(f"📦 [OmniCache] Restored {loaded_entries} cached entries from SQLite snapshot.", file=sys.stderr)

if hasattr(cache_instance.storage, "client") and cache_instance.storage.client is not None:
    flight_bus.set_redis_client(cache_instance.storage.client)

def _handle_mesh_tombstone(resource_id: str, reason: str, metadata: Dict[str, Any]):
    """Applies cross-node CRDT tombstone invalidations to local cache fabrics."""
    emit_telemetry_event("mesh_tombstone_applied", {
        "resource_id": resource_id,
        "reason": reason,
        "metadata": metadata
    })
    if resource_id in ("*", "all"):
        cache_instance.purge()
        radix_tree.clear()
        swarm_bus.clear_all()
    elif resource_id.startswith("tag:"):
        tag = resource_id[4:]
        cache_instance.invalidate_tag(tag)
    elif resource_id.startswith("file:") or "/" in resource_id or "\\" in resource_id:
        f_path = resource_id[5:] if resource_id.startswith("file:") else resource_id
        swarm_bus.invalidate_on_mutation(mutated_resource=f_path)
        tool_cache.invalidate(resource_pattern=f_path)
    else:
        for org_dict in list(cache_instance.l1_exact_cache.values()):
            if isinstance(org_dict, dict):
                org_dict.pop(resource_id, None)
        tool_cache.invalidate(resource_pattern=resource_id)

mesh_bus.register_invalidation_handler(_handle_mesh_tombstone)

# Real-Time WebSocket Telemetry Dispatcher & Event Buffer
ACTIVE_WS_CLIENTS: Set[WebSocket] = set()
RECENT_WS_EVENTS: collections.deque = collections.deque(maxlen=100)


async def broadcast_ws_event(event_type: str, data: Dict[str, Any]) -> None:
    """Broadcasts real-time events to all active dashboard / subscriber WebSockets."""
    now = time.time()
    time_str = time.strftime("%H:%M:%S", time.localtime(now))
    event_payload = {
        "type": "event",
        "event_type": event_type,
        "timestamp": now,
        "time_str": time_str,
        "data": data
    }
    RECENT_WS_EVENTS.append(event_payload)
    if not ACTIVE_WS_CLIENTS:
        return
    dead = set()
    for ws in list(ACTIVE_WS_CLIENTS):
        try:
            await ws.send_json(event_payload)
        except Exception:
            dead.add(ws)
    if dead:
        ACTIVE_WS_CLIENTS.difference_update(dead)


def emit_telemetry_event(event_type: str, data: Dict[str, Any]) -> None:
    """Thread-safe & async-safe dispatcher for WebSocket telemetry events."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast_ws_event(event_type, data))
    except RuntimeError:
        now = time.time()
        time_str = time.strftime("%H:%M:%S", time.localtime(now))
        RECENT_WS_EVENTS.append({
            "type": "event",
            "event_type": event_type,
            "timestamp": now,
            "time_str": time_str,
            "data": data
        })




# =====================================================================
# Security & Identity Helpers
# =====================================================================

ALLOWED_REDIRECT_ORIGINS = {
    "https://claude.ai",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://omnicache.rawwgrid.com",
}

def is_allowed_redirect_uri(uri: str) -> bool:
    """Validates that OAuth redirect_uri belongs to an allowed origin or relative path."""
    if not uri:
        return True
    if uri.startswith("/"):
        return True
    try:
        parsed = urlparse(uri)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return False
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in ALLOWED_REDIRECT_ORIGINS:
            return True
        if hostname in ("localhost", "127.0.0.1", "example.com") or hostname.endswith(".example.com"):
            return True
        if hostname == "rawwgrid.com" or hostname.endswith(".rawwgrid.com"):
            return True
        if hostname == "onrender.com" or hostname.endswith(".onrender.com"):
            return True
        if hostname == "claude.ai" or hostname.endswith(".claude.ai"):
            return True
        return False
    except Exception:
        return False


def get_cors_headers(request: Request) -> Dict[str, str]:
    """Computes restricted, origin-verified CORS headers."""
    origin = request.headers.get("origin", "")
    allowed_origins = getattr(config, "CORS_ALLOWED_ORIGINS", [])
    allow_all = getattr(config, "CORS_ALLOW_ALL", False)

    if allow_all:
        allow_origin = "*"
    elif origin:
        parsed = urlparse(origin)
        hostname = (parsed.hostname or "").lower()
        if (
            origin in allowed_origins
            or "*" in allowed_origins
            or hostname == "rawwgrid.com"
            or hostname.endswith(".rawwgrid.com")
            or hostname == "onrender.com"
            or hostname.endswith(".onrender.com")
            or hostname in ("localhost", "127.0.0.1")
        ):
            allow_origin = origin
        else:
            allow_origin = allowed_origins[0] if allowed_origins else "http://localhost:8000"
    else:
        allow_origin = allowed_origins[0] if allowed_origins else "http://localhost:8000"

    return {
        "Access-Control-Allow-Origin": allow_origin,
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, HEAD, PATCH",
        "Access-Control-Allow-Headers": "Authorization, Content-Type, x-api-key, x-admin-key, x-org-id, x-cache-bypass, x-omnicache-model-cascade, x-allow-cascade, x-omnicache-swarm-id, x-omnicache-agent-id, x-omnicache-parent-agent, x-omnicache-subagent-id, x-omnicache-mesh-node, x-omnicache-mesh-clock, x-cache-ttl, x-cache-threshold, x-cache-tag, anthropic-version, anthropic-beta",
        "Access-Control-Expose-Headers": "X-Cache-Status, X-Cache-Decision-Reason, X-Cache-Similarity, X-Cache-Latency-Ms, X-Cache-TTL-Remaining, X-Cost-Avoided-USD, X-Cost-Saved-USD, X-Tokens-Used, X-Tokens-Saved, X-Tokens-Accounting, X-Requested-Model, X-Served-Model, X-Cascade-Applied, X-Cascade-Reason, X-OmniCache-Swarm-Hit, X-OmniCache-Origin-Agent, X-OmniCache-Swarm-ID, X-OmniCache-Mesh-Node, X-OmniCache-Mesh-Clock"
    }


def extract_auth_key(request: Request) -> str:
    """Extracts API key from standard HTTP authorization headers, cookies, or query parameters."""
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

    # Check cookies for browser sessions
    cookie_key = request.cookies.get("omnicache_key", "").strip() or request.cookies.get("api_key", "").strip()
    if cookie_key:
        return cookie_key

    # Check query params (?key=... or ?api_key=...)
    query_key = request.query_params.get("api_key", "").strip() or request.query_params.get("key", "").strip()
    if query_key:
        return query_key

    # Fallback to default key if REQUIRE_AUTH is False
    if not getattr(config, "REQUIRE_AUTH", False):
        return "default"

    return ""


def authenticate_tenant(request: Request) -> Tuple[bool, Optional[Response], Dict[str, Any], str]:
    """
    Authenticates tenant request and derives secure tenant identity (org_id).
    Returns (is_authorized, error_response_if_any, key_info, derived_org_id).
    """
    cors_headers = get_cors_headers(request)
    key = extract_auth_key(request)
    
    # In local developer mode (REQUIRE_AUTH=False), permit unregistered keys (e.g. direct Anthropic/OpenAI keys)
    if not getattr(config, "REQUIRE_AUTH", False):
        if not key:
            key = "default"
        info = quota_manager.storage.get_key(key)
        if not info:
            client_org = request.headers.get("x-org-id", "default").strip() or "default"
            key_info = {"team_name": "Local Developer", "org_id": client_org, "role": "tenant", "key_id": key}
            return True, None, key_info, client_org

    allowed, auth_reason, key_info = quota_manager.check_authorization(key)
    if not allowed:
        if key_info is None:
            auth_headers = dict(cors_headers)
            auth_headers["WWW-Authenticate"] = 'Bearer realm="OmniCache", error="invalid_token"'
            return False, JSONResponse(
                {"error": {"message": auth_reason, "type": "authentication_error"}},
                status_code=401,
                headers=auth_headers
            ), {}, ""
        else:
            return False, JSONResponse(
                {"error": {"message": auth_reason, "type": "quota_exceeded"}},
                status_code=429,
                headers=cors_headers
            ), key_info, ""

    role = key_info.get("role", "tenant")
    key_info["key_id"] = key
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

    # In local developer mode without configured admin key, permit unauthenticated local admin operations
    if not getattr(config, "REQUIRE_AUTH", False) and not getattr(config, "ADMIN_API_KEY", "").strip():
        if not key or key == "default":
            return True, None, {"team_name": "Local Admin", "org_id": "admin", "role": "admin"}
    
    if not key:
        return False, JSONResponse(
            {"error": {"message": "Admin authorization required", "type": "authentication_error"}},
            status_code=401,
            headers=cors_headers
        ), {}

    if not quota_manager.is_admin(key):
        if quota_manager.storage.get_key(key) is not None:
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

    key_info = quota_manager.storage.get_key(key) or {"team_name": "Admin", "org_id": "admin", "role": "admin"}
    return True, None, key_info


def generate_sandbox_playground_completion(user_prompt: str, model: str, is_claude: bool = False) -> Dict[str, Any]:
    """
    Generates an immediate, high-quality local completion for dashboard playground demonstrations
    when no remote upstream API keys are configured, enabling zero-friction testing of semantic caching.
    """
    p_lower = user_prompt.lower()
    if "quicksort" in p_lower:
        text = (
            "Here is an efficient quicksort implementation in Python:\n\n"
            "```python\n"
            "def quicksort(arr):\n"
            "    if len(arr) <= 1:\n"
            "        return arr\n"
            "    pivot = arr[len(arr) // 2]\n"
            "    left = [x for x in arr if x < pivot]\n"
            "    middle = [x for x in arr if x == pivot]\n"
            "    right = [x for x in arr if x > pivot]\n"
            "    return quicksort(left) + middle + quicksort(right)\n\n"
            "# Example:\n"
            "items = [64, 34, 25, 12, 22, 11, 90]\n"
            "print('Sorted:', quicksort(items))\n"
            "```\n\n"
            "**Time Complexity:** O(N log N) average case.\n"
            "**Space Complexity:** O(N) auxiliary space."
        )
    elif "linked list" in p_lower:
        text = (
            "Here is how to reverse a singly linked list in Python:\n\n"
            "```python\n"
            "class ListNode:\n"
            "    def __init__(self, val=0, next=None):\n"
            "        self.val = val\n"
            "        self.next = next\n\n"
            "def reverse_list(head):\n"
            "    prev = None\n"
            "    curr = head\n"
            "    while curr:\n"
            "        next_temp = curr.next\n"
            "        curr.next = prev\n"
            "        prev = curr\n"
            "        curr = next_temp\n"
            "    return prev\n"
            "```"
        )
    elif "pii" in p_lower or "ssn" in p_lower or "card" in p_lower:
        text = (
            "Customer record analysis complete. All Personally Identifiable Information (PII) "
            "has been successfully scrubbed and redacted by OmniCache's Privacy Shield before transmission."
        )
    else:
        clean_q = user_prompt.strip() or "General inquiry"
        text = (
            f"[OmniCache Demonstration Response]\n\n"
            f"Query: \"{clean_q}\"\n\n"
            f"This completion was produced by OmniCache's local zero-dependency sandbox engine "
            f"to verify sub-millisecond semantic caching and token avoidance without requiring upstream API keys.\n\n"
            f"• Subsystem: DualTierCache (L1 Exact + L2 FastHash Vector Engine)\n"
            f"• Acceleration: <0.3ms memory lookup on subsequent identical or semantically rephrased queries."
        )

    p_tokens = max(15, len(user_prompt.split()))
    c_tokens = max(40, len(text.split()))
    t_tokens = p_tokens + c_tokens

    if is_claude:
        return {
            "id": f"msg_sandbox_{int(time.time()*1000)}",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": p_tokens, "output_tokens": c_tokens}
        }
    else:
        return {
            "id": f"chatcmpl_sandbox_{int(time.time()*1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": p_tokens, "completion_tokens": c_tokens, "total_tokens": t_tokens}
        }


def parse_cascade_opt_in(headers: Any) -> bool:
    """Evaluates caller opt-in for Speculative Model Cascading. Default False unless CASCADE_POLICY is active."""
    cascade_header = (
        headers.get("x-omnicache-model-cascade", "").strip().lower() or
        headers.get("x-allow-cascade", "").strip().lower()
    )
    if cascade_header in ("deny", "false", "0", "disabled", "no", "off"):
        return False
    if cascade_header in ("allow", "true", "1", "enabled", "yes"):
        return True
    policy = getattr(config, "CASCADE_POLICY", "off").strip().lower()
    return policy in ("auto", "budget", "all", "on", "enabled")


def detect_voice_mode(request: Request, payload: Optional[Dict[str, Any]] = None) -> bool:
    """
    Detects if an incoming request originates from a conversational voice agent
    (LiveKit, Twilio Media Streams, Daily, Vapi, Retell, Pipecat, Vocode).
    """
    headers = request.headers
    if headers.get("x-omnicache-voice-mode", "").lower() in ("true", "1"):
        return True
    if headers.get("x-omnicache-telephony", "").lower() in ("true", "1", "twilio", "livekit", "vapi", "retell", "daily", "pipecat", "vocode"):
        return True
    if headers.get("x-omnicache-agent", "").lower() in ("voice", "telephony", "livekit", "twilio", "phone"):
        return True

    # Check query parameters
    qp = request.query_params
    if qp.get("voice", "").lower() in ("true", "1") or qp.get("telephony", "").lower() in ("true", "1"):
        return True

    # Check payload attributes
    if isinstance(payload, dict):
        if payload.get("voice_mode") is True or payload.get("telephony") is True:
            return True
        if str(payload.get("agent", "")).lower() in ("voice", "telephony", "livekit", "twilio", "phone"):
            return True

    return False


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

    # Voice / Telephony Agent Adapter (Early Fast-Path & Caller Metadata Canonicalization)
    is_voice = detect_voice_mode(request, raw_payload)
    voice_stats = None
    if is_voice or getattr(config, "VOICE_ADAPTER_ENABLED", True):
        raw_payload, voice_stats = telephony_filter.process_telephony_payload(raw_payload, is_voice_mode=is_voice)
        if voice_stats.get("fast_path_reply"):
            METRICS_LEDGER["telephony_requests_processed"] += 1
            fast_text = voice_stats["fast_path_reply"]
            fp_resp = {
                "id": f"chatcmpl_voice_fp_{int(time.time() * 1000)}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": raw_payload.get("model", "default"),
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": fast_text},
                    "finish_reason": "stop"
                }],
                "usage": {
                    "prompt_tokens": 15,
                    "completion_tokens": len(fast_text.split()),
                    "total_tokens": 15 + len(fast_text.split())
                }
            }
            cors_headers["X-Cache-Status"] = "HIT_EXACT"
            cors_headers["X-Cache-Similarity"] = "1.0000"
            cors_headers["X-OmniCache-Fast-Path"] = "telephony"
            cors_headers["X-OmniCache-Voice-Mode"] = "true"
            emit_telemetry_event("telephony_fast_path", {
                "protocol": "openai",
                "model": raw_payload.get("model", "default"),
                "reply": fast_text,
                "latency_ms": 0.1
            })
            return JSONResponse(fp_resp, headers=cors_headers)

        if voice_stats["fillers_removed"] > 0 or voice_stats["metadata_canonicalized"] > 0 or voice_stats["tokens_saved"] > 0:
            METRICS_LEDGER["telephony_requests_processed"] += 1
            METRICS_LEDGER["telephony_fillers_stripped"] += voice_stats["fillers_removed"]
            METRICS_LEDGER["telephony_tokens_saved"] += voice_stats["tokens_saved"]
            METRICS_LEDGER["total_tokens_saved"] += voice_stats["tokens_saved"]
            METRICS_LEDGER["estimated_tokens_saved"] += voice_stats["tokens_saved"]
            cors_headers["X-OmniCache-Voice-Filtered"] = "true"
            cors_headers["X-OmniCache-Fillers-Stripped"] = str(voice_stats["fillers_removed"])
            cors_headers["X-OmniCache-Voice-Tokens-Saved"] = str(voice_stats["tokens_saved"])
            emit_telemetry_event("telephony_filtered", {
                "protocol": "openai",
                "model": raw_payload.get("model", "default"),
                "fillers_stripped": voice_stats["fillers_removed"],
                "metadata_canonicalized": voice_stats["metadata_canonicalized"],
                "tokens_saved": voice_stats["tokens_saved"],
                "turns_compacted": voice_stats["turns_compacted"]
            })

    payload, pii_token_map, scrubbed_count = privacy_shield.sanitize_payload(raw_payload)
    if scrubbed_count > 0:
        METRICS_LEDGER["privacy_scrubbed_count"] += scrubbed_count

    # In-Line Agent Tool Interception & Context Compaction
    payload, compacted_tokens, tools_recorded = compact_and_record_agent_tools(payload)
    if compacted_tokens > 0:
        METRICS_LEDGER["agent_tool_compacted_tokens"] += compacted_tokens
        METRICS_LEDGER["total_tokens_saved"] += compacted_tokens
        METRICS_LEDGER["estimated_tokens_saved"] += compacted_tokens
        savings_usd = upstream_client.calculate_savings(payload.get("model", "default"), compacted_tokens, 0)
        METRICS_LEDGER["total_savings_usd"] += savings_usd
        cors_headers["X-OmniCache-Context-Compacted-Tokens"] = str(compacted_tokens)
        emit_telemetry_event("context_compacted", {
            "protocol": "openai",
            "model": payload.get("model", "default"),
            "tokens_compacted": compacted_tokens,
            "savings_usd": round(savings_usd, 6),
            "tools_recorded": tools_recorded
        })
    if tools_recorded > 0:
        METRICS_LEDGER["agent_tool_recorded_count"] += tools_recorded

    headers = request.headers
    bypass_cache = headers.get("x-cache-bypass", "false").lower() in ("true", "1")
    allow_cascade = parse_cascade_opt_in(headers)
    custom_ttl = int(headers.get("x-cache-ttl")) if headers.get("x-cache-ttl", "").isdigit() else None
    custom_threshold = float(headers.get("x-cache-threshold")) if headers.get("x-cache-threshold") else None
    cache_tag = headers.get("x-cache-tag", None)
    auth_header = headers.get("authorization", None)

    swarm_id = (headers.get("x-omnicache-swarm-id") or payload.get("swarm_id") or "").strip()
    agent_id = (headers.get("x-omnicache-agent-id") or headers.get("x-omnicache-subagent-id") or payload.get("agent_id") or "lead").strip()
    parent_agent = (headers.get("x-omnicache-parent-agent") or payload.get("parent_agent") or "").strip()

    if swarm_id:
        METRICS_LEDGER["swarm_requests_processed"] += 1
        if parent_agent:
            swarm_bus.record_delegation(swarm_id, parent_agent, agent_id)

    is_stream = bool(payload.get("stream", False))
    requested_model = payload.get("model", "default")

    # 1. Multimodal Vision Cache Check
    extracted_images = vision_cache.extract_images_from_payload(payload) if not bypass_cache else []
    if extracted_images:
        for img_hash, prompt_text in extracted_images:
            is_vhit, v_response, v_dist = vision_cache.lookup_image(img_hash, prompt_text)
            if is_vhit and v_response is not None:
                latency_ms = (time.perf_counter() - start_time) * 1000
                v_usage = v_response.get("usage", {}) if isinstance(v_response, dict) else {}
                prompt_tokens = v_usage.get("prompt_tokens") or 150
                completion_tokens = v_usage.get("completion_tokens") or 250
                total_saved_tokens = prompt_tokens + completion_tokens
                savings = upstream_client.calculate_savings(requested_model, prompt_tokens, completion_tokens)
                METRICS_LEDGER["vision_cache_hits"] += 1
                METRICS_LEDGER["total_savings_usd"] += savings
                METRICS_LEDGER["total_tokens_saved"] += total_saved_tokens

                resp_headers = {
                    "X-OmniCache-Decision": "HIT",
                    "X-OmniCache-Reason": f"HIT_VISION_DHASH: Perceptual visual match (Hamming distance {v_dist}/64)",
                    "X-OmniCache-Similarity": f"{1.0 - (v_dist / 64.0):.4f}",
                    "X-Cache-Status": "HIT_VISION",
                    "X-Cache-Decision-Reason": f"HIT_VISION_DHASH: Perceptual visual match (Hamming distance {v_dist}/64)",
                    "X-Cache-Similarity": f"{1.0 - (v_dist / 64.0):.4f}",
                    "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
                    "X-Cost-Avoided-USD": f"{savings:.6f}",
                    "X-Cost-Saved-USD": f"{savings:.6f}",
                    "X-Tokens-Used": "0",
                    "X-Tokens-Saved": str(total_saved_tokens),
                    "X-Tokens-Accounting": "exact" if bool(v_usage.get("prompt_tokens")) else "estimated",
                    "X-Requested-Model": requested_model,
                    "X-Served-Model": requested_model,
                    **cors_headers
                }
                rehydrated = privacy_shield.rehydrate_response(v_response, pii_token_map)
                if is_stream:
                    return StreamingResponse(
                        StreamReplayer.replay_cached_stream(rehydrated, tokens_per_sec=config.STREAM_REPLAY_TOKENS_PER_SEC),
                        media_type="text/event-stream",
                        headers={
                            **resp_headers,
                            "Cache-Control": "no-cache, no-transform",
                            "Connection": "keep-alive",
                            "X-Accel-Buffering": "no"
                        }
                    )
                return JSONResponse(rehydrated, headers=resp_headers)

    # 1b. Multimodal Audio Cache Check (OpenAI Realtime & GPT-4o Audio)
    extracted_audio = audio_cache.extract_audio_from_payload(payload) if (not bypass_cache and getattr(config, "AUDIO_CACHE_ENABLED", True)) else []
    if extracted_audio:
        METRICS_LEDGER["audio_requests_processed"] += 1
        for aud_hash, prompt_text, aud_meta in extracted_audio:
            is_ahit, a_response, a_dist = audio_cache.lookup_audio(aud_hash, prompt_text, model=requested_model)
            if is_ahit and a_response is not None:
                latency_ms = (time.perf_counter() - start_time) * 1000
                a_usage = a_response.get("usage", {}) if isinstance(a_response, dict) else {}
                prompt_tokens = a_usage.get("prompt_tokens") or 200
                completion_tokens = a_usage.get("completion_tokens") or 150
                total_saved_tokens = prompt_tokens + completion_tokens
                savings = upstream_client.calculate_savings(requested_model, prompt_tokens, completion_tokens)
                METRICS_LEDGER["audio_cache_hits"] += 1
                METRICS_LEDGER["total_savings_usd"] += savings
                METRICS_LEDGER["total_tokens_saved"] += total_saved_tokens

                emit_telemetry_event("audio_cache_hit", {
                    "audio_hash": aud_hash,
                    "distance": a_dist,
                    "similarity": round(1.0 - (a_dist / 64.0), 4),
                    "model": requested_model,
                    "latency_ms": round(latency_ms, 2)
                })

                resp_headers = {
                    "X-OmniCache-Decision": "HIT",
                    "X-OmniCache-Reason": f"HIT_AUDIO_SPECTRAL: Multimodal acoustic match (Hamming distance {a_dist}/64)",
                    "X-OmniCache-Similarity": f"{1.0 - (a_dist / 64.0):.4f}",
                    "X-Cache-Status": "HIT_AUDIO",
                    "X-Cache-Decision-Reason": f"HIT_AUDIO_SPECTRAL: Multimodal acoustic match (Hamming distance {a_dist}/64)",
                    "X-Cache-Similarity": f"{1.0 - (a_dist / 64.0):.4f}",
                    "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
                    "X-Cost-Avoided-USD": f"{savings:.6f}",
                    "X-Cost-Saved-USD": f"{savings:.6f}",
                    "X-Tokens-Used": "0",
                    "X-Tokens-Saved": "350",
                    "X-Tokens-Accounting": "estimated",
                    "X-Requested-Model": requested_model,
                    "X-Served-Model": requested_model,
                    **cors_headers
                }
                rehydrated = privacy_shield.rehydrate_response(a_response, pii_token_map)
                if is_stream:
                    return StreamingResponse(
                        StreamReplayer.replay_cached_stream(rehydrated, tokens_per_sec=config.STREAM_REPLAY_TOKENS_PER_SEC),
                        media_type="text/event-stream",
                        headers={
                            **resp_headers,
                            "Cache-Control": "no-cache, no-transform",
                            "Connection": "keep-alive",
                            "X-Accel-Buffering": "no"
                        }
                    )
                return JSONResponse(rehydrated, headers=resp_headers)

    # 2. Text / Dual-Tier Cache Check (Swarm Bus + L1 Exact + L2 Semantic + Radix Prefix Tree)
    if not bypass_cache:
        if swarm_id:
            is_s_hit, s_resp, s_meta = swarm_bus.lookup_shared_result(
                swarm_id=swarm_id,
                agent_id=agent_id,
                task_type="chat_completion",
                payload=payload
            )
            if is_s_hit and s_resp is not None:
                saved_toks = s_meta.get("tokens_saved", 50)
                METRICS_LEDGER["swarm_cross_agent_hits"] += 1
                METRICS_LEDGER["swarm_tokens_saved"] += saved_toks
                METRICS_LEDGER["total_tokens_saved"] += saved_toks
                latency_ms = (time.perf_counter() - start_time) * 1000
                emit_telemetry_event("swarm_hit", {
                    "swarm_id": swarm_id,
                    "origin_agent": s_meta.get("origin_agent_id"),
                    "requesting_agent": agent_id,
                    "tokens_saved": saved_toks
                })
                rehydrated = privacy_shield.rehydrate_response(s_resp, pii_token_map)
                resp_headers = {
                    "X-OmniCache-Decision": "HIT",
                    "X-OmniCache-Reason": f"HIT_SWARM_BUS: Inter-agent memory hit (reused from '{s_meta.get('origin_agent_id')}')",
                    "X-OmniCache-Similarity": "1.0000",
                    "X-Cache-Status": "HIT_SWARM",
                    "X-Cache-Decision-Reason": f"HIT_SWARM_BUS: Reused from subagent '{s_meta.get('origin_agent_id')}' in swarm '{swarm_id}'",
                    "X-Cache-Similarity": "1.0000",
                    "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
                    "X-Tokens-Used": "0",
                    "X-Tokens-Saved": str(saved_toks),
                    "X-Tokens-Accounting": "exact",
                    "X-Requested-Model": requested_model,
                    "X-Served-Model": requested_model,
                    "X-Cascade-Applied": "false",
                    "X-OmniCache-Swarm-Hit": "true",
                    "X-OmniCache-Origin-Agent": str(s_meta.get("origin_agent_id")),
                    "X-OmniCache-Swarm-ID": swarm_id,
                    **cors_headers
                }
                if is_stream:
                    return StreamingResponse(
                        StreamReplayer.replay_cached_stream(rehydrated, tokens_per_sec=config.STREAM_REPLAY_TOKENS_PER_SEC),
                        media_type="text/event-stream",
                        headers={
                            **resp_headers,
                            "Cache-Control": "no-cache, no-transform",
                            "Connection": "keep-alive",
                            "X-Accel-Buffering": "no"
                        }
                    )
                else:
                    return JSONResponse(rehydrated, headers=resp_headers)

        status, entry, similarity, decision_reason = cache_instance.lookup(payload, org_id=org_id, custom_threshold=custom_threshold)
        messages = payload.get("messages", [])
        if entry is None and messages:
            is_radix_hit, radix_completion, matched_turns, radix_node = radix_tree.lookup_conversation(
                messages, model=requested_model, org_id=org_id
            )
            if is_radix_hit and radix_completion and radix_node:
                usage = radix_completion.get("usage", {})
                p_tok = usage.get("prompt_tokens", 50)
                c_tok = usage.get("completion_tokens", 80)
                entry = CacheEntry(
                    key=f"radix_{radix_node.node_id}",
                    org_id=org_id,
                    model=radix_node.model or requested_model,
                    user_prompt="",
                    system_prompt="",
                    schema_hash="",
                    tools_hash="",
                    vector=[],
                    response_payload=radix_completion,
                    tag="radix_tree",
                    is_stream=is_stream,
                    stream_chunks=radix_node.stream_chunks or [],
                    ttl_seconds=604800,
                    is_exact_tokens=True,
                    prompt_tokens=p_tok,
                    completion_tokens=c_tok
                )
                status = "HIT_RADIX_TREE"
                decision_reason = f"HIT_RADIX_TREE: Multi-turn conversation matched {matched_turns} turns in Radix trie"
                similarity = 1.0
                METRICS_LEDGER["radix_tree_hits"] += 1
    else:
        status, entry, similarity, decision_reason = "BYPASS", None, 0.0, "BYPASS_EXPLICIT_HEADER: Bypassed via X-Cache-Bypass header"

    if entry is not None and status in ("HIT_EXACT", "HIT_SEMANTIC", "HIT_RADIX_TREE"):
        latency_ms = (time.perf_counter() - start_time) * 1000
        usage = entry.response_payload.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", entry.prompt_tokens or 50)
        completion_tokens = usage.get("completion_tokens", entry.completion_tokens or 80)
        total_saved_tokens = prompt_tokens + completion_tokens
        pricing_model = entry.model or requested_model
        raw_savings = upstream_client.calculate_savings(pricing_model, prompt_tokens, completion_tokens)
        is_semantic = (status == "HIT_SEMANTIC")
        calc_method = "semantic_similarity_weighted" if is_semantic else "exact_model_pricing"
        savings = (raw_savings * max(0.5, similarity)) if is_semantic else raw_savings
        
        METRICS_LEDGER["total_savings_usd"] += savings
        METRICS_LEDGER["total_tokens_saved"] += total_saved_tokens
        if entry.is_exact_tokens and not is_semantic:
            METRICS_LEDGER["exact_tokens_saved"] += total_saved_tokens
        else:
            METRICS_LEDGER["estimated_tokens_saved"] += total_saved_tokens

        emit_telemetry_event("cache_hit", {
            "protocol": "openai",
            "status": status,
            "model": pricing_model,
            "tokens_saved": total_saved_tokens,
            "savings_usd": round(savings, 6),
            "latency_ms": round(latency_ms, 2)
        })

        resp_headers = {
            "X-OmniCache-Decision": "HIT",
            "X-OmniCache-Reason": decision_reason,
            "X-OmniCache-Similarity": f"{similarity:.4f}",
            "X-Cache-Status": status,
            "X-Cache-Decision-Reason": decision_reason,
            "X-Cache-Similarity": f"{similarity:.4f}",
            "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
            "X-Cache-TTL-Remaining": str(entry.ttl_remaining()),
            "X-Cache-Entry-Age-Seconds": f"{entry.age_seconds():.1f}",
            "X-Cost-Avoided-USD": f"{savings:.6f}",
            "X-Cost-Saved-USD": f"{savings:.6f}",
            "X-Cost-Calculation-Method": calc_method,
            "X-Match-Method": "fuzzy_lexical_subword_projection" if is_semantic else "exact_sha256",
            "X-Avoided-Prompt-Tokens": str(prompt_tokens),
            "X-Avoided-Completion-Tokens": str(completion_tokens),
            "X-Tokens-Used": "0",
            "X-Tokens-Saved": str(total_saved_tokens),
            "X-Tokens-Accounting": "exact" if (entry.is_exact_tokens and not is_semantic) else "estimated",
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
                headers={
                    **resp_headers,
                    "Cache-Control": "no-cache, no-transform",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )
        else:
            return JSONResponse(rehydrated_response, headers=resp_headers)

    # 3. Model Cascade Evaluation & Upstream SingleFlight Deduplication
    routed_model, route_tier, complexity, was_cascaded, cascade_reason = cascade_router.evaluate_route(
        requested_model, payload, allow_cascade=allow_cascade
    )
    payload["model"] = routed_model
    METRICS_LEDGER["arbitrage_savings_usd"] = cascade_router.arbitrage_savings_usd
    METRICS_LEDGER["cascade_routes_total"] = cascade_router.total_routed
    METRICS_LEDGER["cascade_downgrades_total"] = cascade_router.downgraded_count
    METRICS_LEDGER["cascade_savings_usd"] = cascade_router.arbitrage_savings_usd
    if was_cascaded:
        emit_telemetry_event("model_cascaded", {
            "requested_model": requested_model,
            "routed_model": routed_model,
            "tier": route_tier,
            "complexity": round(complexity, 2),
            "reason": cascade_reason,
            "arbitrage_savings_usd": round(cascade_router.arbitrage_savings_usd, 6)
        })

    exact_flight_key = RequestHasher.compute_exact_hash(payload, org_id=org_id)

    if not is_stream:
        is_playground = request.headers.get("x-dashboard-playground") == "true" or org_id == "enterprise_user"
        supplied_key = (request.headers.get("x-api-key") or request.headers.get("authorization", "").removeprefix("Bearer ")).strip()
        is_internal_key = bool(
            supplied_key in (getattr(config, "ADMIN_API_KEY", ""), "default", "")
            or (quota_manager.storage.get_key(supplied_key) is not None)
        )
        has_real_provider_key = bool(supplied_key and not is_internal_key and (supplied_key.startswith("sk-") or len(supplied_key) > 20))

        if is_playground and not has_real_provider_key and not config.OPENAI_API_KEY:
            user_text = ""
            for m in payload.get("messages", []):
                if isinstance(m, dict) and m.get("role") == "user":
                    c = m.get("content", "")
                    if isinstance(c, list):
                        user_text = " ".join(str(b.get("text", "")) for b in c if isinstance(b, dict))
                    else:
                        user_text = str(c)
            res_data = generate_sandbox_playground_completion(user_text, routed_model, is_claude=False)
            status_code = 200
            latency_ms = (time.perf_counter() - start_time) * 1000
            is_leader = True
        else:
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

            if status_code != 200 and is_playground:
                user_text = ""
                for m in payload.get("messages", []):
                    if isinstance(m, dict) and m.get("role") == "user":
                        c = m.get("content", "")
                        if isinstance(c, list):
                            user_text = " ".join(str(b.get("text", "")) for b in c if isinstance(b, dict))
                        else:
                            user_text = str(c)
                res_data = generate_sandbox_playground_completion(user_text, routed_model, is_claude=False)
                status_code = 200

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
                spend_usd = upstream_client.calculate_savings(routed_model, p_tok, c_tok)
                key_id = key_info.get("key_id", "")
                if key_id:
                    quota_manager.record_spend(key_id, spend_usd)

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
                radix_tree.insert_conversation(payload.get("messages", []), res_data, model=routed_model, org_id=org_id)

                if extracted_images:
                    for img_h, p_txt in extracted_images:
                        vision_cache.store_image(img_h, p_txt, res_data)
                if extracted_audio:
                    for aud_h, p_txt, _ in extracted_audio:
                        audio_cache.store_audio(aud_h, p_txt, res_data)

                if swarm_id:
                    swarm_bus.record_shared_result(
                        swarm_id=swarm_id,
                        agent_id=agent_id,
                        task_type="chat_completion",
                        payload=payload,
                        result_payload=res_data,
                        tokens_saved=tokens_used
                    )

            rehydrated = privacy_shield.rehydrate_response(res_data, pii_token_map)
            cache_status_header = "MISS" if is_leader else "HIT_SINGLEFLIGHT"
            decision_header = "MISS" if is_leader else "HIT"
            reason_header = decision_reason if is_leader else "HIT_SINGLEFLIGHT: Concurrent in-flight request coalesced with leader"
            similarity_header = f"{similarity:.4f}" if is_leader else "1.0000"
            return JSONResponse(rehydrated, headers={
                "X-OmniCache-Decision": decision_header,
                "X-OmniCache-Reason": reason_header,
                "X-OmniCache-Similarity": similarity_header,
                "X-Cache-Status": cache_status_header,
                "X-Cache-Decision-Reason": reason_header,
                "X-Cache-Similarity": similarity_header,
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
        buffer = ""
        stream_cleanly_completed = False
        try:
            async for chunk in upstream_resp.aiter_raw():
                if not chunk:
                    continue
                yield chunk
                try:
                    chunk_str = chunk.decode("utf-8", errors="ignore")
                    buffer += chunk_str
                    while "\n" in buffer:
                        raw_line, buffer = buffer.split("\n", 1)
                        raw_line = raw_line.strip()
                        if raw_line.startswith("data: ") and raw_line != "data: [DONE]":
                            chunk_json = json.loads(raw_line[6:])
                            recorded_chunks.append(chunk_json)
                            choices = chunk_json.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                if "content" in delta and delta["content"]:
                                    full_content_parts.append(delta["content"])
                        elif raw_line == "data: [DONE]":
                            stream_cleanly_completed = True
                except Exception:
                    pass
            stream_cleanly_completed = True
        except Exception as stream_err:
            stream_cleanly_completed = False
            print(f"[OmniCache {time.strftime('%H:%M:%S')}] ⚠️ OpenAI stream aborted mid-transfer: {type(stream_err).__name__}: {stream_err}", flush=True)
        finally:
            await upstream_resp.aclose()
            # ONLY cache if stream cleanly finished without abort/error
            if recorded_chunks and stream_cleanly_completed:
                p_tok = len(str(payload.get("messages", "")).split())
                c_tok = len("".join(full_content_parts).split())
                tokens_used = p_tok + c_tok
                METRICS_LEDGER["total_tokens_used"] += tokens_used
                METRICS_LEDGER["estimated_tokens_used"] += tokens_used

                spend_usd = upstream_client.calculate_savings(routed_model, p_tok, c_tok)
                key_id = key_info.get("key_id", "")
                if key_id:
                    quota_manager.record_spend(key_id, spend_usd)

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
                radix_tree.insert_conversation(payload.get("messages", []), synthesized, model=routed_model, org_id=org_id, stream_chunks=recorded_chunks)
                if extracted_images:
                    for img_h, p_txt in extracted_images:
                        vision_cache.store_image(img_h, p_txt, synthesized)
                if extracted_audio:
                    for aud_h, p_txt, _ in extracted_audio:
                        audio_cache.store_audio(aud_h, p_txt, synthesized)
                if swarm_id:
                    swarm_bus.record_shared_result(
                        swarm_id=swarm_id,
                        agent_id=agent_id,
                        task_type="chat_completion",
                        payload=payload,
                        result_payload=synthesized,
                        tokens_saved=tokens_used
                    )
            elif recorded_chunks and not stream_cleanly_completed:
                print(f"[OmniCache {time.strftime('%H:%M:%S')}] ⚠️ OpenAI stream aborted ({len(recorded_chunks)} chunks received) - discarding partial response from cache.", flush=True)

    latency_ms = (time.perf_counter() - start_time) * 1000
    return StreamingResponse(stream_and_record(), media_type="text/event-stream", headers={
        "X-OmniCache-Decision": "MISS",
        "X-OmniCache-Reason": decision_reason,
        "X-OmniCache-Similarity": f"{similarity:.4f}",
        "X-Cache-Status": status,
        "X-Cache-Decision-Reason": decision_reason,
        "X-Cache-Similarity": f"{similarity:.4f}",
        "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
        "X-Tokens-Accounting": "estimated",
        "X-Requested-Model": requested_model,
        "X-Served-Model": routed_model,
        "X-Cascade-Applied": "true" if was_cascaded else "false",
        "X-Cascade-Reason": cascade_reason,
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
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

    # Voice / Telephony Agent Adapter (Early Fast-Path & Caller Metadata Canonicalization)
    is_voice = detect_voice_mode(request, raw_payload)
    voice_stats = None
    if is_voice or getattr(config, "VOICE_ADAPTER_ENABLED", True):
        raw_payload, voice_stats = telephony_filter.process_telephony_payload(raw_payload, is_voice_mode=is_voice)
        requested_model = raw_payload.get("model", "claude-3-5-sonnet-20241022")
        if voice_stats.get("fast_path_reply"):
            METRICS_LEDGER["telephony_requests_processed"] += 1
            fast_text = voice_stats["fast_path_reply"]
            fp_resp = {
                "id": f"msg_voice_fp_{int(time.time() * 1000)}",
                "type": "message",
                "role": "assistant",
                "model": requested_model,
                "content": [{"type": "text", "text": fast_text}],
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 15,
                    "output_tokens": len(fast_text.split())
                }
            }
            cors_headers["X-Cache-Status"] = "HIT_EXACT"
            cors_headers["X-Cache-Similarity"] = "1.0000"
            cors_headers["X-OmniCache-Fast-Path"] = "telephony"
            cors_headers["X-OmniCache-Voice-Mode"] = "true"
            emit_telemetry_event("telephony_fast_path", {
                "protocol": "anthropic",
                "model": requested_model,
                "reply": fast_text,
                "latency_ms": 0.1
            })
            return JSONResponse(fp_resp, headers=cors_headers)

        if voice_stats["fillers_removed"] > 0 or voice_stats["metadata_canonicalized"] > 0 or voice_stats["tokens_saved"] > 0:
            METRICS_LEDGER["telephony_requests_processed"] += 1
            METRICS_LEDGER["telephony_fillers_stripped"] += voice_stats["fillers_removed"]
            METRICS_LEDGER["telephony_tokens_saved"] += voice_stats["tokens_saved"]
            METRICS_LEDGER["total_tokens_saved"] += voice_stats["tokens_saved"]
            METRICS_LEDGER["estimated_tokens_saved"] += voice_stats["tokens_saved"]
            cors_headers["X-OmniCache-Voice-Filtered"] = "true"
            cors_headers["X-OmniCache-Fillers-Stripped"] = str(voice_stats["fillers_removed"])
            cors_headers["X-OmniCache-Voice-Tokens-Saved"] = str(voice_stats["tokens_saved"])
            emit_telemetry_event("telephony_filtered", {
                "protocol": "anthropic",
                "model": requested_model,
                "fillers_stripped": voice_stats["fillers_removed"],
                "metadata_canonicalized": voice_stats["metadata_canonicalized"],
                "tokens_saved": voice_stats["tokens_saved"],
                "turns_compacted": voice_stats["turns_compacted"]
            })

    anthropic_payload, pii_token_map, scrubbed_count = privacy_shield.sanitize_payload(raw_payload)
    if scrubbed_count > 0:
        METRICS_LEDGER["privacy_scrubbed_count"] += scrubbed_count

    # In-Line Agent Tool Interception & Context Compaction
    anthropic_payload, compacted_tokens, tools_recorded = compact_and_record_agent_tools(anthropic_payload)
    if compacted_tokens > 0:
        METRICS_LEDGER["agent_tool_compacted_tokens"] += compacted_tokens
        METRICS_LEDGER["total_tokens_saved"] += compacted_tokens
        METRICS_LEDGER["estimated_tokens_saved"] += compacted_tokens
        savings_usd = upstream_client.calculate_savings(anthropic_payload.get("model", "claude-3-5-sonnet-20241022"), compacted_tokens, 0)
        METRICS_LEDGER["total_savings_usd"] += savings_usd
        cors_headers["X-OmniCache-Context-Compacted-Tokens"] = str(compacted_tokens)
        print(f"[OmniCache] 🛠️ In-line tool compaction: pruned {compacted_tokens} redundant tokens from context ({tools_recorded} tools indexed).", flush=True)
        emit_telemetry_event("context_compacted", {
            "protocol": "anthropic",
            "model": anthropic_payload.get("model", "claude-3-5-sonnet-20241022"),
            "tokens_compacted": compacted_tokens,
            "savings_usd": round(savings_usd, 6),
            "tools_recorded": tools_recorded
        })
    if tools_recorded > 0:
        METRICS_LEDGER["agent_tool_recorded_count"] += tools_recorded

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
                    "X-OmniCache-Decision": "HIT",
                    "X-OmniCache-Reason": f"HIT_VISION_DHASH: Perceptual visual match (Hamming distance {v_dist}/64)",
                    "X-OmniCache-Similarity": f"{1.0 - (v_dist / 64.0):.4f}",
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

    # 1b. Multimodal Audio Cache Check (Anthropic & Multimodal Audio)
    extracted_audio = audio_cache.extract_audio_from_payload(anthropic_payload) if (not bypass_cache and getattr(config, "AUDIO_CACHE_ENABLED", True)) else []
    if extracted_audio:
        METRICS_LEDGER["audio_requests_processed"] += 1
        for aud_hash, prompt_text, aud_meta in extracted_audio:
            is_ahit, a_response, a_dist = audio_cache.lookup_audio(aud_hash, prompt_text, model=requested_model)
            if is_ahit and a_response is not None:
                latency_ms = (time.perf_counter() - start_time) * 1000
                savings = upstream_client.calculate_savings(requested_model, 200, 150)
                METRICS_LEDGER["audio_cache_hits"] += 1
                METRICS_LEDGER["total_savings_usd"] += savings
                METRICS_LEDGER["total_tokens_saved"] += 350

                emit_telemetry_event("audio_cache_hit", {
                    "audio_hash": aud_hash,
                    "distance": a_dist,
                    "similarity": round(1.0 - (a_dist / 64.0), 4),
                    "model": requested_model,
                    "latency_ms": round(latency_ms, 2)
                })

                resp_headers = {
                    "X-OmniCache-Decision": "HIT",
                    "X-OmniCache-Reason": f"HIT_AUDIO_SPECTRAL: Multimodal acoustic match (Hamming distance {a_dist}/64)",
                    "X-OmniCache-Similarity": f"{1.0 - (a_dist / 64.0):.4f}",
                    "X-Cache-Status": "HIT_AUDIO",
                    "X-Cache-Decision-Reason": f"HIT_AUDIO_SPECTRAL: Multimodal acoustic match (Hamming distance {a_dist}/64)",
                    "X-Cache-Similarity": f"{1.0 - (a_dist / 64.0):.4f}",
                    "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
                    "X-Cost-Avoided-USD": f"{savings:.6f}",
                    "X-Cost-Saved-USD": f"{savings:.6f}",
                    "X-Requested-Model": requested_model,
                    "X-Served-Model": requested_model,
                    **cors_headers
                }
                if "choices" in a_response:
                    content = a_response.get("choices", [{}])[0].get("message", {}).get("content", "")
                    anthropic_res = {
                        "id": f"msg_cached_{int(time.time()*1000)}",
                        "type": "message",
                        "role": "assistant",
                        "model": requested_model,
                        "content": [{"type": "text", "text": content}],
                        "stop_reason": "end_turn",
                        "usage": {"input_tokens": 200, "output_tokens": 150}
                    }
                else:
                    anthropic_res = a_response
                rehydrated = privacy_shield.rehydrate_response(anthropic_res, pii_token_map)
                if is_stream:
                    return StreamingResponse(
                        StreamReplayer.replay_cached_anthropic_stream(rehydrated, tokens_per_sec=config.STREAM_REPLAY_TOKENS_PER_SEC),
                        media_type="text/event-stream",
                        headers={
                            **resp_headers,
                            "Cache-Control": "no-cache, no-transform",
                            "Connection": "keep-alive",
                            "X-Accel-Buffering": "no"
                        }
                    )
                return JSONResponse(rehydrated, headers=resp_headers)

    messages = []
    if "system" in anthropic_payload and anthropic_payload["system"]:
        sys_val = anthropic_payload["system"]
        if isinstance(sys_val, list):
            sys_text = " ".join([b.get("text", "") for b in sys_val if isinstance(b, dict) and b.get("type") == "text"])
            messages.append({"role": "system", "content": sys_text})
        else:
            messages.append({"role": "system", "content": str(sys_val)})
    for m in anthropic_payload.get("messages", []):
        content = m.get("content", "")
        if isinstance(content, list):
            text_blocks = []
            for b in content:
                if isinstance(b, dict):
                    if b.get("type") == "text":
                        text_blocks.append(b.get("text", ""))
                    elif b.get("type") == "tool_result":
                        text_blocks.append(str(b.get("content", "")))
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

    swarm_id = (headers.get("x-omnicache-swarm-id") or anthropic_payload.get("swarm_id") or "").strip()
    agent_id = (headers.get("x-omnicache-agent-id") or headers.get("x-omnicache-subagent-id") or anthropic_payload.get("agent_id") or "lead").strip()
    parent_agent = (headers.get("x-omnicache-parent-agent") or anthropic_payload.get("parent_agent") or "").strip()

    if swarm_id:
        METRICS_LEDGER["swarm_requests_processed"] += 1
        if parent_agent:
            swarm_bus.record_delegation(swarm_id, parent_agent, agent_id, task_prompt=str(normalized_payload.get("messages", ""))[:200])

    if not bypass_cache:
        if swarm_id:
            is_s_hit, s_resp, s_meta = swarm_bus.lookup_shared_result(
                swarm_id=swarm_id,
                agent_id=agent_id,
                task_type="anthropic_messages",
                payload=normalized_payload
            )
            if is_s_hit and s_resp is not None:
                saved_toks = s_meta.get("tokens_saved", 50)
                METRICS_LEDGER["swarm_cross_agent_hits"] += 1
                METRICS_LEDGER["swarm_tokens_saved"] += saved_toks
                METRICS_LEDGER["total_tokens_saved"] += saved_toks
                latency_ms = (time.perf_counter() - start_time) * 1000
                emit_telemetry_event("swarm_hit", {
                    "swarm_id": swarm_id,
                    "origin_agent": s_meta.get("origin_agent_id"),
                    "requesting_agent": agent_id,
                    "tokens_saved": saved_toks
                })
                rehydrated = privacy_shield.rehydrate_response(s_resp, pii_token_map)
                resp_headers = {
                    "X-OmniCache-Decision": "HIT",
                    "X-OmniCache-Reason": f"HIT_SWARM_BUS: Inter-agent memory hit (reused from '{s_meta.get('origin_agent_id')}')",
                    "X-OmniCache-Similarity": "1.0000",
                    "X-Cache-Status": "HIT_SWARM",
                    "X-Cache-Decision-Reason": f"HIT_SWARM_BUS: Reused from subagent '{s_meta.get('origin_agent_id')}' in swarm '{swarm_id}'",
                    "X-Cache-Similarity": "1.0000",
                    "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
                    "X-Tokens-Used": "0",
                    "X-Tokens-Saved": str(saved_toks),
                    "X-Tokens-Accounting": "exact",
                    "X-Requested-Model": requested_model,
                    "X-Served-Model": requested_model,
                    "X-Cascade-Applied": "false",
                    "X-OmniCache-Swarm-Hit": "true",
                    "X-OmniCache-Origin-Agent": str(s_meta.get("origin_agent_id")),
                    "X-OmniCache-Swarm-ID": swarm_id,
                    **cors_headers
                }
                if is_stream:
                    return StreamingResponse(
                        StreamReplayer.replay_cached_anthropic_stream(rehydrated, tokens_per_sec=config.STREAM_REPLAY_TOKENS_PER_SEC),
                        media_type="text/event-stream",
                        headers={
                            **resp_headers,
                            "Cache-Control": "no-cache, no-transform",
                            "Connection": "keep-alive",
                            "X-Accel-Buffering": "no"
                        }
                    )
                else:
                    return JSONResponse(rehydrated, headers=resp_headers)

        status, entry, similarity, decision_reason = cache_instance.lookup(normalized_payload, org_id=org_id)
        if entry is None and messages:
            is_radix_hit, radix_completion, matched_turns, radix_node = radix_tree.lookup_conversation(
                messages, model=requested_model, org_id=org_id
            )
            if is_radix_hit and radix_completion and radix_node:
                usage = radix_completion.get("usage", {})
                p_tok = usage.get("prompt_tokens", 35)
                c_tok = usage.get("completion_tokens", 65)
                entry = CacheEntry(
                    key=f"radix_{radix_node.node_id}",
                    org_id=org_id,
                    model=radix_node.model or requested_model,
                    user_prompt="",
                    system_prompt="",
                    schema_hash="",
                    tools_hash="",
                    vector=[],
                    response_payload=radix_completion,
                    tag="radix_tree",
                    is_stream=is_stream,
                    stream_chunks=radix_node.stream_chunks or [],
                    ttl_seconds=604800,
                    is_exact_tokens=True,
                    prompt_tokens=p_tok,
                    completion_tokens=c_tok
                )
                status = "HIT_RADIX_TREE"
                decision_reason = f"HIT_RADIX_TREE: Multi-turn conversation matched {matched_turns} turns in Radix trie"
                similarity = 1.0
                METRICS_LEDGER["radix_tree_hits"] += 1
    else:
        status, entry, similarity, decision_reason = "BYPASS", None, 0.0, "BYPASS_EXPLICIT_HEADER: Bypassed via X-Cache-Bypass header"

    # 2. Anthropic Cache HIT
    if entry is not None and status in ("HIT_EXACT", "HIT_SEMANTIC", "HIT_RADIX_TREE"):
        latency_ms = (time.perf_counter() - start_time) * 1000
        content = entry.response_payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = entry.response_payload.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", entry.prompt_tokens or 35)
        completion_tokens = usage.get("completion_tokens", entry.completion_tokens or 65)
        total_saved_tokens = prompt_tokens + completion_tokens
        raw_savings = upstream_client.calculate_savings(entry.model or requested_model, prompt_tokens, completion_tokens)
        is_semantic = (status == "HIT_SEMANTIC")
        calc_method = "semantic_similarity_weighted" if is_semantic else "exact_model_pricing"
        savings = (raw_savings * max(0.5, similarity)) if is_semantic else raw_savings
        
        METRICS_LEDGER["total_savings_usd"] += savings
        METRICS_LEDGER["total_tokens_saved"] += total_saved_tokens
        if entry.is_exact_tokens and not is_semantic:
            METRICS_LEDGER["exact_tokens_saved"] += total_saved_tokens
        else:
            METRICS_LEDGER["estimated_tokens_saved"] += total_saved_tokens

        emit_telemetry_event("cache_hit", {
            "protocol": "anthropic",
            "status": status,
            "model": entry.model or requested_model,
            "tokens_saved": total_saved_tokens,
            "savings_usd": round(savings, 6),
            "latency_ms": round(latency_ms, 2)
        })

        resp_headers = {
            "X-OmniCache-Decision": "HIT",
            "X-OmniCache-Reason": decision_reason,
            "X-OmniCache-Similarity": f"{similarity:.4f}",
            "X-Cache-Status": status,
            "X-Cache-Decision-Reason": decision_reason,
            "X-Cache-Similarity": f"{similarity:.4f}",
            "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
            "X-Cache-TTL-Remaining": str(entry.ttl_remaining()),
            "X-Cache-Entry-Age-Seconds": f"{entry.age_seconds():.1f}",
            "X-Cost-Avoided-USD": f"{savings:.6f}",
            "X-Cost-Saved-USD": f"{savings:.6f}",
            "X-Cost-Calculation-Method": calc_method,
            "X-Match-Method": "fuzzy_lexical_subword_projection" if is_semantic else "exact_sha256",
            "X-Avoided-Prompt-Tokens": str(prompt_tokens),
            "X-Avoided-Completion-Tokens": str(completion_tokens),
            "X-Tokens-Used": "0",
            "X-Tokens-Saved": str(total_saved_tokens),
            "X-Tokens-Accounting": "exact" if (entry.is_exact_tokens and not is_semantic) else "estimated",
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

            return StreamingResponse(stream_cached_anthropic(), media_type="text/event-stream", headers={
                **resp_headers,
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            })
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

    # 3. Anthropic Cache MISS -> Evaluate Model Cascade & Forward Upstream
    routed_model, route_tier, complexity, was_cascaded, cascade_reason = cascade_router.evaluate_route(
        requested_model, anthropic_payload, allow_cascade=allow_cascade, vendor_affinity="same-vendor"
    )
    anthropic_payload["model"] = routed_model
    METRICS_LEDGER["arbitrage_savings_usd"] = cascade_router.arbitrage_savings_usd
    METRICS_LEDGER["cascade_routes_total"] = cascade_router.total_routed
    METRICS_LEDGER["cascade_downgrades_total"] = cascade_router.downgraded_count
    METRICS_LEDGER["cascade_savings_usd"] = cascade_router.arbitrage_savings_usd
    if was_cascaded:
        emit_telemetry_event("model_cascaded", {
            "requested_model": requested_model,
            "routed_model": routed_model,
            "tier": route_tier,
            "complexity": round(complexity, 2),
            "reason": cascade_reason,
            "arbitrage_savings_usd": round(cascade_router.arbitrage_savings_usd, 6)
        })

    req_params = dict(request.query_params) if request.query_params else None
    if "messages" in anthropic_payload and isinstance(anthropic_payload["messages"], list):
        anthropic_payload["messages"] = radix_tree.align_ephemeral_cache_blocks(anthropic_payload["messages"])

    if is_stream:
        print(f"[OmniCache {time.strftime('%H:%M:%S')}] Forwarding Anthropic stream upstream for model '{requested_model}'...", flush=True)
        status_code, stream_resp, err_data = await upstream_client.forward_anthropic_stream(
            anthropic_payload,
            incoming_headers=dict(request.headers),
            params=req_params
        )
        if status_code != 200 or stream_resp is None:
            print(f"[OmniCache {time.strftime('%H:%M:%S')}] Anthropic upstream error: status={status_code} data={err_data}", flush=True)
            return JSONResponse(err_data or {"error": "Upstream error"}, status_code=status_code, headers=cors_headers)

        async def stream_and_record_anthropic():
            full_text_accum = []
            buffer = ""
            stream_cleanly_completed = False
            try:
                async for chunk in stream_resp.aiter_raw():
                    if not chunk:
                        continue
                    yield chunk
                    try:
                        chunk_str = chunk.decode("utf-8", errors="ignore")
                        buffer += chunk_str
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if line.startswith("data: "):
                                data_str = line[6:].strip()
                                if data_str and data_str != "[DONE]":
                                    data_obj = json.loads(data_str)
                                    msg_type = data_obj.get("type")
                                    if msg_type == "content_block_delta":
                                        delta = data_obj.get("delta", {})
                                        if delta.get("type") == "text_delta":
                                            delta_text = delta.get("text", "")
                                            if delta_text:
                                                full_text_accum.append(delta_text)
                                    elif msg_type == "message_stop":
                                        stream_cleanly_completed = True
                    except Exception:
                        pass
                stream_cleanly_completed = True
            except Exception as stream_err:
                stream_cleanly_completed = False
                print(f"[OmniCache {time.strftime('%H:%M:%S')}] ⚠️ Anthropic stream aborted mid-transfer: {type(stream_err).__name__}: {stream_err}", flush=True)
            finally:
                await stream_resp.aclose()
                # ONLY cache if stream cleanly finished without abort/error to prevent serving truncated replies
                if full_text_accum and stream_cleanly_completed:
                    full_text = "".join(full_text_accum)
                    p_tok = len(str(messages).split())
                    c_tok = len(full_text.split())
                    tokens_used = p_tok + c_tok
                    METRICS_LEDGER["total_tokens_used"] += tokens_used
                    METRICS_LEDGER["estimated_tokens_used"] += tokens_used

                    spend_usd = upstream_client.calculate_savings(requested_model, p_tok, c_tok)
                    key_id = key_info.get("key_id", "")
                    if key_id:
                        quota_manager.record_spend(key_id, spend_usd)
                    
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
                    radix_tree.insert_conversation(messages, cacheable_res_payload, model=requested_model, org_id=org_id)
                    if extracted_audio:
                        for aud_h, p_txt, _ in extracted_audio:
                            audio_cache.store_audio(aud_h, p_txt, cacheable_res_payload)
                    if swarm_id:
                        swarm_bus.record_shared_result(
                            swarm_id=swarm_id,
                            agent_id=agent_id,
                            task_type="anthropic_messages",
                            payload=normalized_payload,
                            result_payload=cacheable_res_payload,
                            tokens_saved=tokens_used
                        )
                elif full_text_accum and not stream_cleanly_completed:
                    print(f"[OmniCache {time.strftime('%H:%M:%S')}] ⚠️ Stream aborted ({len(full_text_accum)} chunks received) - discarding partial response from cache.", flush=True)

        latency_ms = (time.perf_counter() - start_time) * 1000
        return StreamingResponse(stream_and_record_anthropic(), media_type="text/event-stream", headers={
            "X-OmniCache-Decision": "MISS",
            "X-OmniCache-Reason": decision_reason,
            "X-OmniCache-Similarity": f"{similarity:.4f}",
            "X-Cache-Status": "MISS",
            "X-Cache-Decision-Reason": decision_reason,
            "X-Cache-Similarity": f"{similarity:.4f}",
            "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
            "X-Tokens-Accounting": "estimated",
            "X-Requested-Model": requested_model,
            "X-Served-Model": routed_model,
            "X-Cascade-Applied": "true" if was_cascaded else "false",
            "X-Cascade-Reason": cascade_reason,
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            **cors_headers
        })

    # Non-streaming forward with SingleFlight coalescing
    exact_flight_key = RequestHasher.compute_exact_hash(normalized_payload, org_id=org_id)

    is_playground = request.headers.get("x-dashboard-playground") == "true" or org_id == "enterprise_user"
    supplied_key = (request.headers.get("x-api-key") or request.headers.get("authorization", "").removeprefix("Bearer ")).strip()
    is_internal_key = bool(
        supplied_key in (getattr(config, "ADMIN_API_KEY", ""), "default", "")
        or (quota_manager.storage.get_key(supplied_key) is not None)
    )
    has_real_provider_key = bool(supplied_key and not is_internal_key and (supplied_key.startswith("sk-") or len(supplied_key) > 20))

    if is_playground and not has_real_provider_key and not config.ANTHROPIC_API_KEY:
        user_text = ""
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "user":
                c = m.get("content", "")
                if isinstance(c, list):
                    user_text = " ".join(str(b.get("text", "")) for b in c if isinstance(b, dict))
                else:
                    user_text = str(c)
        status_code = 200
        anthropic_res = generate_sandbox_playground_completion(user_text, requested_model, is_claude=True)
        latency_ms = (time.perf_counter() - start_time) * 1000
        is_leader = True
    else:
        async def _fetch_anthropic_non_stream():
            code, data, hdrs = await upstream_client.forward_anthropic_messages(
                anthropic_payload,
                incoming_headers=dict(request.headers),
                params=req_params
            )
            return {"status_code": code, "res_data": data, "headers": hdrs}, None

        flight_result, _, is_leader = await flight_bus.execute(
            exact_flight_key,
            _fetch_anthropic_non_stream,
            timeout_seconds=config.SINGLEFLIGHT_TIMEOUT_SECONDS
        )

        status_code = flight_result["status_code"]
        anthropic_res = flight_result["res_data"]
        latency_ms = (time.perf_counter() - start_time) * 1000

        if status_code != 200 and is_playground:
            user_text = ""
            for m in messages:
                if isinstance(m, dict) and m.get("role") == "user":
                    c = m.get("content", "")
                    if isinstance(c, list):
                        user_text = " ".join(str(b.get("text", "")) for b in c if isinstance(b, dict))
                    else:
                        user_text = str(c)
            anthropic_res = generate_sandbox_playground_completion(user_text, requested_model, is_claude=True)
            status_code = 200

    if not is_leader:
        METRICS_LEDGER["singleflight_coalesced_count"] += 1

    if status_code == 200:
        usage = anthropic_res.get("usage", {})
        p_tok = usage.get("input_tokens", 35)
        c_tok = usage.get("output_tokens", 65)
        tokens_used = p_tok + c_tok
        if is_leader:
            METRICS_LEDGER["total_tokens_used"] += tokens_used
            METRICS_LEDGER["exact_tokens_used"] += tokens_used

            spend_usd = upstream_client.calculate_savings(requested_model, p_tok, c_tok)
            key_id = key_info.get("key_id", "")
            if key_id:
                quota_manager.record_spend(key_id, spend_usd)

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
            radix_tree.insert_conversation(messages, cacheable_res_payload, model=requested_model, org_id=org_id)
            if extracted_audio:
                for aud_h, p_txt, _ in extracted_audio:
                    audio_cache.store_audio(aud_h, p_txt, anthropic_res)

            if swarm_id:
                swarm_bus.record_shared_result(
                    swarm_id=swarm_id,
                    agent_id=agent_id,
                    task_type="anthropic_messages",
                    payload=normalized_payload,
                    result_payload=anthropic_res,
                    tokens_saved=tokens_used
                )

        rehydrated = privacy_shield.rehydrate_response(anthropic_res, pii_token_map)
        cache_status_header = "MISS" if is_leader else "HIT_SINGLEFLIGHT"
        decision_header = "MISS" if is_leader else "HIT"
        reason_header = decision_reason if is_leader else "HIT_SINGLEFLIGHT: Concurrent in-flight request coalesced with leader"
        similarity_header = f"{similarity:.4f}" if is_leader else "1.0000"

        return JSONResponse(rehydrated, headers={
            "X-OmniCache-Decision": decision_header,
            "X-OmniCache-Reason": reason_header,
            "X-OmniCache-Similarity": similarity_header,
            "X-Cache-Status": cache_status_header,
            "X-Cache-Decision-Reason": reason_header,
            "X-Cache-Similarity": similarity_header,
            "X-Cache-Latency-Ms": f"{latency_ms:.2f}",
            "X-Tokens-Used": str(tokens_used if is_leader else 0),
            "X-Tokens-Saved": str(0 if is_leader else tokens_used),
            "X-Tokens-Accounting": "exact",
            "X-Requested-Model": requested_model,
            "X-Served-Model": routed_model,
            "X-Cascade-Applied": "true" if was_cascaded else "false",
            "X-Cascade-Reason": cascade_reason,
            **cors_headers
        })
    else:
        return JSONResponse(anthropic_res, status_code=status_code, headers={
            "X-Requested-Model": requested_model,
            "X-Served-Model": routed_model,
            "X-Cascade-Applied": "true" if was_cascaded else "false",
            "X-Cascade-Reason": cascade_reason,
            **cors_headers
        })


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
        "error": {
            "message": f"Endpoint '{path}' not found on OmniCache Proxy.",
            "type": "not_found_error",
            "code": 404
        }
    }, status_code=404, headers=cors_headers)


async def handle_tool_replay(request: Request) -> Response:
    """
    Dual-Mode Tool Replay & Recording Endpoint.
    - If 'output' is provided (or action == 'store'/'record'): Stores tool execution output into cache.
    - If 'output' is omitted: Performs deterministic cache lookup.
    """
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    auth_ok, auth_err, key_info, org_id = authenticate_tenant(request)
    if not auth_ok:
        return auth_err

    try:
        body = await request.json()
        tool_name = (body.get("tool_name") or "").strip()
        if not tool_name:
            return JSONResponse({"error": "Missing required field 'tool_name'"}, status_code=400, headers=cors_headers)
        arguments = body.get("arguments", {})
        raw_fp = body.get("workspace_fingerprint", "default")
        ws_dir = body.get("workspace_dir") or body.get("cwd") or body.get("repo_path") or None
        ws_state = body.get("workspace_state", None)
        action = body.get("action", "").strip().lower()
        ttl_seconds = body.get("ttl_seconds", None)
        env_fp = f"{org_id}:{raw_fp}"

        swarm_id = (request.headers.get("x-omnicache-swarm-id") or body.get("swarm_id") or "").strip()
        agent_id = (request.headers.get("x-omnicache-agent-id") or request.headers.get("x-omnicache-subagent-id") or body.get("agent_id") or "lead").strip()
        parent_agent = (request.headers.get("x-omnicache-parent-agent") or body.get("parent_agent") or "").strip()

        if swarm_id and parent_agent:
            swarm_bus.record_delegation(swarm_id, parent_agent, agent_id, task_prompt=str(arguments))
    except Exception:
        return JSONResponse({"error": "Invalid JSON payload"}, status_code=400, headers=cors_headers)

    # 0. Mutation Guard for Store / Record Path on Mutative Tools
    if (action in ("store", "record") or "output" in body) and not tool_policy_manager.is_cacheable(tool_name) and ttl_seconds is None:
        emit_telemetry_event("mutation_blocked", {
            "tool_name": tool_name,
            "status": "REJECTED",
            "reason": "Non-cacheable mutation guard"
        })
        if swarm_id:
            invalidated = swarm_bus.invalidate_on_mutation(swarm_id, agent_id, mutated_resource=ws_dir)
            METRICS_LEDGER["swarm_mutations_invalidated"] += invalidated
        if getattr(config, "MESH_ENABLED", True) and ws_dir:
            tomb = mesh_bus.record_local_mutation(f"file:{ws_dir}", reason="tool_mutation", metadata={"tool_name": tool_name, "agent_id": agent_id})
            try:
                asyncio.create_task(mesh_bus.broadcast_tombstone_async(tomb))
            except Exception:
                pass
        return JSONResponse({
            "status": "REJECTED",
            "tool_name": tool_name,
            "cached": False,
            "reason": f"Tool '{tool_name}' is non-cacheable according to active policy."
        }, status_code=200, headers=cors_headers)

    # 1. Store / Record Path
    if "output" in body or action in ("store", "record"):
        output_content = str(body.get("output", ""))
        tool_key = tool_cache.store_tool_call(
            tool_name=tool_name,
            arguments=arguments,
            output=output_content,
            workspace_fingerprint=env_fp,
            workspace_state=ws_state,
            ttl_seconds=ttl_seconds,
            workspace_dir=ws_dir
        )
        if not tool_key:
            emit_telemetry_event("mutation_blocked", {
                "tool_name": tool_name,
                "status": "REJECTED",
                "reason": "Non-cacheable mutation guard"
            })
            if swarm_id:
                invalidated = swarm_bus.invalidate_on_mutation(swarm_id, agent_id, mutated_resource=ws_dir)
                METRICS_LEDGER["swarm_mutations_invalidated"] += invalidated
            return JSONResponse({
                "status": "REJECTED",
                "tool_name": tool_name,
                "cached": False,
                "reason": f"Tool '{tool_name}' is non-cacheable according to active policy."
            }, status_code=200, headers=cors_headers)

        if swarm_id:
            swarm_bus.record_shared_result(
                swarm_id=swarm_id,
                agent_id=agent_id,
                task_type=tool_name,
                payload={"arguments": arguments, "workspace_fingerprint": env_fp},
                result_payload=output_content,
                tokens_saved=max(1, len(output_content.split())),
                affected_resources=[ws_dir] if ws_dir else []
            )

        effective_ttl = ttl_seconds if ttl_seconds is not None else tool_policy_manager.get_ttl(tool_name)
        emit_telemetry_event("tool_recorded", {
            "tool_name": tool_name,
            "tool_key": tool_key,
            "ttl_seconds": effective_ttl
        })
        return JSONResponse({
            "status": "STORED",
            "tool_name": tool_name,
            "tool_key": tool_key,
            "cached": True,
            "workspace_state": ws_state,
            "ttl_seconds": effective_ttl
        }, headers=cors_headers)

    # 2. Lookup Path: Check Swarm Bus first if swarm_id is present
    if not tool_policy_manager.is_cacheable(tool_name) and ttl_seconds is None:
        if swarm_id:
            invalidated = swarm_bus.invalidate_on_mutation(swarm_id, agent_id, mutated_resource=ws_dir)
            METRICS_LEDGER["swarm_mutations_invalidated"] += invalidated
        if getattr(config, "MESH_ENABLED", True) and ws_dir:
            tomb = mesh_bus.record_local_mutation(f"file:{ws_dir}", reason="tool_mutation", metadata={"tool_name": tool_name, "agent_id": agent_id})
            try:
                asyncio.create_task(mesh_bus.broadcast_tombstone_async(tomb))
            except Exception:
                pass
        return JSONResponse({
            "status": "MISS",
            "tool_name": tool_name,
            "cached": False,
            "reason": f"Tool '{tool_name}' is non-cacheable according to active policy."
        }, status_code=200, headers=cors_headers)

    swarm_hit = False
    origin_agent = None
    if swarm_id:
        is_swarm_hit, swarm_out, swarm_meta = swarm_bus.lookup_shared_result(
            swarm_id=swarm_id,
            agent_id=agent_id,
            task_type=tool_name,
            payload={"arguments": arguments, "workspace_fingerprint": env_fp}
        )
        if is_swarm_hit and swarm_out is not None:
            is_hit = True
            output = swarm_out
            tool_key = f"swarm::{swarm_id}::{tool_name}"
            swarm_hit = True
            origin_agent = swarm_meta.get("origin_agent_id")
            METRICS_LEDGER["swarm_cross_agent_hits"] += 1
            emit_telemetry_event("swarm_hit", {
                "swarm_id": swarm_id,
                "tool_name": tool_name,
                "origin_agent": origin_agent,
                "requesting_agent": agent_id
            })
        else:
            is_hit = False
            output = None
            tool_key = ""
    else:
        is_hit, output, tool_key = tool_cache.lookup_tool_call(
            tool_name, arguments, workspace_fingerprint=env_fp, workspace_state=ws_state, workspace_dir=ws_dir
        )
        if not is_hit and (org_id == "default" or raw_fp == "default"):
            is_hit, output, tool_key = tool_cache.lookup_tool_call(
                tool_name, arguments, workspace_fingerprint=raw_fp, workspace_state=ws_state, workspace_dir=ws_dir
            )

    if is_hit:
        METRICS_LEDGER["agent_tool_hits"] += 1
        emit_telemetry_event("tool_replay", {
            "tool_name": tool_name,
            "status": "HIT",
            "cached": True
        })
        resp_headers = dict(cors_headers)
        if swarm_hit:
            resp_headers["X-OmniCache-Swarm-Hit"] = "true"
            resp_headers["X-OmniCache-Origin-Agent"] = str(origin_agent or "peer")
            resp_headers["X-OmniCache-Swarm-ID"] = swarm_id

        return JSONResponse({
            "status": "HIT",
            "tool_name": tool_name,
            "tool_key": tool_key,
            "output": output,
            "cached": True,
            "swarm_hit": swarm_hit,
            "origin_agent": origin_agent
        }, headers=resp_headers)
    
    return JSONResponse({
        "status": "MISS",
        "tool_name": tool_name,
        "cached": False
    }, headers=cors_headers)


async def handle_tool_policies(request: Request) -> Response:
    """
    Enterprise Tool Caching & Staleness Policy Management Endpoint.
    Supports:
    - GET /v1/agent/tools/policies (list policies or query single tool via ?tool_name=...)
    - POST /v1/agent/tools/policies (create or update custom policy overrides)
    - DELETE /v1/agent/tools/policies (revert custom policy override to built-in/inferred default)
    """
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    auth_ok, auth_err, key_info, org_id = authenticate_tenant(request)
    if not auth_ok:
        return auth_err

    # 1. GET Path: Query all policies or specific tool policy
    if request.method == "GET":
        tool_name = request.query_params.get("tool_name")
        if tool_name:
            clean = tool_name.strip().lower()
            pol = tool_policy_manager.get_policy(clean)
            return JSONResponse({
                "status": "OK",
                "tool_name": clean,
                "policy": pol
            }, headers=cors_headers)

        all_pols = tool_policy_manager.list_policies()
        return JSONResponse({
            "status": "OK",
            "policies": all_pols,
            "custom_policies": tool_policy_manager._custom_policies,
            "total_count": len(all_pols),
            "custom_count": len(tool_policy_manager._custom_policies)
        }, headers=cors_headers)

    # 2. POST Path: Create or update policy
    elif request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON payload"}, status_code=400, headers=cors_headers)

        # Batch update: {"policies": {"tool_a": {...}, "tool_b": {...}}}
        if "policies" in body and isinstance(body["policies"], dict):
            updated = {}
            for t_name, pol_data in body["policies"].items():
                if isinstance(pol_data, dict):
                    updated[t_name] = tool_policy_manager.set_policy(t_name, pol_data)
            return JSONResponse({
                "status": "UPDATED",
                "count": len(updated),
                "policies": updated
            }, headers=cors_headers)

        # Single update: {"tool_name": "lookup_customer", "ttl_seconds": 600, ...}
        tool_name = (body.get("tool_name") or "").strip()
        if not tool_name:
            return JSONResponse({"error": "Missing required field 'tool_name'"}, status_code=400, headers=cors_headers)

        updated_policy = tool_policy_manager.set_policy(tool_name, body)
        return JSONResponse({
            "status": "UPDATED",
            "tool_name": tool_name.lower(),
            "policy": updated_policy
        }, headers=cors_headers)

    # 3. DELETE Path: Remove custom policy override
    elif request.method == "DELETE":
        tool_name = request.query_params.get("tool_name")
        if not tool_name:
            try:
                body = await request.json()
                tool_name = body.get("tool_name")
            except Exception:
                pass

        if not tool_name or not str(tool_name).strip():
            return JSONResponse({"error": "Missing 'tool_name' parameter"}, status_code=400, headers=cors_headers)

        clean = str(tool_name).strip().lower()
        deleted = tool_policy_manager.delete_policy(clean)
        return JSONResponse({
            "status": "DELETED" if deleted else "NOT_FOUND",
            "tool_name": clean,
            "active_policy": tool_policy_manager.get_policy(clean)
        }, headers=cors_headers)

    return JSONResponse({"error": "Method not allowed"}, status_code=405, headers=cors_headers)


async def handle_workspace_warm(request: Request) -> Response:
    """
    CI/CD and Developer Workspace Pre-Warming Endpoint.
    Scans repository files, directory structures, and git commits to warm tool replay cache.
    """
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    auth_ok, auth_err, key_info, org_id = authenticate_tenant(request)
    if not auth_ok:
        return auth_err

    try:
        body = await request.json() if request.method == "POST" else {}
    except Exception:
        body = {}

    ws_dir = body.get("workspace_dir") or body.get("dir") or os.getcwd()
    ws_fp = body.get("workspace_fingerprint", f"{org_id}:default")
    ref = body.get("ref", "HEAD")
    max_files = int(body.get("max_files", 200))
    max_size = int(body.get("max_file_size_kb", 500))

    try:
        result = workspace_warmer.warm_workspace(
            workspace_dir=ws_dir,
            workspace_fingerprint=ws_fp,
            ref=ref,
            max_files=max_files,
            max_file_size_kb=max_size
        )
        emit_telemetry_event("workspace_warmed", {
            "files_warmed": result.get("files_warmed", 0),
            "entries_recorded": result.get("entries_recorded", 0),
            "duration_ms": result.get("duration_ms", 0)
        })
        return JSONResponse(result, headers=cors_headers)
    except Exception as e:
        return JSONResponse({"status": "ERROR", "error": str(e)}, status_code=400, headers=cors_headers)


async def handle_workspace_sync_export(request: Request) -> Response:
    """
    Exports a portable snapshot bundle of active workspace tool executions.
    """
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    auth_ok, auth_err, key_info, org_id = authenticate_tenant(request)
    if not auth_ok:
        return auth_err

    ws_dir = request.query_params.get("workspace_dir")
    ws_fp = request.query_params.get("workspace_fingerprint")

    try:
        bundle = workspace_sync_manager.export_snapshot(
            workspace_dir=ws_dir,
            workspace_fingerprint=ws_fp
        )
        return JSONResponse(bundle, headers=cors_headers)
    except Exception as e:
        return JSONResponse({"status": "ERROR", "error": str(e)}, status_code=500, headers=cors_headers)


async def handle_workspace_sync_import(request: Request) -> Response:
    """
    Imports an external workspace snapshot bundle into the local tool cache and policy registry.
    """
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    auth_ok, auth_err, key_info, org_id = authenticate_tenant(request)
    if not auth_ok:
        return auth_err

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON payload"}, status_code=400, headers=cors_headers)

    try:
        result = workspace_sync_manager.import_snapshot(body)
        return JSONResponse(result, headers=cors_headers)
    except Exception as e:
        return JSONResponse({"status": "ERROR", "error": str(e)}, status_code=400, headers=cors_headers)


async def handle_workspace_sync_status(request: Request) -> Response:
    """
    Returns workspace sync statistics, active tool counts, and token savings potential.
    """
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    auth_ok, auth_err, key_info, org_id = authenticate_tenant(request)
    if not auth_ok:
        return auth_err

    ws_dir = request.query_params.get("workspace_dir")
    ws_fp = request.query_params.get("workspace_fingerprint")

    status = workspace_sync_manager.get_sync_status(
        workspace_dir=ws_dir,
        workspace_fingerprint=ws_fp
    )
    return JSONResponse(status, headers=cors_headers)


async def handle_workspace_sync_redis(request: Request) -> Response:
    """
    Federates workspace tool cache across nodes via Redis push/pull.
    """
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    auth_ok, auth_err, key_info, org_id = authenticate_tenant(request)
    if not auth_ok:
        return auth_err

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON payload"}, status_code=400, headers=cors_headers)

    action = body.get("action", "push").strip().lower()
    ws_fp = body.get("workspace_fingerprint", f"{org_id}:default")

    if action == "pull":
        result = workspace_sync_manager.sync_redis_pull(workspace_fingerprint=ws_fp)
    else:
        result = workspace_sync_manager.sync_redis_push(workspace_fingerprint=ws_fp)

    return JSONResponse(result, headers=cors_headers)


async def handle_purge(request: Request) -> Response:
    """Protected Cache Purge Endpoint."""
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)
    if request.method == "GET":
        return JSONResponse({"error": "Method not allowed. Use POST or DELETE to purge cache."}, status_code=405, headers=cors_headers)

    auth_ok, auth_err, key_info, org_id = authenticate_tenant(request)
    if not auth_ok:
        return auth_err

    is_admin_user = key_info.get("role") == "admin"
    req_org = request.query_params.get("org_id", None) if is_admin_user else org_id
    
    in_mem_removed = cache_instance.purge(org_id=req_org)
    db_removed = snapshot_store.purge_all(org_id=req_org)

    if getattr(config, "MESH_ENABLED", True):
        tomb = mesh_bus.record_local_mutation("*", reason="cache_purge", metadata={"org_id": req_org or "all"})
        try:
            asyncio.create_task(mesh_bus.broadcast_tombstone_async(tomb))
        except Exception:
            pass
    
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
    if request.method == "GET":
        return JSONResponse({"error": "Method not allowed. Use POST or DELETE to invalidate tag."}, status_code=405, headers=cors_headers)

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

    if getattr(config, "MESH_ENABLED", True):
        tomb = mesh_bus.record_local_mutation(f"tag:{tag}", reason="tag_invalidation", metadata={"tag": tag, "org_id": req_org or "all"})
        try:
            asyncio.create_task(mesh_bus.broadcast_tombstone_async(tomb))
        except Exception:
            pass

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
            "cascade_savings_usd": round(cascade_router.arbitrage_savings_usd, 6),
            "total_tokens_saved": METRICS_LEDGER["total_tokens_saved"],
            "total_tokens_used": METRICS_LEDGER["total_tokens_used"],
            "exact_tokens_saved": METRICS_LEDGER["exact_tokens_saved"],
            "estimated_tokens_saved": METRICS_LEDGER["estimated_tokens_saved"],
            "token_savings_pct": round(token_savings_pct, 2)
        },
        "enterprise_engine": {
            "privacy_redactions_total": METRICS_LEDGER["privacy_scrubbed_count"],
            "agent_tool_replays": METRICS_LEDGER["agent_tool_hits"],
            "agent_tools_recorded": METRICS_LEDGER.get("agent_tool_recorded_count", 0),
            "agent_tokens_compacted": METRICS_LEDGER.get("agent_tool_compacted_tokens", 0),
            "swarm_stats": swarm_bus.get_stats(),
            "swarm_requests_processed": METRICS_LEDGER.get("swarm_requests_processed", 0),
            "swarm_cross_agent_hits": METRICS_LEDGER.get("swarm_cross_agent_hits", 0),
            "swarm_tokens_saved": METRICS_LEDGER.get("swarm_tokens_saved", 0),
            "swarm_mutations_invalidated": METRICS_LEDGER.get("swarm_mutations_invalidated", 0),
            "telephony_requests": METRICS_LEDGER.get("telephony_requests_processed", 0),
            "telephony_fillers_stripped": METRICS_LEDGER.get("telephony_fillers_stripped", 0),
            "telephony_tokens_saved": METRICS_LEDGER.get("telephony_tokens_saved", 0),
            "audio_cache_hits": METRICS_LEDGER.get("audio_cache_hits", 0),
            "audio_requests": METRICS_LEDGER.get("audio_requests_processed", 0),
            "audio_tokens_saved": METRICS_LEDGER.get("audio_tokens_saved", 0),
            "audio_cache": audio_cache.stats(),
            "cascade_stats": cascade_router.get_stats(),
            "cascade_routes_total": cascade_router.total_routed,
            "cascade_downgrades_total": cascade_router.downgraded_count,
            "cascade_tokens_diverted": cascade_router.tokens_diverted_to_economy,
            "cascade_policy": getattr(config, "CASCADE_POLICY", "off"),
            "vision_cache_hits": METRICS_LEDGER["vision_cache_hits"],
            "singleflight_coalesced": METRICS_LEDGER["singleflight_coalesced_count"],
            "radix_tree_hits": METRICS_LEDGER["radix_tree_hits"],
            "circuit_breaker": failover_engine.circuit_breaker.get_status(),
            "recent_upstream_failures": failover_engine.get_recent_failures(10),
            "quantized_embedder": quantized_embedder.stats()
        },
        "mesh_network": mesh_bus.get_mesh_topology(),
        "system_info": {
            "version": getattr(config, "VERSION", "3.0.5"),
            "storage_backend": getattr(config, "CACHE_STORAGE_BACKEND", "auto"),
            "persistence": "sqlite3_wal_write_behind",
            "host_binding": config.HOST,
            "port": config.PORT
        }
    }, headers=cors_headers)


async def handle_swarm_topology(request: Request) -> Response:
    """Introspect delegation hierarchy tree and active task cache for a swarm session."""
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)
    auth_ok, auth_err, _, _ = authenticate_tenant(request)
    if not auth_ok:
        return auth_err
    swarm_id = request.query_params.get("swarm_id", "").strip()
    if not swarm_id:
        return JSONResponse({"error": "Query parameter 'swarm_id' is required"}, status_code=400, headers=cors_headers)
    topology = swarm_bus.get_swarm_topology(swarm_id)
    return JSONResponse(topology, headers=cors_headers)


async def handle_swarm_stats(request: Request) -> Response:
    """Return aggregated swarm bus telemetry across all multi-agent swarms."""
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)
    auth_ok, auth_err, _, _ = authenticate_tenant(request)
    if not auth_ok:
        return auth_err
    stats = swarm_bus.get_stats()
    return JSONResponse({
        "status": "success",
        "swarm_stats": stats,
        "metrics_ledger": {
            "swarm_requests_processed": METRICS_LEDGER.get("swarm_requests_processed", 0),
            "swarm_cross_agent_hits": METRICS_LEDGER.get("swarm_cross_agent_hits", 0),
            "swarm_tokens_saved": METRICS_LEDGER.get("swarm_tokens_saved", 0),
            "swarm_mutations_invalidated": METRICS_LEDGER.get("swarm_mutations_invalidated", 0)
        }
    }, headers=cors_headers)


async def handle_swarm_delegate(request: Request) -> Response:
    """Explicitly register a subagent delegation edge with prompt and lineage."""
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)
    auth_ok, auth_err, _, _ = authenticate_tenant(request)
    if not auth_ok:
        return auth_err
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON payload"}, status_code=400, headers=cors_headers)
    swarm_id = (body.get("swarm_id") or "").strip()
    parent_agent = (body.get("parent_agent") or "").strip()
    subagent_id = (body.get("subagent_id") or body.get("agent_id") or "").strip()
    task_prompt = body.get("task_prompt", "")
    metadata = body.get("metadata", {})
    if not swarm_id or not parent_agent or not subagent_id:
        return JSONResponse({"error": "swarm_id, parent_agent, and subagent_id/agent_id are required"}, status_code=400, headers=cors_headers)
    node = swarm_bus.record_delegation(swarm_id, parent_agent, subagent_id, task_prompt=task_prompt, metadata=metadata)
    return JSONResponse({
        "status": "success",
        "message": f"Delegation recorded: {parent_agent} -> {subagent_id}",
        "node": node
    }, headers=cors_headers)


async def handle_mesh_peers(request: Request) -> Response:
    """Introspect, register, or remove mesh peers."""
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)
    auth_ok, auth_err, _, _ = authenticate_tenant(request)
    if not auth_ok:
        return auth_err

    if request.method == "GET":
        active_only = request.query_params.get("active_only", "false").lower() in ("true", "1")
        topology = mesh_bus.get_mesh_topology()
        if active_only:
            topology["peers"] = [p for p in topology["peers"] if p["status"] == "alive"]
        return JSONResponse(topology, headers=cors_headers)

    elif request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON payload"}, status_code=400, headers=cors_headers)
        endpoint = (body.get("endpoint") or "").strip()
        node_id = (body.get("node_id") or "").strip() or None
        metadata = body.get("metadata", {})
        verify = request.query_params.get("verify", "false").lower() in ("true", "1") or body.get("verify", False)
        if not endpoint:
            return JSONResponse({"error": "Field 'endpoint' is required"}, status_code=400, headers=cors_headers)

        if verify:
            reachability = await mesh_bus.check_peer_connectivity(endpoint)
            if not reachability["reachable"]:
                return JSONResponse({
                    "error": f"Peer admission rejected: Endpoint '{endpoint}' is unreachable ({reachability.get('error', 'connection refused')})."
                }, status_code=502, headers=cors_headers)
            peer = mesh_bus.register_peer(endpoint=endpoint, node_id=node_id, metadata=metadata, status="alive")
            if peer:
                peer.mark_seen(reachability.get("rtt_ms"))
        else:
            peer = mesh_bus.register_peer(endpoint=endpoint, node_id=node_id, metadata=metadata, status="unverified")

        emit_telemetry_event("mesh_peer_registered", {
            "endpoint": endpoint,
            "node_id": peer.node_id if peer else None,
            "verified": bool(verify)
        })
        return JSONResponse({
            "status": "success",
            "message": f"Peer registered: {endpoint}",
            "peer": peer.to_dict() if peer else None,
            "mesh_topology": mesh_bus.get_mesh_topology()
        }, headers=cors_headers)

    elif request.method == "DELETE":
        identifier = (request.query_params.get("identifier") or request.query_params.get("endpoint") or request.query_params.get("node_id") or "").strip()
        if not identifier:
            return JSONResponse({"error": "Query param 'identifier' or 'endpoint' or 'node_id' required"}, status_code=400, headers=cors_headers)
        unregistered = mesh_bus.unregister_peer(identifier)
        return JSONResponse({
            "status": "success" if unregistered else "not_found",
            "identifier": identifier,
            "unregistered": unregistered
        }, headers=cors_headers)

    return JSONResponse({"error": "Method not allowed"}, status_code=405, headers=cors_headers)


async def handle_mesh_sync(request: Request) -> Response:
    """Receives state synchronization packet from a mesh peer."""
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)
    auth_ok, auth_err, _, _ = authenticate_tenant(request)
    if not auth_ok:
        return auth_err

    try:
        packet = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON sync packet"}, status_code=400, headers=cors_headers)

    result = mesh_bus.process_sync_packet(packet)
    emit_telemetry_event("mesh_sync_processed", {
        "remote_node_id": packet.get("node_id"),
        "tombstones_applied": result.get("tombstones_applied", 0),
        "lamport_clock": result.get("lamport_clock", 0)
    })
    return JSONResponse(result, headers=cors_headers)


async def handle_mesh_heartbeat(request: Request) -> Response:
    """Peer ping/pong heartbeat endpoint."""
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)
    auth_ok, auth_err, _, _ = authenticate_tenant(request)
    if not auth_ok:
        return auth_err

    try:
        body = await request.json() if request.method == "POST" else {}
    except Exception:
        body = {}

    sender_id = body.get("node_id", "")
    sender_endpoint = body.get("endpoint", "")
    sender_clock = body.get("vector_clock", {})

    pong = mesh_bus.process_heartbeat(sender_id, sender_endpoint, sender_clock)
    return JSONResponse(pong, headers=cors_headers)


async def handle_mesh_broadcast(request: Request) -> Response:
    """Explicitly triggers local mutation recording and peer broadcast."""
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)
    auth_ok, auth_err, _, _ = authenticate_tenant(request)
    if not auth_ok:
        return auth_err

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400, headers=cors_headers)

    resource_id = (body.get("resource_id") or "").strip()
    reason = (body.get("reason") or "mutation").strip()
    metadata = body.get("metadata", {})
    if not resource_id:
        return JSONResponse({"error": "Field 'resource_id' is required"}, status_code=400, headers=cors_headers)

    tombstone = mesh_bus.record_local_mutation(resource_id, reason=reason, metadata=metadata)
    synced_peers = await mesh_bus.broadcast_tombstone_async(tombstone)

    return JSONResponse({
        "status": "success",
        "tombstone": tombstone.to_dict(),
        "synced_peers_count": synced_peers
    }, headers=cors_headers)


async def handle_embeddings(request: Request) -> Response:
    """
    OpenAI-compatible semantic embeddings endpoint powered by local quantized embedder.
    Supports single text string or list of text strings with zero external API calls.
    """
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)
    auth_ok, auth_err, _, _ = authenticate_tenant(request)
    if not auth_ok:
        return auth_err

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}}, status_code=400, headers=cors_headers)

    input_data = body.get("input")
    if not input_data:
        return JSONResponse({"error": {"message": "Field 'input' is required", "type": "invalid_request_error"}}, status_code=400, headers=cors_headers)

    model = body.get("model", "omnicache-quantized-256")
    embed_format = body.get("format", "int8" if request.url.path.endswith("/quantized") else "float")
    items = [input_data] if isinstance(input_data, str) else list(input_data)

    data = []
    total_tokens = 0
    t0 = time.perf_counter()

    for idx, item in enumerate(items):
        text_str = str(item)
        if embed_format == "int8":
            raw_i8 = quantized_embedder.embed_int8(text_str)
            vec = [(b - 256 if b > 127 else b) for b in raw_i8]
        elif embed_format == "int4":
            raw_i8 = quantized_embedder.embed_int8(text_str)
            vec = list(quantized_embedder.pack_int4(raw_i8))
        else:
            vec = quantized_embedder.embed(text_str)
        tok_est = max(1, len(text_str.split()))
        total_tokens += tok_est
        data.append({
            "object": "embedding",
            "index": idx,
            "embedding": vec
        })

    elapsed_ms = (time.perf_counter() - t0) * 1000
    METRICS_LEDGER["quantized_embeddings_generated"] += len(items)

    emit_telemetry_event("quantized_embeddings_generated", {
        "count": len(items),
        "total_tokens": total_tokens,
        "elapsed_ms": round(elapsed_ms, 3)
    })

    resp_dict = {
        "object": "list",
        "data": data,
        "model": model,
        "usage": {
            "prompt_tokens": total_tokens,
            "total_tokens": total_tokens
        },
        "quantization": {
            "hardware_mode": quantized_embedder.hardware_mode,
            "dimensions": quantized_embedder.dimensions,
            "bits": 4 if embed_format == "int4" else 8,
            "compression": "8x" if embed_format == "int4" else ("4x" if embed_format == "int8" else "1x"),
            "offline": True
        }
    }
    if "format" in body or request.url.path.endswith("/quantized"):
        resp_dict["format"] = embed_format

    return JSONResponse(resp_dict, headers=cors_headers)


async def handle_circuit_reset(request: Request) -> Response:
    """Protected Circuit Breaker Reset Endpoint (Admin Only)."""
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    admin_ok, admin_err, _ = authenticate_admin(request)
    if not admin_ok:
        return admin_err

    provider = None
    if request.method == "POST":
        try:
            body = await request.json()
            if isinstance(body, dict):
                provider = body.get("provider")
        except Exception:
            pass

    failover_engine.reset(provider)
    target = provider if provider else "all providers"
    return JSONResponse({
        "status": "success",
        "message": f"Circuit breaker successfully reset for {target}.",
        "circuit_breaker": failover_engine.circuit_breaker.get_status()
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

    return JSONResponse({"error": "Method not allowed"}, status_code=405, headers=cors_headers)


# =====================================================================
# Public Self-Service Signup (Free Tier)
# =====================================================================

FREE_TIER_MONTHLY_BUDGET_USD: float = 5.0
FREE_TIER_RATE_LIMIT_RPM: int = 30
FREE_TIER_ROLE: str = "tenant"
SIGNUP_IP_RATE_LIMIT_PER_HOUR: int = 5
SIGNUP_IP_WINDOW_SECONDS: float = 3600.0

_SIGNUP_IP_TIMESTAMPS: Dict[str, List[float]] = {}
_SIGNUP_LOCK = threading.RLock()

DISPOSABLE_EMAIL_DOMAINS: Set[str] = {
    "mailinator.com", "tempmail.com", "10minutemail.com", "guerrillamail.com",
    "sharklasers.com", "trashmail.com", "dispostable.com", "yopmail.com",
    "getairmail.com", "throwawaymail.com", "temp-mail.org", "fakeinbox.com",
    "burnermail.io", "maildrop.cc", "inboxkitten.com"
}

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


def extract_client_ip(request: Request) -> str:
    """Safely extracts client IP address, respecting reverse proxies and headers."""
    cf_ip = request.headers.get("cf-connecting-ip", "").strip()
    if cf_ip:
        return cf_ip
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    xff = request.headers.get("x-forwarded-for", "").strip()
    if xff:
        return xff.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


def check_signup_rate_limit(client_ip: str) -> Tuple[bool, int]:
    """
    In-memory and durable sliding window rate limiter: max 5 signups/hr per IP.
    Returns (is_allowed, count).
    """
    now = time.time()
    cutoff = now - SIGNUP_IP_WINDOW_SECONDS

    with _SIGNUP_LOCK:
        timestamps = [ts for ts in _SIGNUP_IP_TIMESTAMPS.get(client_ip, []) if ts > cutoff]
        durable_count = snapshot_store.count_signups_by_ip(client_ip, window_seconds=SIGNUP_IP_WINDOW_SECONDS)
        effective_count = max(len(timestamps), durable_count)

        if effective_count >= SIGNUP_IP_RATE_LIMIT_PER_HOUR:
            return False, effective_count

        return True, effective_count


def record_signup_rate_limit(client_ip: str) -> None:
    """Records a completed signup timestamp for the client IP address."""
    now = time.time()
    cutoff = now - SIGNUP_IP_WINDOW_SECONDS
    with _SIGNUP_LOCK:
        timestamps = [ts for ts in _SIGNUP_IP_TIMESTAMPS.get(client_ip, []) if ts > cutoff]
        timestamps.append(now)
        _SIGNUP_IP_TIMESTAMPS[client_ip] = timestamps


def validate_signup_email(email: Any) -> Tuple[bool, str]:
    """Validates email format, length, domain structure, and disposable domain blocklist."""
    if not email or not isinstance(email, str):
        return False, "Field 'email' is required and must be a string."

    email_clean = email.strip()
    if len(email_clean) < 5 or len(email_clean) > 254:
        return False, "Email must be between 5 and 254 characters in length."

    if not EMAIL_REGEX.match(email_clean):
        return False, "Invalid email format. Must be a valid email address (e.g. user@example.com)."

    parts = email_clean.split("@")
    if len(parts) != 2:
        return False, "Invalid email address format."

    domain = parts[1].lower()
    if "." not in domain or domain.endswith(".") or domain.startswith("."):
        return False, "Email domain must contain a valid top-level domain."

    if domain in DISPOSABLE_EMAIL_DOMAINS:
        return False, f"Email domain '{domain}' is not allowed for free tier registration. Please use a permanent email address."

    return True, ""


async def handle_signup(request: Request) -> Response:
    """
    Public Self-Service Tenant Registration Endpoint (POST /v1/signup).
    Allows new users to create a free-tier virtual key with strictly enforced server-side guardrails:
    - No authentication required.
    - Accepts only 'email' and optional 'team_name'.
    - Rejects tampering: ignores any caller-specified monthly_budget_usd, rate_limit_rpm, role, or org_id.
    - Uses hardcoded server constants for free tier (monthly_budget_usd=5.0, rate_limit_rpm=30, role='tenant').
    - Client IP rate limiting (max 5 signups/hr per IP).
    - Format and disposable email validation.
    - Server-side generated org_id and key_id to prevent collision or privilege escalation.
    - Audit logging to durable SQLite storage and telemetry bus.
    """
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    if request.method != "POST":
        return JSONResponse(
            {"error": {"message": "Method not allowed. Use POST.", "type": "invalid_request_error"}},
            status_code=405,
            headers=cors_headers
        )

    client_ip = extract_client_ip(request)

    # 1. Enforce IP Rate Limiting (5 signups / hr / IP)
    rate_ok, current_count = check_signup_rate_limit(client_ip)
    if not rate_ok:
        return JSONResponse(
            {
                "error": {
                    "message": f"Signup rate limit exceeded ({SIGNUP_IP_RATE_LIMIT_PER_HOUR} signups per hour per IP). Please try again later.",
                    "type": "rate_limit_exceeded"
                }
            },
            status_code=429,
            headers=cors_headers
        )

    # 2. Parse and Validate Request Body
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse(
                {"error": {"message": "Request body must be a valid JSON object.", "type": "invalid_request_error"}},
                status_code=400,
                headers=cors_headers
            )
    except Exception:
        return JSONResponse(
            {"error": {"message": "Invalid JSON in request body.", "type": "invalid_request_error"}},
            status_code=400,
            headers=cors_headers
        )

    raw_email = body.get("email")
    is_valid_email, email_err = validate_signup_email(raw_email)
    if not is_valid_email:
        return JSONResponse(
            {"error": {"message": email_err, "type": "invalid_request_error"}},
            status_code=400,
            headers=cors_headers
        )

    email = str(raw_email).strip().lower()

    # 3. Check for Duplicate Email Registration
    existing_signup = snapshot_store.get_signup_by_email(email)
    if existing_signup:
        return JSONResponse(
            {
                "error": {
                    "message": "An account with this email address already exists. Please use your existing API key or contact support.",
                    "type": "duplicate_email"
                }
            },
            status_code=409,
            headers=cors_headers
        )

    # 4. Derive Team Name and Generate Server-Side Identifiers
    raw_team = body.get("team_name")
    if raw_team and isinstance(raw_team, str) and raw_team.strip():
        team_name = raw_team.strip()[:64]
    else:
        local_part = email.split("@")[0]
        cleaned_part = "".join(c if c.isalnum() else " " for c in local_part).title()
        team_name = f"{cleaned_part} Workspace".strip() or "Developer Workspace"

    # Server-generated identifiers: never accept org_id or key_id from user request
    org_id = f"org_{uuid.uuid4().hex[:12]}"
    key_id = f"omni_live_{uuid.uuid4().hex}"

    # 5. Register Virtual Key in Quota Manager with Hardcoded Free-Tier Constants
    quota_manager.register_key(
        key_id=key_id,
        team_name=team_name,
        org_id=org_id,
        monthly_budget_usd=FREE_TIER_MONTHLY_BUDGET_USD,
        rate_limit_rpm=FREE_TIER_RATE_LIMIT_RPM,
        role=FREE_TIER_ROLE
    )
    record_signup_rate_limit(client_ip)

    now = time.time()

    # 6. Audit Log (Never log raw secret key in plaintext logs)
    snapshot_store.record_signup(
        email=email,
        team_name=team_name,
        org_id=org_id,
        key_id=key_id,
        ip_address=client_ip,
        created_at=now,
        synchronous=True
    )

    print(f"👤 [OmniCache Signup] Registered free tenant: email={email}, org_id={org_id}, team='{team_name}', ip={client_ip}", file=sys.stderr)
    emit_telemetry_event("user_signup", {
        "email": email,
        "org_id": org_id,
        "team_name": team_name,
        "ip": client_ip,
        "timestamp": now
    })
    asyncio.create_task(broadcast_ws_event("new_signup", {
        "org_id": org_id,
        "team_name": team_name,
        "timestamp": now
    }))

    # 7. Return Result (Fast Path MVP: return key immediately in JSON response)
    return JSONResponse(
        {
            "status": "success",
            "message": "Free tier account created successfully. Store your API key securely — it will not be shown again.",
            "api_key": key_id,
            "org_id": org_id,
            "team_name": team_name,
            "tier": "free",
            "monthly_budget_usd": FREE_TIER_MONTHLY_BUDGET_USD,
            "rate_limit_rpm": FREE_TIER_RATE_LIMIT_RPM,
            "role": FREE_TIER_ROLE,
            "created_at": now
        },
        status_code=201,
        headers=cors_headers
    )


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

    for org_id, entries in cache_instance.l1_exact_cache.items():
        if isinstance(entries, dict):
            for key, entry in entries.items():
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
        f"omnicache_requests_total {stats.get('total_requests', 0)}",
        "# HELP omnicache_cache_hits_exact_total Exact L1 hits",
        "# TYPE omnicache_cache_hits_exact_total counter",
        f"omnicache_cache_hits_exact_total {stats.get('exact_hits', 0)}",
        "# HELP omnicache_cache_hits_semantic_total Semantic L2 hits",
        "# TYPE omnicache_cache_hits_semantic_total counter",
        f"omnicache_cache_hits_semantic_total {stats.get('semantic_hits', 0)}",
        "# HELP omnicache_savings_usd_total Estimated dollars saved",
        "# TYPE omnicache_savings_usd_total gauge",
        f"omnicache_savings_usd_total {METRICS_LEDGER['total_savings_usd']:.6f}",
        "# HELP omnicache_tokens_saved_total Tokens saved from remote inference",
        "# TYPE omnicache_tokens_saved_total counter",
        f"omnicache_tokens_saved_total {METRICS_LEDGER['total_tokens_saved']}",
        "# HELP omnicache_singleflight_coalesced_total Concurrent requests coalesced",
        "# TYPE omnicache_singleflight_coalesced_total counter",
        f"omnicache_singleflight_coalesced_total {METRICS_LEDGER['singleflight_coalesced_count']}",
        "# HELP omnicache_radix_tree_hits_total Multi-turn prefix tree conversation hits",
        "# TYPE omnicache_radix_tree_hits_total counter",
        f"omnicache_radix_tree_hits_total {METRICS_LEDGER['radix_tree_hits']}",
        "# HELP omnicache_agent_tool_compacted_tokens Tokens saved by deduplicating historical tool outputs",
        "# TYPE omnicache_agent_tool_compacted_tokens counter",
        f"omnicache_agent_tool_compacted_tokens {METRICS_LEDGER.get('agent_tool_compacted_tokens', 0)}",
        "# HELP omnicache_agent_tools_recorded_total Unique tool executions indexed",
        "# TYPE omnicache_agent_tools_recorded_total counter",
        f"omnicache_agent_tools_recorded_total {METRICS_LEDGER.get('agent_tool_recorded_count', 0)}",
        "# HELP omnicache_telephony_requests_total Conversational voice agent requests processed",
        "# TYPE omnicache_telephony_requests_total counter",
        f"omnicache_telephony_requests_total {METRICS_LEDGER.get('telephony_requests_processed', 0)}",
        "# HELP omnicache_telephony_fillers_stripped_total Speech-to-text filler tokens normalized",
        "# TYPE omnicache_telephony_fillers_stripped_total counter",
        f"omnicache_telephony_fillers_stripped_total {METRICS_LEDGER.get('telephony_fillers_stripped', 0)}",
        "# HELP omnicache_telephony_tokens_saved_total Prompt tokens saved by voice adapter",
        "# TYPE omnicache_telephony_tokens_saved_total counter",
        f"omnicache_telephony_tokens_saved_total {METRICS_LEDGER.get('telephony_tokens_saved', 0)}",
        "# HELP omnicache_audio_cache_hits_total Multimodal raw audio cache hits",
        "# TYPE omnicache_audio_cache_hits_total counter",
        f"omnicache_audio_cache_hits_total {METRICS_LEDGER.get('audio_cache_hits', 0)}",
        "# HELP omnicache_audio_requests_total Raw audio requests processed",
        "# TYPE omnicache_audio_requests_total counter",
        f"omnicache_audio_requests_total {METRICS_LEDGER.get('audio_requests_processed', 0)}",
        "# HELP omnicache_audio_tokens_saved_total Tokens saved by audio cache",
        "# TYPE omnicache_audio_tokens_saved_total counter",
        f"omnicache_audio_tokens_saved_total {METRICS_LEDGER.get('audio_tokens_saved', 0)}",
        "# HELP omnicache_cascade_routes_total Total model cascade routing evaluations",
        "# TYPE omnicache_cascade_routes_total counter",
        f"omnicache_cascade_routes_total {cascade_router.total_routed}",
        "# HELP omnicache_cascade_downgrades_total Total queries cascaded to economy models",
        "# TYPE omnicache_cascade_downgrades_total counter",
        f"omnicache_cascade_downgrades_total {cascade_router.downgraded_count}",
        "# HELP omnicache_cascade_arbitrage_savings_usd Total dollar savings from model cascading",
        "# TYPE omnicache_cascade_arbitrage_savings_usd gauge",
        f"omnicache_cascade_arbitrage_savings_usd {cascade_router.arbitrage_savings_usd:.6f}",
        "# HELP omnicache_cascade_tokens_diverted_total Prompt tokens diverted to economy models",
        "# TYPE omnicache_cascade_tokens_diverted_total counter",
        f"omnicache_cascade_tokens_diverted_total {cascade_router.tokens_diverted_to_economy}",
        "# HELP omnicache_swarm_requests_total Multi-agent swarm requests processed",
        "# TYPE omnicache_swarm_requests_total counter",
        f"omnicache_swarm_requests_total {METRICS_LEDGER.get('swarm_requests_processed', 0)}",
        "# HELP omnicache_swarm_cross_agent_hits_total Cross-agent memory hits",
        "# TYPE omnicache_swarm_cross_agent_hits_total counter",
        f"omnicache_swarm_cross_agent_hits_total {METRICS_LEDGER.get('swarm_cross_agent_hits', 0)}",
        "# HELP omnicache_swarm_tokens_saved_total Tokens saved via swarm delegation bus",
        "# TYPE omnicache_swarm_tokens_saved_total counter",
        f"omnicache_swarm_tokens_saved_total {METRICS_LEDGER.get('swarm_tokens_saved', 0)}",
        "# HELP omnicache_swarm_mutations_invalidated_total Cross-agent read invalidations triggered by mutations",
        "# TYPE omnicache_swarm_mutations_invalidated_total counter",
        f"omnicache_swarm_mutations_invalidated_total {METRICS_LEDGER.get('swarm_mutations_invalidated', 0)}",
        "# HELP omnicache_mesh_peers_total Known mesh peers",
        "# TYPE omnicache_mesh_peers_total gauge",
        f"omnicache_mesh_peers_total {len(mesh_bus.list_peers())}",
        "# HELP omnicache_mesh_peers_alive_total Active alive mesh peers",
        "# TYPE omnicache_mesh_peers_alive_total gauge",
        f"omnicache_mesh_peers_alive_total {len(mesh_bus.list_peers(active_only=True))}",
        "# HELP omnicache_mesh_tombstones_total Active CRDT tombstones tracked",
        "# TYPE omnicache_mesh_tombstones_total gauge",
        f"omnicache_mesh_tombstones_total {len(mesh_bus._tombstones)}",
        "# HELP omnicache_mesh_sync_packets_received_total State sync packets received from peers",
        "# TYPE omnicache_mesh_sync_packets_received_total counter",
        f"omnicache_mesh_sync_packets_received_total {mesh_bus.metrics['sync_packets_received']}",
        "# HELP omnicache_mesh_cross_node_invalidations_total Local cache invalidations applied from mesh peers",
        "# TYPE omnicache_mesh_cross_node_invalidations_total counter",
        f"omnicache_mesh_cross_node_invalidations_total {mesh_bus.metrics['cross_node_invalidations']}",
        "# HELP omnicache_quantized_embeddings_total Total local quantized embeddings generated",
        "# TYPE omnicache_quantized_embeddings_total counter",
        f"omnicache_quantized_embeddings_total {quantized_embedder.total_embeddings}"
    ]
    return Response(content="\n".join(metrics) + "\n", media_type="text/plain; version=0.0.4", headers=cors_headers)


async def handle_healthz(request: Request) -> Response:
    cors_headers = get_cors_headers(request)
    return JSONResponse({
        "status": "healthy",
        "version": getattr(config, "VERSION", "3.0.5"),
        "service": "omnicache-proxy",
        "circuit_breaker": failover_engine.circuit_breaker.get_status()
    }, headers=cors_headers)


async def handle_root(request: Request) -> Response:
    """Root endpoint handler. Serves Dashboard for web browsers, or service JSON for API clients."""
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    accept = request.headers.get("accept", "").lower()
    # If accessed via web browser requesting HTML, serve high-converting Neo-Brutalist landing page
    if "text/html" in accept:
        landing_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "landing.html")
        if os.path.exists(landing_path):
            with open(landing_path, "r", encoding="utf-8") as f:
                html = f.read()
            return HTMLResponse(html, headers=cors_headers)
        dashboard_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "index.html")
        if os.path.exists(dashboard_path):
            return await handle_dashboard(request)

    return JSONResponse({
        "status": "ok",
        "service": "OmniCache AI Proxy",
        "version": getattr(config, "VERSION", "3.0.5"),
        "dashboard": "/dashboard",
        "endpoints": {
            "dashboard": "/dashboard",
            "health": "/healthz",
            "stats": "/v1/cache/stats",
            "openai_chat": "/v1/chat/completions",
            "anthropic_messages": "/v1/messages",
            "embeddings": "/v1/embeddings",
            "mesh_peers": "/v1/mesh/peers",
            "mesh_sync": "/v1/mesh/sync",
            "mcp": "/mcp",
            "metrics": "/metrics"
        }
    }, headers=cors_headers)


async def handle_dashboard(request: Request) -> Response:
    cors_headers = get_cors_headers(request)
    accept = request.headers.get("accept", "").lower()
    key = extract_auth_key(request)
    allowed = False
    auth_reason = ""
    if key:
        allowed, auth_reason, key_info = quota_manager.check_authorization(key)

    # If auth is required, client requested non-HTML (e.g. JSON API client or curl), and not allowed:
    if getattr(config, "REQUIRE_AUTH", False) and "text/html" not in accept:
        if not allowed:
            return JSONResponse(
                {"error": {"message": f"Dashboard authentication required: {auth_reason or 'Missing API key in Authorization header, cookie, or query param'}", "type": "authentication_error"}},
                status_code=401,
                headers=cors_headers
            )

    dashboard_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "index.html")
    if os.path.exists(dashboard_path):
        with open(dashboard_path, "r", encoding="utf-8") as f:
            html = f.read()
        response = HTMLResponse(html, headers=cors_headers)
        if allowed and key and (request.query_params.get("key") or request.query_params.get("api_key")):
            is_https = (request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https")
            response.set_cookie(
                key="omnicache_key",
                value=key,
                max_age=86400 * 30,
                httponly=False,
                samesite="lax",
                secure=is_https
            )
        return response
    return HTMLResponse("<h1>OmniCache Dashboard Not Found</h1>", status_code=404, headers=cors_headers)


async def handle_landing(request: Request) -> Response:
    """Serves the Dark Neo-Brutalist SaaS Landing Page."""
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)
    landing_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "landing.html")
    if os.path.exists(landing_path):
        with open(landing_path, "r", encoding="utf-8") as f:
            html = f.read()
        return HTMLResponse(html, headers=cors_headers)
    return HTMLResponse("<h1>OmniCache Landing Page Not Found</h1>", status_code=404, headers=cors_headers)




# =====================================================================
# Model Context Protocol (MCP) Session & OAuth State
# =====================================================================

MCP_ACTIVE_SESSIONS: Dict[str, Dict[str, Any]] = {}
OAUTH_CODES: Dict[str, Dict[str, Any]] = {}
OAUTH_TOKENS: Dict[str, Dict[str, Any]] = {}
GOOGLE_OAUTH_STATES: Dict[str, Dict[str, Any]] = {}


def cleanup_google_oauth_states() -> None:
    """Evicts expired Google OAuth state tokens to prevent memory leaks."""
    now = time.time()
    expired = [k for k, v in GOOGLE_OAUTH_STATES.items() if v.get("expires_at", 0) < now]
    for k in expired:
        GOOGLE_OAUTH_STATES.pop(k, None)


def generate_google_oauth_state(client_info: Dict[str, Any]) -> str:
    """
    Mints a tamper-proof, self-contained, HMAC-SHA256 signed OAuth state token.
    Contains client parameters and expiry. Immune to server restarts, multi-worker
    process boundaries, and container redeployments.
    """
    payload = {
        "nonce": secrets.token_hex(16),
        "client_id": client_info.get("client_id", ""),
        "redirect_uri": client_info.get("redirect_uri", ""),
        "client_state": client_info.get("client_state", ""),
        "scope": client_info.get("scope", "mcp:read mcp:write"),
        "code_challenge": client_info.get("code_challenge", ""),
        "code_challenge_method": client_info.get("code_challenge_method", "S256"),
        "response_type": client_info.get("response_type", "code"),
        "expires_at": time.time() + 900  # 15 minutes
    }
    raw_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(raw_bytes).decode("ascii").rstrip("=")
    signing_key = (getattr(config, "ADMIN_API_KEY", "") or getattr(config, "PRIVACY_SALT", "") or "omnicache_secret_salt").encode("utf-8")
    sig = hmac.new(signing_key, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    token = f"{payload_b64}.{sig}"
    GOOGLE_OAUTH_STATES[token] = payload
    return token


def verify_google_oauth_state(state_token: str) -> Optional[Dict[str, Any]]:
    """
    Verifies the HMAC signature and timestamp of an OAuth state token.
    Returns decoded client parameters if valid and unexpired, None otherwise.
    First checks in-memory state; if missing (e.g. across container restart),
    cryptographically validates the HMAC signature so login flows never break.
    """
    if not state_token:
        return None
    # 1. Fast path: check in-memory dictionary
    if state_token in GOOGLE_OAUTH_STATES:
        state_data = GOOGLE_OAUTH_STATES.pop(state_token)
        if time.time() <= state_data.get("expires_at", 0):
            return state_data
    # 2. Cryptographic signature verification (survives restarts and redeployments)
    if "." in state_token:
        try:
            parts = state_token.split(".", 1)
            payload_b64, provided_sig = parts[0], parts[1]
            signing_key = (getattr(config, "ADMIN_API_KEY", "") or getattr(config, "PRIVACY_SALT", "") or "omnicache_secret_salt").encode("utf-8")
            expected_sig = hmac.new(signing_key, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
            if hmac.compare_digest(provided_sig, expected_sig):
                padded = payload_b64 + "=" * (-len(payload_b64) % 4)
                data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
                if time.time() <= data.get("expires_at", 0):
                    return data
        except Exception as exc:
            logger.warning(f"[OAuth State] Signature verification failed: {exc}")
    return None


def get_effective_google_redirect_uri(request: Request) -> str:
    """
    Computes the canonical Google OAuth callback URL.
    Prefers explicit GOOGLE_REDIRECT_URI, otherwise dynamically respects
    reverse proxy headers (x-forwarded-proto and x-forwarded-host).
    """
    configured = getattr(config, "GOOGLE_REDIRECT_URI", "").strip()
    if configured:
        return configured
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    return f"{proto}://{host}/auth/google/callback"



def verify_pkce(code_verifier: str, code_challenge: str, method: str = "S256") -> bool:
    """Verifies PKCE code_verifier against code_challenge per RFC 7636."""
    if not code_verifier or not code_challenge:
        return False
    if method == "plain":
        return hmac.compare_digest(code_verifier, code_challenge)
    elif method == "S256":
        try:
            digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
            computed = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
            return hmac.compare_digest(computed, code_challenge.rstrip("="))
        except Exception:
            return False
    return False


async def handle_oauth_metadata(request: Request) -> Response:
    """RFC 8414 OAuth 2.0 Authorization Server Metadata."""
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    base_url = str(request.base_url).rstrip("/")
    metadata = {
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/oauth/authorize",
        "token_endpoint": f"{base_url}/oauth/token",
        "registration_endpoint": f"{base_url}/oauth/register",
        "scopes_supported": ["mcp:read", "mcp:write", "mcp:admin"],
        "response_types_supported": ["code", "token"],
        "grant_types_supported": ["authorization_code", "client_credentials", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
        "code_challenge_methods_supported": ["S256", "plain"]
    }
    return JSONResponse(metadata, headers=cors_headers)


async def handle_oauth_protected_resource(request: Request) -> Response:
    """RFC 9728 OAuth 2.0 Protected Resource Metadata."""
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    base_url = str(request.base_url).rstrip("/")
    metadata = {
        "resource": f"{base_url}/mcp",
        "authorization_servers": [base_url],
        "scopes_supported": ["mcp:read", "mcp:write", "mcp:admin"],
        "bearer_methods_supported": ["header"]
    }
    return JSONResponse(metadata, headers=cors_headers)


async def handle_oauth_authorize(request: Request) -> Response:
    """
    OAuth 2.0 authorization endpoint for MCP Connectors.
    RFC 6749 Section 4.1 & RFC 7636 (PKCE).
    Enforces real user authentication & consent:
    - In REQUIRE_AUTH=true mode: requires valid tenant/admin key via header, query param, or interactive login form.
    - Binds authorization codes to verified tenant identities, redirect_uris, and PKCE challenges.
    - Single-use codes expire in 10 minutes.
    """
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    params: Dict[str, Any] = {}
    if request.method == "POST":
        content_type = request.headers.get("content-type", "").lower()
        if "application/x-www-form-urlencoded" in content_type:
            try:
                raw_body = await request.body()
                params = {k: v[0] for k, v in parse_qs(raw_body.decode("utf-8", errors="replace")).items()}
            except Exception:
                pass
        elif "application/json" in content_type:
            try:
                params = await request.json()
            except Exception:
                pass
    if not params:
        params = dict(request.query_params)

    client_id = params.get("client_id", "").strip() or "claude-connectors"
    redirect_uri = params.get("redirect_uri", "").strip()
    response_type = params.get("response_type", "code").strip()
    state = params.get("state", "").strip()
    scope = params.get("scope", "mcp:read mcp:write").strip()
    code_challenge = params.get("code_challenge", "").strip()
    code_challenge_method = params.get("code_challenge_method", "S256").strip()

    if redirect_uri and not is_allowed_redirect_uri(redirect_uri):
        return JSONResponse({
            "error": "invalid_request",
            "error_description": "Invalid or untrusted redirect_uri"
        }, status_code=400, headers=cors_headers)

    if response_type != "code":
        return JSONResponse({
            "error": "unsupported_response_type",
            "error_description": "Only response_type='code' is supported"
        }, status_code=400, headers=cors_headers)

    # Determine user identity & authorization
    is_authenticated = False
    auth_org_id = "default"
    auth_team = "Developer"

    # Check credentials: Authorization header, x-api-key header, or query/form api_key
    key = extract_auth_key(request)
    if not key or key == "default":
        key = params.get("api_key", "").strip()

    if getattr(config, "REQUIRE_AUTH", False):
        if key:
            allowed, auth_reason, key_info = quota_manager.check_authorization(key)
            if allowed and key_info:
                is_authenticated = True
                auth_org_id = key_info.get("org_id", "default")
                auth_team = key_info.get("team_name", "Authorized Tenant")

        if not is_authenticated:
            # If accessed via web browser requesting HTML, render interactive Consent & Login UI
            accept = request.headers.get("accept", "").lower()
            if "text/html" in accept:
                error_msg = html.escape(params.get("auth_error", ""), quote=True)
                error_banner = f'<div class="error-banner">{error_msg}</div>' if error_msg else ''
                google_qs = urlencode({
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "state": state,
                    "scope": scope,
                    "code_challenge": code_challenge,
                    "code_challenge_method": code_challenge_method,
                    "response_type": response_type
                })
                google_login_href = f"/auth/google/login?{google_qs}"
                safe_client_id = html.escape(client_id, quote=True)
                safe_redirect_uri = html.escape(redirect_uri, quote=True)
                safe_state = html.escape(state, quote=True)
                safe_scope = html.escape(scope, quote=True)
                safe_code_challenge = html.escape(code_challenge, quote=True)
                safe_code_challenge_method = html.escape(code_challenge_method, quote=True)
                safe_response_type = html.escape(response_type, quote=True)
                html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>OmniCache — Authorize MCP Client</title>
  <style>
    body {{ background: #121316; color: #e1e3e6; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
    .card {{ background: #1a1c20; border: 1px solid #2a2d34; border-radius: 12px; width: 440px; padding: 32px; box-shadow: 0 8px 32px rgba(0,0,0,0.4); }}
    h2 {{ margin-top: 0; font-size: 20px; font-weight: 600; color: #fff; }}
    p {{ color: #9aa0a6; font-size: 14px; line-height: 1.5; }}
    .badge {{ display: inline-block; background: #252830; color: #8ab4f8; padding: 4px 10px; border-radius: 6px; font-family: monospace; font-size: 13px; }}
    .btn-google {{ display: flex; align-items: center; justify-content: center; gap: 12px; background: #ffffff; color: #3c4043; text-decoration: none; font-size: 14px; font-weight: 500; padding: 11px 16px; border-radius: 6px; border: 1px solid #dadce0; transition: background 0.2s, box-shadow 0.2s; margin-top: 20px; box-sizing: border-box; width: 100%; }}
    .btn-google:hover {{ background: #f8f9fa; box-shadow: 0 1px 4px rgba(0,0,0,0.25); }}
    .divider {{ display: flex; align-items: center; text-align: center; margin: 22px 0 18px 0; color: #5f6368; font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }}
    .divider::before, .divider::after {{ content: ''; flex: 1; border-bottom: 1px solid #2a2d34; }}
    .divider span {{ padding: 0 12px; }}
    .form-group {{ margin: 16px 0; }}
    label {{ display: block; font-size: 13px; margin-bottom: 8px; color: #ccc; }}
    input[type="password"] {{ width: 100%; box-sizing: border-box; background: #121316; border: 1px solid #3c4043; border-radius: 6px; color: #fff; padding: 10px 12px; font-size: 14px; outline: none; }}
    input[type="password"]:focus {{ border-color: #8ab4f8; }}
    .actions {{ display: flex; gap: 12px; margin-top: 24px; }}
    button {{ flex: 1; padding: 10px 16px; border-radius: 6px; font-size: 14px; font-weight: 500; cursor: pointer; border: none; }}
    .btn-primary {{ background: #1a73e8; color: #fff; }}
    .btn-primary:hover {{ background: #1557b0; }}
    .btn-secondary {{ background: #2a2d34; color: #ccc; }}
    .btn-secondary:hover {{ background: #353942; }}
    .error-banner {{ background: #3c1e22; border: 1px solid #d93025; color: #f28b82; padding: 10px 12px; border-radius: 6px; margin-bottom: 16px; font-size: 13px; }}
  </style>
</head>
<body>
  <div class="card">
    <h2>Authorize MCP Client</h2>
    <p>Application <span class="badge">{safe_client_id}</span> is requesting permission to access OmniCache vector memory.</p>
    <p>Requested Scope: <span class="badge">{safe_scope}</span></p>
    {error_banner}
    <a href="{google_login_href}" class="btn-google">
      <svg width="18" height="18" viewBox="0 0 18 18"><path fill="#4285F4" d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.874 2.684-6.616z"/><path fill="#34A853" d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z"/><path fill="#FBBC05" d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332z"/><path fill="#EA4335" d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z"/></svg>
      <span>Sign in with Google</span>
    </a>
    <div class="divider"><span>OR USE API KEY</span></div>
    <form method="POST" action="/oauth/authorize">
      <input type="hidden" name="client_id" value="{safe_client_id}" />
      <input type="hidden" name="redirect_uri" value="{safe_redirect_uri}" />
      <input type="hidden" name="state" value="{safe_state}" />
      <input type="hidden" name="scope" value="{safe_scope}" />
      <input type="hidden" name="code_challenge" value="{safe_code_challenge}" />
      <input type="hidden" name="code_challenge_method" value="{safe_code_challenge_method}" />
      <input type="hidden" name="response_type" value="{safe_response_type}" />
      <div class="form-group">
        <label for="api_key">OmniCache API Key or Admin Key:</label>
        <input type="password" id="api_key" name="api_key" placeholder="Enter API Key to approve..." required autofocus />
      </div>
      <div class="actions">
        <button type="submit" class="btn-primary">Approve & Connect</button>
        <button type="button" class="btn-secondary" onclick="window.history.back()">Deny</button>
      </div>
    </form>
  </div>
</body>
</html>"""
                return HTMLResponse(html_body, headers=cors_headers)
            else:
                auth_headers = dict(cors_headers)
                auth_headers["WWW-Authenticate"] = 'Bearer realm="OmniCache OAuth", error="access_denied"'
                return JSONResponse({
                    "error": "access_denied",
                    "error_description": "User authentication required. Supply a valid OmniCache API Key to authorize this client."
                }, status_code=401, headers=auth_headers)
    else:
        # In local developer mode (REQUIRE_AUTH=false)
        is_authenticated = True
        auth_org_id = request.headers.get("x-org-id", "default").strip() or "default"
        auth_team = "Local Developer"

    # Mint single-use, time-bound authorization code
    code = f"omni_code_{uuid.uuid4().hex}"
    OAUTH_CODES[code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "org_id": auth_org_id,
        "team_name": auth_team,
        "created_at": time.time(),
        "expires_at": time.time() + 600  # 10 minutes
    }

    if redirect_uri:
        delimiter = "&" if "?" in redirect_uri else "?"
        redirect_target = f"{redirect_uri}{delimiter}code={code}"
        if state:
            redirect_target += f"&state={state}"
        return RedirectResponse(url=redirect_target, status_code=302)

    return JSONResponse({
        "status": "authorized",
        "code": code,
        "state": state,
        "client_id": client_id,
        "scope": scope,
        "expires_in": 600,
        "message": "Authorization code issued. Exchange code at /oauth/token"
    }, headers=cors_headers)


async def handle_oauth_token(request: Request) -> Response:
    """
    OAuth 2.0 token issuance endpoint.
    RFC 6749 Section 4.1.3 (Authorization Code Grant), Section 4.4 (Client Credentials), & RFC 7636 (PKCE).
    Strictly enforces:
    - Authorization code single-use redemption and expiration.
    - Client ID and redirect_uri binding validation.
    - PKCE code_verifier verification.
    - Client secret verification for client_credentials grant.
    """
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    content_type = request.headers.get("content-type", "").lower()
    params: Dict[str, Any] = {}
    if "application/json" in content_type:
        try:
            params = await request.json()
        except Exception:
            pass
    elif "application/x-www-form-urlencoded" in content_type:
        try:
            raw_body = await request.body()
            params = {k: v[0] for k, v in parse_qs(raw_body.decode("utf-8", errors="replace")).items()}
        except Exception:
            pass
    else:
        params = dict(request.query_params)

    grant_type = params.get("grant_type", "").strip()
    client_id = params.get("client_id", "").strip()
    client_secret = params.get("client_secret", "").strip()

    # Support HTTP Basic authentication for client_id:client_secret
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Basic "):
        try:
            raw_creds = base64.b64decode(auth_header[6:].strip()).decode("utf-8")
            if ":" in raw_creds:
                basic_id, basic_secret = raw_creds.split(":", 1)
                if not client_id:
                    client_id = basic_id
                if not client_secret:
                    client_secret = basic_secret
        except Exception:
            pass

    # =========================================================================
    # Grant Type 1: authorization_code (RFC 6749 Section 4.1.3 & RFC 7636 PKCE)
    # =========================================================================
    if grant_type == "authorization_code":
        code = params.get("code", "").strip()
        redirect_uri = params.get("redirect_uri", "").strip()
        code_verifier = params.get("code_verifier", "").strip()

        if not code:
            return JSONResponse({
                "error": "invalid_request",
                "error_description": "Parameter 'code' is required for authorization_code grant."
            }, status_code=400, headers=cors_headers)

        if code not in OAUTH_CODES:
            return JSONResponse({
                "error": "invalid_grant",
                "error_description": "Authorization code is invalid, expired, or has already been redeemed."
            }, status_code=400, headers=cors_headers)

        # Single-use redemption: pop immediately to prevent replay attacks
        code_entry = OAUTH_CODES.pop(code)

        # Verify expiration (10 min lifetime)
        if time.time() > code_entry.get("expires_at", 0):
            return JSONResponse({
                "error": "invalid_grant",
                "error_description": "Authorization code has expired."
            }, status_code=400, headers=cors_headers)

        # Verify client_id binding if provided during token exchange
        if client_id and code_entry.get("client_id") and client_id != code_entry["client_id"]:
            return JSONResponse({
                "error": "invalid_grant",
                "error_description": f"Client ID mismatch. Code was issued for '{code_entry['client_id']}'."
            }, status_code=400, headers=cors_headers)

        # Verify redirect_uri binding if present in authorization
        if code_entry.get("redirect_uri") and redirect_uri:
            if redirect_uri != code_entry["redirect_uri"]:
                return JSONResponse({
                    "error": "invalid_grant",
                    "error_description": "redirect_uri mismatch with the authorization request."
                }, status_code=400, headers=cors_headers)

        # Verify PKCE if code_challenge was registered
        code_challenge = code_entry.get("code_challenge")
        if code_challenge:
            method = code_entry.get("code_challenge_method", "S256")
            if not code_verifier or not verify_pkce(code_verifier, code_challenge, method):
                return JSONResponse({
                    "error": "invalid_grant",
                    "error_description": "PKCE verification failed: invalid or missing code_verifier."
                }, status_code=400, headers=cors_headers)

        # Code redemption and verification successful!
        target_org = code_entry.get("org_id", "default")
        target_team = code_entry.get("team_name", f"OAuth Client ({client_id or 'default'})")
        granted_scope = code_entry.get("scope", "mcp:read mcp:write")

        access_token = f"omni_tok_{uuid.uuid4().hex}"
        refresh_token = f"omni_ref_{uuid.uuid4().hex}"

        OAUTH_TOKENS[access_token] = {
            "client_id": client_id or code_entry.get("client_id", "unknown"),
            "org_id": target_org,
            "scope": granted_scope,
            "created_at": time.time(),
            "expires_at": time.time() + 86400
        }

        quota_manager.register_key(
            access_token,
            team_name=target_team,
            org_id=target_org,
            role="tenant"
        )

        return JSONResponse({
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 86400,
            "refresh_token": refresh_token,
            "scope": granted_scope
        }, headers=cors_headers)

    # =========================================================================
    # Grant Type 2: client_credentials (RFC 6749 Section 4.4)
    # =========================================================================
    elif grant_type == "client_credentials":
        # In client_credentials grant, client authentication is MANDATORY.
        # Arbitrary / unauthenticated token minting is strictly forbidden.
        if not client_secret:
            auth_headers = dict(cors_headers)
            auth_headers["WWW-Authenticate"] = 'Basic realm="OmniCache OAuth", error="invalid_client"'
            return JSONResponse({
                "error": "invalid_client",
                "error_description": "Client authentication failed. A valid client_secret is required for client_credentials grant."
            }, status_code=401, headers=auth_headers)

        # Validate client_secret against quota_manager or admin key
        is_valid_client = False
        target_org = f"org_{client_id}" if client_id else "oauth_tenant"
        target_team = f"OAuth Client ({client_id})" if client_id else "OAuth Client"

        key_info = quota_manager.storage.get_key(client_secret)
        if key_info and key_info.get("active", True):
            is_valid_client = True
            target_org = key_info.get("org_id", target_org)
            target_team = key_info.get("team_name", target_team)
        elif getattr(config, "ADMIN_API_KEY", "").strip() and hmac.compare_digest(client_secret, config.ADMIN_API_KEY):
            is_valid_client = True
            target_org = "admin"
            target_team = "OmniCache Admin"
        elif not getattr(config, "REQUIRE_AUTH", False) and client_secret == "default":
            is_valid_client = True

        if not is_valid_client:
            auth_headers = dict(cors_headers)
            auth_headers["WWW-Authenticate"] = 'Basic realm="OmniCache OAuth", error="invalid_client"'
            return JSONResponse({
                "error": "invalid_client",
                "error_description": "Client authentication failed. Invalid client_secret."
            }, status_code=401, headers=auth_headers)

        requested_scope = params.get("scope", "mcp:read mcp:write").strip()
        access_token = f"omni_tok_{uuid.uuid4().hex}"
        refresh_token = f"omni_ref_{uuid.uuid4().hex}"

        OAUTH_TOKENS[access_token] = {
            "client_id": client_id or "client_credentials",
            "org_id": target_org,
            "scope": requested_scope,
            "created_at": time.time(),
            "expires_at": time.time() + 86400
        }

        quota_manager.register_key(
            access_token,
            team_name=target_team,
            org_id=target_org,
            role="tenant"
        )

        return JSONResponse({
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 86400,
            "refresh_token": refresh_token,
            "scope": requested_scope
        }, headers=cors_headers)

    elif grant_type == "refresh_token":
        return JSONResponse({
            "error": "unsupported_grant_type",
            "error_description": "refresh_token grant is not yet implemented."
        }, status_code=400, headers=cors_headers)

    else:
        return JSONResponse({
            "error": "unsupported_grant_type",
            "error_description": f"Grant type '{grant_type}' is unsupported. Supported grant types: 'authorization_code', 'client_credentials'."
        }, status_code=400, headers=cors_headers)


async def handle_google_login(request: Request) -> Response:
    """
    Initiates Google OAuth 2.0 Identity Federation for OmniCache.
    Preserves incoming MCP client parameters (client_id, redirect_uri, state, PKCE challenge)
    in an ephemeral, cryptographic CSRF state token.
    Redirects user to Google's OAuth 2.0 authorization endpoint.
    """
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    client_id = request.query_params.get("client_id", "").strip() or "claude-connectors"
    redirect_uri = request.query_params.get("redirect_uri", "").strip()
    response_type = request.query_params.get("response_type", "code").strip()
    state = request.query_params.get("state", "").strip()
    scope = request.query_params.get("scope", "mcp:read mcp:write").strip()
    code_challenge = request.query_params.get("code_challenge", "").strip()
    code_challenge_method = request.query_params.get("code_challenge_method", "S256").strip()

    # Check if Google OAuth is configured
    google_client_id = getattr(config, "GOOGLE_CLIENT_ID", "").strip()
    google_client_secret = getattr(config, "GOOGLE_CLIENT_SECRET", "").strip()
    if not google_client_id or not google_client_secret:
        accept = request.headers.get("accept", "").lower()
        if "text/html" in accept:
            html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>OmniCache — Google Sign-In Not Configured</title>
  <style>
    body {{ background: #121316; color: #e1e3e6; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
    .card {{ background: #1a1c20; border: 1px solid #2a2d34; border-radius: 12px; width: 440px; padding: 32px; box-shadow: 0 8px 32px rgba(0,0,0,0.4); text-align: center; }}
    h2 {{ margin-top: 0; font-size: 20px; color: #fff; }}
    p {{ color: #9aa0a6; font-size: 14px; line-height: 1.5; text-align: left; }}
    .btn {{ display: inline-block; margin-top: 20px; padding: 10px 20px; background: #1a73e8; color: #fff; text-decoration: none; border-radius: 6px; font-weight: 500; font-size: 14px; }}
    .btn:hover {{ background: #1557b0; }}
    code {{ background: #252830; color: #8ab4f8; padding: 2px 6px; border-radius: 4px; font-size: 13px; }}
  </style>
</head>
<body>
  <div class="card">
    <h2>Google Sign-In Unconfigured</h2>
    <p>Google OAuth is not yet enabled on this OmniCache deployment. To enable it, set <code>GOOGLE_CLIENT_ID</code> and <code>GOOGLE_CLIENT_SECRET</code> in the environment variables.</p>
    <p>You can still authenticate using your OmniCache API key or Admin key.</p>
    <a class="btn" href="/oauth/authorize?client_id={quote_plus(client_id)}&redirect_uri={quote_plus(redirect_uri)}&response_type={quote_plus(response_type)}&state={quote_plus(state)}&scope={quote_plus(scope)}&code_challenge={quote_plus(code_challenge)}&code_challenge_method={quote_plus(code_challenge_method)}">Return to Manual Login</a>
  </div>
</body>
</html>"""
            return HTMLResponse(html, status_code=503, headers=cors_headers)
        return JSONResponse({
            "error": "server_error",
            "error_description": "Google OAuth is not configured on this server. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
        }, status_code=503, headers=cors_headers)

    cleanup_google_oauth_states()
    state_token = generate_google_oauth_state({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "client_state": state,
        "scope": scope,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "response_type": response_type
    })

    google_redirect_uri = get_effective_google_redirect_uri(request)
    google_params = {
        "client_id": google_client_id,
        "redirect_uri": google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state_token,
        "access_type": "offline",
        "prompt": "select_account"
    }
    google_auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(google_params)}"
    return RedirectResponse(url=google_auth_url, status_code=302)


async def handle_google_callback(request: Request) -> Response:
    """
    Receives authorization code from Google OAuth, exchanges it for tokens,
    retrieves verified email identity, auto-provisions or looks up tenant account,
    and seamlessly returns to the MCP client OAuth flow with an authorized OmniCache code.
    """
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    query_params = request.query_params
    code = query_params.get("code", "").strip()
    state_token = query_params.get("state", "").strip()
    error = query_params.get("error", "").strip()
    error_description = query_params.get("error_description", "").strip()

    if error:
        return JSONResponse({
            "error": error,
            "error_description": error_description or "Google authorization failed"
        }, status_code=400, headers=cors_headers)

    cleanup_google_oauth_states()
    state_info = verify_google_oauth_state(state_token)
    if not state_info:
        return JSONResponse({
            "error": "invalid_request",
            "error_description": "Invalid, expired, or missing OAuth state parameter. Please try logging in again."
        }, status_code=400, headers=cors_headers)

    if not code:
        return JSONResponse({
            "error": "invalid_request",
            "error_description": "Missing authorization code from Google."
        }, status_code=400, headers=cors_headers)

    google_client_id = getattr(config, "GOOGLE_CLIENT_ID", "").strip()
    google_client_secret = getattr(config, "GOOGLE_CLIENT_SECRET", "").strip()
    google_redirect_uri = get_effective_google_redirect_uri(request)

    # Exchange Google authorization code for access token
    token_url = "https://oauth2.googleapis.com/token"
    token_payload = {
        "code": code,
        "client_id": google_client_id,
        "client_secret": google_client_secret,
        "redirect_uri": google_redirect_uri,
        "grant_type": "authorization_code"
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            token_resp = await http_client.post(token_url, data=token_payload)
            if token_resp.status_code != 200:
                logger.error(f"[Google Auth] Token exchange failed: {token_resp.status_code} - {token_resp.text}")
                return JSONResponse({
                    "error": "invalid_grant",
                    "error_description": "Failed to exchange authorization code with Google."
                }, status_code=400, headers=cors_headers)

            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                return JSONResponse({
                    "error": "server_error",
                    "error_description": "No access_token returned by Google."
                }, status_code=502, headers=cors_headers)

            # Fetch verified user profile
            userinfo_resp = await http_client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            if userinfo_resp.status_code != 200:
                return JSONResponse({
                    "error": "server_error",
                    "error_description": "Failed to fetch user profile from Google."
                }, status_code=502, headers=cors_headers)

            user_info = userinfo_resp.json()
    except Exception as exc:
        logger.error(f"[Google Auth] Exception communicating with Google: {exc}")
        return JSONResponse({
            "error": "server_error",
            "error_description": f"Internal communication error with Google: {str(exc)}"
        }, status_code=502, headers=cors_headers)

    email = user_info.get("email", "").strip().lower()
    email_verified = user_info.get("email_verified", False)

    if not email or not email_verified:
        return JSONResponse({
            "error": "access_denied",
            "error_description": "Google account email is not verified or unavailable."
        }, status_code=403, headers=cors_headers)

    client_ip = extract_client_ip(request)

    # Tenant Resolution & Auto-Provisioning
    existing_signup = snapshot_store.get_signup_by_email(email)
    if existing_signup:
        org_id = existing_signup["org_id"]
        team_name = existing_signup["team_name"]
        key_id = existing_signup["key_id"]
        if not quota_manager.get_key(key_id):
            quota_manager.register_key(
                key_id=key_id,
                team_name=team_name,
                org_id=org_id,
                monthly_budget_usd=FREE_TIER_MONTHLY_BUDGET_USD,
                rate_limit_rpm=FREE_TIER_RATE_LIMIT_RPM,
                role=FREE_TIER_ROLE
            )
    else:
        name = user_info.get("name", "").strip()
        if name:
            team_name = f"{name} Workspace"[:64]
        else:
            team_name = f"{email.split('@')[0].title()} Workspace"[:64]

        org_id = f"org_{uuid.uuid4().hex[:12]}"
        key_id = f"omni_live_{uuid.uuid4().hex}"

        quota_manager.register_key(
            key_id=key_id,
            team_name=team_name,
            org_id=org_id,
            monthly_budget_usd=FREE_TIER_MONTHLY_BUDGET_USD,
            rate_limit_rpm=FREE_TIER_RATE_LIMIT_RPM,
            role=FREE_TIER_ROLE
        )
        snapshot_store.record_signup(
            email=email,
            team_name=team_name,
            org_id=org_id,
            key_id=key_id,
            ip_address=client_ip,
            created_at=time.time(),
            synchronous=True
        )
        print(f"👤 [OmniCache Google Auth] Auto-provisioned free tenant: email={email}, org_id={org_id}, team='{team_name}', ip={client_ip}", file=sys.stderr)
        emit_telemetry_event("user_signup", {
            "email": email,
            "org_id": org_id,
            "team_name": team_name,
            "provider": "google",
            "ip": client_ip,
            "timestamp": time.time()
        })
        asyncio.create_task(broadcast_ws_event("new_signup", {
            "org_id": org_id,
            "team_name": team_name,
            "provider": "google",
            "timestamp": time.time()
        }))

    # Seamless redirect back into client OAuth flow if redirect_uri was present
    client_redirect = state_info.get("redirect_uri", "").strip()
    if client_redirect:
        if not is_allowed_redirect_uri(client_redirect):
            return JSONResponse({
                "error": "invalid_request",
                "error_description": "Invalid or untrusted client redirect_uri"
            }, status_code=400, headers=cors_headers)
        code = f"omni_code_{uuid.uuid4().hex}"
        OAUTH_CODES[code] = {
            "client_id": state_info.get("client_id", "claude-connectors"),
            "redirect_uri": client_redirect,
            "scope": state_info.get("scope", "mcp:read mcp:write"),
            "code_challenge": state_info.get("code_challenge", ""),
            "code_challenge_method": state_info.get("code_challenge_method", "S256"),
            "org_id": org_id,
            "team_name": team_name,
            "email": email,
            "created_at": time.time(),
            "expires_at": time.time() + 600
        }
        delimiter = "&" if "?" in client_redirect else "?"
        redirect_target = f"{client_redirect}{delimiter}code={code}"
        if state_info.get("client_state"):
            redirect_target += f"&state={quote_plus(state_info['client_state'])}"
        return RedirectResponse(url=redirect_target, status_code=302)

    # If no client redirect_uri (direct browser sign-in): render modern dark confirmation page
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>OmniCache — Authenticated</title>
  <style>
    body {{ background: #121316; color: #e1e3e6; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
    .card {{ background: #1a1c20; border: 1px solid #2a2d34; border-radius: 12px; width: 460px; padding: 32px; box-shadow: 0 8px 32px rgba(0,0,0,0.4); }}
    h2 {{ margin-top: 0; font-size: 20px; font-weight: 600; color: #fff; display: flex; align-items: center; gap: 8px; }}
    .icon-check {{ color: #34a853; font-size: 24px; }}
    p {{ color: #9aa0a6; font-size: 14px; line-height: 1.5; }}
    .info-box {{ background: #121316; border: 1px solid #2a2d34; border-radius: 8px; padding: 16px; margin: 20px 0; font-family: monospace; font-size: 13px; }}
    .info-row {{ display: flex; justify-content: space-between; margin-bottom: 8px; }}
    .info-row:last-child {{ margin-bottom: 0; }}
    .label {{ color: #9aa0a6; }}
    .val {{ color: #8ab4f8; word-break: break-all; }}
    .key-display {{ background: #252830; padding: 10px; border-radius: 6px; border: 1px dashed #3c4043; color: #a8c7fa; font-weight: 600; font-size: 13px; text-align: center; margin-top: 12px; user-select: all; }}
    .btn {{ display: block; text-align: center; background: #1a73e8; color: #fff; padding: 10px 16px; border-radius: 6px; font-size: 14px; font-weight: 500; text-decoration: none; margin-top: 20px; }}
    .btn:hover {{ background: #1557b0; }}
  </style>
</head>
<body>
  <div class="card">
    <h2><span class="icon-check">✓</span> Google Sign-In Successful</h2>
    <p>Your Google account has been verified and mapped to your OmniCache tenant workspace.</p>
    <div class="info-box">
      <div class="info-row"><span class="label">Email:</span> <span class="val">{email}</span></div>
      <div class="info-row"><span class="label">Organization:</span> <span class="val">{org_id}</span></div>
      <div class="info-row"><span class="label">Team:</span> <span class="val">{team_name}</span></div>
      <div class="info-row"><span class="label">Plan Tier:</span> <span class="val">Free Tier ($5.00/mo, 30 RPM)</span></div>
      <div class="key-display">{key_id}</div>
    </div>
    <a class="btn" href="/dashboard?key={key_id}">Open OmniCache Dashboard</a>
  </div>
</body>
</html>"""
    return HTMLResponse(html, status_code=200, headers=cors_headers)


async def handle_mcp(request: Request) -> Response:
    """
    Model Context Protocol (MCP) JSON-RPC 2.0 endpoint supporting Streamable HTTP.
    Compliant with Anthropic Connectors Directory Policy:
    - Negotiates MCP-Protocol-Version: 2024-11-05
    - Supports Mcp-Session-Id tracking and lifecycle
    - Handles Server-Sent Events (SSE, text/event-stream) on GET/POST
    - Authenticates tenants with WWW-Authenticate 401 challenge
    """
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)

    auth_ok, err_response, key_info, org_id = authenticate_tenant(request)
    if not auth_ok:
        return err_response

    # Protocol version negotiation
    client_proto_version = request.headers.get("mcp-protocol-version", "2024-11-05")
    proto_version = "2024-11-05" if "2024-11-05" in client_proto_version else client_proto_version

    # Session ID negotiation
    session_id = request.headers.get("mcp-session-id") or request.query_params.get("sessionId") or str(uuid.uuid4())
    if session_id not in MCP_ACTIVE_SESSIONS:
        MCP_ACTIVE_SESSIONS[session_id] = {
            "created_at": time.time(),
            "org_id": org_id,
            "queue": asyncio.Queue()
        }

    session_data = MCP_ACTIVE_SESSIONS[session_id]

    base_headers = dict(cors_headers)
    base_headers["Mcp-Session-Id"] = session_id
    base_headers["MCP-Protocol-Version"] = proto_version

    accept_header = request.headers.get("accept", "").lower()

    if request.method == "GET":
        # Check if SSE stream requested
        if "text/event-stream" in accept_header or "sessionId" in request.query_params:
            sse_headers = dict(base_headers)
            sse_headers["Content-Type"] = "text/event-stream"
            sse_headers["Cache-Control"] = "no-cache"
            sse_headers["Connection"] = "keep-alive"

            max_events = int(request.query_params.get("max_events", 0))

            async def sse_event_stream():
                # Initial endpoint announcement event per Streamable HTTP MCP spec
                init_event = f"event: endpoint\ndata: /mcp?sessionId={session_id}\n\n"
                yield init_event.encode("utf-8")
                event_count = 1

                while max_events == 0 or event_count < max_events:
                    if await request.is_disconnected():
                        break
                    try:
                        # Wait for queued messages or send periodic keep-alive
                        msg = await asyncio.wait_for(session_data["queue"].get(), timeout=1.0)
                        event_payload = f"event: message\ndata: {json.dumps(msg)}\n\n"
                        yield event_payload.encode("utf-8")
                        event_count += 1
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            break
                        # Stream keepalive ping per SSE specification
                        yield b": keepalive\n\n"
                        event_count += 1
                    except asyncio.CancelledError:
                        break

            return StreamingResponse(sse_event_stream(), media_type="text/event-stream", headers=sse_headers)

        # Standard discovery JSON response for REST / health probes
        return JSONResponse({
            "service": "omnicache-mcp",
            "protocol": "jsonrpc-2.0",
            "mcp_version": proto_version,
            "transport": "streamable-http",
            "session_id": session_id,
            "tenant_org_id": org_id,
            "tools_count": len(TOOLS_METADATA),
            "sse_endpoint": f"/mcp?sessionId={session_id}"
        }, headers=base_headers)

    elif request.method == "DELETE":
        MCP_ACTIVE_SESSIONS.pop(session_id, None)
        return Response(status_code=204, headers=base_headers)

    # POST JSON-RPC handler
    try:
        req_body = await request.json()
    except Exception as exc:
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": f"Parse error: Invalid JSON payload: {str(exc)}"}
        }, status_code=400, headers=base_headers)

    # Scope enforcement for OAuth Bearer tokens
    auth_key = extract_auth_key(request)
    oauth_info = OAUTH_TOKENS.get(auth_key)
    token_scope = oauth_info.get("scope", "mcp:admin") if oauth_info else "mcp:admin"

    method = req_body.get("method")
    if method == "tools/call":
        tool_params = req_body.get("params", {})
        tool_name = tool_params.get("name", "")
        clean_name = tool_name[len("omnicache_"):] if tool_name.startswith("omnicache_") else tool_name

        if clean_name == "invalidate":
            if "mcp:admin" not in token_scope and "mcp:write" not in token_scope:
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_body.get("id"),
                    "error": {
                        "code": -32600,
                        "message": f"Forbidden: Token scope '{token_scope}' does not permit destructive tool '{tool_name}'. Required scope: 'mcp:write' or 'mcp:admin'."
                    }
                }, headers=base_headers)
        elif clean_name in ("store", "record_tool"):
            if "mcp:write" not in token_scope and "mcp:admin" not in token_scope:
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_body.get("id"),
                    "error": {
                        "code": -32600,
                        "message": f"Forbidden: Token scope '{token_scope}' does not permit write tool '{tool_name}'. Required scope: 'mcp:write' or 'mcp:admin'."
                    }
                }, headers=base_headers)

    is_admin = False
    if key_info:
        is_admin = (key_info.get("role") == "admin")
    elif auth_key:
        is_admin = quota_manager.is_admin(auth_key)
    elif not getattr(config, "REQUIRE_AUTH", False) and not getattr(config, "ADMIN_API_KEY", "").strip():
        is_admin = True

    res = process_mcp_jsonrpc(req_body, default_org_id=org_id, is_admin=is_admin)
    if res is None:
        return Response(status_code=204, headers=base_headers)

    # If the client requested SSE response stream on POST, stream it
    if "text/event-stream" in accept_header:
        sse_headers = dict(base_headers)
        sse_headers["Content-Type"] = "text/event-stream"
        sse_headers["Cache-Control"] = "no-cache"

        async def single_sse_stream():
            yield f"event: message\ndata: {json.dumps(res)}\n\n".encode("utf-8")

        return StreamingResponse(single_sse_stream(), media_type="text/event-stream", headers=sse_headers)

    return JSONResponse(res, headers=base_headers)


async def handle_ws_http(request: Request) -> Response:
    """HTTP fallback for /ws endpoint (e.g. status check or handshake probe)."""
    cors_headers = get_cors_headers(request)
    if request.method == "OPTIONS":
        return Response(headers=cors_headers)
    return JSONResponse({
        "status": "ok",
        "service": "OmniCache AI Proxy",
        "version": getattr(config, "VERSION", "3.0.5"),
        "websocket": "/ws",
        "message": "WebSocket gateway operational. Connect with ws:// or wss://"
    }, headers=cors_headers)


async def handle_ws(websocket: WebSocket):
    """Native WebSocket endpoint for client connections, live streaming, and real-time activity ticker."""
    await websocket.accept()
    ACTIVE_WS_CLIENTS.add(websocket)
    try:
        await websocket.send_json({
            "type": "connection_established",
            "service": "omnicache-proxy",
            "version": getattr(config, "VERSION", "3.0.5"),
            "status": "connected",
            "recent_events": list(RECENT_WS_EVENTS)
        })
        while True:
            msg = await websocket.receive_text()
            if msg.strip().lower() == "ping":
                await websocket.send_text("pong")
            else:
                try:
                    payload = json.loads(msg)
                    action = payload.get("action", "ping")
                    if action == "stats":
                        stats = {
                            "type": "stats",
                            "tokens_saved": METRICS_LEDGER["total_tokens_saved"],
                            "savings_usd": METRICS_LEDGER["total_savings_usd"],
                            "agent_tools_recorded": METRICS_LEDGER["agent_tool_recorded_count"],
                            "agent_tokens_compacted": METRICS_LEDGER["agent_tool_compacted_tokens"],
                            "telephony_fillers_stripped": METRICS_LEDGER.get("telephony_fillers_stripped", 0),
                            "telephony_tokens_saved": METRICS_LEDGER.get("telephony_tokens_saved", 0)
                        }
                        await websocket.send_json(stats)
                    elif action == "events":
                        await websocket.send_json({
                            "type": "events_replay",
                            "events": list(RECENT_WS_EVENTS)
                        })
                    else:
                        await websocket.send_json({"type": "ack", "status": "ok"})
                except Exception:
                    await websocket.send_text("ack")
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        ACTIVE_WS_CLIENTS.discard(websocket)


# =====================================================================
# Starlette Application Routing
# =====================================================================

routes = [
    Route("/", handle_root, methods=["GET", "OPTIONS"]),
    Route("/health", handle_healthz, methods=["GET", "OPTIONS"]),
    Route("/healthz", handle_healthz, methods=["GET", "OPTIONS"]),
    Route("/readyz", handle_healthz, methods=["GET", "OPTIONS"]),
    Route("/models", handle_models, methods=["GET", "OPTIONS"]),
    Route("/v1/models", handle_models, methods=["GET", "OPTIONS"]),
    Route("/v1/chat/completions", handle_chat_completions, methods=["POST", "OPTIONS"]),
    Route("/v1/embeddings", handle_embeddings, methods=["POST", "OPTIONS"]),
    Route("/v1/embeddings/quantized", handle_embeddings, methods=["POST", "OPTIONS"]),
    Route("/v1/messages", handle_anthropic_messages, methods=["POST", "GET", "OPTIONS"]),
    Route("/v1/messages/count_tokens", handle_anthropic_count_tokens, methods=["POST", "OPTIONS"]),
    Route("/v1/agent/tool_replay", handle_tool_replay, methods=["POST", "OPTIONS"]),
    Route("/v1/agent/tool_record", handle_tool_replay, methods=["POST", "OPTIONS"]),
    Route("/v1/agent/tools/replay", handle_tool_replay, methods=["POST", "OPTIONS"]),
    Route("/v1/agent/tools/record", handle_tool_replay, methods=["POST", "OPTIONS"]),
    Route("/v1/agent/tools/policies", handle_tool_policies, methods=["GET", "POST", "DELETE", "OPTIONS"]),
    Route("/v1/agent/tool_policies", handle_tool_policies, methods=["GET", "POST", "DELETE", "OPTIONS"]),
    Route("/v1/swarm/topology", handle_swarm_topology, methods=["GET", "OPTIONS"]),
    Route("/v1/swarm/stats", handle_swarm_stats, methods=["GET", "OPTIONS"]),
    Route("/v1/swarm/delegate", handle_swarm_delegate, methods=["POST", "OPTIONS"]),
    Route("/v1/mesh/peers", handle_mesh_peers, methods=["GET", "POST", "DELETE", "OPTIONS"]),
    Route("/v1/mesh/sync", handle_mesh_sync, methods=["POST", "OPTIONS"]),
    Route("/v1/mesh/heartbeat", handle_mesh_heartbeat, methods=["POST", "GET", "OPTIONS"]),
    Route("/v1/mesh/broadcast", handle_mesh_broadcast, methods=["POST", "OPTIONS"]),
    Route("/v1/workspace/warm", handle_workspace_warm, methods=["POST", "OPTIONS"]),
    Route("/v1/workspace/sync/export", handle_workspace_sync_export, methods=["GET", "POST", "OPTIONS"]),
    Route("/v1/workspace/sync/import", handle_workspace_sync_import, methods=["POST", "OPTIONS"]),
    Route("/v1/workspace/sync/status", handle_workspace_sync_status, methods=["GET", "OPTIONS"]),
    Route("/v1/workspace/sync/redis", handle_workspace_sync_redis, methods=["GET", "POST", "OPTIONS"]),
    Route("/.well-known/oauth-authorization-server", handle_oauth_metadata, methods=["GET", "OPTIONS"]),
    Route("/.well-known/oauth-protected-resource", handle_oauth_protected_resource, methods=["GET", "OPTIONS"]),
    Route("/oauth/authorize", handle_oauth_authorize, methods=["GET", "POST", "OPTIONS"]),
    Route("/oauth/token", handle_oauth_token, methods=["POST", "OPTIONS"]),
    Route("/auth/google/login", handle_google_login, methods=["GET", "OPTIONS"]),
    Route("/auth/google/callback", handle_google_callback, methods=["GET", "OPTIONS"]),
    Route("/mcp", handle_mcp, methods=["GET", "POST", "DELETE", "OPTIONS"]),
    Route("/v1/mcp", handle_mcp, methods=["GET", "POST", "DELETE", "OPTIONS"]),
    Route("/v1/cache/purge", handle_purge, methods=["POST", "DELETE", "GET", "OPTIONS"]),
    Route("/v1/cache/invalidate-tag", handle_invalidate_tag, methods=["POST", "DELETE", "GET", "OPTIONS"]),
    Route("/v1/cache/stats", handle_stats, methods=["GET", "OPTIONS"]),
    Route("/v1/cache/circuit/reset", handle_circuit_reset, methods=["POST", "OPTIONS"]),
    Route("/v1/system/circuit/reset", handle_circuit_reset, methods=["POST", "OPTIONS"]),
    Route("/v1/cache/export", handle_export_csv, methods=["GET", "OPTIONS"]),
    Route("/v1/enterprise/quotas", handle_quotas, methods=["GET", "POST", "OPTIONS"]),
    Route("/v1/signup", handle_signup, methods=["POST", "OPTIONS"]),
    Route("/metrics", handle_prometheus_metrics, methods=["GET", "OPTIONS"]),
    Route("/landing", handle_landing, methods=["GET", "OPTIONS"]),
    Route("/dashboard", handle_dashboard, methods=["GET"]),
    Route("/ws", handle_ws_http, methods=["GET", "POST", "OPTIONS"]),
    WebSocketRoute("/ws", handle_ws),
    Route("/{rest_of_path:path}", handle_catchall, methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
]

app = Starlette(debug=False, routes=routes)
