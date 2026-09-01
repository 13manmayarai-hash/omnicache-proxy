#!/usr/bin/env python3
"""
Interactive Claude Terminal Assistant powered by OmniCache AI Proxy.
Allows you to chat with Claude directly in your terminal with instant caching.
"""

import sys
import json
import time
import urllib.request
import urllib.error

OMNICACHE_URL = "http://localhost:8000/v1/messages"

def query_omnicache(prompt: str, model: str = "claude-3-5-sonnet-20241022", org_id: str = "terminal_user"):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1024
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OMNICACHE_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-org-id": org_id
        },
        method="POST"
    )

    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            dt = (time.perf_counter() - t0) * 1000
            cache_status = resp.headers.get("X-Cache-Status", "MISS")
            sim = resp.headers.get("X-Cache-Similarity", "0.0000")
            tokens_saved = resp.headers.get("X-Tokens-Saved", "0")
            cost_saved = resp.headers.get("X-Cost-Saved-USD", "0.000000")
            
            res_body = json.loads(resp.read().decode("utf-8"))
            content = res_body.get("content", [{}])[0].get("text", "")
            return {
                "content": content,
                "status": cache_status,
                "similarity": sim,
                "latency_ms": dt,
                "tokens_saved": tokens_saved,
                "cost_saved": cost_saved
            }
    except urllib.error.URLError as e:
        return {"error": f"Cannot connect to OmniCache on localhost:8000 ({e})"}

def main():
    print("═" * 65)
    print("🤖 Claude Terminal Assistant (Powered by OmniCache Proxy)")
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
        print(f"\n{status_icon} [Cache: {res['status']}] [⚡ {res['latency_ms']:.2f}ms] [💰 Tokens Saved: {res['tokens_saved']}] [Sim: {res['similarity']}]")
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
            print(f"\n{status_icon} [Cache: {res['status']}] [⚡ {res['latency_ms']:.2f}ms] [💰 Tokens Saved: {res['tokens_saved']}] [Sim: {res['similarity']}]")
            print("─" * 65)
            print(res["content"])
            print("─" * 65)

        except (KeyboardInterrupt, EOFError):
            print("\n👋 Bye!")
            break

if __name__ == "__main__":
    main()
