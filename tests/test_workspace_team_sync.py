"""
Unit and Integration Test Suite for Multi-Agent Workspace Team Sync & CI/CD Cache Warming.
Verifies repo pre-warming, portable snapshot export/import, Gateway REST routes, and CLI commands.
"""

import os
import sys
import json
import time
import subprocess
import pytest
from starlette.testclient import TestClient
from server.gateway import app, METRICS_LEDGER
from server.tool_replayer import tool_cache, tool_policy_manager
from server.workspace_sync import workspace_warmer, workspace_sync_manager
from server.cli import run_warm, run_sync


@pytest.fixture
def sample_workspace(tmp_path):
    """Creates a realistic Git repository workspace with files and commits."""
    ws = tmp_path / "mock_repo"
    ws.mkdir()
    ws_dir = str(ws)

    # Initialize git repo
    subprocess.run(["git", "init", "-b", "main"], cwd=ws_dir, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.name", "CI Bot"], cwd=ws_dir, check=True)
    subprocess.run(["git", "config", "user.email", "ci@omnicache.test"], cwd=ws_dir, check=True)

    # Add files
    readme = ws / "README.md"
    readme.write_text("# Mock Project\nAI Coding Agent Warm Cache Test.\n", encoding="utf-8")

    src = ws / "src"
    src.mkdir()
    app_py = src / "app.py"
    app_py.write_text("def hello():\n    return 'Hello OmniCache'\n", encoding="utf-8")

    pyproject = ws / "pyproject.toml"
    pyproject.write_text("[project]\nname = 'mock-proj'\nversion = '1.0.0'\n", encoding="utf-8")

    # Git commit
    subprocess.run(["git", "add", "."], cwd=ws_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=ws_dir, check=True, stdout=subprocess.PIPE)

    yield ws_dir


@pytest.fixture
def client():
    METRICS_LEDGER["agent_tool_hits"] = 0
    tool_cache.clear()
    tool_policy_manager.clear_custom_policies()
    yield TestClient(app)
    tool_cache.clear()
    tool_policy_manager.clear_custom_policies()


def test_01_workspace_warming_and_immediate_replay(sample_workspace):
    """Verify workspace_warmer pre-records git status, directory tree, and file reads."""
    tool_cache.clear()

    report = workspace_warmer.warm_workspace(
        workspace_dir=sample_workspace,
        max_files=50
    )

    assert report["status"] == "WARMED"
    assert report["is_git_repo"] is True
    assert report["git_commit"] is not None
    assert report["git_branch"] == "main"
    assert report["files_warmed"] >= 3
    assert report["dirs_warmed"] >= 2
    assert report["tools_recorded"] >= 8
    assert report["tokens_warmed"] > 0
    assert report["duration_ms"] >= 0

    # Immediate replay: read_file README.md -> HIT
    is_hit_readme, content_readme, _ = tool_cache.lookup_tool_call(
        tool_name="read_file",
        arguments={"file_path": "README.md"},
        workspace_dir=sample_workspace
    )
    assert is_hit_readme is True
    assert "Mock Project" in content_readme

    # Immediate replay: view_file app.py -> HIT
    abs_app_py = os.path.join(sample_workspace, "src", "app.py")
    is_hit_app, content_app, _ = tool_cache.lookup_tool_call(
        tool_name="view_file",
        arguments={"AbsolutePath": abs_app_py},
        workspace_dir=sample_workspace
    )
    assert is_hit_app is True
    assert "Hello OmniCache" in content_app

    # Immediate replay: git_status -> HIT
    is_hit_status, out_status, _ = tool_cache.lookup_tool_call(
        tool_name="git_status",
        arguments={},
        workspace_dir=sample_workspace
    )
    assert is_hit_status is True
    assert "On branch main" in out_status or "working tree clean" in out_status


def test_02_snapshot_export_and_import(sample_workspace, tmp_path):
    """Verify exporting workspace cache snapshot to JSON and compressed .gz and re-importing into fresh cache."""
    tool_cache.clear()

    # 1. Warm workspace
    workspace_warmer.warm_workspace(workspace_dir=sample_workspace)

    # 2. Add a custom policy to ensure policies are included in export
    tool_policy_manager.set_policy("lookup_test_item", {"ttl_seconds": 999, "cacheable": True})

    json_export = str(tmp_path / "cache_export.json")
    gz_export = str(tmp_path / "cache_export.tar.gz")

    # 3. Export JSON
    bundle_json = workspace_sync_manager.export_snapshot(
        workspace_dir=sample_workspace,
        output_path=json_export
    )
    assert bundle_json["format"] == "omnicache_workspace_sync_v1"
    assert bundle_json["record_count"] >= 8
    assert "lookup_test_item" in bundle_json["custom_policies"]
    assert os.path.exists(json_export)

    # 4. Export compressed GZ
    bundle_gz = workspace_sync_manager.export_snapshot(
        workspace_dir=sample_workspace,
        output_path=gz_export
    )
    assert os.path.exists(gz_export)
    assert os.path.getsize(gz_export) > 0

    # 5. Clear all caches (simulate fresh machine / developer station)
    tool_cache.clear()
    tool_policy_manager.clear_custom_policies()

    # Verify miss on fresh station
    is_hit_pre, _, _ = tool_cache.lookup_tool_call(
        tool_name="read_file",
        arguments={"file_path": "README.md"},
        workspace_dir=sample_workspace
    )
    assert is_hit_pre is False

    # 6. Import compressed GZ snapshot
    imp_res = workspace_sync_manager.import_snapshot(gz_export)
    assert imp_res["status"] == "IMPORTED"
    assert imp_res["records_imported"] >= 8
    assert imp_res["policies_imported"] >= 1

    # 7. Verify policy and tool hits on the fresh station
    assert tool_policy_manager.get_ttl("lookup_test_item") == 999
    is_hit_post, content, _ = tool_cache.lookup_tool_call(
        tool_name="read_file",
        arguments={"file_path": "README.md"},
        workspace_dir=sample_workspace
    )
    assert is_hit_post is True
    assert "Mock Project" in content


def test_03_sync_status_reporting(sample_workspace):
    """Verify get_sync_status returns accurate telemetry and distribution."""
    tool_cache.clear()
    workspace_warmer.warm_workspace(workspace_dir=sample_workspace)

    status = workspace_sync_manager.get_sync_status(workspace_dir=sample_workspace)
    assert status["status"] == "OK"
    assert status["active_tool_records"] >= 8
    assert status["saved_tokens_potential"] > 0
    dist = status["tool_distribution"]
    assert "read_file" in dist
    assert "list_dir" in dist


def test_04_gateway_workspace_endpoints(client, sample_workspace):
    """Verify REST API /v1/workspace/warm, /v1/workspace/sync/export, import, and status."""
    tool_cache.clear()

    # 1. POST /v1/workspace/warm
    res_warm = client.post("/v1/workspace/warm", json={
        "workspace_dir": sample_workspace,
        "max_files": 100
    })
    assert res_warm.status_code == 200
    w_data = res_warm.json()
    assert w_data["status"] == "WARMED"
    assert w_data["files_warmed"] >= 3

    # 2. GET /v1/workspace/sync/status
    res_status = client.get(f"/v1/workspace/sync/status?workspace_dir={sample_workspace}")
    assert res_status.status_code == 200
    s_data = res_status.json()
    assert s_data["status"] == "OK"
    assert s_data["active_tool_records"] >= 8

    # 3. GET /v1/workspace/sync/export
    res_exp = client.get(f"/v1/workspace/sync/export?workspace_dir={sample_workspace}")
    assert res_exp.status_code == 200
    bundle = res_exp.json()
    assert bundle["format"] == "omnicache_workspace_sync_v1"
    assert bundle["record_count"] >= 8

    # 4. Clear cache and test POST /v1/workspace/sync/import
    tool_cache.clear()
    res_imp = client.post("/v1/workspace/sync/import", json=bundle)
    assert res_imp.status_code == 200
    imp_data = res_imp.json()
    assert imp_data["status"] == "IMPORTED"
    assert imp_data["records_imported"] >= 8

    # Verify tool hit via Gateway replay
    res_replay = client.post("/v1/agent/tool_replay", json={
        "tool_name": "read_file",
        "arguments": {"file_path": "README.md"},
        "workspace_dir": sample_workspace
    })
    assert res_replay.status_code == 200
    rep_data = res_replay.json()
    assert rep_data["status"] == "HIT"
    assert "Mock Project" in rep_data["output"]


def test_05_cli_warm_and_sync_commands(sample_workspace, tmp_path, capsys):
    """Verify CLI functions run_warm and run_sync execute without errors."""
    tool_cache.clear()

    # Run warm CLI
    run_warm(workspace_dir=sample_workspace, max_files=50)
    captured = capsys.readouterr()
    assert "Warming OmniCache tool replay cache" in captured.out
    assert "Workspace is warm" in captured.out

    # Run sync export CLI
    cli_out_file = str(tmp_path / "cli_cache.json")
    run_sync(action="export", output_path=cli_out_file, workspace_dir=sample_workspace)
    captured_exp = capsys.readouterr()
    assert "Exported" in captured_exp.out
    assert os.path.exists(cli_out_file)

    # Clear and Run sync import CLI
    tool_cache.clear()
    run_sync(action="import", input_path=cli_out_file)
    captured_imp = capsys.readouterr()
    assert "Successfully imported" in captured_imp.out

    # Run sync status CLI
    run_sync(action="status")
    captured_status = capsys.readouterr()
    assert "OmniCache Workspace Sync Status" in captured_status.out
    assert "Active Tool Executions" in captured_status.out
