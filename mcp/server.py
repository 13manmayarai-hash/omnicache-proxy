"""
OmniCache Model Context Protocol (MCP) Server & Engine.
Standard JSON-RPC 2.0 server supporting both Stdio transport and authenticated HTTP/SSE transports.
Provides semantic caching, vector search, knowledge storage, and cost telemetry tools to AI agents and IDEs.
"""

import sys
import os
import json
import time
from typing import Dict, Any, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.config import config
from core.embeddings import FastSemanticEmbedder
from core.vector_cache import cache_instance
from persistence.snapshot_store import snapshot_store
from server.upstream import upstream_client
from server.tool_replayer import tool_cache
from core.privacy_shield import PrivacyShield

# Restore persistent entries into cache
snapshot_store.load_into_cache(cache_instance)

# Internal fallback when a client omits `model`. L2 lookups are scoped by model family,
# so this stays at the value earlier releases used to keep previously stored entries reachable.
# It is not advertised in the tool schemas.
DEFAULT_MCP_MODEL = "gpt-4o"

# Tools that operate on the caller's local workspace (paths, git state). They are only
# meaningful over the local stdio transport and are not exposed on the hosted HTTP endpoint.
LOCAL_ONLY_TOOLS = {"omnicache_replay_tool", "omnicache_record_tool"}

TOOLS_METADATA = [
    {
        "name": "omnicache_query",
        "description": "Looks up a previously stored answer whose prompt is semantically similar to the given prompt. Returns the stored answer and its similarity score, or a miss if nothing is close enough.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The user prompt or query to look up in cache."},
                "model": {"type": "string", "description": "Optional model label used to scope the lookup to answers stored for the same model family."},
                "org_id": {"type": "string", "description": "Tenant ID (default: default).", "default": "default"},
                "threshold": {"type": "number", "description": "Optional minimum cosine similarity (0.0 - 1.0)."}
            },
            "required": ["prompt"]
        },
        "annotations": {
            "title": "Semantic Cache Query",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False
        }
    },
    {
        "name": "omnicache_store",
        "description": "Stores an answer, documentation snippet, or code solution in your OmniCache memory so it can be found later with omnicache_query or omnicache_search.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The prompt or question associated with this answer."},
                "answer": {"type": "string", "description": "The generated answer, code, or explanation to cache."},
                "model": {"type": "string", "description": "Optional model label to associate with the stored answer."},
                "tag": {"type": "string", "description": "Optional domain tag (e.g. 'docs-v1', 'sql-tips')."},
                "org_id": {"type": "string", "description": "Tenant ID (default: default).", "default": "default"},
                "ttl_seconds": {"type": "integer", "description": "Optional time-to-live in seconds (default: 604800, i.e. 7 days).", "default": 604800}
            },
            "required": ["prompt", "answer"]
        },
        "annotations": {
            "title": "Store Knowledge in Cache",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False
        }
    },
    {
        "name": "omnicache_search",
        "description": "Searches your stored entries by meaning and returns the closest matches with their similarity scores.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term or concept."},
                "org_id": {"type": "string", "description": "Tenant ID (default: default).", "default": "default"},
                "top_k": {"type": "integer", "description": "Number of top results to return (default: 5).", "default": 5}
            },
            "required": ["query"]
        },
        "annotations": {
            "title": "Semantic Vector Search",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False
        }
    },
    {
        "name": "omnicache_replay_tool",
        "description": "Looks up cached execution outputs for deterministic agent tools (e.g. read_file, git_status, grep) with workspace state validation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "Name of the tool (e.g. 'read_file', 'git_status')."},
                "arguments": {"type": "object", "description": "Arguments passed to the tool."},
                "workspace_fingerprint": {"type": "string", "description": "Workspace identifier (default: default).", "default": "default"},
                "workspace_state": {"type": "string", "description": "Optional explicit git/workspace state."}
            },
            "required": ["tool_name"]
        },
        "annotations": {
            "title": "Replay Deterministic Tool",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False
        }
    },
    {
        "name": "omnicache_record_tool",
        "description": "Records and caches execution output of a deterministic tool run for fast subsequent replay.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "Name of the tool."},
                "arguments": {"type": "object", "description": "Arguments passed to the tool."},
                "output": {"type": "string", "description": "Execution output of the tool to cache."},
                "workspace_fingerprint": {"type": "string", "description": "Workspace identifier (default: default).", "default": "default"},
                "workspace_state": {"type": "string", "description": "Optional explicit git/workspace state."},
                "ttl_seconds": {"type": "integer", "description": "Custom TTL in seconds."}
            },
            "required": ["tool_name", "output"]
        },
        "annotations": {
            "title": "Record Tool Execution",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False
        }
    },
    {
        "name": "omnicache_invalidate",
        "description": "Deletes stored entries. With a tag, deletes only entries carrying that tag; without a tag, deletes all of your entries.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "Tag to invalidate (e.g. 'docs-v1')."},
                "org_id": {"type": "string", "description": "Tenant ID to purge completely."}
            }
        },
        "annotations": {
            "title": "Invalidate Cache Entries",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False
        }
    },
    {
        "name": "omnicache_stats",
        "description": "Returns usage statistics for your entries: entry counts, total lookups and hit rate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "org_id": {"type": "string", "description": "Optional tenant ID filter."}
            }
        },
        "annotations": {
            "title": "Cache Telemetry Stats",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False
        }
    },
    {
        "name": "omnicache_health",
        "description": "Reports whether the service is ready: storage connectivity and the number of active entries.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        },
        "annotations": {
            "title": "System Health & Readiness",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False
        }
    }
]


