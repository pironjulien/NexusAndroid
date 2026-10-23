"""The Windows check must fail locally with an empty cache, without fetching its prerequisites."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(
    os.name != "nt" or not POWERSHELL or not shutil.which("uv") or not shutil.which("node"),
    reason="the Windows runner needs PowerShell, uv and Node.js",
)


@pytest.fixture
def offline_checkout(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    for name in ("test.ps1", "pyproject.toml", "uv.lock", ".python-version"):
        if (ROOT / name).exists():
            shutil.copyfile(ROOT / name, checkout / name)
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "UV_PYTHON_INSTALL_DIR": str(tmp_path / "uv-python"),
        # The runner must enforce its contract even if the caller permitted downloads.
        "UV_OFFLINE": "0",
        "UV_PYTHON_DOWNLOADS": "automatic",
        "PATH": sys.base_prefix + os.pathsep + os.environ["PATH"],
        "NO_COLOR": "1",
    }
    return checkout, env


def run_check(checkout: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    assert POWERSHELL
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-File", str(checkout / "test.ps1"), *args],
        cwd=checkout, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45,
    )


def test_unavailable_python_fails_without_downloading(offline_checkout):
    checkout, env = offline_checkout
    result = run_check(checkout, env, "-Python", "3.99")
    assert result.returncode != 0
    assert "Could not create the test environment offline" in result.stdout + result.stderr
    assert not list(Path(env["UV_PYTHON_INSTALL_DIR"]).glob("cpython-*"))


def test_empty_dependency_cache_fails_without_downloading(offline_checkout):
    checkout, env = offline_checkout
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    result = run_check(checkout, env, "-Python", version)
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "Offline dependency sync failed" in output
    assert "network connectivity is disabled" in output.lower()


def test_missing_node_fails_before_setting_up_python(offline_checkout):
    checkout, env = offline_checkout
    env["PATH"] = str(checkout)
    result = run_check(checkout, env)
    assert result.returncode != 0
    assert "Node.js must already be installed" in result.stdout + result.stderr
    assert not (Path(env["USERPROFILE"]) / ".nexus").exists()
