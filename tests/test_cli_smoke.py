"""
CI / Release Smoke Gate Tests.
Ensures CLI entrypoints, type annotations, doctor diagnostics, and benchmark commands
launch and execute without missing imports or runtime crashes across all Python versions.
"""

import sys
import subprocess
import typing
import pytest
import server.cli


def test_cli_type_annotations_valid_across_python_versions():
    """
    Prevents regression of missing typing imports (e.g. Optional, List, Dict).
    Eagerly evaluates annotations on all functions in server.cli.
    """
    for attr_name in dir(server.cli):
        obj = getattr(server.cli, attr_name)
        if callable(obj) and getattr(obj, "__module__", "") == "server.cli":
            try:
                typing.get_type_hints(obj)
            except Exception as e:
                pytest.fail(f"Type annotation resolution failed on server.cli.{attr_name}: {e}")


def test_cli_help_subprocess():
    """Verifies that omnicache CLI launches without crash."""
    res = subprocess.run(
        [sys.executable, "-m", "server.cli", "--help"],
        capture_output=True,
        text=True,
        timeout=10
    )
    assert res.returncode == 0
    assert "omnicache" in res.stdout


def test_cli_version_subprocess():
    """Verifies that omnicache version command succeeds."""
    res = subprocess.run(
        [sys.executable, "-m", "server.cli", "version"],
        capture_output=True,
        text=True,
        timeout=10
    )
    assert res.returncode == 0
    assert "OmniCache" in res.stdout


def test_doctor_execution():
    """Verifies that run_doctor() executes without crashing."""
    try:
        server.cli.run_doctor()
    except Exception as e:
        pytest.fail(f"run_doctor() crashed: {e}")


def test_benchmark_reports_nonzero_tool_signatures(capsys):
    """Verifies that run_benchmark() reports non-zero tool signatures (prevents dict key regression)."""
    server.cli.run_benchmark(iterations=2)
    captured = capsys.readouterr().out
    assert "OmniCache AI Acceleration Benchmark" in captured
    assert "tool signatures" in captured
    assert "(0 tool signatures)" not in captured, "Failed: Benchmark reported 0 tool signatures!"