def handle_tool_call(name: str, arguments: dict, default_org_id: str = "default", is_admin: bool = False, remote: bool = False) -> dict:
    target_org = arguments.get("org_id")
    if target_org and target_org != default_org_id and not is_admin:
        return {
            "error": {
                "code": -32600,
                "message": f"Forbidden: Tenant '{default_org_id}' cannot access or manipulate org_id '{target_org}'. Admin privileges required."
            }
        }
    org_id = target_org if is_admin and target_org else default_org_id
    clean_name = name[len("omnicache_"):] if name.startswith("omnicache_") else name

    if clean_name == "query":
        prompt = arguments.get("prompt", "")
        model = arguments.get("model") or DEFAULT_MCP_MODEL
        threshold = arguments.get("threshold", None)

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0
        }
        status, entry, sim, reason = cache_instance.lookup(payload, org_id=org_id, custom_threshold=threshold)

        if entry and status in ("HIT_EXACT", "HIT_SEMANTIC"):
            content = entry.response_payload.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "cache_status": status,
                        "similarity_score": round(sim, 4),
                        "cached_model": entry.model,
                        "cached_response": content
                    }, indent=2)
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "cache_status": "MISS",
                        "best_similarity": round(sim, 4),
                        "message": "No sufficiently close cached answer found."
                    }, indent=2)
                }]
            }

    elif clean_name == "store":
        prompt = arguments.get("prompt", "")
        answer = arguments.get("answer", "")
        clean_prompt, _, _ = PrivacyShield.sanitize_text(prompt)
        clean_answer, _, _ = PrivacyShield.sanitize_text(answer)
        model = arguments.get("model") or DEFAULT_MCP_MODEL
        tag = arguments.get("tag", None)
        raw_ttl = arguments.get("ttl_seconds")
        ttl_seconds = None
        if raw_ttl is not None:
            try:
                ttl_seconds = max(60, int(raw_ttl))
            except (ValueError, TypeError):
                ttl_seconds = None

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": clean_prompt}],
            "temperature": 0.0
        }
        res_payload = {
            "id": f"chatcmpl-mcp-{int(time.time()*1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"message": {"role": "assistant", "content": clean_answer}}],
            "usage": {"prompt_tokens": len(clean_prompt.split()), "completion_tokens": len(clean_answer.split())}
        }
        entry = cache_instance.store(payload, res_payload, org_id=org_id, tag=tag, custom_ttl=ttl_seconds)
        snapshot_store.persist_entry(entry, synchronous=False)

        return {
            "content": [{
                "type": "text",
                "text": f"Successfully stored entry into OmniCache (Key: {entry.key[:12]}..., Tag: {tag}, Org: {org_id})."
            }]
        }

    elif clean_name == "search":
        query = arguments.get("query", "")
        top_k = arguments.get("top_k", 5)
        entries = cache_instance.l2_semantic_cache.get(org_id, [])
        query_vec = FastSemanticEmbedder.embed(query)

        scored = []
        for e in entries:
            sim = FastSemanticEmbedder.cosine_similarity(query_vec, e.vector)
            scored.append({
                "user_prompt": e.user_prompt,
                "similarity": round(sim, 4),
                "model": e.model,
                "tag": e.tag,
                "response_snippet": e.response_payload.get("choices", [{}])[0].get("message", {}).get("content", "")[:200]
            })

        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return {
            "content": [{"type": "text", "text": json.dumps(scored[:top_k], indent=2)}]
        }

    elif clean_name == "replay_tool":
        tool_name = arguments.get("tool_name", "")
        tool_args = arguments.get("arguments", {})
        raw_fp = arguments.get("workspace_fingerprint", "default")
        ws_dir = arguments.get("workspace_dir") or arguments.get("cwd") or arguments.get("repo_path") or None
        ws_state = arguments.get("workspace_state", None)
        env_fp = f"{org_id}:{raw_fp}"

        is_hit, output, tool_key = tool_cache.lookup_tool_call(
            tool_name, tool_args, workspace_fingerprint=env_fp, workspace_state=ws_state, workspace_dir=ws_dir
        )
        if not is_hit and (org_id == "default" or raw_fp == "default"):
            is_hit, output, tool_key = tool_cache.lookup_tool_call(
                tool_name, tool_args, workspace_fingerprint=raw_fp, workspace_state=ws_state, workspace_dir=ws_dir
            )

        if is_hit:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "status": "HIT",
                        "tool_name": tool_name,
                        "tool_key": tool_key,
                        "output": output,
                        "cached": True
                    }, indent=2)
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "status": "MISS",
                        "tool_name": tool_name,
                        "cached": False
                    }, indent=2)
                }]
            }

    elif clean_name == "record_tool":
        tool_name = arguments.get("tool_name", "")
        tool_args = arguments.get("arguments", {})
        output = str(arguments.get("output", ""))
        clean_output, _, _ = PrivacyShield.sanitize_text(output)
        raw_fp = arguments.get("workspace_fingerprint", "default")
        ws_dir = arguments.get("workspace_dir") or arguments.get("cwd") or arguments.get("repo_path") or None
        ws_state = arguments.get("workspace_state", None)
        ttl = arguments.get("ttl_seconds", None)
        env_fp = f"{org_id}:{raw_fp}"

        tool_key = tool_cache.store_tool_call(
            tool_name=tool_name,
            arguments=tool_args,
            output=clean_output,
            workspace_fingerprint=env_fp,
            workspace_state=ws_state,
            ttl_seconds=ttl,
            workspace_dir=ws_dir
        )
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "status": "STORED",
                    "tool_name": tool_name,
                    "tool_key": tool_key,
                    "cached": True
                }, indent=2)
            }]
        }

    elif clean_name == "invalidate":
        tag = arguments.get("tag")
        if tag:
            removed = cache_instance.invalidate_tag(tag, org_id=org_id)
            snapshot_store.remove_by_tag(tag, org_id=org_id)
            return {"content": [{"type": "text", "text": f"Invalidated {removed} entries with tag '{tag}'."}]}
        else:
            removed = cache_instance.purge(org_id=org_id)
            snapshot_store.purge_all(org_id=org_id)
            return {"content": [{"type": "text", "text": f"Purged {removed} entries for tenant '{org_id}'."}]}

    elif clean_name == "stats":
        stats = cache_instance.get_stats(org_id)
        return {"content": [{"type": "text", "text": json.dumps(stats, indent=2)}]}

    elif clean_name == "health":
        stats = cache_instance.get_stats(org_id)
        db_exists = os.path.exists(snapshot_store.db_path)
        persistence_info = {"connected": db_exists}
        if not remote:
            persistence_info["sqlite_path"] = snapshot_store.db_path
        health_info = {
            "status": "healthy",
            "version": getattr(config, "VERSION", "3.1.0"),
            "tenant_id": org_id,
            "persistence": persistence_info,
            "vector_cache": {
                "active_l1_exact": stats.get("active_l1_exact_entries", 0),
                "active_l2_semantic": stats.get("active_l2_semantic_entries", 0),
                "total_requests": stats.get("total_requests", 0),
                "hit_rate_pct": stats.get("hit_rate_percentage", 0.0)
            },
            "tool_replayer": {
                "status": "active",
                "registered_signatures": len(getattr(tool_cache, "_cache", {}))
            }
        }
        return {"content": [{"type": "text", "text": json.dumps(health_info, indent=2)}]}

    return {"error": {"code": -32601, "message": f"Unknown tool: {name}"}}


