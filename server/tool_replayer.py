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

# Explicit tool mutation & safety registries
DEFAULT_MUTATION_PREFIXES = (
    "create", "delete", "update", "insert", "post", "write", "charge",
    "pay", "send", "execute", "modify", "remove", "drop", "cancel",
    "refund", "edit", "patch", "put", "process", "submit", "trigger"
)

DEFAULT_SAFE_PREFIXES = (
    "read", "view", "get", "fetch", "list", "search", "find",
    "cat", "check", "inspect", "show", "lookup", "query", "scan", "describe"
)

# Built-in tool staleness and cacheability policy registry
DEFAULT_BUILTIN_POLICIES: Dict[str, Dict[str, Any]] = {
    # 1. Target File Specific Tools: Only invalidate when the specific target file is modified
    "read_file": {"type": "target_file", "ttl_seconds": 3600, "cacheable": True, "deduplicate_history": True},
    "view_file": {"type": "target_file", "ttl_seconds": 3600, "cacheable": True, "deduplicate_history": True},
    "cat": {"type": "target_file", "ttl_seconds": 3600, "cacheable": True, "deduplicate_history": True},

    # 2. Scoped Directory Tools: Only invalidate when files within the target directory scope change
    "grep_search": {"type": "scoped_git_workspace", "ttl_seconds": 1800, "cacheable": True, "deduplicate_history": True},
    "find_by_name": {"type": "scoped_git_workspace", "ttl_seconds": 1800, "cacheable": True, "deduplicate_history": True},
    "list_dir": {"type": "scoped_git_workspace", "ttl_seconds": 1800, "cacheable": True, "deduplicate_history": True},
    "ls": {"type": "scoped_git_workspace", "ttl_seconds": 1800, "cacheable": True, "deduplicate_history": True},
    "grep": {"type": "scoped_git_workspace", "ttl_seconds": 1800, "cacheable": True, "deduplicate_history": True},

    # 3. Global Git Status Tools: Invalidate when any repo status changes
    "git_status": {"type": "git_workspace", "ttl_seconds": 1800, "cacheable": True, "deduplicate_history": True},
    "git_diff": {"type": "git_workspace", "ttl_seconds": 1800, "cacheable": True, "deduplicate_history": True},
    "git_log": {"type": "git_workspace", "ttl_seconds": 1800, "cacheable": True, "deduplicate_history": True},

    # 4. Command Execution Tool
    "bash": {"type": "git_workspace", "ttl_seconds": 1800, "cacheable": True, "deduplicate_history": True},

    # 5. External Network tools: Short, tool-specific TTL (60s)
    "read_url_content": {"type": "external_network", "ttl_seconds": 60, "cacheable": True, "deduplicate_history": True},
    "fetch_web": {"type": "external_network", "ttl_seconds": 60, "cacheable": True, "deduplicate_history": True},

    # 6. Non-Cacheable Mutating Tools
    "run_command": {"type": "mutation", "ttl_seconds": 0, "cacheable": False, "deduplicate_history": False},
    "write_file": {"type": "mutation", "ttl_seconds": 0, "cacheable": False, "deduplicate_history": False},
    "replace_file_content": {"type": "mutation", "ttl_seconds": 0, "cacheable": False, "deduplicate_history": False},
}

