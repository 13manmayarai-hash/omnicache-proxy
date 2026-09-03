"""
OmniCache AI Proxy - CLI Entry Point.
"""

import sys
import uvicorn
from core.config import config
from server.gateway import app

def main():
    print(f"🚀 Starting OmniCache AI Proxy v2.0.1 on http://{config.HOST}:{config.PORT}")
    print("⚡ Real-time semantic cache, Claude Code accelerator & proxy active.")
    uvicorn.run(app, host=config.HOST, port=config.PORT)

if __name__ == "__main__":
    main()
