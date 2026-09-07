"""
Tests for OmniCache Live Agent Integration Harness and Agent Profiles.
Verifies compatibility and automated setup for Claude Code, Cursor, Cline, and OpenHands.
"""

import os
import json
import pytest
from server.agent_harness import AgentHarness
from server.cli import run_init

def test_01_agent_harness_full_scorecard():
    """Verifies all 8 checks in the Agent Integration Harness pass."""
    success = AgentHarness.run()
    assert success is True


def test_02_run_init_show_only(capsys):
    """Verifies `omnicache init --show` outputs accurate presets for each agent."""
    # Test all
    run_init(agent="all", show_only=True)
    out_all = capsys.readouterr().out
    assert "Claude Code" in out_all
    assert "Cursor IDE" in out_all
    assert "Cline" in out_all
    assert "OpenHands" in out_all
    assert "ANTHROPIC_BASE_URL" in out_all
    assert "LLM_BASE_URL" in out_all

    # Test openhands only
    run_init(agent="openhands", show_only=True)
    out_oh = capsys.readouterr().out
    assert "OpenHands" in out_oh
    assert "config.toml" in out_oh
    assert "[llm]" in out_oh
    assert "Claude Code" not in out_oh

    # Test cline only
    run_init(agent="cline", show_only=True)
    out_cl = capsys.readouterr().out
    assert "Cline" in out_cl
    assert "cline_mcp_settings.json" in out_cl


def test_03_run_init_file_generation(tmp_path, monkeypatch):
    """Verifies `omnicache init` writes configuration files for all agents."""
    test_home = str(tmp_path / "home")
    test_proj = str(tmp_path / "project")
    os.makedirs(test_home, exist_ok=True)
    os.makedirs(test_proj, exist_ok=True)

    monkeypatch.setenv("HOME", test_home)
    monkeypatch.setattr(os.path, "expanduser", lambda path: path.replace("~", test_home))
    monkeypatch.setattr(os, "getcwd", lambda: test_proj)

    run_init(agent="all", show_only=False)

    # Check Claude Code configs
    assert os.path.exists(os.path.join(test_home, ".claude.json"))
    assert os.path.exists(os.path.join(test_home, ".claude", "settings.json"))

    # Check Cursor config
    assert os.path.exists(os.path.join(test_home, ".cursor", "mcp.json"))
    assert os.path.exists(os.path.join(test_proj, ".cursor", "mcp.json"))

    # Check Cline config
    assert os.path.exists(os.path.join(test_proj, ".vscode", "cline_mcp_settings.json"))
    with open(os.path.join(test_proj, ".vscode", "cline_mcp_settings.json"), "r") as f:
        cline_data = json.load(f)
        assert "omnicache" in cline_data.get("mcpServers", {})

    # Check OpenHands config
    assert os.path.exists(os.path.join(test_proj, "config.toml"))
    with open(os.path.join(test_proj, "config.toml"), "r") as f:
        oh_content = f.read()
        assert "[llm]" in oh_content
        assert "base_url" in oh_content

    # Check Shell helper
    assert os.path.exists(os.path.join(test_home, ".omnicache", "env.sh"))
    with open(os.path.join(test_home, ".omnicache", "env.sh"), "r") as f:
        env_content = f.read()
        assert "LLM_BASE_URL" in env_content
        assert "OMNICACHE_ACCELERATED" in env_content
