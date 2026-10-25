"""Mises à jour faites par le serveur (updates.py) et Platform Tools à nous remises à jour (platformtools.refresh)."""

from __future__ import annotations

import json

import pytest

from nexus_android import __version__, platformtools, updates

NEWER = "99.0.0"


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(updates, "memo_file", lambda: tmp_path / "update.json")
    monkeypatch.setattr(updates, "DISABLED", False)
    monkeypatch.setattr(updates, "MANAGED", "")
    monkeypatch.setattr(updates.platformtools, "refresh", lambda: None)
    return tmp_path / ".nexus" / "status" / "android.json"


def _status(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_up_to_date(state, monkeypatch):
    monkeypatch.setattr(updates, "latest_on_pypi", lambda: __version__)
    updates.check()
    data = _status(state)
    assert {k: data[k] for k in ("product", "version", "latest", "state")} == {
        "product": "android", "version": __version__, "latest": __version__, "state": "idle"}
    assert data["checked_at"].endswith("Z") and "up to date" in data["message"]


def test_newer_prepared_through_uvx(state, monkeypatch):
    monkeypatch.setattr(updates, "latest_on_pypi", lambda: NEWER)
    monkeypatch.setattr(updates, "via_uvx", lambda: True)
    asked = []
    monkeypatch.setattr(updates, "prepare", lambda version: asked.append(version))
    updates.check()
    assert asked == [NEWER]
    data = _status(state)
    assert data["state"] == "done" and data["latest"] == NEWER and "next start" in data["message"]


def test_failed_preparation_keeps_the_running_version(state, monkeypatch):
    monkeypatch.setattr(updates, "latest_on_pypi", lambda: NEWER)
    monkeypatch.setattr(updates, "via_uvx", lambda: True)
    monkeypatch.setattr(updates, "prepare", lambda version: "uvx not found on the PATH")
    updates.check()
    data = _status(state)
    assert data["state"] == "failed" and f"{__version__} kept" in data["message"]


def test_other_installs_only_told(state, monkeypatch):
    monkeypatch.setattr(updates, "latest_on_pypi", lambda: NEWER)
    monkeypatch.setattr(updates, "via_uvx", lambda: False)
    monkeypatch.setattr(updates, "prepare", lambda version: pytest.fail("not through uvx"))
    updates.check()
    assert _status(state)["state"] == "idle" and "installer" in _status(state)["message"]


def test_offline_and_once_a_day(state, monkeypatch):
    def offline():
        raise OSError("network unreachable")

    monkeypatch.setattr(updates, "latest_on_pypi", offline)
    updates.check()
    assert _status(state)["state"] == "failed"
    monkeypatch.setattr(updates, "latest_on_pypi", lambda: __version__)
    updates.check(now=1000.0)
    monkeypatch.setattr(updates, "latest_on_pypi", lambda: pytest.fail("checked twice the same day"))
    updates.check(now=1000.0 + 3600)
    assert _status(state)["state"] == "idle"


def test_turned_off(state, monkeypatch):
    monkeypatch.setattr(updates, "DISABLED", True)
    monkeypatch.setattr(updates, "latest_on_pypi", lambda: pytest.fail("no request when turned off"))
    updates.check()
    assert "turned off" in _status(state)["message"]


def test_platform_tools_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(platformtools, "TOOLS", tmp_path)
    monkeypatch.setattr(platformtools, "LOCAL", tmp_path / "platform-tools")
    monkeypatch.setattr(platformtools, "EXE", "adb.exe")
    old = tmp_path / "platform-tools-36.0.0"
    old.mkdir()
    (old / "adb.exe").write_bytes(b"MZ")
    (old / "source.properties").write_text("Pkg.Revision=36.0.0\n", encoding="utf-8")
    monkeypatch.setattr(platformtools, "installed", lambda: str(old / "adb.exe"))
    monkeypatch.setattr(platformtools, "_get", lambda url, limit: b"")
    monkeypatch.setattr(platformtools, "latest", lambda manifest, host: ("u", "s", 1, "37.0.1"))

    def fetch(say=None, known=("", "", 0, "")):
        new = tmp_path / f"platform-tools-{known[3]}"
        new.mkdir()
        (new / "adb.exe").write_bytes(b"MZ")
        return str(new / "adb.exe")

    monkeypatch.setattr(platformtools, "fetch", fetch)
    assert platformtools.refresh() == "37.0.1"
    assert not old.exists() and (tmp_path / "platform-tools-37.0.1" / "adb.exe").is_file()
    monkeypatch.setattr(platformtools, "installed", lambda: str(tmp_path / "platform-tools-37.0.1" / "adb.exe"))
    assert platformtools.refresh() is None  # à jour (version tirée du nom du dossier)


def test_user_adb_never_refreshed(tmp_path, monkeypatch):
    monkeypatch.setattr(platformtools, "TOOLS", tmp_path / "tools")
    monkeypatch.setattr(platformtools, "installed", lambda: str(tmp_path / "winget" / "adb.exe"))
    monkeypatch.setattr(platformtools, "_get", lambda url, limit: pytest.fail("no request for a user adb"))
    assert platformtools.refresh() is None


def test_managed_by_a_plugin(state, monkeypatch):
    monkeypatch.setattr(updates, "MANAGED", "nexus-plugins catalog")
    monkeypatch.setattr(updates, "latest_on_pypi", lambda: NEWER)
    monkeypatch.setattr(updates, "via_uvx", lambda: True)
    monkeypatch.setattr(updates, "prepare", lambda version: pytest.fail("a pinned plugin is updated by its catalog"))
    updates.check()
    assert _status(state)["message"].endswith("updated with the nexus-plugins catalog")
