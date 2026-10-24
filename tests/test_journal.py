"""Journal commun des outils Nexus (FORMAT.md, produit android) : une ligne par geste, l'IA appelante reconnue, jamais
de texte d'écran, de SMS, de texte tapé ni de code, et jamais bloquant."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from test_extras import field
from test_guards import FakePhone, node, phone, ui  # noqa: F401  (fixture phone)

from nexus_android import clip, journal, server
from nexus_android.adb import PhoneError
from nexus_android.server import Step

SECRET = "Code 482913 pour Mamie Josette"


@pytest.fixture
def lignes(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_JOURNAL_HOME", str(tmp_path))
    token = server._AGENT.set("claude")

    def read(agent: str = "claude") -> list[dict]:
        journal.flush()
        files = list((tmp_path / "android" / agent).glob("*.jsonl"))
        return [json.loads(x) for f in files for x in f.read_text(encoding="utf-8").splitlines()]

    yield read
    server._AGENT.reset(token)


@pytest.mark.parametrize("name,agent", [
    ("claude-code", "claude"), ("codex-mcp-client", "codex"), ("Codex Desktop/1.0", "codex"),
    ("antigravity", "antigravity"), ("Jetski/1.2", "antigravity"), ("curl/8", None), (None, None)])
def test_the_calling_ai_is_recognised(name, agent):
    assert journal.agent_of(name) == agent


def test_one_line_per_step_without_any_screen_text(phone, monkeypatch, lignes):  # noqa: F811
    monkeypatch.setattr(clip, "paste", lambda adb, text, kept=None: True)
    phone.uis = [ui(field()), ui(field(SECRET))]
    server._act([Step(do="type", text=SECRET)], "none")
    phone.uis = [ui(node("Envoyer à Mamie Josette", (0, 0, 500, 100)))]
    server._act([Step(do="tap", target="Rappeler Mamie Josette 482913")], "none")  # absent : échec
    rows = lignes()
    assert [(r["action"], r["issue"]) for r in rows] == [("type", "~"), ("tap", "✗")]  # champ masqué (code) : ~
    text = json.dumps(rows, ensure_ascii=False)
    for secret in ("482913", "Mamie", "Josette", "Envoyer", "Rappeler"):
        assert secret not in text
    first = rows[0]
    assert set(first) == {"v", "ts", "agent", "outil", "appel", "app", "cadre", "action", "technique", "issue", "ms",
                          "premier_plan", "erreur"}
    assert first["agent"] == "claude" and first["premier_plan"] is False and first["cadre"] is None
    assert rows[1]["erreur"] == "target not found"  # cause fixe, jamais le libellé venu de l'écran


def test_a_refused_step_is_logged_as_refus(phone, lignes):  # noqa: F811
    phone.uis = [ui(field(password=True))]
    server._act([Step(do="type", text=SECRET)], "none")
    assert [r["issue"] for r in lignes()] == ["refus"]
    assert "482913" not in json.dumps(lignes())


def test_a_tool_call_is_one_line_and_errors_lose_their_screen_text(phone, lignes):  # noqa: F811
    def _look(*a):
        raise PhoneError(f"\"{SECRET}\" not found on screen")

    with pytest.raises(server.ToolError):
        asyncio.run(server._run(_look))
    rows = lignes()
    assert [(r["action"], r["issue"], r["erreur"]) for r in rows] == [("look", "✗", "target not found")]


def test_tool_calls_carry_the_mcp_tool_name(phone, lignes):  # noqa: F811
    async def look():  # même nom que l'outil MCP : _run lit le nom de l'outil appelant
        return await server._run(lambda: "ok")

    async def phone_map():
        return await server._run(lambda: "ok")

    asyncio.run(look())
    asyncio.run(phone_map())
    assert [r["action"] for r in lignes()] == ["look", "map"]


def test_act_calls_are_not_logged_twice(phone, lignes):  # noqa: F811
    phone.uis = [ui(node("OK", (0, 0, 100, 100)))]
    asyncio.run(server._run(server._act, [Step(do="tap", target="OK")], "none"))
    assert [r["action"] for r in lignes()] == ["tap"]


def test_unknown_agent_writes_nothing_and_the_journal_never_blocks(tmp_path, monkeypatch, phone):  # noqa: F811
    monkeypatch.setenv("NEXUS_JOURNAL_HOME", str(tmp_path))
    phone.uis = [ui(node("OK", (0, 0, 100, 100)))]
    server._act([Step(do="tap", target="OK")], "none")  # aucune IA reconnue : rien
    assert not list(tmp_path.rglob("*.jsonl"))
    token = server._AGENT.set("codex")
    try:
        monkeypatch.setattr(journal, "_write", lambda *a: (_ for _ in ()).throw(OSError("disque plein")))
        assert server._act([Step(do="tap", target="OK")], "none")[0].startswith(("✓", "~"))  # le geste passe
    finally:
        server._AGENT.reset(token)


def test_only_a_package_name_goes_in_app():
    assert journal.app_propre("com.whatsapp") == "com.whatsapp"
    assert journal.app_propre("Mamie Josette") is None and journal.app_propre(None) is None


def test_agent_from_the_mcp_client_name_is_kept_for_the_session(monkeypatch):
    params = SimpleNamespace(clientInfo=SimpleNamespace(name="codex-mcp-client"))
    first = SimpleNamespace(request=SimpleNamespace(headers={"mcp-session-id": "s1", "user-agent": "python-httpx"}),
                            session=SimpleNamespace(client_params=params))
    later = SimpleNamespace(request=SimpleNamespace(headers={"mcp-session-id": "s1", "user-agent": "python-httpx"}),
                            session=SimpleNamespace(client_params=None))
    assert server._agent_key(first) == "codex" and server._agent_key(later) == "codex"


# ---------------------------------------------------------------- relecture Codex (journal)


@pytest.mark.parametrize("message", [
    "Mamie Josette 482913 has several numbers and no conversation: give the number",
    "draft for Mamie Josette 0612345678 not opened",
    "D:\\Partage\\Documents\\secret.pdf: no such file",
    "Wi-Fi Maison 482913"])
def test_no_part_of_a_free_message_reaches_the_journal(message):
    """Même le début d'un message peut porter un nom, un numéro ou un chemin : seule une cause fixe est écrite."""
    label = journal.cause(message)
    assert label in {lbl for _, lbl in journal.CAUSES} | {"error"}
    for secret in ("Mamie", "482913", "0612345678", "secret", "Maison", "Partage"):
        assert secret not in label


