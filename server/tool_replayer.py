"""
Deterministic Agent Tool-Call & Execution Replayer.
Caches idempotent tool executions (file reads, grep, git status, inspection) for coding agents
(Claude Code, Cursor, Devin) and synthesizes synchronized tool_call_ids with workspace state fingerprinting.
"""

import hashlib
import json
import time
import os
import subprocess
from typing import Dict, Any, Optional, Tuple, List

# Explicit tool staleness policy registry
TOOL_POLICIES: Dict[str, Dict[str, Any]] = {
    # Git & Workspace mutable tools: Output invalidates when files / git status change
    "git_status": {"type": "git_workspace", "ttl_seconds": 1800},
    "git_diff": {"type": "git_workspace", "ttl_seconds": 1800},
    "git_log": {"type": "git_workspace", "ttl_seconds": 1800},
    "read_file": {"type": "git_workspace", "ttl_seconds": 3600},
    "view_file": {"type": "git_workspace", "ttl_seconds": 3600},
    "cat": {"type": "git_workspace", "ttl_seconds": 3600},
    "grep_search": {"type": "git_workspace", "ttl_seconds": 1800},
    "find_by_name": {"type": "git_workspace", "ttl_seconds": 1800},
    "list_dir": {"type": "git_workspace", "ttl_seconds": 1800},
    "ls": {"type": "git_workspace", "ttl_seconds": 1800},
    "grep": {"type": "git_workspace", "ttl_seconds": 1800},
    
    # External Network tools: Short, tool-specific TTL (60s)
    "read_url_content": {"type": "external_network", "ttl_seconds": 60},
    "fetch_web": {"type": "external_network", "ttl_seconds": 60},
}


def get_file_fingerprint(file_path: str) -> Optional[str]:
    """Computes a fast timestamp + size fingerprint of a file or directory."""
    try:
        if os.path.exists(file_path):
            st = os.stat(file_path)
            return f"{st.st_mtime_ns}_{st.st_size}"
        return "missing"
    except Exception:
        return None


