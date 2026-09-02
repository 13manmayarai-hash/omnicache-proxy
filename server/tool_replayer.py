"""
Deterministic Agent Tool-Call & Execution Replayer.
Caches idempotent tool executions (file reads, grep, git status, inspection) for coding agents
(Claude Code, Cursor, Devin) and synthesizes synchronized tool_call_ids.
"""

import hashlib
import json
import time
from typing import Dict, Any, Optional, Tuple, List

# List of known idempotent / read-only agent tools
IDEMPOTENT_TOOLS = {
    "read_file", "view_file", "grep_search", "find_by_name", "list_dir",
    "git_status", "git_log", "git_diff", "cat", "ls", "grep", "read_url_content"
}

class ToolExecutionCache:
    """In-memory cache for deterministic agent tool execution outputs."""
    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.tool_hits = 0
        self.tool_misses = 0
        self.tokens_saved = 0

    @staticmethod
    def compute_tool_hash(tool_name: str, arguments: Dict[str, Any], workspace_fingerprint: str = "default") -> str:
        """Computes a deterministic hash of tool invocation."""
        raw = f"{tool_name.strip().lower()}:{json.dumps(arguments, sort_keys=True)}:{workspace_fingerprint}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def is_idempotent(self, tool_name: str) -> bool:
        """Checks if a tool is safe to cache and replay."""
        clean_name = tool_name.strip().lower()
        return clean_name in IDEMPOTENT_TOOLS

    def lookup_tool_call(self, tool_name: str, arguments: Dict[str, Any], workspace_fingerprint: str = "default") -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Looks up if a tool execution is cached.
        Returns (is_hit, cached_output, tool_key).
        """
        if not self.is_idempotent(tool_name):
            return False, None, None

        key = self.compute_tool_hash(tool_name, arguments, workspace_fingerprint)
        if key in self._cache:
            entry = self._cache[key]
            self.tool_hits += 1
            saved_tokens = entry.get("estimated_tokens", 50)
            self.tokens_saved += saved_tokens
            return True, entry.get("output"), key

        self.tool_misses += 1
        return False, None, key

    def store_tool_call(self, tool_name: str, arguments: Dict[str, Any], output: str, workspace_fingerprint: str = "default", ttl_seconds: int = 3600) -> str:
        """Stores a deterministic tool execution output."""
        key = self.compute_tool_hash(tool_name, arguments, workspace_fingerprint)
        est_tokens = int(len(output.split()) * 1.3) + 10
        self._cache[key] = {
            "tool_name": tool_name,
            "output": output,
            "estimated_tokens": est_tokens,
            "stored_at": time.time(),
            "expires_at": time.time() + ttl_seconds
        }
        return key

    def synthesize_tool_call_delta(self, tool_name: str, arguments: Dict[str, Any], cached_output: str, call_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Synthesizes standard OpenAI / Anthropic compliant tool_call message structure.
        """
        synced_id = call_id or f"call_{hashlib.sha256(f'{tool_name}:{time.time()}'.encode()).hexdigest()[:12]}"
        return {
            "id": synced_id,
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(arguments) if isinstance(arguments, dict) else str(arguments)
            },
            "_cached_output": cached_output,
            "_replayed_by": "omnicache_agent_accelerator"
        }

# Global Tool Execution Cache instance
tool_cache = ToolExecutionCache()