TOOL_POLICIES: Dict[str, Dict[str, Any]] = dict(DEFAULT_BUILTIN_POLICIES)


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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_policies_records (
                tool_name TEXT PRIMARY KEY,
                policy_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        return conn
    except Exception:
        return None


class ToolPolicyManager:
    """
    Enterprise Policy Manager for Tool Caching, Staleness TTLs, and Mutation Protection.
    Supports in-memory caching backed by SQLite persistence (tool_policies_records).
    """
    def __init__(self):
        self._custom_policies: Dict[str, Dict[str, Any]] = {}
        self._load_from_db()

    def _load_from_db(self) -> None:
        conn = _get_tool_db_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            rows = cur.execute("SELECT tool_name, policy_json FROM tool_policies_records").fetchall()
            for t_name, p_json in rows:
                try:
                    self._custom_policies[t_name.strip().lower()] = json.loads(p_json)
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            conn.close()

    def get_policy(self, tool_name: str) -> Dict[str, Any]:
        """Returns effective policy for tool: custom override > built-in > prefix inference > custom fallback."""
        clean = (tool_name or "").strip().lower()
        if not clean:
            return {"type": "unknown", "ttl_seconds": 0, "cacheable": False, "deduplicate_history": False}

        # 1. Custom runtime override from SQLite/RAM
        if clean in self._custom_policies:
            return dict(self._custom_policies[clean])

        # 2. Built-in registry policy
        if clean in DEFAULT_BUILTIN_POLICIES:
            return dict(DEFAULT_BUILTIN_POLICIES[clean])

        # 3. Intelligent prefix inference
        parts = clean.replace("-", "_").replace(".", "_").split("_")
        first_word = parts[0] if parts else ""

        # Mutation prefix checks (e.g. process_payment, charge_card, delete_user)
        if first_word in DEFAULT_MUTATION_PREFIXES or any(clean.startswith(p) for p in DEFAULT_MUTATION_PREFIXES):
            return {
                "type": "mutation",
                "ttl_seconds": 0,
                "cacheable": False,
                "deduplicate_history": False
            }

        # Safe prefix checks (e.g. lookup_customer, query_inventory, check_order_status)
        if first_word in DEFAULT_SAFE_PREFIXES or any(clean.startswith(p) for p in DEFAULT_SAFE_PREFIXES):
            return {
                "type": "business_api",
                "ttl_seconds": 300,
                "cacheable": True,
                "deduplicate_history": True
            }

        # Secondary parts matching
        if any(p in parts for p in DEFAULT_MUTATION_PREFIXES):
            return {
                "type": "mutation",
                "ttl_seconds": 0,
                "cacheable": False,
                "deduplicate_history": False
            }
        if any(p in parts for p in DEFAULT_SAFE_PREFIXES):
            return {
                "type": "business_api",
                "ttl_seconds": 300,
                "cacheable": True,
                "deduplicate_history": True
            }

        # 4. Fallback for custom non-mutating tools
        return {
            "type": "custom",
            "ttl_seconds": 1800,
            "cacheable": True,
            "deduplicate_history": True
        }

    def set_policy(self, tool_name: str, policy: Dict[str, Any]) -> Dict[str, Any]:
        """Sets custom policy override, stores in RAM and SQLite."""
        clean = (tool_name or "").strip().lower()
        if not clean:
            raise ValueError("Tool name cannot be empty")

        cacheable = bool(policy.get("cacheable", True))
        ttl_seconds = max(0, int(policy.get("ttl_seconds", 300 if cacheable else 0)))
        dedup = bool(policy.get("deduplicate_history", cacheable))
        pol_type = str(policy.get("type", "business_api" if cacheable else "mutation"))

        normalized = {
            "type": pol_type,
            "ttl_seconds": ttl_seconds,
            "cacheable": cacheable,
            "deduplicate_history": dedup
        }

        self._custom_policies[clean] = normalized
        TOOL_POLICIES[clean] = normalized

        conn = _get_tool_db_conn()
        if conn:
            try:
                with conn:
                    conn.execute("""
                        INSERT OR REPLACE INTO tool_policies_records (tool_name, policy_json, updated_at)
                        VALUES (?, ?, ?)
                    """, (clean, json.dumps(normalized), time.time()))
            except Exception:
                pass
            finally:
                conn.close()

        return normalized

    def delete_policy(self, tool_name: str) -> bool:
        """Deletes custom policy override, reverting to built-in or inferred policy."""
        clean = (tool_name or "").strip().lower()
        existed = clean in self._custom_policies
        self._custom_policies.pop(clean, None)
        if clean in DEFAULT_BUILTIN_POLICIES:
            TOOL_POLICIES[clean] = dict(DEFAULT_BUILTIN_POLICIES[clean])
        else:
            TOOL_POLICIES.pop(clean, None)

        conn = _get_tool_db_conn()
        if conn:
            try:
                with conn:
                    conn.execute("DELETE FROM tool_policies_records WHERE tool_name = ?", (clean,))
            except Exception:
                pass
            finally:
                conn.close()

        return existed

    def list_policies(self) -> Dict[str, Dict[str, Any]]:
        """Returns all effective policies (defaults + custom overrides)."""
        self._load_from_db()
        merged = dict(DEFAULT_BUILTIN_POLICIES)
        merged.update(self._custom_policies)
        return merged

    def is_cacheable(self, tool_name: str) -> bool:
        return bool(self.get_policy(tool_name).get("cacheable", False))

    def should_deduplicate(self, tool_name: str) -> bool:
        return bool(self.get_policy(tool_name).get("deduplicate_history", False))

    def get_ttl(self, tool_name: str) -> int:
        return int(self.get_policy(tool_name).get("ttl_seconds", 300))

    def clear_custom_policies(self) -> None:
        """Clears custom policies from memory and DB."""
        self._custom_policies.clear()
        conn = _get_tool_db_conn()
        if conn:
            try:
                with conn:
                    conn.execute("DELETE FROM tool_policies_records")
            except Exception:
                pass
            finally:
                conn.close()
        TOOL_POLICIES.clear()
        TOOL_POLICIES.update(DEFAULT_BUILTIN_POLICIES)


# Global Tool Policy Manager singleton
tool_policy_manager = ToolPolicyManager()


class ToolExecutionCache:
    """In-memory cache with durable SQLite backing for deterministic agent tool execution outputs."""
    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.tool_hits = 0
        self.tool_misses = 0
        self.tokens_saved = 0

    @staticmethod
    def is_eligible(tool_name: str) -> bool:
        """Checks if a tool is eligible for tool-call caching based on active policy."""
        return tool_policy_manager.is_cacheable(tool_name)

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
        policy = tool_policy_manager.get_policy(clean_name)
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
        if not tool_policy_manager.is_cacheable(clean_name):
            return False, None, None

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
        policy = tool_policy_manager.get_policy(clean_name)
        if not policy.get("cacheable", False) and ttl_seconds is None:
            return ""

        effective_ttl = ttl_seconds if ttl_seconds is not None else policy.get("ttl_seconds", 1800)
        if effective_ttl <= 0:
            return ""

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


def compact_and_record_agent_tools(
    payload: Dict[str, Any],
    workspace_dir: Optional[str] = None
) -> Tuple[Dict[str, Any], int, int]:
    """
    In-Line Agent Tool Interceptor & Context Window Compactor.
    Works transparently on both Anthropic (/v1/messages) and OpenAI (/v1/chat/completions).
    
    1. Scans conversation messages for (tool_use, tool_result) execution pairs.
    2. Auto-records valid tool executions into durable tool_cache with workspace/git state.
    3. Identifies redundant/duplicate tool executions across historical turns (e.g. repeated
       read_file on unchanged files, repeated clean git_status) and compacts older occurrences,
       saving massive prompt tokens while keeping the latest execution intact.
    
    Returns (modified_payload, tokens_compacted, tools_recorded).
    """
    messages = payload.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        return payload, 0, 0

    workspace_dir = workspace_dir or os.getcwd()

    # Registry of tool calls: call_id -> { "name": tool_name, "input": tool_input, "format": "anthropic"|"openai" }
    tool_use_registry: Dict[str, Dict[str, Any]] = {}

    for msg_idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")

        # Anthropic format: role == "assistant", content is list with type == "tool_use"
        if role == "assistant" and isinstance(content, list):
            for block_idx, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    t_id = block.get("id")
                    if t_id:
                        tool_use_registry[t_id] = {
                            "name": block.get("name", ""),
                            "input": block.get("input", {}) or {},
                            "msg_idx": msg_idx,
                            "block_idx": block_idx,
                            "format": "anthropic"
                        }

        # OpenAI format: role == "assistant", tool_calls list
        tool_calls = msg.get("tool_calls")
        if role == "assistant" and isinstance(tool_calls, list):
            for tc_idx, tc in enumerate(tool_calls):
                if isinstance(tc, dict) and tc.get("id"):
                    fn = tc.get("function", {})
                    fn_name = fn.get("name", "")
                    fn_args_raw = fn.get("arguments", "{}")
                    try:
                        fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
                    except Exception:
                        fn_args = {"raw": fn_args_raw}
                    tool_use_registry[tc["id"]] = {
                        "name": fn_name,
                        "input": fn_args,
                        "msg_idx": msg_idx,
                        "tc_idx": tc_idx,
                        "format": "openai"
                    }

    if not tool_use_registry:
        return payload, 0, 0

    # Group executed results by canonical tool signature: f"{name}:{sorted_args_json}"
    tool_executions_by_sig: Dict[str, List[Dict[str, Any]]] = {}
    tools_recorded = 0

    for msg_idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")

        # Anthropic format: role == "user", content has type == "tool_result"
        if role == "user" and isinstance(content, list):
            for block_idx, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    t_id = block.get("tool_use_id")
                    if t_id and t_id in tool_use_registry:
                        meta = tool_use_registry[t_id]
                        t_name = meta["name"]
                        t_input = meta["input"]
                        is_error = bool(block.get("is_error", False))

                        b_content = block.get("content", "")
                        if isinstance(b_content, list):
                            text_parts = [b.get("text", "") for b in b_content if isinstance(b, dict) and b.get("type") == "text"]
                            output_str = "\n".join(text_parts)
                        else:
                            output_str = str(b_content or "")

                        if not is_error and output_str.strip() and tool_policy_manager.is_cacheable(t_name):
                            try:
                                stored_key = tool_cache.store_tool_call(
                                    tool_name=t_name,
                                    arguments=t_input if isinstance(t_input, dict) else {},
                                    output=output_str,
                                    workspace_dir=workspace_dir
                                )
                                if stored_key:
                                    tools_recorded += 1
                            except Exception:
                                pass

                        sig = f"{t_name.lower()}:{json.dumps(t_input, sort_keys=True)}"
                        if sig not in tool_executions_by_sig:
                            tool_executions_by_sig[sig] = []
                        tool_executions_by_sig[sig].append({
                            "tool_use_id": t_id,
                            "msg_idx": msg_idx,
                            "block_idx": block_idx,
                            "output_text": output_str,
                            "is_error": is_error,
                            "tool_name": t_name,
                            "format": "anthropic"
                        })

        # OpenAI format: role == "tool", tool_call_id
        if role == "tool":
            t_id = msg.get("tool_call_id")
            if t_id and t_id in tool_use_registry:
                meta = tool_use_registry[t_id]
                t_name = meta["name"]
                t_input = meta["input"]
                output_str = str(content or "")

                if output_str.strip() and tool_policy_manager.is_cacheable(t_name):
                    try:
                        stored_key = tool_cache.store_tool_call(
                            tool_name=t_name,
                            arguments=t_input if isinstance(t_input, dict) else {},
                            output=output_str,
                            workspace_dir=workspace_dir
                        )
                        if stored_key:
                            tools_recorded += 1
                    except Exception:
                        pass

                sig = f"{t_name.lower()}:{json.dumps(t_input, sort_keys=True)}"
                if sig not in tool_executions_by_sig:
                    tool_executions_by_sig[sig] = []
                tool_executions_by_sig[sig].append({
                    "tool_use_id": t_id,
                    "msg_idx": msg_idx,
                    "output_text": output_str,
                    "is_error": False,
                    "tool_name": t_name,
                    "format": "openai"
                })

    # Identify duplicate tool runs to compact
    tokens_compacted = 0
    anthropic_compacted: Dict[Tuple[int, int], str] = {}
    openai_compacted: Dict[int, str] = {}

    for sig, runs in tool_executions_by_sig.items():
        if len(runs) < 2:
            continue
        latest_run = runs[-1]
        if latest_run["is_error"]:
            continue

        latest_output = latest_run["output_text"]
        latest_id = latest_run["tool_use_id"]

        for earlier_run in runs[:-1]:
            if earlier_run["is_error"]:
                continue
            if not tool_policy_manager.should_deduplicate(earlier_run.get("tool_name", "")):
                continue
            earlier_output = earlier_run["output_text"]
            # Compact if output is identical or bulky (> 120 characters)
            if earlier_output == latest_output and len(earlier_output) > 120:
                orig_tokens = int(len(earlier_output.split()) * 1.3)
                pruned_note = (
                    f"[OmniCache: Output identical to subsequent execution ({latest_id}). "
                    f"Historical content pruned for prompt acceleration.]"
                )
                new_tokens = int(len(pruned_note.split()) * 1.3)
                saved = max(0, orig_tokens - new_tokens)
                tokens_compacted += saved

                if earlier_run["format"] == "anthropic":
                    anthropic_compacted[(earlier_run["msg_idx"], earlier_run["block_idx"])] = pruned_note
                else:
                    openai_compacted[earlier_run["msg_idx"]] = pruned_note

    if not anthropic_compacted and not openai_compacted:
        return payload, 0, tools_recorded

    # Build compacted messages
    new_messages = []
    for msg_idx, msg in enumerate(messages):
        msg_copy = dict(msg)
        if msg_idx in openai_compacted:
            msg_copy["content"] = openai_compacted[msg_idx]
        elif isinstance(msg.get("content"), list):
            new_content = []
            for block_idx, block in enumerate(msg["content"]):
                block_copy = dict(block)
                if (msg_idx, block_idx) in anthropic_compacted:
                    block_copy["content"] = anthropic_compacted[(msg_idx, block_idx)]
                new_content.append(block_copy)
            msg_copy["content"] = new_content
        new_messages.append(msg_copy)

    new_payload = dict(payload)
    new_payload["messages"] = new_messages
    return new_payload, tokens_compacted, tools_recorded