def get_git_workspace_state(workspace_dir: Optional[str] = None, arguments: Optional[Dict[str, Any]] = None) -> str:
    """
    Computes a cryptographic fingerprint of the workspace state.
    For file-specific operations, incorporates target file mtime_ns + size.
    For git repos, incorporates git commit + uncommitted porcelain status.
    For non-git repos, incorporates file or directory stats.
    """
    # 1. Check if a specific file or directory is passed in arguments
    candidate_path = None
    if arguments and isinstance(arguments, dict):
        for key in (
            "file", "filepath", "path", "target", "filename", "file_path",
            "target_file", "AbsolutePath", "TargetFile", "SearchDirectory", "DirectoryPath"
        ):
            val = arguments.get(key)
            if isinstance(val, str) and val.strip():
                candidate_path = val.strip()
                break

    # Determine relevant target directory and file fingerprint
    if candidate_path:
        if not os.path.isabs(candidate_path):
            candidate_path = os.path.join(workspace_dir or os.getcwd(), candidate_path)
        target_dir = os.path.dirname(candidate_path) if os.path.isfile(candidate_path) or not os.path.isdir(candidate_path) else candidate_path
        file_fp = get_file_fingerprint(candidate_path)
    else:
        target_dir = workspace_dir or os.getcwd()
        file_fp = None

    if not os.path.exists(target_dir):
        target_dir = workspace_dir or os.getcwd()

    # 2. Check git state for target_dir
    git_state = None
    try:
        is_git = subprocess.run(
            ["git", "-C", target_dir, "rev-parse", "--is-inside-work-tree"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1.0
        )
        if is_git.returncode == 0 and is_git.stdout.strip() == "true":
            head_res = subprocess.run(
                ["git", "-C", target_dir, "rev-parse", "HEAD"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1.0
            )
            head_sha = head_res.stdout.strip() if head_res.returncode == 0 else "unknown_head"

            status_res = subprocess.run(
                ["git", "-C", target_dir, "status", "--porcelain"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1.0
            )
            status_raw = status_res.stdout if status_res.returncode == 0 else ""
            status_hash = hashlib.sha256(status_raw.encode("utf-8")).hexdigest()[:16]
            git_state = f"{head_sha}:{status_hash}"
    except Exception:
        pass

    # 3. Combine file fingerprint and git state
    if file_fp and git_state:
        return f"{git_state}:file:{file_fp}"
    elif file_fp:
        return f"file_stat:{file_fp}"
    elif git_state:
        return git_state

    # 4. Non-git directory stat fallback
    try:
        dir_st = os.stat(target_dir)
        return f"nogit_dir:{dir_st.st_mtime_ns}_{dir_st.st_size}"
    except Exception:
        return "nogit"


class ToolExecutionCache:
    """In-memory cache for deterministic agent tool execution outputs with workspace state validation."""
    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.tool_hits = 0
        self.tool_misses = 0
        self.tokens_saved = 0

    @staticmethod
    def is_eligible(tool_name: str) -> bool:
        """Checks if a tool is in the explicit allowlist of replayable tools."""
        return tool_name.strip().lower() in TOOL_POLICIES

    def is_idempotent(self, tool_name: str) -> bool:
        """Backward-compatible alias for is_eligible."""
        return self.is_eligible(tool_name)

    @classmethod
    def compute_tool_hash(
        cls,
        tool_name: str,
        arguments: Dict[str, Any],
        workspace_fingerprint: str = "default",
        workspace_state: Optional[str] = None
    ) -> str:
        """Computes a deterministic hash of tool invocation including workspace state."""
        clean_name = tool_name.strip().lower()
        policy = TOOL_POLICIES.get(clean_name, {})
        policy_type = policy.get("type", "static")

        # Capture dynamic git workspace state for git_workspace tools
        state_str = workspace_state
        if state_str is None:
            if policy_type == "git_workspace":
                state_str = get_git_workspace_state(arguments=arguments)
            else:
                state_str = "static"

        raw = f"{clean_name}:{json.dumps(arguments, sort_keys=True)}:{workspace_fingerprint}:{state_str}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def lookup_tool_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        workspace_fingerprint: str = "default",
        workspace_state: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Looks up if a tool execution is cached and active (not expired, matching workspace state).
        Returns (is_hit, cached_output, tool_key).
        """
        if not self.is_eligible(tool_name):
            return False, None, None

        key = self.compute_tool_hash(tool_name, arguments, workspace_fingerprint, workspace_state)
        if key in self._cache:
            entry = self._cache[key]
            # TTL Expiration Check: Evict if stale
            if time.time() > entry.get("expires_at", float("inf")):
                del self._cache[key]
                self.tool_misses += 1
                return False, None, key

            self.tool_hits += 1
            saved_tokens = entry.get("estimated_tokens", 50)
            self.tokens_saved += saved_tokens
            return True, entry.get("output"), key

        self.tool_misses += 1
        return False, None, key

    def store_tool_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        output: str,
        workspace_fingerprint: str = "default",
        workspace_state: Optional[str] = None,
        ttl_seconds: Optional[int] = None
    ) -> str:
        """Stores a deterministic tool execution output with policy-driven TTL."""
        clean_name = tool_name.strip().lower()
        policy = TOOL_POLICIES.get(clean_name, {})
        effective_ttl = ttl_seconds if ttl_seconds is not None else policy.get("ttl_seconds", 1800)

        key = self.compute_tool_hash(tool_name, arguments, workspace_fingerprint, workspace_state)
        est_tokens = int(len(output.split()) * 1.3) + 10
        self._cache[key] = {
            "tool_name": clean_name,
            "output": output,
            "estimated_tokens": est_tokens,
            "stored_at": time.time(),
            "expires_at": time.time() + effective_ttl,
            "workspace_state": workspace_state
        }
        return key

    def evict_expired(self) -> int:
        """Evicts all expired tool cache entries."""
        now = time.time()
        expired_keys = [k for k, v in self._cache.items() if now > v.get("expires_at", float("inf"))]
        for k in expired_keys:
            del self._cache[k]
        return len(expired_keys)

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
