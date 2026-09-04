"""
Real-World Option A: Claude Code / Coding Agent Simulation Script
Tests OmniCache with full multi-turn agent execution, SSE streaming jitter,
tool call recording & replay, and live disk modification invalidation.
"""

import os
import sys
import time
import json
import shutil
import tempfile
import subprocess
from starlette.testclient import TestClient

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from server.gateway import app
from core.vector_cache import cache_instance
from core.config import config

def log_step(title: str):
    print(f"\n\033[1;36m{'='*65}\n▶ {title}\n{'='*65}\033[0m")

def log_success(msg: str):
    print(f"\033[1;32m  ✔ {msg}\033[0m")

def log_info(msg: str):
    print(f"\033[0;33m  ℹ {msg}\033[0m")

def run_agent_simulation():
    log_step("STEP 1: Setting up Real-World Git Repository Workspace")
    
    workspace_dir = tempfile.mkdtemp(prefix="omnicache_agent_test_")
    print(f"  📁 Test Workspace: {workspace_dir}")
    
    try:
        # Initialize real git repository
        subprocess.run(["git", "init", workspace_dir], check=True, capture_output=True)
        subprocess.run(["git", "-C", workspace_dir, "config", "user.email", "agent@anthropic.com"], check=True)
        subprocess.run(["git", "-C", workspace_dir, "config", "user.name", "Claude Code Agent"], check=True)
        
        # Create initial files
        main_py = os.path.join(workspace_dir, "app.py")
        with open(main_py, "w") as f:
            f.write("def calculate_fibonacci(n):\n    if n <= 1: return n\n    return calculate_fibonacci(n-1) + calculate_fibonacci(n-2)\n")
        
        subprocess.run(["git", "-C", workspace_dir, "add", "app.py"], check=True)
        subprocess.run(["git", "-C", workspace_dir, "commit", "-m", "Initial commit: fibonacci function"], check=True)
        log_success("Git repository initialized with initial commit.")

        client = TestClient(app)

        # -------------------------------------------------------------
        # STEP 2: Agent Tool Execution & Replay
        # -------------------------------------------------------------
        log_step("STEP 2: Agent Executes Initial Tool Invocations")
        
        # Agent calls git_status
        status_payload = {
            "tool_name": "git_status",
            "arguments": {},
            "workspace_dir": workspace_dir,
            "workspace_fingerprint": workspace_dir
        }
        
        t0 = time.perf_counter()
        lookup1 = client.post("/v1/agent/tool_replay", json=status_payload)
        t_lookup1 = (time.perf_counter() - t0) * 1000
        assert lookup1.status_code == 200
        assert lookup1.json().get("status") == "MISS"
        log_info(f"git_status 1st lookup: MISS (latency: {t_lookup1:.2f}ms)")
        
        # Agent records tool execution result
        clean_status_output = "On branch main\nnothing to commit, working tree clean"
        record_res = client.post("/v1/agent/tool_record", json={
            **status_payload,
            "output": clean_status_output
        })
        assert record_res.status_code == 200
        assert record_res.json().get("status") == "STORED"
        log_success("git_status output successfully recorded in OmniCache.")

        # Agent reads app.py
        read_payload = {
            "tool_name": "read_file",
            "arguments": {"file": "app.py"},
            "workspace_dir": workspace_dir,
            "workspace_fingerprint": workspace_dir,
            "output": open(main_py).read()
        }
        client.post("/v1/agent/tool_record", json=read_payload)
        log_success("read_file(app.py) recorded in OmniCache.")

        # Agent repeats git_status in next reasoning turn
        t0 = time.perf_counter()
        lookup2 = client.post("/v1/agent/tool_replay", json=status_payload)
        t_lookup2 = (time.perf_counter() - t0) * 1000
        assert lookup2.status_code == 200
        assert lookup2.json().get("status") == "HIT"
        assert lookup2.json().get("output") == clean_status_output
        log_success(f"git_status 2nd lookup: HIT in {t_lookup2:.3f}ms! (Saved tool execution time)")

        # -------------------------------------------------------------
        # STEP 3: Multi-Turn Anthropic Streaming API Simulation
        # -------------------------------------------------------------
        log_step("STEP 3: Agent Multi-Turn Anthropic Messages SSE Stream Replay")
        
        messages_prompt = [
            {"role": "user", "content": "Analyze app.py and explain how to optimize calculate_fibonacci with memoization."}
        ]
        
        simulated_response_text = (
            "To optimize `calculate_fibonacci`, you can use dynamic programming or `@functools.lru_cache(None)`.\n\n"
            "```python\nfrom functools import lru_cache\n\n@lru_cache(maxsize=None)\ndef calculate_fibonacci(n):\n"
            "    if n <= 1: return n\n    return calculate_fibonacci(n-1) + calculate_fibonacci(n-2)\n```\n"
            "This reduces time complexity from O(2^N) exponential to O(N) linear time."
        )
        
        openai_payload = {
            "model": "claude-3-7-sonnet-20250219",
            "temperature": 0.0,
            "messages": messages_prompt
        }
        openai_response = {
            "id": "chatcmpl-simulated-1",
            "object": "chat.completion",
            "model": "claude-3-7-sonnet-20250219",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": simulated_response_text
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": 45,
                "completion_tokens": 92,
                "total_tokens": 137
            }
        }
        
        cache_instance.store(openai_payload, openai_response, org_id="default")
        log_success("Pre-cached prompt in vector cache engine.")

        # Stream request via POST /v1/messages
        t_start = time.perf_counter()
        stream_res = client.post(
            "/v1/messages",
            headers={"x-api-key": "test-mock-key", "anthropic-version": "2023-06-01"},
            json={
                "model": "claude-3-7-sonnet-20250219",
                "max_tokens": 1024,
                "stream": True,
                "messages": messages_prompt
            }
        )
        
        assert stream_res.status_code == 200
        assert stream_res.headers.get("x-omnicache-decision") == "HIT"
        assert stream_res.headers.get("x-cache-status") == "HIT_EXACT"
        tokens_saved = stream_res.headers.get("x-tokens-saved")
        cost_saved = stream_res.headers.get("x-cost-saved-usd")
        
        log_success(f"Stream Request Intercepted: HIT | Tokens Saved: {tokens_saved} | USD Saved: ${cost_saved}")

        # Parse SSE Event Stream and measure chunk timing
        chunks_received = 0
        reconstructed_text = ""
        first_chunk_time = None
        
        for line in stream_res.iter_lines():
            if not line:
                continue
            if line.startswith("data: "):
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                    if first_chunk_time is None:
                        first_chunk_time = (time.perf_counter() - t_start) * 1000
                    if event.get("type") == "content_block_delta":
                        delta_text = event.get("delta", {}).get("text", "")
                        reconstructed_text += delta_text
                        chunks_received += 1
                except Exception:
                    pass

        total_stream_time = (time.perf_counter() - t_start) * 1000
        log_success(f"Stream Replay Completed in {total_stream_time:.1f}ms across {chunks_received} SSE delta chunks.")
        log_info(f"TTFT (Time To First Token): {first_chunk_time:.2f}ms")
        print(f"\n\033[0;32m--- Streamed Response Text ---\n{reconstructed_text}\n------------------------------\033[0m")

        # -------------------------------------------------------------
        # STEP 4: Workspace Mutation & Invalidation Verification
        # -------------------------------------------------------------
        log_step("STEP 4: Developer / Agent Modifies Workspace Files")
        
        # Agent edits app.py
        with open(main_py, "w") as f:
            f.write("# Optimized Fibonacci with memoization\nfrom functools import lru_cache\n\n@lru_cache(None)\ndef calculate_fibonacci(n):\n    return n if n <= 1 else calculate_fibonacci(n-1) + calculate_fibonacci(n-2)\n")
        log_info("Modified app.py in workspace (created dirty working tree).")

        # Check git_status again -> MUST BE MISS!
        t0 = time.perf_counter()
        lookup_dirty = client.post("/v1/agent/tool_replay", json=status_payload)
        t_dirty = (time.perf_counter() - t0) * 1000
        assert lookup_dirty.status_code == 200
        assert lookup_dirty.json().get("status") == "MISS", "CRITICAL BUG: git_status returned stale HIT after file edit!"
        log_success(f"git_status post-modification correctly returned MISS in {t_dirty:.2f}ms!")

        # Check read_file(app.py) -> MUST BE MISS!
        lookup_read_dirty = client.post("/v1/agent/tool_replay", json={
            "tool_name": "read_file",
            "arguments": {"file": "app.py"},
            "workspace_dir": workspace_dir,
            "workspace_fingerprint": workspace_dir
        })
        assert lookup_read_dirty.status_code == 200
        assert lookup_read_dirty.json().get("status") == "MISS", "CRITICAL BUG: read_file returned stale HIT after file edit!"
        log_success("read_file(app.py) post-modification correctly returned MISS!")

        # -------------------------------------------------------------
        # STEP 5: Re-record & Git Commit Transition
        # -------------------------------------------------------------
        log_step("STEP 5: Re-recording Updated Tool Output & Committing")
        
        subprocess.run(["git", "-C", workspace_dir, "add", "app.py"], check=True)
        subprocess.run(["git", "-C", workspace_dir, "commit", "-m", "feat: add lru_cache optimization"], check=True)
        log_info("Committed changes to git repo (clean tree at new HEAD commit).")

        new_status_output = "On branch main\nnothing to commit, working tree clean"
        client.post("/v1/agent/tool_record", json={
            **status_payload,
            "output": new_status_output
        })

        # Lookup clean status at new HEAD -> HIT!
        lookup_committed = client.post("/v1/agent/tool_replay", json=status_payload)
        assert lookup_committed.status_code == 200
        assert lookup_committed.json().get("status") == "HIT"
        assert lookup_committed.json().get("output") == new_status_output
        log_success("git_status on new HEAD commit cleanly returns updated HIT!")

        print("\n\033[1;32m🎉 Option A: Claude Code / Agent Simulation PASSED WITH 100% SUCCESS!\033[0m\n")

    finally:
        shutil.rmtree(workspace_dir, ignore_errors=True)

if __name__ == "__main__":
    run_agent_simulation()
