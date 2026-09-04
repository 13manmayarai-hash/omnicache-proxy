"""
Unit & Integration Tests for Smart Fine-Grained Code Invalidation.
Verifies that editing non-target files (like README.md) does NOT invalidate target code files (like app.py),
while editing the target file or running global git_status correctly triggers invalidation.
"""

import os
import sys
import tempfile
import subprocess
import shutil
import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from server.gateway import app

@pytest.fixture
def test_workspace():
    workspace = tempfile.mkdtemp(prefix="omnicache_smart_inv_")
    subprocess.run(["git", "init", workspace], check=True, capture_output=True)
    subprocess.run(["git", "-C", workspace, "config", "user.email", "test@omnicache.ai"], check=True)
    subprocess.run(["git", "-C", workspace, "config", "user.name", "OmniCache Test"], check=True)

    app_py = os.path.join(workspace, "app.py")
    readme_md = os.path.join(workspace, "README.md")
    
    with open(app_py, "w") as f:
        f.write("def calculate_tax(amount): return amount * 0.2\n")
        
    with open(readme_md, "w") as f:
        f.write("# Project Docs\nInitial documentation.\n")

    subprocess.run(["git", "-C", workspace, "add", "."], check=True)
    subprocess.run(["git", "-C", workspace, "commit", "-m", "Initial commit"], check=True)

    yield workspace, app_py, readme_md

    shutil.rmtree(workspace, ignore_errors=True)

def test_smart_fine_grained_invalidation(test_workspace):
    workspace, app_py, readme_md = test_workspace
    client = TestClient(app)

    # 1. Record read_file for both app.py and README.md
    client.post("/v1/agent/tool_record", json={
        "tool_name": "read_file",
        "arguments": {"file": "app.py"},
        "workspace_dir": workspace,
        "workspace_fingerprint": workspace,
        "output": open(app_py).read()
    })

    client.post("/v1/agent/tool_record", json={
        "tool_name": "read_file",
        "arguments": {"file": "README.md"},
        "workspace_dir": workspace,
        "workspace_fingerprint": workspace,
        "output": open(readme_md).read()
    })

    # Record clean git_status
    client.post("/v1/agent/tool_record", json={
        "tool_name": "git_status",
        "arguments": {},
        "workspace_dir": workspace,
        "workspace_fingerprint": workspace,
        "output": "working tree clean"
    })

    lookup_app = {
        "tool_name": "read_file",
        "arguments": {"file": "app.py"},
        "workspace_dir": workspace,
        "workspace_fingerprint": workspace
    }
    lookup_readme = {
        "tool_name": "read_file",
        "arguments": {"file": "README.md"},
        "workspace_dir": workspace,
        "workspace_fingerprint": workspace
    }
    lookup_status = {
        "tool_name": "git_status",
        "arguments": {},
        "workspace_dir": workspace,
        "workspace_fingerprint": workspace
    }

    # 2. Verify all are HITs initially
    res1 = client.post("/v1/agent/tool_replay", json=lookup_app)
    assert res1.json().get("status") == "HIT"

    res2 = client.post("/v1/agent/tool_replay", json=lookup_readme)
    assert res2.json().get("status") == "HIT"

    res3 = client.post("/v1/agent/tool_replay", json=lookup_status)
    assert res3.json().get("status") == "HIT"

    # 3. Edit ONLY README.md (dirty git working tree!)
    with open(readme_md, "w") as f:
        f.write("# Project Docs\nUpdated documentation with new notes.\n")

    # 4. SMART BEHAVIOR:
    # - read_file(app.py) MUST STILL BE A HIT! (app.py was not modified!)
    res_app_after_readme = client.post("/v1/agent/tool_replay", json=lookup_app)
    assert res_app_after_readme.json().get("status") == "HIT", "Smart Invalidation Failed: editing README.md flushed app.py cache!"

    # - read_file(README.md) MUST BE A MISS! (README.md was modified)
    res_readme_after_edit = client.post("/v1/agent/tool_replay", json=lookup_readme)
    assert res_readme_after_edit.json().get("status") == "MISS", "Failed: edited README.md returned stale HIT!"

    # - git_status() MUST BE A MISS! (Working tree has uncommitted edits)
    res_status_after_edit = client.post("/v1/agent/tool_replay", json=lookup_status)
    assert res_status_after_edit.json().get("status") == "MISS", "Failed: git_status returned stale HIT after workspace edit!"

    # 5. Now edit app.py
    with open(app_py, "w") as f:
        f.write("def calculate_tax(amount): return amount * 0.25  # Updated rate\n")

    # - read_file(app.py) MUST NOW BE A MISS!
    res_app_after_edit = client.post("/v1/agent/tool_replay", json=lookup_app)
    assert res_app_after_edit.json().get("status") == "MISS", "Failed: edited app.py returned stale HIT!"