def log_audit_event(tool_name: str, org_id: str, duration_ms: float, status: str, details: Optional[dict] = None):
    """Appends structured audit log for enterprise compliance and SOC2 traceability."""
    audit_file = os.environ.get("OMNICACHE_AUDIT_LOG_PATH")
    if not audit_file:
        homedir = os.path.expanduser("~")
        omni_dir = os.path.join(homedir, ".omnicache")
        if os.path.isdir(omni_dir):
            audit_file = os.path.join(omni_dir, "mcp_audit.jsonl")
    if audit_file:
        try:
            event = {
                "timestamp": time.time(),
                "iso_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "tool": tool_name,
                "org_id": org_id,
                "duration_ms": duration_ms,
                "status": status
            }
            if details:
                event["details"] = details
            with open(audit_file, "a", encoding="utf-8") as af:
                af.write(json.dumps(event) + "\n")
        except Exception:
            pass


def prune_jsonl_audit_logs(retention_days: int = 90) -> int:
    """Prunes JSONL audit file entries older than retention_days (SOC2/GDPR retention enforcement)."""
    audit_file = os.environ.get("OMNICACHE_AUDIT_LOG_PATH")
    if not audit_file:
        homedir = os.path.expanduser("~")
        omni_dir = os.path.join(homedir, ".omnicache")
        if os.path.isdir(omni_dir):
            audit_file = os.path.join(omni_dir, "mcp_audit.jsonl")
    if not audit_file or not os.path.isfile(audit_file):
        return 0

    cutoff = time.time() - (retention_days * 86400)
    pruned_count = 0
    try:
        surviving_lines = []
        with open(audit_file, "r", encoding="utf-8") as af:
            for line in af:
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                    ts = ev.get("timestamp", 0)
                    if ts >= cutoff:
                        surviving_lines.append(line)
                    else:
                        pruned_count += 1
                except Exception:
                    surviving_lines.append(line)
        if pruned_count > 0:
            with open(audit_file, "w", encoding="utf-8") as af:
                af.writelines(surviving_lines)
    except Exception:
        pass
    return pruned_count


