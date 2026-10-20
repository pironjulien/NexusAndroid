"""Routines partagées : enregistrement par repères stables (id:, texte, desc:), refus des cibles sans nom, paramètres,
rejeu avec les garde-fous, oubli ; avec le faux téléphone des garde-fous."""

from __future__ import annotations

import asyncio
import json

import pytest
from test_guards import FakePhone, node, ui

from nexus_android import routines, server
from nexus_android.screen import Screen
from nexus_android.server import Step


@pytest.fixture
def phone(monkeypatch, tmp_path):
    import time
    fake = FakePhone()
    scr = Screen(fake)  # type: ignore[arg-type]
    scr.size = (1080, 2000)
    monkeypatch.setattr(server, "ADB", fake)
    monkeypatch.setattr(server, "SCREEN", scr)
    monkeypatch.setattr(server, "MESSAGES", object())
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(routines, "ROUTINES", tmp_path / "routines.json")
    return fake


SEARCH = node("", (0, 100, 1080, 200), rid="com.app:id/search", cls="android.widget.EditText", focused=True)
GO = node("", (900, 100, 1080, 200), rid="com.app:id/go", desc="Lancer la recherche")


def act(**kw) -> str:
    return asyncio.run(server.act(**kw))[0]


def test_saved_with_stable_targets_and_replayed(phone):
    phone.uis = [ui(SEARCH, GO, node("Wi-Fi", (0, 300, 1080, 400)))]
    report = act(steps=[Step(do="tap", target="300,150"), Step(do="tap", target="Wi-Fi"),
                        Step(do="tap", target="desc:Lancer la recherche")], then="none", remember="wifi rapide")
    assert 'Routine "wifi rapide" saved (3 steps, com.app)' in report
    saved = json.loads(routines.ROUTINES.read_text(encoding="utf-8"))["wifi rapide"]
    assert [s["target"] for s in saved["steps"]] == ["id:search", "Wi-Fi", "id:go"]  # id: avant tout, jamais x,y
    assert all("confirm" not in s and "accord" not in s for s in saved["steps"])
    phone.calls.clear()
    replay = act(routine="wifi rapide", then="none")
    assert replay.startswith('Routine "wifi rapide" (3 steps') and replay.count("~") == 3
    assert [c[:2] for c in phone.inputs] == [("input", "tap")] * 3


def test_unnamed_target_is_not_saved(phone):
    phone.uis = [ui(node("", (0, 0, 500, 500)), node("", (500, 0, 1080, 500)))]
    report = act(steps=[Step(do="tap", target="100,100")], then="none", remember="flou")
    assert "not saved: step 1 aims at something without a stable name" in report
    assert not routines.ROUTINES.exists()


def test_failed_batch_is_not_saved(phone):
    phone.uis = [ui(node("OK", (0, 0, 500, 500)))]
    report = act(steps=[Step(do="tap", target="OK"), Step(do="tap", target="Absent")], then="none", remember="x")
    assert "✗ 2." in report and 'Routine "x" not saved: the batch did not go through every step' in report


def test_parameters_and_forget(phone):
    phone.uis = [ui(SEARCH), ui(node("wifi", (0, 100, 1080, 200), rid="com.app:id/search",
                                     cls="android.widget.EditText", focused=True))]
    report = act(steps=[Step(do="type", target="id:search", text="{{q}}")], then="none", remember="chercher",
                 params={"q": "wifi"})
    assert "saved" in report and ("input", "text", "wifi") in phone.inputs  # tapé avec la valeur
    assert json.loads(routines.ROUTINES.read_text(encoding="utf-8"))["chercher"]["steps"][0]["text"] == "{{q}}"
    with pytest.raises(Exception, match="parameters missing: q"):
        act(routine="chercher")
    with pytest.raises(Exception, match="made only of parameters"):
        routines.remember("perso", [{"do": "type", "text": "Salut {{nom}}"}], "com.app")
    assert act(steps=[], remember="chercher") == 'Routine "chercher" forgotten.'
    with pytest.raises(Exception, match='no routine named "chercher"'):
        act(routine="chercher")


def test_replay_keeps_the_guardrails(phone):
    phone.uis = [ui(node("Envoyer", (0, 0, 500, 200), rid="com.app:id/send"))]
    routines.remember("envoi", [{"do": "tap", "target": "id:send"}], "com.app")
    report = act(routine="envoi", then="none")
    assert "✗ 1." in report and phone.inputs == []  # « Envoyer » exige confirm et accord, jamais enregistrés


def test_look_lists_the_routines_of_the_app(phone):
    routines.remember("ouvrir", [{"do": "tap", "target": "id:go"}], "com.app")
    routines.remember("ailleurs", [{"do": "tap", "target": "id:x"}], "com.other")
    phone.uis = [ui(GO)]
    text = server._view("elements")[0]
    assert "Routines for this app: ouvrir (act(routine=…))" in text and "ailleurs" not in text


def test_launch_already_open_closes_the_pages_above(phone):
    answers = iter([(0, "Warning: Activity not started, its current task has been brought to the front", ""),
                    (0, "Starting: Intent", "")])
    original = phone.run

    def run(*argv, timeout=30.0):
        if argv[0] == "am":
            phone.calls.append(argv)
            return next(answers)
        if argv[:2] == ("cmd", "package"):
            return 0, "com.android.settings/.Settings\n", ""
        return original(*argv, timeout=timeout)

    phone.run = run  # type: ignore[method-assign]
    phone.package = "com.android.settings"
    text = server._launch(action="android.settings.SETTINGS")[0]
    starts = [c for c in phone.calls if c[0] == "am"]
    assert len(starts) == 2 and "--activity-clear-top" in starts[1]
    assert "pages above the asked screen were closed" in text


def test_replay_only_in_its_app_and_never_with_saved_confirm(phone):
    """Relecture d'Antigravity : rejouée sur une autre appli ; confirm glissé dans le fichier à la main."""
    routines.remember("ouvrir", [{"do": "tap", "target": "id:go"}], "com.app")
    phone.package = "com.whatsapp"
    with pytest.raises(Exception, match="recorded in com.app, but com.whatsapp is in the foreground"):
        act(routine="ouvrir")
    phone.package = "com.app"
    data = json.loads(routines.ROUTINES.read_text(encoding="utf-8"))
    data["envoi"] = {"package": "com.app", "steps": [{"do": "tap", "target": "id:send", "confirm": True,
                                                      "accord": "deadbeef"}]}
    routines.ROUTINES.write_text(json.dumps(data), encoding="utf-8")
    phone.uis = [ui(node("Envoyer", (0, 0, 500, 200), rid="com.app:id/send"))]
    report = act(routine="envoi", then="none")
    assert "✗ 1." in report and phone.inputs == []  # confirm et accord du fichier ignorés


def test_unread_field_stops_the_batch_before_a_send(phone):
    phone.uis = [ui(SEARCH), ui(node("Envoyer", (0, 300, 500, 400)))]  # champ disparu après la saisie
    report = act(steps=[Step(do="type", text="salut"), Step(do="tap", target="Envoyer")], then="none")
    assert "could not be read back" in report and "2." not in report
