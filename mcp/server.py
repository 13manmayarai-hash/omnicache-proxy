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

# Restore persistent entries into cache
snapshot_store.load_into_cache(cache_instance)

TOOLS_METADATA = [
    {
        "name": "omnicache_query",
        "description": "Performs an intent-gated semantic cache lookup for a prompt. Returns cached answer if similarity exceeds threshold (<1ms latency), saving 100% LLM tokens and API cost.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The user prompt or query to look up in cache."},
                "model": {"type": "string", "description": "Target model (default: gpt-4o).", "default": "gpt-4o"},
                "org_id": {"type": "string", "description": "Tenant ID (default: default).", "default": "default"},
                "threshold": {"type": "number", "description": "Optional minimum cosine similarity (0.0 - 1.0)."}
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "omnicache_store",
        "description": "Explicitly stores a high-value answer, documentation snippet, or code solution into the OmniCache vector memory for future instant retrieval.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The prompt or question associated with this answer."},
                "answer": {"type": "string", "description": "The generated answer, code, or explanation to cache."},
                "model": {"type": "string", "description": "Model associated with answer (default: gpt-4o).", "default": "gpt-4o"},
                "tag": {"type": "string", "description": "Optional domain tag (e.g. 'docs-v1', 'sql-tips')."},
                "org_id": {"type": "string", "description": "Tenant ID (default: default).", "default": "default"}
            },
            "required": ["prompt", "answer"]
        }
    },
    {
        "name": "omnicache_search",
        "description": "Performs sub-millisecond semantic similarity vector search across all cached prompts and solutions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term or concept."},
                "org_id": {"type": "string", "description": "Tenant ID (default: default).", "default": "default"},
                "top_k": {"type": "integer", "description": "Number of top results to return (default: 5).", "default": 5}
            },
            "required": ["query"]
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
        }
    },
    {
        "name": "omnicache_invalidate",
        "description": "Purges or invalidates cached knowledge by tag, tenant ID, or pattern.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "Tag to invalidate (e.g. 'docs-v1')."},
                "org_id": {"type": "string", "description": "Tenant ID to purge completely."}
            }
        }
    },
    {
        "name": "omnicache_stats",
        "description": "Returns real-time telemetry: cache hit ratios, total requests, and total dollars saved in LLM costs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "org_id": {"type": "string", "description": "Optional tenant ID filter."}
            }
        }
    },
    {
        "name": "replay_tool",
        "description": "Alias for omnicache_replay_tool. Looks up cached execution outputs for deterministic agent tools.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "Name of the tool (e.g. 'read_file', 'git_status')."},
                "arguments": {"type": "object", "description": "Arguments passed to the tool."},
                "workspace_fingerprint": {"type": "string", "description": "Workspace identifier (default: default).", "default": "default"},
                "workspace_state": {"type": "string", "description": "Optional explicit git/workspace state."}
            },
            "required": ["tool_name"]
        }
    },
    {
        "name": "record_tool",
        "description": "Alias for omnicache_record_tool. Records and caches execution output of a deterministic tool run.",
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
        }
    }
]


def handle_tool_call(name: str, arguments: dict, default_org_id: str = "default") -> dict:
    org_id = arguments.get("org_id") or default_org_id
    clean_name = name[len("omnicache_"):] if name.startswith("omnicache_") else name

    if clean_name == "query":
        prompt = arguments.get("prompt", "")
        model = arguments.get("model", "gpt-4o")
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
        model = arguments.get("model", "gpt-4o")
        tag = arguments.get("tag", None)

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0
        }
        res_payload = {
            "id": f"chatcmpl-mcp-{int(time.time()*1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"message": {"role": "assistant", "content": answer}}],
            "usage": {"prompt_tokens": len(prompt.split()), "completion_tokens": len(answer.split())}
        }
        entry = cache_instance.store(payload, res_payload, org_id=org_id, tag=tag)
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
        raw_fp = arguments.get("workspace_fingerprint", "default")
        ws_dir = arguments.get("workspace_dir") or arguments.get("cwd") or arguments.get("repo_path") or None
        ws_state = arguments.get("workspace_state", None)
        ttl = arguments.get("ttl_seconds", None)
        env_fp = f"{org_id}:{raw_fp}"

        tool_key = tool_cache.store_tool_call(
            tool_name=tool_name,
            arguments=tool_args,
            output=output,
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

    return {"error": {"code": -32601, "message": f"Unknown tool: {name}"}}


def process_mcp_jsonrpc(req: Dict[str, Any], default_org_id: str = "default") -> Dict[str, Any]:
    """Processes a standard JSON-RPC 2.0 MCP message and returns the response dictionary."""
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {
                    "name": "omnicache-mcp",
                    "version": getattr(config, "VERSION", "2.5.4")
                },
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"listChanged": False}
                }
            }
        }
    elif method in ("notifications/initialized", "initialized"):
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    elif method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS_METADATA}
        }
    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        tool_res = handle_tool_call(tool_name, tool_args, default_org_id=default_org_id)
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
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}
        }


def run_stdio_server():
    """Main JSON-RPC stdio event loop."""
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue

        res = process_mcp_jsonrpc(req, default_org_id="default")
        sys.stdout.write(json.dumps(res) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    run_stdio_server()
