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


def test_circuit_reset_and_error_recording(capsys):
    from server.failover import CircuitBreaker, failover_engine
    from server.cli import run_reset_circuit
    from starlette.testclient import TestClient
    from server.gateway import app

    cb = CircuitBreaker(failure_threshold=2, recovery_timeout_seconds=60.0)
    cb.record_failure("anthropic", status_code=429, error_message="Rate limit exceeded", model="claude-3-7-sonnet", endpoint="/v1/messages")
    cb.record_failure("anthropic", status_code=500, error_message="Overloaded", model="claude-3-7-sonnet", endpoint="/v1/messages")

    # Verify circuit is open and error is logged
    assert not cb.is_available("anthropic")
    recent = cb.get_recent_failures(5)
    assert len(recent) == 2
    assert recent[0]["status_code"] in (429, 500)
    assert recent[0]["provider"] == "anthropic"
    assert "claude" in recent[0]["model"]

    # Reset circuit
    cb.reset("anthropic")
    assert cb.is_available("anthropic")
    status = cb.get_status()
    assert status["anthropic"]["state"] == "closed"
    assert status["anthropic"]["consecutive_failures"] == 0

    # Test HTTP endpoint reset
    client = TestClient(app)
    resp = client.post("/v1/cache/circuit/reset", json={"provider": "anthropic"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["circuit_breaker"]["anthropic"]["state"] == "closed"

    # Test CLI reset
    run_reset_circuit(provider="anthropic")
    captured = capsys.readouterr()
    assert "reset" in captured.out.lower()

