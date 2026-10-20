"""Gestion des applications : arrêt libre, vidage, désinstallation et installation avec confirm=true, rouages du système
et applis bancaires jamais vidés ni désinstallés, appli du système seulement masquée ; adb simulé."""

from __future__ import annotations

import asyncio

import pytest
from test_robustness import _NoLock

from nexus_android import server


class AppPhone:
    serial = "x"

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def shell(self, *argv: str, timeout: float = 30.0, check: bool = True) -> str:
        self.calls.append(argv)
        if argv[:3] == ("pm", "list", "packages"):
            if "-s" in argv:
                return "package:com.samsung.android.bixby.agent\npackage:com.android.settings\n"
            return ("package:com.example.game\npackage:com.samsung.android.bixby.agent\npackage:com.android.settings\n"
                    "package:fr.creditagricole.androidapp\n")
        if argv[:2] in (("pm", "clear"), ("pm", "uninstall")):
            return "Success\n"
        if argv[:3] == ("cmd", "package", "install-existing"):
            return "Package com.samsung.android.bixby.agent installed for user: 0\n"
        if argv[0] == "pidof":
            return ""
        if argv[:2] == ("dumpsys", "package"):
            return ("    versionCode=42 minSdk=26\n    versionName=1.2.3\n    firstInstallTime=2026-01-02\n"
                    "    lastUpdateTime=2026-10-01\n    installerPackageName=com.android.vending\n")
        if argv[:3] == ("cmd", "package", "resolve-activity"):
            return ""
        return ""

    def host(self, *args: str, timeout: float = 60.0) -> str:
        self.calls.append(args)
        return "Performing Streamed Install\nSuccess"


@pytest.fixture
def phone(monkeypatch):
    fake = AppPhone()
    monkeypatch.setattr(server, "ADB", fake)
    monkeypatch.setattr(server, "SCREEN", object())
    monkeypatch.setattr(server, "MESSAGES", object())
    monkeypatch.setattr(server, "PhoneLock", _NoLock)
    monkeypatch.setattr(server, "_PACKAGES", {})
    return fake


def run(**kw) -> str:
    return asyncio.run(server.apps(**kw))


def test_info_and_stop_are_free(phone):
    assert run(action="info", app="game").startswith("com.example.game: version 1.2.3 (42)")
    assert run(action="stop", app="game") == "com.example.game: stopped (no process left)"
    assert ("am", "force-stop", "com.example.game") in phone.calls


def test_clear_and_uninstall_need_confirm(phone):
    with pytest.raises(Exception, match="deletes all its data"):
        run(action="clear", app="game")
    assert not any(c[:2] == ("pm", "clear") for c in phone.calls)
    assert "data cleared" in run(action="clear", app="game", confirm=True)
    with pytest.raises(Exception, match="removed with its data"):
        run(action="uninstall", app="game")
    assert run(action="uninstall", app="game", confirm=True) == "com.example.game: uninstalled"
    assert ("pm", "uninstall", "com.example.game") in phone.calls


def test_system_app_only_hidden_and_restored(phone):
    assert "hidden for the user" in run(action="uninstall", app="bixby", confirm=True)
    assert ("pm", "uninstall", "--user", "0", "com.samsung.android.bixby.agent") in phone.calls
    assert "back for the user" in run(action="restore", app="com.samsung.android.bixby.agent")
    with pytest.raises(Exception, match="full package name"):
        run(action="restore", app="bixby")


def test_core_and_banking_never_cleared_or_uninstalled(phone):
    with pytest.raises(Exception, match="part of the system"):
        run(action="clear", app="com.android.settings", confirm=True)
    with pytest.raises(Exception, match="banking, payment or identity"):
        run(action="uninstall", app="creditagricole", confirm=True)
    assert not any(c[:2] in (("pm", "clear"), ("pm", "uninstall")) for c in phone.calls)


def test_install_needs_confirm_and_an_apk(phone, tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    apk = tmp_path / "jeu.apk"
    apk.write_bytes(b"PK")
    with pytest.raises(Exception, match="not an .apk"):
        run(action="install", path=str(tmp_path / "jeu.txt"), confirm=True)
    with pytest.raises(Exception, match="permissions it asks for"):
        run(action="install", path=str(apk))
    assert run(action="install", path=str(apk), confirm=True) == "jeu.apk: installed"
    assert ("install", "-r", str(apk.resolve())) in phone.calls
    secret = tmp_path / ".ssh" / "x.apk"
    secret.parent.mkdir()
    secret.write_bytes(b"PK")
    with pytest.raises(Exception, match="sensitive path"):
        run(action="install", path=str(secret), confirm=True)


def test_core_functions_guard_banking_apps_themselves(phone):
    from nexus_android import apps
    with pytest.raises(Exception, match="banking"):
        apps.clear(phone, "fr.creditagricole.androidapp", True)  # type: ignore[arg-type]
