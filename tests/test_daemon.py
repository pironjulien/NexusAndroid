"""Démon HTTP partagé : jeton exigé, plusieurs sessions sur un seul processus, vue propre à chaque session."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from nexus_android import server

TOKEN = "t" * 40


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_views_are_per_session(monkeypatch):
    monkeypatch.setattr(server, "ADB", object())
    monkeypatch.setattr(server, "SCREEN", object())
    monkeypatch.setattr(server, "MESSAGES", object())
    monkeypatch.setattr(server, "Screen", lambda adb: object())
    monkeypatch.setattr(server, "Messages", lambda adb: object())
    a, b = type("S", (), {})(), type("S", (), {})()
    assert server._init()[1] is server.SCREEN  # stdio, minuteur : vue globale
    tok = server._SESSION.set(a)
    view_a = server._init()
    assert view_a[1] is not server.SCREEN and server._init()[1:] == view_a[1:]  # stable dans la session
    server._SESSION.reset(tok)
    tok = server._SESSION.set(b)
    assert server._init()[1] is not view_a[1] and server._init()[2] is not view_a[2]  # [N] propres à chaque session
    assert server._init()[0] is view_a[0]  # liaison ADB commune
    server._SESSION.reset(tok)


def test_session_key_stable_across_calls_over_http(monkeypatch):
    """Vrai SDK, vrai transport HTTP : la clé de vue est la même pour tous les appels d'une session et diffère d'une
    session à l'autre (le SDK 2.x crée un ctx.session par requête : la liste [N] du look se perdait à l'appel suivant)."""
    import threading

    import uvicorn

    seen: list[object] = []

    async def record(ctx, call_next):
        if ctx.method == "tools/list":
            seen.append(server._SESSION.get())
        return await call_next(ctx)

    chain = server.mcp.middleware
    before = list(chain)
    chain += [server._bind_session, record]
    port = _free_port()
    web = uvicorn.Server(uvicorn.Config(server.mcp.streamable_http_app(host="127.0.0.1"), host="127.0.0.1", port=port,
                                        log_level="warning"))
    thread = threading.Thread(target=web.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 30
        while not web.started and time.time() < deadline:
            time.sleep(0.05)

        async def session() -> None:
            async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (r, w), ClientSession(r, w) as s:
                await s.initialize()
                await s.list_tools()
                await s.list_tools()

        async def check() -> None:
            with anyio.fail_after(60):
                await session()
                await session()

        anyio.run(check)
    finally:
        chain[:] = before
        web.should_exit = True
        thread.join(10)
    assert len(seen) == 4 and all(isinstance(k, str) and k for k in seen)
    assert seen[0] == seen[1] and seen[2] == seen[3] and seen[0] != seen[2]


def test_daemon_token_and_two_sessions(tmp_path):
    port = _free_port()
    env = {**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path), "NEXUS_ANDROID_KEEP_AWAKE": "0",
           "NEXUS_MCP_TOKEN": TOKEN}
    proc = subprocess.Popen([sys.executable, "-m", "nexus_android", "--http", str(port)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), 0.5).close()
                break
            except OSError:
                time.sleep(0.2)

        async def names(token: str) -> set[str]:
            client = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
            async with client, streamable_http_client(url, http_client=client) as (r, w), ClientSession(r, w) as s:
                await s.initialize()
                return {t.name for t in (await s.list_tools()).tools}

        async def check():
            with anyio.fail_after(60):
                results: list[set[str]] = []

                async def one():
                    results.append(await names(TOKEN))

                async with anyio.create_task_group() as tg:  # deux sessions en même temps sur le même démon
                    tg.start_soon(one)
                    tg.start_soon(one)
                assert len(results) == 2 and all("look" in r for r in results)
                refused = False
                try:
                    await names("x" * 40)
                except Exception:  # noqa: BLE001 — 401 remonte en erreur de transport
                    refused = True
                assert refused

        anyio.run(check)
    finally:
        proc.terminate()
        proc.wait(10)


def test_daemon_refuses_short_token(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path), "NEXUS_ANDROID_KEEP_AWAKE": "0",
           "NEXUS_MCP_TOKEN": "court"}
    env.pop("NEXUS_MCP_TOKEN_FILE", None)
    out = subprocess.run([sys.executable, "-m", "nexus_android", "--http", str(_free_port())], env=env,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode != 0 and "Token missing or too short" in out.stderr
