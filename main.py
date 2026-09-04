"""
OmniCache AI Proxy Entry Point.
Runs the high-performance ASGI gateway on the configured host & port.
"""

import uvicorn
from core.config import config, validate_startup_security_invariants
from server.gateway import app

def start():
    validate_startup_security_invariants(config.HOST)
    print(f"🚀 Starting OmniCache AI Proxy on http://{config.HOST}:{config.PORT}")
    print("⚡ Endpoints active:")
    print("   - POST /v1/chat/completions  (OpenAI Drop-In Gateway)")
    print("   - POST /v1/cache/purge       (Tenant Cache Invalidation)")
    print("   - POST /v1/cache/invalidate-tag (Tag-Based Invalidation)")
    print("   - GET  /v1/cache/stats       (Real-Time Savings & Telemetry)")
    print("   - GET  /healthz              (Health Check)")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")

if __name__ == "__main__":
    start()
