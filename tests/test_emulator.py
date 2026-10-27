"""Tests d'intégration réels contre un émulateur Android : le vrai serveur MCP par stdio, le vrai adb.

Lancés par le job CI « emulator » du dépôt public (ubuntu + KVM) et à la main contre n'importe quel émulateur :
    NEXUS_ANDROID_SERIAL=emulator-5554 uv run pytest -m emulator tests/test_emulator.py
Sans NEXUS_ANDROID_SERIAL d'émulateur, ils sont sautés : jamais le téléphone de l'utilisateur. Chaque réglage changé est
remis à sa valeur d'origine.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, ImageContent, TextContent

SERIAL = os.environ.get("NEXUS_ANDROID_SERIAL", "")
pytestmark = [
    pytest.mark.emulator,
    pytest.mark.skipif(not SERIAL.startswith("emulator-"), reason="NEXUS_ANDROID_SERIAL=emulator-NNNN required"),
]
SETTINGS = "com.android.settings"


def _session(tmp_path, body: Callable[[ClientSession], Awaitable[None]]) -> None:
    # Profil temporaire (état, journal, captures) ; adb et l'émulateur restent les vrais.
    env = {**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path), "NEXUS_ANDROID_UPDATE": "0",
           "NEXUS_JOURNAL_HOME": str(tmp_path / "journal")}
    params = StdioServerParameters(command=sys.executable, args=["-m", "nexus_android"], env=env)

    async def run() -> None:
        with anyio.fail_after(300):
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                await body(session)

    anyio.run(run)


async def _call(session: ClientSession, tool: str, **arguments: Any) -> CallToolResult:
    result = await session.call_tool(tool, arguments)
    assert not result.is_error, f"{tool}({arguments}): {_text(result)}"
    return result


def _text(result: CallToolResult) -> str:
    return "\n".join(c.text for c in result.content if isinstance(c, TextContent))


async def _foreground(session: ClientSession, package: str, limit: float = 60.0) -> None:
    """Premier démarrage d'une application sur un émulateur neuf : bien plus long que les 5 s après lesquelles launch
    dit « not verified ». Relu par phone() jusqu'à `limit` s."""
    with anyio.fail_after(limit):
        while True:
            status = (await _call(session, "phone")).structured_content
            if status and status["foreground"] == package:
                return
            await anyio.sleep(1)


def test_phone_status(tmp_path):
    async def body(session: ClientSession) -> None:
        status = (await _call(session, "phone")).structured_content
        assert status and status["serial"] == SERIAL
        assert status["transport"] == "usb" and re.match(r"\d+", status["android"])
        assert status["model"] and not status["code_required"]

    _session(tmp_path, body)


def test_look_elements_and_image(tmp_path):
    async def body(session: ClientSession) -> None:
        await _call(session, "act", steps=[{"do": "key", "keys": "home"}], then="none")
        listing = _text(await _call(session, "look", show="elements"))
        assert re.search(r"^\[1\]", listing, re.M), listing
        both = await _call(session, "look", show="both")
        images = [c for c in both.content if isinstance(c, ImageContent)]
        assert images and images[0].mime_type.startswith("image/") and len(images[0].data) > 1000

    _session(tmp_path, body)


def test_launch_act_and_back_home(tmp_path):
    async def body(session: ClientSession) -> None:
        opened = _text(await _call(session, "launch", app=SETTINGS, then="elements"))
        assert SETTINGS in opened, opened
        await _foreground(session, SETTINGS)
        scrolled = _text(await _call(session, "act", steps=[{"do": "scroll", "direction": "down"},
                                                            {"do": "scroll", "direction": "up"}], then="none"))
        assert "✗" not in scrolled, scrolled
        back = _text(await _call(session, "act", steps=[{"do": "key", "keys": "home"}], then="none"))
        assert "✗" not in back, back
        status = (await _call(session, "phone")).structured_content
        assert status and status["foreground"] != SETTINGS

    _session(tmp_path, body)


def test_type_and_read_back(tmp_path):
    # Champ de saisie réel : la recherche des Paramètres ; texte ASCII par `input text`, relu à l'écran.
    async def body(session: ClientSession) -> None:
        opened = _text(await _call(session, "launch", action="android.settings.APP_SEARCH_SETTINGS", then="none"))
        found = re.search(r"(?:Opened|Intent sent to): ([\w.]+)/", opened)
        assert found, opened
        await _foreground(session, found.group(1))
        typed = _text(await _call(session, "act", steps=[{"do": "type", "text": "nexus"}], then="elements"))
        assert "nexus" in typed and "✗" not in typed, typed
        await _call(session, "act", steps=[{"do": "key", "keys": "back"}, {"do": "key", "keys": "home"}],
                    then="none")

    _session(tmp_path, body)


def test_settings_read_and_restore(tmp_path):
    async def body(session: ClientSession) -> None:
        for name in ("wifi", "bluetooth", "location", "brightness", "volume", "dnd"):
            read = (await _call(session, "settings", name=name)).structured_content
            assert read and read["name"] == name and not read["changed"] and read["value"], (name, read)
        before = (await _call(session, "settings", name="volume")).structured_content
        assert before
        original = re.search(r"\d+", before["value"])
        assert original, before
        target = "40" if original.group() != "40" else "60"
        try:
            changed = (await _call(session, "settings", name="volume", value=target)).structured_content
            assert changed and changed["changed"] and abs(int(re.search(r"\d+", changed["value"]).group())  # type: ignore[union-attr]
                                                          - int(target)) <= 10, changed
        finally:
            await _call(session, "settings", name="volume", value=original.group())

    _session(tmp_path, body)


def test_apps_files_notifications_checkup(tmp_path):
    async def body(session: ClientSession) -> None:
        info = _text(await _call(session, "apps", action="info", app=SETTINGS))
        assert SETTINGS in info, info
        listing = _text(await _call(session, "files", action="list", path="/sdcard"))
        assert "Download" in listing, listing
        notes = _text(await _call(session, "notifications"))
        assert notes, notes
        checkup = _text(await _call(session, "checkup"))
        assert "Android" in checkup and "Battery" in checkup, checkup
        crashes = _text(await _call(session, "logs", kind="crash"))
        assert crashes, crashes

    _session(tmp_path, body)


def test_clipboard_round_trip(tmp_path):
    async def body(session: ClientSession) -> None:
        wrote = _text(await _call(session, "clipboard", action="write", text="NexusAndroid émulateur 1618"))
        assert wrote, wrote
        read = _text(await _call(session, "clipboard", action="read"))
        assert "NexusAndroid émulateur 1618" in read, read

    _session(tmp_path, body)


def test_tools_listed_match(tmp_path):
    # Le même serveur, cette fois avec un appareil : aucun outil ne disparaît ni ne casse son schéma.
    async def body(session: ClientSession) -> None:
        tools = (await session.list_tools()).tools
        assert {t.name for t in tools} >= {"look", "act", "launch", "settings", "apps", "phone"}
        json.dumps([t.model_dump(mode="json") for t in tools])

    _session(tmp_path, body)
