"""
Multi-Agent Workspace Team Sync & CI/CD Cache Warming Engine.
Pre-warms Git repositories, directory structures, and file reads for AI coding agents
(Claude Code, Cursor, Devin) and synchronizes tool caches across distributed teams and CI/CD pipelines.
"""

import os
import sys
import json
import time
import gzip
import hashlib
import subprocess
from typing import Dict, Any, Optional, List, Tuple, Union

from core.config import config
from server.tool_replayer import (
    tool_cache,
    tool_policy_manager,
    _get_tool_db_conn,
    get_git_workspace_state,
    get_file_fingerprint
)

# Standard directories to skip during workspace repository scanning
DEFAULT_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build",
    ".egg-info", ".omnicache", ".gemini", ".cursor", ".idea", ".vscode",
    "target", "vendor", "bin", "obj"
}

# Binary / media file extensions to skip during file content warming
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".pyc", ".whl",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".db", ".sqlite", ".sqlite3"
}


class WorkspaceWarmer:
    """
    Scans a project workspace and pre-records idempotent tool executions
    (file contents, directory trees, git status, git log) into OmniCache.
    """

    @staticmethod
    def warm_workspace(
        workspace_dir: Optional[str] = None,
        workspace_fingerprint: str = "default",
        ref: str = "HEAD",
        max_files: int = 200,
        max_file_size_kb: int = 500,
        file_extensions: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        start_time = time.perf_counter()
        target_dir = os.path.abspath(workspace_dir or os.getcwd())
        if not os.path.exists(target_dir):
            raise FileNotFoundError(f"Workspace directory does not exist: {target_dir}")

        files_warmed = 0
        dirs_warmed = 0
        tools_recorded = 0
        tokens_warmed = 0

        # 1. Probe Git state if target_dir is a Git repository
        git_commit = None
        git_branch = None
        git_status_str = ""
        is_git_repo = False

        try:
            chk = subprocess.run(
                ["git", "-C", target_dir, "rev-parse", "--is-inside-work-tree"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2.0
            )
            if chk.returncode == 0 and chk.stdout.strip() == "true":
                is_git_repo = True
                # Get commit SHA
                c_res = subprocess.run(
                    ["git", "-C", target_dir, "rev-parse", ref],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2.0
                )
                if c_res.returncode == 0:
                    git_commit = c_res.stdout.strip()

                # Get branch name
                b_res = subprocess.run(
                    ["git", "-C", target_dir, "rev-parse", "--abbrev-ref", "HEAD"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2.0
                )
                if b_res.returncode == 0:
                    git_branch = b_res.stdout.strip()

                # Get porcelain git status
                s_res = subprocess.run(
                    ["git", "-C", target_dir, "status"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2.0
                )
                if s_res.returncode == 0:
                    git_status_str = s_res.stdout

                # Get git log
                l_res = subprocess.run(
                    ["git", "-C", target_dir, "log", "-n", "10", "--oneline"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2.0
                )
                git_log_str = l_res.stdout if l_res.returncode == 0 else ""

                # Get git diff
                d_res = subprocess.run(
                    ["git", "-C", target_dir, "diff"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2.0
                )
                git_diff_str = d_res.stdout if d_res.returncode == 0 else ""

                # Record git status tool variants
                for s_args in [{}, {"workspace_dir": target_dir}, {"cwd": target_dir}]:
                    k1 = tool_cache.store_tool_call(
                        tool_name="git_status",
                        arguments=s_args,
                        output=git_status_str,
                        workspace_fingerprint=workspace_fingerprint,
                        workspace_dir=target_dir
                    )
                    if k1:
                        tools_recorded += 1

                # Record bash 'git status'
                k_bash_status = tool_cache.store_tool_call(
                    tool_name="bash",
                    arguments={"command": "git status"},
                    output=git_status_str,
                    workspace_fingerprint=workspace_fingerprint,
                    workspace_dir=target_dir
                )
                if k_bash_status:
                    tools_recorded += 1

                # Record git log tool variants
                for l_args in [{}, {"n": 10}, {"max_count": 10}]:
                    k_log = tool_cache.store_tool_call(
                        tool_name="git_log",
                        arguments=l_args,
                        output=git_log_str,
                        workspace_fingerprint=workspace_fingerprint,
                        workspace_dir=target_dir
                    )
                    if k_log:
                        tools_recorded += 1

                # Record git diff
                k_diff = tool_cache.store_tool_call(
                    tool_name="git_diff",
                    arguments={},
                    output=git_diff_str,
                    workspace_fingerprint=workspace_fingerprint,
                    workspace_dir=target_dir
                )
                if k_diff:
                    tools_recorded += 1

        except Exception:
            pass

        # 2. Walk directory tree and pre-record files and listings
        max_bytes = max_file_size_kb * 1024
        allowed_exts = set(ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in file_extensions) if file_extensions else None

        for root, dirs, files in os.walk(target_dir):
            # Prune ignored directories
            dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORE_DIRS and not d.startswith(".")]

            rel_root = os.path.relpath(root, target_dir)
            if rel_root == ".":
                rel_root_str = ""
            else:
                rel_root_str = rel_root

            # Pre-record directory listing
            dir_entries = sorted(dirs + files)
            listing_output = "\n".join(dir_entries)

            # Record list_dir and ls variants
            list_args_candidates = [
                {"DirectoryPath": root},
                {"DirectoryPath": rel_root_str or "."},
                {"path": rel_root_str or "."},
                {"dir": rel_root_str or "."},
            ]
            if rel_root_str == "":
                list_args_candidates.append({})

            for l_args in list_args_candidates:
                k = tool_cache.store_tool_call(
                    tool_name="list_dir",
                    arguments=l_args,
                    output=listing_output,
                    workspace_fingerprint=workspace_fingerprint,
                    workspace_dir=target_dir
                )
                if k:
                    tools_recorded += 1

            k_ls = tool_cache.store_tool_call(
                tool_name="ls",
                arguments={"path": rel_root_str or "."},
                output=listing_output,
                workspace_fingerprint=workspace_fingerprint,
                workspace_dir=target_dir
            )
            if k_ls:
                tools_recorded += 1
            dirs_warmed += 1

            # Pre-record file contents
            for filename in sorted(files):
                if files_warmed >= max_files:
                    break

                abs_file = os.path.join(root, filename)
                rel_file = os.path.relpath(abs_file, target_dir)

                _, ext = os.path.splitext(filename)
                ext_clean = ext.lower()

                if ext_clean in BINARY_EXTENSIONS:
                    continue
                if allowed_exts and ext_clean not in allowed_exts:
                    continue

                try:
                    st = os.stat(abs_file)
                    if st.st_size > max_bytes:
                        continue

                    with open(abs_file, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()

                    # Pre-record read_file, view_file, cat
                    read_variants = [
                        {"file_path": rel_file},
                        {"file_path": abs_file},
                        {"path": rel_file},
                        {"path": abs_file}
                    ]
                    for r_arg in read_variants:
                        k_read = tool_cache.store_tool_call(
                            tool_name="read_file",
                            arguments=r_arg,
                            output=content,
                            workspace_fingerprint=workspace_fingerprint,
                            workspace_dir=target_dir
                        )
                        if k_read:
                            tools_recorded += 1

                    k_view = tool_cache.store_tool_call(
                        tool_name="view_file",
                        arguments={"AbsolutePath": abs_file},
                        output=content,
                        workspace_fingerprint=workspace_fingerprint,
                        workspace_dir=target_dir
                    )
                    if k_view:
                        tools_recorded += 1

                    k_cat = tool_cache.store_tool_call(
                        tool_name="cat",
                        arguments={"file": rel_file},
                        output=content,
                        workspace_fingerprint=workspace_fingerprint,
                        workspace_dir=target_dir
                    )
                    if k_cat:
                        tools_recorded += 1

                    files_warmed += 1
                    tokens_warmed += int(len(content.split()) * 1.3) + 10

                except Exception:
                    continue

            if files_warmed >= max_files:
                break

        elapsed = time.perf_counter() - start_time
        return {
            "status": "WARMED",
            "workspace_dir": target_dir,
            "workspace_fingerprint": workspace_fingerprint,
            "is_git_repo": is_git_repo,
            "git_commit": git_commit,
            "git_branch": git_branch,
            "files_warmed": files_warmed,
            "dirs_warmed": dirs_warmed,
            "tools_recorded": tools_recorded,
            "tokens_warmed": tokens_warmed,
            "duration_ms": round(elapsed * 1000, 2)
        }


class WorkspaceSyncManager:
    """
    Exports and imports portable workspace tool execution bundles across
    multi-agent teams, distributed worker nodes, and CI/CD pipelines.
    """

    @staticmethod
    def export_snapshot(
        workspace_dir: Optional[str] = None,
        workspace_fingerprint: Optional[str] = None,
        output_path: Optional[str] = None
    ) -> Dict[str, Any]:
        target_dir = os.path.abspath(workspace_dir) if workspace_dir else None
        conn = _get_tool_db_conn()
        if not conn:
            raise RuntimeError("Database connection unavailable for workspace export")

        records: List[Dict[str, Any]] = []
        now = time.time()
        try:
            cur = conn.cursor()
            query = """
                SELECT key, tool_name, arguments_json, output,
                       workspace_fingerprint, workspace_state,
                       estimated_tokens, stored_at, expires_at
                FROM tool_call_records
                WHERE expires_at > ?
            """
            params: List[Any] = [now]
            if workspace_fingerprint:
                query += " AND workspace_fingerprint = ?"
                params.append(workspace_fingerprint)

            rows = cur.execute(query, tuple(params)).fetchall()
            for r in rows:
                key, t_name, args_j, out_val, ws_fp, ws_st, est_tok, st_at, exp_at = r
                records.append({
                    "key": key,
                    "tool_name": t_name,
                    "arguments_json": args_j,
                    "output": out_val,
                    "workspace_fingerprint": ws_fp,
                    "workspace_state": ws_st,
                    "estimated_tokens": est_tok,
                    "stored_at": st_at,
                    "expires_at": exp_at
                })
        finally:
            conn.close()

        custom_policies = tool_policy_manager._custom_policies

        bundle = {
            "format": "omnicache_workspace_sync_v1",
            "version": config.VERSION,
            "exported_at": now,
            "workspace_dir": target_dir,
            "workspace_fingerprint": workspace_fingerprint or "default",
            "record_count": len(records),
            "tool_records": records,
            "custom_policies": custom_policies
        }

        if output_path:
            out_file = os.path.abspath(output_path)
            os.makedirs(os.path.dirname(out_file), exist_ok=True)
            bundle_str = json.dumps(bundle, indent=2)
            if out_file.endswith(".gz") or out_file.endswith(".tar.gz"):
                with gzip.open(out_file, "wt", encoding="utf-8") as f:
                    f.write(bundle_str)
            else:
                with open(out_file, "w", encoding="utf-8") as f:
                    f.write(bundle_str)
            bundle["saved_to"] = out_file

        return bundle

    @staticmethod
    def import_snapshot(
        bundle_or_path: Union[Dict[str, Any], str],
        overwrite: bool = True
    ) -> Dict[str, Any]:
        if isinstance(bundle_or_path, str):
            in_file = os.path.abspath(bundle_or_path)
            if not os.path.exists(in_file):
                raise FileNotFoundError(f"Snapshot file not found: {in_file}")

            if in_file.endswith(".gz") or in_file.endswith(".tar.gz"):
                with gzip.open(in_file, "rt", encoding="utf-8") as f:
                    bundle = json.load(f)
            else:
                with open(in_file, "r", encoding="utf-8") as f:
                    bundle = json.load(f)
        elif isinstance(bundle_or_path, dict):
            bundle = bundle_or_path
        else:
            raise ValueError("Expected dict bundle or file path")

        if not isinstance(bundle, dict) or not bundle.get("format", "").startswith("omnicache_workspace_sync"):
            raise ValueError("Invalid snapshot bundle format")

        records = bundle.get("tool_records", [])
        custom_policies = bundle.get("custom_policies", {})

        records_imported = 0
        policies_imported = 0
        now = time.time()

        # Import tool policies first
        if isinstance(custom_policies, dict):
            for t_name, pol_data in custom_policies.items():
                if isinstance(pol_data, dict):
                    tool_policy_manager.set_policy(t_name, pol_data)
                    policies_imported += 1

        # Import tool records into SQLite & hot memory
        conn = _get_tool_db_conn()
        if conn:
            try:
                with conn:
                    for rec in records:
                        exp_at = rec.get("expires_at", 0)
                        if exp_at <= now:
                            continue  # Skip already expired records

                        key = rec.get("key")
                        t_name = rec.get("tool_name", "")
                        args_j = rec.get("arguments_json", "{}")
                        out_val = rec.get("output", "")
                        ws_fp = rec.get("workspace_fingerprint", "default")
                        ws_st = rec.get("workspace_state")
                        est_tok = rec.get("estimated_tokens", 50)
                        st_at = rec.get("stored_at", now)

                        conn.execute("""
                            INSERT OR REPLACE INTO tool_call_records (
                                key, tool_name, arguments_json, output,
                                workspace_fingerprint, workspace_state,
                                estimated_tokens, stored_at, expires_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (key, t_name, args_j, out_val, ws_fp, ws_st, est_tok, st_at, exp_at))

                        # Populate hot cache RAM
                        tool_cache._cache[key] = {
                            "tool_name": t_name,
                            "output": out_val,
                            "estimated_tokens": est_tok,
                            "stored_at": st_at,
                            "expires_at": exp_at,
                            "workspace_state": ws_st
                        }
                        records_imported += 1
            finally:
                conn.close()

        return {
            "status": "IMPORTED",
            "records_imported": records_imported,
            "policies_imported": policies_imported,
            "workspace_fingerprint": bundle.get("workspace_fingerprint"),
            "workspace_dir": bundle.get("workspace_dir")
        }

    @staticmethod
    def get_sync_status(
        workspace_dir: Optional[str] = None,
        workspace_fingerprint: Optional[str] = None
    ) -> Dict[str, Any]:
        conn = _get_tool_db_conn()
        if not conn:
            return {"status": "ERROR", "error": "Database unavailable"}

        now = time.time()
        tool_counts: Dict[str, int] = {}
        total_records = 0
        total_tokens_potential = 0

        try:
            cur = conn.cursor()
            query = """
                SELECT tool_name, estimated_tokens, expires_at
                FROM tool_call_records
                WHERE expires_at > ?
            """
            params: List[Any] = [now]
            if workspace_fingerprint:
                query += " AND workspace_fingerprint = ?"
                params.append(workspace_fingerprint)

            rows = cur.execute(query, tuple(params)).fetchall()
            for t_name, est_tok, exp_at in rows:
                total_records += 1
                total_tokens_potential += (est_tok or 50)
                tool_counts[t_name] = tool_counts.get(t_name, 0) + 1
        finally:
            conn.close()

        return {
            "status": "OK",
            "workspace_fingerprint": workspace_fingerprint or "all",
            "active_tool_records": total_records,
            "saved_tokens_potential": total_tokens_potential,
            "tool_distribution": tool_counts,
            "custom_policies_active": len(tool_policy_manager._custom_policies)
        }

    @staticmethod
    def sync_redis_push(
        workspace_fingerprint: str = "default",
        redis_url: Optional[str] = None
    ) -> Dict[str, Any]:
        bundle = WorkspaceSyncManager.export_snapshot(workspace_fingerprint=workspace_fingerprint)
        r_url = redis_url or os.getenv("REDIS_URL", getattr(config, "REDIS_URL", "redis://localhost:6379/0"))
        try:
            import redis
            client = redis.Redis.from_url(r_url, decode_responses=True, socket_timeout=3.0)
            redis_key = f"omnicache:workspace:sync:{workspace_fingerprint}"
            payload = json.dumps(bundle)
            client.setex(redis_key, 604800, payload)
            return {
                "status": "PUSHED_TO_REDIS",
                "redis_key": redis_key,
                "records_pushed": bundle["record_count"],
                "workspace_fingerprint": workspace_fingerprint
            }
        except Exception as e:
            return {
                "status": "REDIS_ERROR",
                "error": str(e),
                "workspace_fingerprint": workspace_fingerprint
            }

    @staticmethod
    def sync_redis_pull(
        workspace_fingerprint: str = "default",
        redis_url: Optional[str] = None
    ) -> Dict[str, Any]:
        r_url = redis_url or os.getenv("REDIS_URL", getattr(config, "REDIS_URL", "redis://localhost:6379/0"))
        try:
            import redis
            client = redis.Redis.from_url(r_url, decode_responses=True, socket_timeout=3.0)
            redis_key = f"omnicache:workspace:sync:{workspace_fingerprint}"
            raw = client.get(redis_key)
            if not raw:
                return {
                    "status": "NOT_FOUND_IN_REDIS",
                    "redis_key": redis_key,
                    "workspace_fingerprint": workspace_fingerprint
                }
            bundle = json.loads(raw)
            result = WorkspaceSyncManager.import_snapshot(bundle)
            result["source"] = "REDIS"
            result["redis_key"] = redis_key
            return result
        except Exception as e:
            return {
                "status": "REDIS_ERROR",
                "error": str(e),
                "workspace_fingerprint": workspace_fingerprint
            }


# Module Singletons
workspace_warmer = WorkspaceWarmer()
workspace_sync_manager = WorkspaceSyncManager()
