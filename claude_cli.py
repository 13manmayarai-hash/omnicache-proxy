#!/usr/bin/env python3
"""
Interactive Claude Terminal Assistant powered by OmniCache AI Proxy.
Allows you to chat with Claude directly in your terminal with instant caching.
"""

import sys
import os
import json
import time
import urllib.request
import urllib.error

OMNICACHE_URL = os.environ.get("OMNICACHE_URL", "http://localhost:8000/v1/messages")
DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
DEFAULT_MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "4096"))

def query_omnicache(prompt: str, model: str = DEFAULT_MODEL, org_id: str = "terminal_user", max_tokens: int = DEFAULT_MAX_TOKENS):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens
    }
    data = json.dumps(payload).encode("utf-8")
    
    headers = {
        "Content-Type": "application/json",
        "x-org-id": org_id
    }
    
    # Forward API key if provided in environment
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OMNICACHE_API_KEY")
    if api_key:
        headers["x-api-key"] = api_key
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(
        OMNICACHE_URL,
        data=data,
        headers=headers,
        method="POST"
    )

    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            dt = (time.perf_counter() - t0) * 1000
            cache_status = resp.headers.get("X-Cache-Status", "MISS")
            sim = resp.headers.get("X-Cache-Similarity", "0.0000")
            tokens_saved = resp.headers.get("X-Tokens-Saved", "0")
            cost_saved = resp.headers.get("X-Cost-Saved-USD", "0.000000")
            model_served = resp.headers.get("X-Served-Model", model)
            
            res_body = json.loads(resp.read().decode("utf-8"))
            content = ""
            for block in res_body.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    content += block.get("text", "")
                elif isinstance(block, str):
                    content += block
                    
            if not content and "text" in res_body:
                content = res_body["text"]

            return {
                "content": content,
                "status": cache_status,
                "similarity": sim,
                "latency_ms": dt,
                "tokens_saved": tokens_saved,
                "cost_saved": cost_saved,
                "model": model_served
            }
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")
        except Exception:
            pass
        return {"error": f"HTTP {e.code} ({e.reason}): {error_body or 'No detail returned'}"}
    except urllib.error.URLError as e:
        return {"error": f"Cannot connect to OmniCache on {OMNICACHE_URL} ({e.reason})"}
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}

def main():
    print("═" * 65)
    print("🤖 Claude Terminal Assistant (Powered by OmniCache Proxy)")
    print(f"📡 Target: {OMNICACHE_URL} | Model: {DEFAULT_MODEL}")
    print("═" * 65)
    print("Type your coding question or prompt below.")
    print("Type 'exit' or 'quit' to close.\n")

    # If prompt passed via CLI argument: e.g. python3 claude_cli.py "explain quicksort"
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
        print(f"You: {prompt}")
        res = query_omnicache(prompt)
        if "error" in res:
            print(f"❌ Error: {res['error']}")
            return
        
        status_icon = "🟢" if "HIT" in res["status"] else "🟠"
        print(f"\n{status_icon} [Cache: {res['status']}] [⚡ {res['latency_ms']:.2f}ms] [💰 Tokens Saved: {res['tokens_saved']}] [Sim: {res['similarity']}] [Model: {res.get('model', DEFAULT_MODEL)}]")
        print("─" * 65)
        print(res["content"])
        print("═" * 65)
        return

    # Interactive Loop
    while True:
        try:
            prompt = input("\n💬 You > ").strip()
            if not prompt:
                continue
            if prompt.lower() in ("exit", "quit", "q"):
                print("👋 Bye!")
                break

            res = query_omnicache(prompt)
            if "error" in res:
                print(f"❌ Error: {res['error']}")
                continue

            status_icon = "🟢" if "HIT" in res["status"] else "🟠"
            print(f"\n{status_icon} [Cache: {res['status']}] [⚡ {res['latency_ms']:.2f}ms] [💰 Tokens Saved: {res['tokens_saved']}] [Sim: {res['similarity']}] [Model: {res.get('model', DEFAULT_MODEL)}]")
            print("─" * 65)
            print(res["content"])
            print("─" * 65)

        except (KeyboardInterrupt, EOFError):
            print("\n👋 Bye!")
            break

if __name__ == "__main__":
    main()
