"""`nexus-android mirror` : scrcpy lancé avec le même adb que le serveur, message clair s'il manque."""

from __future__ import annotations

import sys

import pytest

from nexus_android import mirror, platformtools, server
from nexus_android.adb import PhoneError


def test_missing_scrcpy_says_how_to_install(monkeypatch, capsys):
    monkeypatch.delenv("NEXUS_ANDROID_SCRCPY", raising=False)
    monkeypatch.setattr(mirror.shutil, "which", lambda _name: None)
    assert mirror.run([]) == 1
    assert "winget install Genymobile.scrcpy" in capsys.readouterr().out


def test_scrcpy_gets_the_server_adb_and_options(monkeypatch, tmp_path):
    exe = tmp_path / "scrcpy.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("NEXUS_ANDROID_SCRCPY", str(exe))
    monkeypatch.setattr(platformtools, "find", lambda say=None: r"C:\pt\adb.exe")
    seen = {}

    def call(cmd, env):
        seen.update(cmd=cmd, adb=env["ADB"])
        return 0

    monkeypatch.setattr(mirror.subprocess, "call", call)
    assert mirror.run(["--turn-screen-off"]) == 0
    assert seen == {"cmd": [str(exe), "--turn-screen-off"], "adb": r"C:\pt\adb.exe"}


def test_no_adb_is_reported(monkeypatch, tmp_path, capsys):
    exe = tmp_path / "scrcpy.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("NEXUS_ANDROID_SCRCPY", str(exe))

    def find(say=None):
        raise PhoneError("adb not found")

    monkeypatch.setattr(platformtools, "find", find)
    assert mirror.run([]) == 1
    assert "adb not found" in capsys.readouterr().out


def test_main_routes_mirror(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["nexus-android", "mirror", "-s", "X"])
    monkeypatch.setattr(mirror, "run", lambda argv: 7 if argv == ["-s", "X"] else 0)
    with pytest.raises(SystemExit) as stop:
        server.main()
    assert stop.value.code == 7
