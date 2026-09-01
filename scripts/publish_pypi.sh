#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

echo "📦 Building OmniCache distribution package..."
python3 -m pip install --upgrade build twine
python3 -m build

echo "🚀 To upload to PyPI, run:"
echo "   twine upload dist/*"
