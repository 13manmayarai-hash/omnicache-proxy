"""
Tests for OmniCache Turnkey CI/CD Workflow, Health Probe, and Markdown Step Summary.
"""

import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from server.cli import run_health, run_ci_summary


def test_run_health_unreachable():
    """Verifies that run_health reports failure and returns False when daemon is not listening."""
    # Using an unlikely port
    result = run_health(host="127.0.0.1", port=59999)
    assert result is False


def test_run_health_mocked_success():
    """Verifies that run_health reports success and returns True when daemon responds 200."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b'{"status": "healthy", "version": "2.9.5"}'
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = run_health(host="127.0.0.1", port=8000)
        assert result is True


def test_run_ci_summary_markdown_generation():
    """Verifies run_ci_summary outputs valid GitHub Flavored Markdown with required metrics."""
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        md = run_ci_summary(output_path=tmp_path)
        assert "### ⚡ OmniCache AI Acceleration & Cost Savings Report" in md
        assert "| **Total Cost Avoided** |" in md
        assert "| **Remote Tokens Avoided** |" in md
        assert "| **Cache Hit Rate** |" in md
        assert "| **Agent Tool Replays** |" in md
        assert "| **Context Tokens Pruned** |" in md

        with open(tmp_path, "r", encoding="utf-8") as f:
            disk_content = f.read()
        assert disk_content == md
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_run_ci_summary_github_step_summary_env(monkeypatch):
    """Verifies that run_ci_summary appends to $GITHUB_STEP_SUMMARY if present."""
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tmp:
        summary_path = tmp.name

    try:
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", summary_path)
        md = run_ci_summary()
        with open(summary_path, "r", encoding="utf-8") as f:
            written = f.read()
        assert "### ⚡ OmniCache AI Acceleration & Cost Savings Report" in written
        assert "| **Total Cost Avoided** |" in written
    finally:
        if os.path.exists(summary_path):
            os.remove(summary_path)
