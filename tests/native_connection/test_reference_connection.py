"""Identical synthetic transports for the Python reference and native ADB selector."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from nexus_android import adb as reference

ROOT = Path(__file__).resolve().parents[2]
KNOWN = {"serialno": "MY-HARDWARE", "usb": "MY-USB", "address": "192.0.2.67:33915", "custom": "preserved"}


def source_fingerprint() -> str:
    fingerprint = hashlib.sha256()
    for folder in (ROOT / "native", Path(__file__).parent):
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path.suffix in {".cs", ".csproj", ".props", ".json"}:
                fingerprint.update(str(path.relative_to(ROOT)).encode())
                fingerprint.update(path.read_bytes())
    for name in ("garde-fous.json", "nexus-query.dex"):
        fingerprint.update((ROOT / "src" / "nexus_android" / name).read_bytes())
    return fingerprint.hexdigest()


@pytest.fixture(scope="session")
def connection_fixture_exe() -> Path:
    fingerprint = source_fingerprint()
    directory = Path(os.environ["LOCALAPPDATA"]) / "NexusAndroid" / "connection-differential" / fingerprint[:20]
    directory.mkdir(parents=True, exist_ok=True)
    props = directory / "isolated.props"
    props.write_text('<Project><PropertyGroup><BaseOutputPath>$(MSBuildThisFileDirectory)build\\$(MSBuildProjectName)\\bin\\</BaseOutputPath>'
                     '<BaseIntermediateOutputPath>$(MSBuildThisFileDirectory)build\\$(MSBuildProjectName)\\obj\\</BaseIntermediateOutputPath>'
                     '<MSBuildProjectExtensionsPath>$(BaseIntermediateOutputPath)</MSBuildProjectExtensionsPath></PropertyGroup></Project>', encoding="utf-8")
    result = subprocess.run(["dotnet", "build", str(Path(__file__).with_name("ConnectionContracts.csproj")), "-c", "Release",
                             f"-p:DirectoryBuildPropsPath={props}", "-o", str(directory / "product"), "--nologo", "-v:q"],
                            capture_output=True, encoding="utf-8", check=False, timeout=120,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stdout + result.stderr
    assert source_fingerprint() == fingerprint, "Sources changed during native connection qualification; retry with stable sources"
    return directory / "product" / "ConnectionContracts.exe"


def test_native_connection_invariants(connection_fixture_exe: Path) -> None:
    result = subprocess.run([str(connection_fixture_exe)], capture_output=True, encoding="utf-8", check=False,
                            timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "47 passed, 0 failed" in result.stdout


SCENARIOS: list[dict[str, Any]] = [
    {"devices": [{"serial": "192.0.2.8:40000"}], "identities": {"192.0.2.8:40000": "OTHER"}},
    {"devices": [{"serial": "emulator-5554"}], "identities": {"emulator-5554": "EMULATOR"}},
    {"devices": [{"serial": "MY-USB"}], "identities": {"MY-USB": "MY-HARDWARE"}},
    {"devices": [{"serial": "ONE"}, {"serial": "TWO"}], "identities": {"ONE": "ONE-ID", "TWO": "TWO-ID"}},
    {"devices": [{"serial": "MY-USB"}], "identities": {}},
    {"state": KNOWN, "devices": [{"serial": "FOREIGN"}, {"serial": "192.0.2.67:33915"}],
     "identities": {"FOREIGN": "OTHER", "192.0.2.67:33915": "MY-HARDWARE"}},
    {"state": KNOWN, "devices": [{"serial": "192.0.2.67:33915"}, {"serial": "MY-USB"}],
     "identities": {"MY-USB": "MY-HARDWARE", "192.0.2.67:33915": "MY-HARDWARE"}},
    {"state": {"usb": "OLD-ID", "custom": "preserved"}, "devices": [{"serial": "OLD-ID"}], "identities": {"OLD-ID": "OLD-ID"}},
    {"state": KNOWN, "forced": "emulator-5554", "devices": [{"serial": "emulator-5554"}]},
    {"state": KNOWN, "forced": "missing", "devices": [{"serial": "MY-USB"}], "identities": {"MY-USB": "MY-HARDWARE"}},
    {"state": KNOWN, "forced": "192.0.2.3:40000", "answers": ["192.0.2.3:40000"]},
    {"state": KNOWN, "answers": ["192.0.2.67:33915"], "identities": {"192.0.2.67:33915": "MY-HARDWARE"}},
    {"state": KNOWN, "answers": ["192.0.2.67:33915"], "identities": {"192.0.2.67:33915": "FOREIGN"}},
    {"state": KNOWN, "mdns": "x _adb-tls-connect._tcp. 192.0.2.67:40001\n", "answers": ["192.0.2.67:40001"],
     "identities": {"192.0.2.67:40001": "MY-HARDWARE"}},
    {"state": KNOWN, "ports": [40000, 40001], "answers": ["192.0.2.67:40001"], "identities": {"192.0.2.67:40001": "MY-HARDWARE"}},
    {"state": KNOWN, "ports": [40000], "same_subnet": False},
    {"state": KNOWN, "ports": [40000], "scan": False},
    {"state": KNOWN, "devices": [{"serial": "MY-USB", "state": "offline"}], "restore_offline": True, "identities": {"MY-USB": "MY-HARDWARE"}},
    {"state": KNOWN, "devices": [{"serial": "MY-USB", "state": "unauthorized"}]},
    {"state": KNOWN, "devices": [{"serial": "MY-USB", "state": "no permissions"}]},
    {"state": KNOWN, "devices": [{"serial": "adb-MY_123._adb-tls-connect._tcp"}], "identities": {"adb-MY_123._adb-tls-connect._tcp": "MY-HARDWARE"}},
]


@pytest.mark.parametrize("fixture", SCENARIOS)
def test_native_selection_matches_reference(connection_fixture_exe: Path, monkeypatch: pytest.MonkeyPatch,
                                            tmp_path: Path, fixture: dict[str, Any]) -> None:
    state_path = tmp_path / "wifi.json"
    if "state" in fixture:
        state_path.write_text(json.dumps(fixture["state"]), encoding="utf-8")
    monkeypatch.setattr(reference, "WIFI_STATE", state_path)
    monkeypatch.setattr(reference, "_find_adb", lambda: "never-real-adb")
    monkeypatch.setattr(reference, "same_subnet", lambda _: fixture.get("same_subnet", True))
    scans: list[str] = []
    monkeypatch.setattr(reference, "open_ports", lambda host: scans.append(host) or fixture.get("ports", []))
    monkeypatch.setattr(reference.time, "sleep", lambda _: None)
    if fixture.get("forced"):
        monkeypatch.setenv("NEXUS_ANDROID_SERIAL", fixture["forced"])
    else:
        monkeypatch.delenv("NEXUS_ANDROID_SERIAL", raising=False)
    devices = [dict(row) for row in fixture.get("devices", [])]

    def raw(self: reference.Adb, args: list[str], timeout: float = 30.0, serial: bool = True) -> subprocess.CompletedProcess[bytes]:
        del self, timeout, serial
        output = ""
        if args == ["devices", "-l"]:
            output = "List of devices attached\n" + "".join(f"{d['serial']}\t{d.get('state', 'device')} model:Fixture\n" for d in devices)
        elif args == ["mdns", "services"]:
            output = fixture.get("mdns", "")
        elif args == ["reconnect", "offline"] and fixture.get("restore_offline"):
            for device in devices:
                if device.get("state") == "offline":
                    device["state"] = "device"
        elif args[0] == "connect" and args[1] in fixture.get("answers", []):
            if not any(d["serial"] == args[1] for d in devices):
                devices.append({"serial": args[1]})
            output = "connected to " + args[1]
        elif args[0] == "disconnect":
            devices[:] = [device for device in devices if device["serial"] != args[1]]
        elif args[0] == "-s" and args[2] == "shell":
            output = fixture.get("identities", {}).get(args[1], "") if args[3:] == ["getprop", "ro.serialno"] else fixture.get("shell", {}).get(" ".join(args[3:]), "")
        elif args[:1] == ["shell"]:
            output = fixture.get("shell", {}).get(" ".join(args[1:]), "")
        return subprocess.CompletedProcess(args, 0, output.encode(), b"")

    monkeypatch.setattr(reference.Adb, "_raw", raw)
    expected: dict[str, Any] = {}
    try:
        picked = reference.Adb().connect(scan=fixture.get("scan", True))
        expected.update(selected=picked.serial, transport=picked.transport)
    except reference.PhoneError:
        expected["error"] = True
    expected["state"] = json.loads(state_path.read_text()) if state_path.exists() else {}
    native = subprocess.run([str(connection_fixture_exe), "--fixture-json"], input=json.dumps(fixture) + "\n",
                            capture_output=True, encoding="utf-8", check=False, timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert native.returncode == 0, native.stderr
    actual = json.loads(native.stdout)
    assert bool(actual.get("error")) == bool(expected.get("error")), (expected, actual)
    if not expected.get("error"):
        assert (actual["selected"], actual["transport"]) == (expected["selected"], expected["transport"])
    assert actual["state"] == expected["state"], (expected, actual)
    assert actual["scans"] == scans
