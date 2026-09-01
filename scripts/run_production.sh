#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

echo "🚀 Launching OmniCache AI Proxy in Production Mode..."

# Load environment variables if .env exists
if [ -f .env ]; then
  export $(cat .env | grep -v '#' | xargs)
fi

exec python3 main.py