def list_tools(remote: bool = False) -> list:
    """Returns the tool definitions exposed on the given transport."""
    if not remote:
        return TOOLS_METADATA
    return [t for t in TOOLS_METADATA if t["name"] not in LOCAL_ONLY_TOOLS]


def build_server_instructions(remote: bool = False) -> str:
    """Describes what the server offers. Deliberately factual: it does not tell the model when to call tools."""
    lines = [
        "OmniCache is a searchable memory for answers, code snippets and notes, scoped to your account.",
        "",
        "Tools:",
        "- omnicache_store: save an answer, snippet or note, optionally with a tag.",
        "- omnicache_query: look up a stored answer for a semantically similar prompt.",
        "- omnicache_search: search stored entries by meaning.",
        "- omnicache_invalidate: delete entries by tag, or all of your entries (destructive).",
        "- omnicache_stats: usage statistics for your entries.",
        "- omnicache_health: service readiness.",
    ]
    if not remote:
        lines += [
            "- omnicache_replay_tool: look up a recorded output of a deterministic local tool call.",
            "- omnicache_record_tool: record a local tool call output for later replay.",
        ]
    return "\n".join(lines)


def _process_single_jsonrpc(req: Dict[str, Any], default_org_id: str = "default", is_admin: bool = False, token_scope: str = "mcp:admin", remote: bool = False) -> Optional[Dict[str, Any]]:
    """Processes a single JSON-RPC 2.0 MCP request dict. Returns None for notifications."""
    is_notification = ("id" not in req)
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params", {})

    if method == "initialize":
        if is_notification:
            return None
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {
                    "name": "omnicache-mcp",
                    "version": getattr(config, "VERSION", "3.1.0")
                },
                "instructions": build_server_instructions(remote),
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"listChanged": False},
                    "prompts": {"listChanged": False}
                }
            }
        }
    elif method in ("notifications/initialized", "initialized"):
        return None if is_notification else {"jsonrpc": "2.0", "id": req_id, "result": {}}
    elif method == "ping":
        return None if is_notification else {"jsonrpc": "2.0", "id": req_id, "result": {}}
    elif method == "tools/list":
        if is_notification:
            return None
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": list_tools(remote)}
        }
    elif method == "resources/list":
        if is_notification:
            return None
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"resources": []}
        }
    elif method == "prompts/list":
        if is_notification:
            return None
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"prompts": []}
        }
    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        clean_name = tool_name[len("omnicache_"):] if tool_name.startswith("omnicache_") else tool_name

        if remote and f"omnicache_{clean_name}" in LOCAL_ONLY_TOOLS:
            if is_notification:
                return None
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}
            }

        if clean_name == "invalidate" and ("mcp:admin" not in token_scope and "mcp:write" not in token_scope):
            if is_notification:
                return None
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32600,
                    "message": f"Forbidden: Token scope '{token_scope}' does not permit destructive tool '{tool_name}'. Required scope: 'mcp:write' or 'mcp:admin'."
                }
            }
        if clean_name in ("store", "record_tool") and ("mcp:write" not in token_scope and "mcp:admin" not in token_scope):
            if is_notification:
                return None
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32600,
                    "message": f"Forbidden: Token scope '{token_scope}' does not permit write tool '{tool_name}'. Required scope: 'mcp:write' or 'mcp:admin'."
                }
            }

        target_org = tool_args.get("org_id")
        if target_org and target_org != default_org_id and not is_admin:
            if is_notification:
                return None
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32600,
                    "message": f"Forbidden: Tenant '{default_org_id}' cannot access or manipulate org_id '{target_org}'. Admin privileges required."
                }
            }
        org = target_org if is_admin and target_org else default_org_id
        t0 = time.perf_counter()
        try:
            tool_res = handle_tool_call(tool_name, tool_args, default_org_id=org, is_admin=is_admin, remote=remote)
        except Exception as exc:
            dur = round((time.perf_counter() - t0) * 1000, 3)
            log_audit_event(tool_name, org, dur, "error", {"error": str(exc)})
            if is_notification:
                return None
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32603,
                    "message": f"Internal error during tool execution: {str(exc)}"
                }
            }
        dur = round((time.perf_counter() - t0) * 1000, 3)
        status_str = "error" if "error" in tool_res else "success"
        log_audit_event(tool_name, org, dur, status_str)
        if is_notification:
            return None
        if "error" in tool_res:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": tool_res["error"]
            }
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": tool_res
        }
    else:
        if is_notification:
            return None
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}
        }


