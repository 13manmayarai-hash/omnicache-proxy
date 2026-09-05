"""
Deterministic Agent Tool-Call & Execution Replayer.
Caches idempotent tool executions (file reads, grep, git status, inspection) for coding agents
(Claude Code, Cursor, Devin) and synthesizes synchronized tool_call_ids with workspace state fingerprinting.
"""

import hashlib
import json
import time
import os
import sqlite3
import subprocess
from typing import Dict, Any, Optional, Tuple, List

# Explicit tool staleness policy registry
TOOL_POLICIES: Dict[str, Dict[str, Any]] = {
    # 1. Target File Specific Tools: Only invalidate when the specific target file is modified
    "read_file": {"type": "target_file", "ttl_seconds": 3600},
    "view_file": {"type": "target_file", "ttl_seconds": 3600},
    "cat": {"type": "target_file", "ttl_seconds": 3600},

    # 2. Scoped Directory Tools: Only invalidate when files within the target directory scope change
    "grep_search": {"type": "scoped_git_workspace", "ttl_seconds": 1800},
    "find_by_name": {"type": "scoped_git_workspace", "ttl_seconds": 1800},
    "list_dir": {"type": "scoped_git_workspace", "ttl_seconds": 1800},
    "ls": {"type": "scoped_git_workspace", "ttl_seconds": 1800},
    "grep": {"type": "scoped_git_workspace", "ttl_seconds": 1800},

    # 3. Global Git Status Tools: Invalidate when any repo status changes
    "git_status": {"type": "git_workspace", "ttl_seconds": 1800},
    "git_diff": {"type": "git_workspace", "ttl_seconds": 1800},
    "git_log": {"type": "git_workspace", "ttl_seconds": 1800},
    
    # 4. External Network tools: Short, tool-specific TTL (60s)
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


def extract_candidate_path(
    workspace_dir: Optional[str] = None,
    arguments: Optional[Dict[str, Any]] = None,
    workspace_fingerprint: Optional[str] = None
) -> Tuple[str, Optional[str]]:
    """
    Extracts the most specific target directory and optional target file from all available inputs.
    Returns (target_dir, file_path_if_any).

    Note on Workspace Resolution:
    - If a real filesystem path is provided (via workspace_dir, arguments.cwd/dir, or workspace_fingerprint),
      it resolves to that directory to probe live git/file staleness on disk.
    - If an opaque string label is passed (e.g. "my-project-alpha"), no disk path exists to probe for
      git state changes; the label partitions the cache key namespace, but live file modification invalidation
      requires an actual resolvable directory path.
    """
    target_file = None
    target_dir = None

    # 1. Check direct workspace_dir parameter
    if workspace_dir and isinstance(workspace_dir, str) and workspace_dir.strip():
        w_dir = os.path.expanduser(workspace_dir.strip())
        if os.path.exists(w_dir):
            target_dir = w_dir if os.path.isdir(w_dir) else os.path.dirname(w_dir)
            if os.path.isfile(w_dir):
                target_file = w_dir

    # 2. Check arguments dictionary for explicit directory or file paths
    if arguments and isinstance(arguments, dict):
        # Check explicit directory keys first (e.g. for git_status, git_diff, ls)
        for key in (
            "cwd", "workspace_dir", "repo_path", "working_directory", "dir",
            "root_dir", "directory", "SearchDirectory", "DirectoryPath"
        ):
            val = arguments.get(key)
            if isinstance(val, str) and val.strip():
                c_dir = os.path.expanduser(val.strip())
                if os.path.exists(c_dir) and os.path.isdir(c_dir):
                    target_dir = c_dir
                    break
                elif not os.path.isabs(c_dir) and target_dir and os.path.exists(os.path.join(target_dir, c_dir)):
                    target_dir = os.path.join(target_dir, c_dir)
                    break

        # Check explicit file keys (e.g. for read_file, view_file, cat)
        for key in (
            "file", "filepath", "path", "target", "filename", "file_path",
            "target_file", "AbsolutePath", "TargetFile"
        ):
            val = arguments.get(key)
            if isinstance(val, str) and val.strip():
                c_file = os.path.expanduser(val.strip())
                if not os.path.isabs(c_file) and target_dir:
                    c_file = os.path.join(target_dir, c_file)
                elif not os.path.isabs(c_file):
                    c_file = os.path.join(os.getcwd(), c_file)
                
                target_file = c_file
                if os.path.exists(c_file):
                    target_dir = os.path.dirname(c_file) if os.path.isfile(c_file) else c_file
                break

    # 3. Check workspace_fingerprint if target_dir not yet resolved to an existing disk path
    if (not target_dir or not os.path.exists(target_dir)) and workspace_fingerprint and isinstance(workspace_fingerprint, str):
        raw = workspace_fingerprint.strip()
        # Handle "org_id:path" format
        if ":" in raw:
            parts = raw.split(":", 1)
            raw = parts[1].strip()
        
        expanded = os.path.expanduser(raw)
        if os.path.exists(expanded):
            target_dir = expanded if os.path.isdir(expanded) else os.path.dirname(expanded)
            if os.path.isfile(expanded):
                target_file = expanded

    # 4. Fallback to current working directory
    if not target_dir or not os.path.exists(target_dir):
        target_dir = os.getcwd()

    return target_dir, target_file


def get_git_workspace_state(
    workspace_dir: Optional[str] = None,
    arguments: Optional[Dict[str, Any]] = None,
    workspace_fingerprint: Optional[str] = None,
    policy_type: str = "git_workspace"
) -> str:
    """
    Computes a fine-grained cryptographic fingerprint of the workspace state.
    - target_file policy: Fingerprints ONLY the specific target file (editing docs won't invalidate code files!).
    - scoped_git_workspace policy: Fingerprints Git status scoped only to the target subdirectory.
    - git_workspace policy: Fingerprints full repository Git HEAD + porcelain dirty status.
    - Non-git fallback: Fingerprints directory mtime/size.
    """
    target_dir, target_file = extract_candidate_path(workspace_dir, arguments, workspace_fingerprint)
    file_fp = get_file_fingerprint(target_file) if target_file else None

    # 1. Smart target_file policy: depend strictly on target file's own mtime/size
    if policy_type == "target_file" and target_file:
        if file_fp and file_fp != "missing":
            return f"target_file:{target_file}:{file_fp}"
        return f"target_file_missing:{target_file}"

    # 2. Check git state for target_dir
    git_state = None
    try:
        is_git = subprocess.run(
            ["git", "-C", target_dir, "rev-parse", "--is-inside-work-tree"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1.5
        )
        if is_git.returncode == 0 and is_git.stdout.strip() == "true":
            head_res = subprocess.run(
                ["git", "-C", target_dir, "rev-parse", "HEAD"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1.5
            )
            head_sha = head_res.stdout.strip() if head_res.returncode == 0 else "unknown_head"

            # Scoped status for directory-specific searches
            if policy_type == "scoped_git_workspace":
                status_res = subprocess.run(
                    ["git", "-C", target_dir, "status", "--porcelain", "."],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1.5
                )
            else:
                status_res = subprocess.run(
                    ["git", "-C", target_dir, "status", "--porcelain"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1.5
                )

            status_raw = status_res.stdout if status_res.returncode == 0 else ""
            status_hash = hashlib.sha256(status_raw.encode("utf-8")).hexdigest()[:16]
            git_state = f"{head_sha}:{status_hash}"
    except Exception:
        pass

    # Combine file fingerprint and git state
    if file_fp and git_state:
        return f"{git_state}:file:{file_fp}"
    elif file_fp:
        return f"file_stat:{file_fp}"
    elif git_state:
        return git_state

    # Non-git directory stat fallback
    try:
        dir_st = os.stat(target_dir)
        return f"nogit_dir:{dir_st.st_mtime_ns}_{dir_st.st_size}"
    except Exception:
        return "nogit"


def _get_tool_db_conn() -> Optional[sqlite3.Connection]:
    base_dir = os.getenv("OMNICACHE_DATA_DIR", os.path.expanduser("~/.omnicache"))
    db_path = os.getenv("OMNICACHE_DB_PATH", os.path.join(base_dir, "omnicache.db"))
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_call_records (
                key TEXT PRIMARY KEY,
                tool_name TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                output TEXT NOT NULL,
                workspace_fingerprint TEXT,
                workspace_state TEXT,
                estimated_tokens INTEGER DEFAULT 50,
                stored_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_expiry ON tool_call_records(expires_at)")
        return conn
    except Exception:
        return None


class ToolExecutionCache:
    """In-memory cache with durable SQLite backing for deterministic agent tool execution outputs."""
    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.tool_hits = 0
        self.tool_misses = 0
        self.tokens_saved = 0

    @staticmethod
    def is_eligible(tool_name: str) -> bool:
        """Checks if a tool is eligible for tool-call caching."""
        clean = tool_name.strip().lower()
        if clean in TOOL_POLICIES:
            return True
        safe_prefixes = ("read", "view", "get", "fetch", "list", "search", "find", "cat", "check", "inspect", "show")
        return any(clean.startswith(p) for p in safe_prefixes)

    def is_idempotent(self, tool_name: str) -> bool:
        """Backward-compatible alias for is_eligible."""
        return self.is_eligible(tool_name)

    @classmethod
    def compute_tool_hash(
        cls,
        tool_name: str,
        arguments: Dict[str, Any],
        workspace_fingerprint: str = "default",
        workspace_state: Optional[str] = None,
        workspace_dir: Optional[str] = None
    ) -> str:
        """Computes a deterministic hash of tool invocation including workspace state."""
        clean_name = tool_name.strip().lower()
        policy = TOOL_POLICIES.get(clean_name, {})
        policy_type = policy.get("type", "static")

        # Capture dynamic git workspace state with fine-grained policy
        state_str = workspace_state
        if state_str is None:
            if policy_type in ("git_workspace", "target_file", "scoped_git_workspace"):
                state_str = get_git_workspace_state(
                    workspace_dir=workspace_dir,
                    arguments=arguments,
                    workspace_fingerprint=workspace_fingerprint,
                    policy_type=policy_type
                )
            else:
                state_str = "static"

        raw = f"{clean_name}:{json.dumps(arguments, sort_keys=True)}:{workspace_fingerprint}:{state_str}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def lookup_tool_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        workspace_fingerprint: str = "default",
        workspace_state: Optional[str] = None,
        workspace_dir: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Looks up if a tool execution is cached and active (not expired, matching workspace state).
        Returns (is_hit, cached_output, tool_key).
        """
        clean_name = tool_name.strip().lower()
        key = self.compute_tool_hash(
            tool_name, arguments, workspace_fingerprint, workspace_state, workspace_dir=workspace_dir
        )

        # 1. Check in-memory hot cache
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

        # 2. Check durable SQLite store (cross-process sharing between Gateway & MCP)
        conn = _get_tool_db_conn()
        if conn:
            try:
                cur = conn.cursor()
                row = cur.execute(
                    "SELECT output, estimated_tokens, expires_at, workspace_state FROM tool_call_records WHERE key = ?",
                    (key,)
                ).fetchone()
                if row:
                    output_val, est_tokens, expires_at, ws_state = row
                    if time.time() <= expires_at:
                        self._cache[key] = {
                            "tool_name": clean_name,
                            "output": output_val,
                            "estimated_tokens": est_tokens,
                            "stored_at": time.time(),
                            "expires_at": expires_at,
                            "workspace_state": ws_state
                        }
                        self.tool_hits += 1
                        self.tokens_saved += est_tokens
                        return True, output_val, key
                    else:
                        with conn:
                            conn.execute("DELETE FROM tool_call_records WHERE key = ?", (key,))
            except Exception:
                pass
            finally:
                conn.close()

        self.tool_misses += 1
        return False, None, key

    def store_tool_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        output: str,
        workspace_fingerprint: str = "default",
        workspace_state: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
        workspace_dir: Optional[str] = None
    ) -> str:
        """Stores a deterministic tool execution output with policy-driven TTL."""
        clean_name = tool_name.strip().lower()
        policy = TOOL_POLICIES.get(clean_name, {})
        effective_ttl = ttl_seconds if ttl_seconds is not None else policy.get("ttl_seconds", 1800)

        key = self.compute_tool_hash(
            tool_name, arguments, workspace_fingerprint, workspace_state, workspace_dir=workspace_dir
        )
        est_tokens = int(len(output.split()) * 1.3) + 10
        now = time.time()
        expires_at = now + effective_ttl

        self._cache[key] = {
            "tool_name": clean_name,
            "output": output,
            "estimated_tokens": est_tokens,
            "stored_at": now,
            "expires_at": expires_at,
            "workspace_state": workspace_state
        }

        # Persist to SQLite for cross-process MCP / Gateway sharing
        conn = _get_tool_db_conn()
        if conn:
            try:
                with conn:
                    conn.execute("""
                        INSERT OR REPLACE INTO tool_call_records (
                            key, tool_name, arguments_json, output,
                            workspace_fingerprint, workspace_state,
                            estimated_tokens, stored_at, expires_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        key, clean_name, json.dumps(arguments, sort_keys=True),
                        output, workspace_fingerprint, workspace_state,
                        est_tokens, now, expires_at
                    ))
            except Exception:
                pass
            finally:
                conn.close()

        return key

    def evict_expired(self) -> int:
        """Evicts all expired tool cache entries from RAM and SQLite."""
        now = time.time()
        expired_keys = [k for k, v in self._cache.items() if now > v.get("expires_at", float("inf"))]
        for k in expired_keys:
            del self._cache[k]
        conn = _get_tool_db_conn()
        if conn:
            try:
                with conn:
                    conn.execute("DELETE FROM tool_call_records WHERE expires_at < ?", (now,))
            except Exception:
                pass
            finally:
                conn.close()
        return len(expired_keys)

    def clear(self) -> None:
        """Clears all in-memory and durable SQLite tool records."""
        self._cache.clear()
        conn = _get_tool_db_conn()
        if conn:
            try:
                with conn:
                    conn.execute("DELETE FROM tool_call_records")
            except Exception:
                pass
            finally:
                conn.close()

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
