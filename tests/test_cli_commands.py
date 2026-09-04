"""
CLI Commands Test Suite for OmniCache.
Verifies `omnicache init`, `omnicache doctor`, and `omnicache stats` CLI commands.
"""

import os
import json
import pytest
from server.cli import run_init, run_doctor, run_stats

def test_run_init_generates_configurations(tmp_path, monkeypatch):
    test_home = str(tmp_path / "home")
    test_proj = str(tmp_path / "project")
    os.makedirs(test_home, exist_ok=True)
    os.makedirs(test_proj, exist_ok=True)

    monkeypatch.setenv("HOME", test_home)
    monkeypatch.setattr(os.path, "expanduser", lambda path: path.replace("~", test_home))
    monkeypatch.setattr(os, "getcwd", lambda: test_proj)

    # Run init
    run_init()

    claude_json = os.path.join(test_home, ".claude.json")
    claude_settings = os.path.join(test_home, ".claude", "settings.json")
    cursor_json = os.path.join(test_home, ".cursor", "mcp.json")
    env_sh = os.path.join(test_home, ".omnicache", "env.sh")

    assert os.path.exists(claude_json)
    assert os.path.exists(claude_settings)
    assert os.path.exists(cursor_json)
    assert os.path.exists(env_sh)

    with open(claude_json, "r") as f:
        data = json.load(f)
        assert "omnicache" in data.get("mcpServers", {})

    with open(env_sh, "r") as f:
        content = f.read()
        assert "ANTHROPIC_BASE_URL" in content
        assert "OPENAI_BASE_URL" in content


def test_run_doctor_and_stats():
    # Test doctor runs without throwing
    run_doctor()
    # Test stats runs without throwing
    run_stats()
