"""Tests boite noire du moteur natif C# (.NET 10) de NexusAndroid (Phase 4 : parité vérifiée et bascule réversible)."""

from __future__ import annotations

import json
import os
import subprocess
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE_DIR = ROOT / "native"
CSPROJ = NATIVE_DIR / "NexusAndroid.csproj"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

EXPECTED_TOOLS = {
    "look",
    "act",
    "launch",
    "messages",
    "draft",
    "notifications",
    "files",
    "capture",
    "logs",
    "clipboard",
    "info",
    "settings",
    "functions",
    "web",
    "apps",
    "map",
    "phone",
    "checkup",
}


@cache
def _native_exe() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    configured = os.environ.get("NEXUS_ANDROID_NATIVE_EXE")
    exe = Path(configured) if configured else Path(local_app_data) / "NexusAndroid" / "test-native" / "NexusAndroid.exe"
    if not configured:
        build_root = Path(local_app_data) / "NexusAndroid" / "build"
        cmd = [
            "dotnet",
            "publish",
            str(CSPROJ),
            "-c",
            "Release",
            "-r",
            "win-x64",
            "--self-contained",
            "false",
            "-p:PublishSingleFile=true",
            "-o",
            str(exe.parent),
            f"-p:NexusLocalBuildRoot={build_root}",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        assert res.returncode == 0, f"dotnet publish failed: {res.stderr}\n{res.stdout}"
    assert exe.is_file(), f"NexusAndroid.exe missing at {exe}"
    return exe


def test_native_behavior_contracts() -> None:
    """Actual handlers against controlled phone/ADB transports, never a real gesture."""
    result = subprocess.run(
        ["dotnet", "run", "--project", str(ROOT / "tests" / "native" / "NativeContracts.csproj"), "-c", "Release"],
        capture_output=True, text=True, check=False, timeout=120, creationflags=NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "51 passed, 0 failed" in result.stdout


def test_native_advanced_contracts() -> None:
    """Web, AppFunctions and map safety against isolated exact transports."""
    result = subprocess.run(
        ["dotnet", "run", "--project", str(ROOT / "tests" / "native_advanced" / "NativeAdvancedContracts.csproj"), "-c", "Release"],
        capture_output=True, text=True, check=False, timeout=120, creationflags=NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "22 passed, 0 failed" in result.stdout


def test_native_http_health_auth_sessions_and_schemas() -> None:
    """Protocol only: ephemeral port, synthetic token, no phone query or gesture."""
    import socket
    import time
    import urllib.error
    import urllib.request

    with socket.socket() as allocation:
        allocation.bind(("127.0.0.1", 0))
        port = allocation.getsockname()[1]
    env = {**os.environ, "NEXUS_MCP_TOKEN": "synthetic-native-contract-token"}
    proc = subprocess.Popen([str(_native_exe()), "--http", str(port)], env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=NO_WINDOW)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with urllib.request.urlopen(base + "/health", timeout=1) as reply:
                    health = json.load(reply)
                break
            except (OSError, urllib.error.URLError):
                assert time.monotonic() < deadline, "native HTTP daemon did not start"
                time.sleep(0.05)
        assert health["service"] == "nexus-android"
        assert health["runtime"] == "native"
        assert health["pid"] == proc.pid
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": "2025-06-18"}}).encode()
        denied = urllib.request.Request(base + "/mcp", data=payload,
                                        headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(denied, timeout=3)
            raise AssertionError("HTTP call without token was accepted")
        except urllib.error.HTTPError as error:
            assert error.code == 401
        headers = {"Content-Type": "application/json", "Authorization": "Bearer synthetic-native-contract-token"}
        with urllib.request.urlopen(urllib.request.Request(base + "/mcp", data=payload, headers=headers), timeout=3) as reply:
            session = reply.headers["Mcp-Session-Id"]
            initialize = json.load(reply)
        assert session
        assert initialize["result"]["protocolVersion"] == "2025-06-18"
        headers["Mcp-Session-Id"] = session
        listing = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).encode()
        with urllib.request.urlopen(urllib.request.Request(base + "/mcp", data=listing, headers=headers), timeout=3) as reply:
            tools = json.load(reply)["result"]["tools"]
        assert {tool["name"] for tool in tools} == EXPECTED_TOOLS
        act = next(tool for tool in tools if tool["name"] == "act")
        assert "do" in act["inputSchema"]["$defs"]["Step"]["properties"]
        ping = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"}).encode()
        with urllib.request.urlopen(urllib.request.Request(base + "/mcp", data=ping, headers=headers), timeout=3) as reply:
            assert json.load(reply)["result"] == {}
        for method, expected in (("GET", 405),):
            try:
                urllib.request.urlopen(urllib.request.Request(base + "/mcp", headers=headers, method=method), timeout=3)
                raise AssertionError(f"{method} unexpectedly succeeded")
            except urllib.error.HTTPError as error:
                assert error.code == expected
        with urllib.request.urlopen(urllib.request.Request(base + "/mcp", headers=headers, method="DELETE"), timeout=3) as reply:
            assert reply.status == 204
        try:
            urllib.request.urlopen(urllib.request.Request(base + "/mcp", data=listing, headers=headers), timeout=3)
            raise AssertionError("closed session was reused")
        except urllib.error.HTTPError as error:
            assert error.code == 404
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_native_version() -> None:
    exe = _native_exe()
    res = subprocess.run([str(exe), "--version"], capture_output=True, text=True, check=False)
    assert res.returncode == 0
    assert "NexusAndroid" in res.stdout
    assert "0.20.3" in res.stdout


def test_native_selftest() -> None:
    exe = _native_exe()
    res = subprocess.run([str(exe), "--selftest"], capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"Selftest failed: {res.stdout}\n{res.stderr}"
    assert "Self-tests finished: 15 passed, 0 failed" in res.stdout


def test_native_mcp_initialize_and_tools() -> None:
    exe = _native_exe()
    proc = subprocess.Popen(
        [str(exe), "--mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=NO_WINDOW,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None

    try:
        init_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        proc.stdin.write(init_req + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        init_resp = json.loads(line)
        assert init_resp.get("jsonrpc") == "2.0"
        assert init_resp.get("id") == 1
        assert init_resp.get("result", {}).get("serverInfo", {}).get("name") == "nexus-android"

        tools_req = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        proc.stdin.write(tools_req + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        tools_resp = json.loads(line)
        assert tools_resp.get("id") == 2
        tools_list = tools_resp.get("result", {}).get("tools", [])
        names = {t["name"] for t in tools_list}
        assert EXPECTED_TOOLS.issubset(names), f"Missing tools: {EXPECTED_TOOLS - names}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
