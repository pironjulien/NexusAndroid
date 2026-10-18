"""Le serveur démarre seul, sans téléphone ni adb, sur tout système (Windows, Linux) : initialize puis tools/list.

C'est ce que vérifient les annuaires MCP (Glama) dans un conteneur Linux ; la CI le lance aussi sous Linux.
"""

from __future__ import annotations

import os
import sys

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from nexus_android import __version__

TOOLS = {"look", "act", "launch", "messages", "draft", "notifications", "files", "info", "settings", "phone",
         "checkup"}


def test_initialize_and_list_tools(tmp_path):
    # État hors du vrai profil (HOME et USERPROFILE), écran gardé allumé coupé : aucun appel à adb au démarrage.
    env = {**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path), "NEXUS_ANDROID_KEEP_AWAKE": "0"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "nexus_android"], env=env)

    async def check():
        with anyio.fail_after(60):
            await session_check()

    async def session_check():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.server_info.name == "nexus-android"
            assert init.server_info.version == __version__
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert set(tools) == TOOLS
            for tool in tools.values():
                hints = tool.annotations
                assert hints is not None and hints.title, tool.name
                assert hints.read_only_hint is not None and hints.destructive_hint is not None, tool.name
                assert hints.idempotent_hint is not None, tool.name
            hints = {name: tool.annotations for name, tool in tools.items()}
            assert hints["look"] and hints["look"].read_only_hint
            assert hints["act"] and hints["act"].destructive_hint

    anyio.run(check)
