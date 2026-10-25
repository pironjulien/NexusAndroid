"""adb trouvé ou téléchargé (Platform Tools officielles), pas-à-pas du premier branchement, `setup` et `--version`."""

from __future__ import annotations

import hashlib
import io
import sys
import zipfile

import pytest

from nexus_android import adb as adb_mod
from nexus_android import firstrun, platformtools
from nexus_android.adb import PhoneError, parse_devices

MANIFEST = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sdk:sdk-repository xmlns:sdk="http://schemas.android.com/sdk/android/repo/repository2/03"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <remotePackage path="platform-tools">
    <revision><major>99</major><minor>0</minor><micro>0</micro></revision>
    <channelRef ref="channel-3"/>
    <archives><archive><complete><size>1</size><checksum type="sha1">00</checksum>
      <url>platform-tools_r99.0.0-win.zip</url></complete><host-os>windows</host-os></archive></archives>
  </remotePackage>
  <remotePackage path="platform-tools">
    <revision><major>37</major><minor>0</minor><micro>1</micro></revision>
    <channelRef ref="channel-0"/>
    <archives>
      <archive><complete><size>SIZE</size><checksum type="sha1">SHA1</checksum>
        <url>platform-tools_r37.0.1-linux.zip</url></complete><host-os>linux</host-os></archive>
      <archive><complete><size>SIZE</size><checksum type="sha1">SHA1</checksum>
        <url>platform-tools_r37.0.1-win.zip</url></complete><host-os>windows</host-os></archive>
    </archives>
  </remotePackage>
</sdk:sdk-repository>"""


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _serve(monkeypatch, archive: bytes, sha1: str | None = None, size: int | None = None) -> list[str]:
    manifest = (MANIFEST.replace(b"SHA1", (sha1 or hashlib.sha1(archive).hexdigest()).encode())
                .replace(b"SIZE", str(size if size is not None else len(archive)).encode()))
    asked: list[str] = []

    def get(url: str, limit: int) -> bytes:
        asked.append(url)
        return manifest if url == platformtools.MANIFEST else archive

    monkeypatch.setattr(platformtools, "_get", get)
    return asked


@pytest.fixture
def tools(tmp_path, monkeypatch):
    monkeypatch.setattr(platformtools, "TOOLS", tmp_path)
    monkeypatch.setattr(platformtools, "LOCAL", tmp_path / "platform-tools")
    monkeypatch.setattr(platformtools, "EXE", "adb.exe")
    monkeypatch.setattr(platformtools, "host_os", lambda: "windows")
    monkeypatch.setattr(platformtools, "installed", lambda: None)
    monkeypatch.setattr(platformtools, "DISABLED", False)
    return tmp_path


def test_latest_takes_the_stable_archive_for_this_os():
    url, sha1, size, version = platformtools.latest(MANIFEST.replace(b"SIZE", b"5").replace(b"SHA1", b"AB"), "windows")
    assert url == "https://dl.google.com/android/repository/platform-tools_r37.0.1-win.zip"
    assert (sha1, size, version) == ("ab", 5, "37.0.1")
    with pytest.raises(ValueError):
        platformtools.latest(MANIFEST.replace(b"SIZE", b"5"), "macosx")


def test_fetch_unpacks_the_official_archive(tools, monkeypatch):
    asked = _serve(monkeypatch, _zip({"platform-tools/adb.exe": b"MZ", "platform-tools/AdbWinApi.dll": b"x"}))
    path = platformtools.find()
    assert path == str(tools / "platform-tools" / "adb.exe")
    assert (tools / "platform-tools" / "AdbWinApi.dll").is_file()
    assert asked[-1].endswith("platform-tools_r37.0.1-win.zip")
    assert not list(tools.glob("*.part"))


def test_fetch_rejects_a_wrong_hash_or_size(tools, monkeypatch):
    archive = _zip({"platform-tools/adb.exe": b"MZ"})
    _serve(monkeypatch, archive, sha1="0" * 40)
    with pytest.raises(PhoneError, match="SHA-1"):
        platformtools.find()
    _serve(monkeypatch, archive, size=len(archive) + 1)
    with pytest.raises(PhoneError, match="winget install Google.PlatformTools"):
        platformtools.find()
    assert not (tools / "platform-tools").exists()


def test_fetch_rejects_paths_outside_the_folder(tools, monkeypatch):
    _serve(monkeypatch, _zip({"platform-tools/adb.exe": b"MZ", "../evil.txt": b"x"}))
    with pytest.raises(PhoneError, match="unsafe path"):
        platformtools.find()
    assert not (tools.parent / "evil.txt").exists()


def test_download_can_be_turned_off(tools, monkeypatch):
    monkeypatch.setattr(platformtools, "DISABLED", True)
    with pytest.raises(PhoneError, match="NEXUS_ANDROID_ADB_DOWNLOAD=0"):
        platformtools.find()


def test_offline_gives_the_manual_way(tools, monkeypatch):
    def get(url, limit):
        raise OSError("network unreachable")

    monkeypatch.setattr(platformtools, "_get", get)
    with pytest.raises(PhoneError, match="developer.android.com/tools/releases/platform-tools"):
        platformtools.find()


def test_installed_adb_comes_first(tmp_path, monkeypatch):
    adb = tmp_path / "adb.exe"
    adb.write_bytes(b"MZ")
    monkeypatch.setenv("NEXUS_ANDROID_ADB", str(adb))
    assert platformtools.installed() == str(adb)
    monkeypatch.delenv("NEXUS_ANDROID_ADB")
    monkeypatch.setattr(platformtools.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "none"))
    monkeypatch.setenv("ANDROID_HOME", str(tmp_path / "sdk"))
    (tmp_path / "sdk" / "platform-tools").mkdir(parents=True)
    (tmp_path / "sdk" / "platform-tools" / platformtools.EXE).write_bytes(b"MZ")
    assert platformtools.installed() == str(tmp_path / "sdk" / "platform-tools" / platformtools.EXE)


def test_linux_no_permissions_state():
    devices = parse_devices("List of devices attached\nR5CR00000 no permissions (user in plugdev group); see [x]\n")
    assert devices[0].state == "no permissions"


class _Raw:
    def __init__(self, out: str):
        self.returncode, self.stdout, self.stderr = 0, out.encode(), b""


def _adb(monkeypatch, tmp_path, devices: str) -> adb_mod.Adb:
    monkeypatch.setattr(adb_mod, "_find_adb", lambda: "adb")
    monkeypatch.setattr(adb_mod, "WIFI_STATE", tmp_path / "wifi.json")
    phone = adb_mod.Adb()
    monkeypatch.setattr(phone, "_raw", lambda args, timeout=30.0, serial=True: _Raw(devices))
    return phone


def test_first_plug_guides_step_by_step(monkeypatch, tmp_path):
    phone = _adb(monkeypatch, tmp_path, "List of devices attached\n")
    message = phone._missing("")
    for step in ("Build number", "USB debugging", "File transfer", "Allow USB debugging?", "oem-usb"):
        assert step in message
    assert "Revoke USB debugging" in _adb(monkeypatch, tmp_path, "List of devices attached\nR5C unauthorized\n")._missing("")


def test_setup_steps_one_per_line():
    lines = firstrun._steps(adb_mod.FIRST_PLUG).splitlines()
    assert [line.strip()[:2] for line in lines] == ["1.", "2.", "3.", "4."]


def test_version_flag(monkeypatch, capsys):
    from nexus_android import __version__, server

    monkeypatch.setattr(sys, "argv", ["nexus-android", "--version"])
    server.main()
    assert capsys.readouterr().out.strip() == f"nexus-android {__version__}"
