"""
OmniCache AI Proxy - Silent CLI Entry Point.
"""

import sys
import uvicorn
from core.config import config
from server.gateway import app

def main():
    print(f"🚀 Starting OmniCache AI Proxy on http://{config.HOST}:{config.PORT}")
    print("⚡ Real-time semantic cache active. (Silent logging enabled)")
    # Disable noisy HTTP access logs so they don't interrupt terminal chats
    uvicorn.run(app, host=config.HOST, port=config.PORT, access_log=False, log_level="warning")

if __name__ == "__main__":
    main()