def test_a_stuck_disk_never_holds_the_phone(monkeypatch, tmp_path):
    """Écriture retenue (disque, antivirus) : ecrire rend la main tout de suite, le geste n'attend pas."""
    import threading
    import time

    gate = threading.Event()
    monkeypatch.setattr(journal, "_write", lambda *a: gate.wait(5))
    tick = time.monotonic()
    for _ in range(5):
        journal.ecrire("claude", None, "com.app", "tap", "✓", 1.0)
    assert time.monotonic() - tick < 0.2
    gate.set()
    journal.flush()


def test_a_line_waiting_past_midnight_stays_in_its_month(monkeypatch, tmp_path):
    """Relecture Codex : le fichier du mois vient de l'heure du geste, pas de celle de l'écriture."""
    import threading
    from datetime import datetime as real

    gate = threading.Event()
    written = []
    monkeypatch.setattr(journal, "_write", lambda agent, mois, text: (gate.wait(5), written.append(mois)))

    class Late(real):
        @classmethod
        def now(cls, tz=None):
            return real(2026, 10, 31, 23, 59, 59, tzinfo=tz)

    monkeypatch.setattr(journal, "datetime", Late)
    journal.ecrire("codex", None, "com.app", "tap", "✓", 1.0)
    monkeypatch.setattr(journal, "datetime", real)  # minuit passe pendant que la ligne attend
    gate.set()
    journal.flush()
    assert written == ["2026-10"]


def test_an_act_refused_before_its_first_step_is_logged_once(phone, monkeypatch, lignes):  # noqa: F811
    def busy(*a):
        raise PhoneError("The user is using the phone right now: nothing was done.")

    monkeypatch.setattr(server, "_wait_for_owner", busy)
    with pytest.raises(server.ToolError):
        asyncio.run(server._run(server._act, [Step(do="tap", target="OK")], "none"))
    assert [(r["action"], r["issue"], r["erreur"]) for r in lignes()] == [("act", "différé", "user busy")]


def _ctx(session=None, client=None, ua=None):
    params = SimpleNamespace(clientInfo=SimpleNamespace(name=client)) if client else None
    headers = {k: v for k, v in (("mcp-session-id", session), ("user-agent", ua)) if v}
    return SimpleNamespace(request=SimpleNamespace(headers=headers), session=SimpleNamespace(client_params=params))


def test_the_mcp_client_name_wins_and_a_session_keeps_its_ai(monkeypatch):
    monkeypatch.setattr(server, "_AGENTS_BY_SESSION", {})
    assert server._agent_key(_ctx("s2", client="claude-code", ua="Codex Desktop/1.0")) == "claude"
    assert server._agent_key(_ctx("s3", client="codex-mcp-client")) == "codex"
    assert server._agent_key(_ctx("s3", ua="Jetski/1.2")) == "codex"  # établie : un User-Agent ne la remplace pas


def test_an_unknown_client_of_the_shared_daemon_belongs_to_nobody(monkeypatch):
    monkeypatch.setattr(server, "_SHARED", True)
    monkeypatch.setenv("NEXUS_AGENT", "claude")
    assert server._agent_key(_ctx(ua="python-httpx/0.28")) is None
    assert server._who() is None
    monkeypatch.setattr(server, "_SHARED", False)  # serveur stdio : une seule IA, NEXUS_AGENT vaut
    assert server._agent_key(_ctx()) == "claude"