def process_mcp_jsonrpc(req: Any, default_org_id: str = "default", is_admin: bool = False, token_scope: str = "mcp:admin", remote: bool = False) -> Optional[Any]:
    """Processes a standard JSON-RPC 2.0 MCP message (single dict or batch list) and returns response."""
    if isinstance(req, list):
        if not req:
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request: empty batch"}
            }
        batch_responses = []
        for single_req in req:
            if not isinstance(single_req, dict):
                batch_responses.append({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "Invalid Request: expected object"}
                })
                continue
            item_res = _process_single_jsonrpc(single_req, default_org_id=default_org_id, is_admin=is_admin, token_scope=token_scope, remote=remote)
            if item_res is not None:
                batch_responses.append(item_res)
        return batch_responses if batch_responses else None

    if not isinstance(req, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid Request: expected object or array"}
        }

    return _process_single_jsonrpc(req, default_org_id=default_org_id, is_admin=is_admin, token_scope=token_scope, remote=remote)


def run_stdio_server():
    """Main JSON-RPC stdio event loop with comprehensive exception resilience."""
    default_org = os.environ.get("OMNICACHE_ORG_ID", "default")
    admin_key = os.environ.get("ADMIN_API_KEY", "").strip()
    is_admin = bool(admin_key) or not getattr(config, "REQUIRE_AUTH", False)

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
        except Exception as exc:
            err_res = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {str(exc)}"}
            }
            sys.stdout.write(json.dumps(err_res) + "\n")
            sys.stdout.flush()
            continue

        try:
            res = process_mcp_jsonrpc(req, default_org_id=default_org, is_admin=is_admin)
        except Exception as exc:
            res = {
                "jsonrpc": "2.0",
                "id": req.get("id") if isinstance(req, dict) else None,
                "error": {"code": -32603, "message": f"Internal error: {str(exc)}"}
            }
        if res is not None:
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_stdio_server()
