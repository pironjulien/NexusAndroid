"""Lecture visuelle (OCR) quand l'arbre n'a aucun texte : lignes listées par look, cible trouvée par son texte, garde-
fous appliqués au texte vu (envoi, paiement), codes masqués ; moteur OCR simulé, sortie JSON du script analysée."""

from __future__ import annotations

import pytest
from test_guards import FakePhone, act, node, ui

from nexus_android import ocr, server
from nexus_android.screen import Screen
from nexus_android.server import Step


@pytest.fixture
def phone(monkeypatch):
    import time
    fake = FakePhone()
    scr = Screen(fake)  # type: ignore[arg-type]
    scr.size = (1080, 2000)
    monkeypatch.setattr(server, "ADB", fake)
    monkeypatch.setattr(server, "SCREEN", scr)
    monkeypatch.setattr(server, "MESSAGES", object())
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return fake

LINES = [ocr.Line("JOUER", (400, 900, 680, 980)), ocr.Line("Acheter 500 gemmes", (300, 1500, 780, 1560)),
         ocr.Line("Votre code : 482913", (100, 200, 700, 260))]


def fake_ocr(monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "read", lambda image, factor=1.0: list(LINES))


def test_parse_script_output():
    lines = ocr.parse('[{"text":"Bonjour","box":[10,20,110,40]},{"text":" ","box":[0,0,1,1]}]', 2.0)
    assert lines == [ocr.Line("Bonjour", (20, 40, 220, 80))] and lines[0].center == (120, 60)
    assert ocr.parse('{"text":"Seul","box":[0,0,4,4]}', 1.0)[0].text == "Seul"  # une seule ligne : objet, pas liste


def test_look_lists_ocr_text_when_the_tree_is_empty(phone, monkeypatch):
    fake_ocr(monkeypatch)
    phone.uis = [ui(node("", (0, 0, 1080, 2000), cls="android.view.SurfaceView", clickable=False))]
    text = server._view("elements")[0]
    assert "text seen on the image (OCR" in text and '· "JOUER" @' in text
    assert "482913" not in text  # code masqué comme dans l'arbre


def test_tap_by_text_found_on_the_image_with_guardrails(phone, monkeypatch):
    fake_ocr(monkeypatch)
    phone.uis = [ui(node("", (0, 0, 1080, 2000), cls="android.view.SurfaceView", clickable=False))]
    assert act(Step(do="tap", target="jouer")).startswith('~ 1. tap "JOUER"')
    assert ("input", "tap", "540", "940") in phone.inputs
    phone.calls.clear()
    report = act(Step(do="tap", target="Acheter 500 gemmes"))
    assert report.startswith("✗") and "irreversible" in report and phone.inputs == []


def test_not_found_says_the_image_was_searched(phone, monkeypatch):
    fake_ocr(monkeypatch)
    phone.uis = [ui(node("", (0, 0, 1080, 2000), clickable=False))]
    assert "not found on screen (nor on the image, by OCR)" in act(Step(do="tap", target="Quitter"))


def test_closest_tolerates_ocr_noise_but_not_other_words():
    """Vu sur le Fold : « Champ-de-Mars » lu « *dhamp7de-Mars » sur la vue satellite de Maps."""
    from types import SimpleNamespace
    seen = [SimpleNamespace(text=t) for t in ("*dhamp7de-Mars", "Rue Saint-Domifiique", "Tasca")]
    assert getattr(ocr.closest("Champ-de-Mars", seen), "text", None) == "*dhamp7de-Mars"
    assert getattr(ocr.closest("Rue Saint-Dominique", seen), "text", None) == "Rue Saint-Domifiique"
    assert ocr.closest("Tour Montparnasse", seen) is None and ocr.closest("ab", seen) is None


@pytest.mark.parametrize("package,seen_text,target,confirm", [
    ("com.whatsapp", "Envover", "Envoyer", False),
    ("com.whatsapp", "Envover", "Envoyer", True),  # confirm sans accord
    ("com.game", "Paver", "Payer", False),
    ("com.revolut.revolut", "Paver", "Payer", True),  # paiement dans une appli protégée : jamais
])
def test_approximate_match_keeps_the_guardrails(phone, monkeypatch, package, seen_text, target, confirm):
    """Relecture de Codex : « Envover » rapproché d'« Envoyer » n'était jugé que sur le texte bruité."""
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "read", lambda image, factor=1.0: [ocr.Line(seen_text, (300, 1500, 780, 1560))])
    phone.package = package
    phone.uis = [ui(node("", (0, 0, 1080, 2000), cls="android.view.SurfaceView", clickable=False, package=package))]
    report = act(Step(do="tap", target=target, confirm=confirm))
    assert report.startswith("✗") and phone.inputs == [], report


def test_approximate_match_still_taps_a_place_name(phone, monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "read", lambda image, factor=1.0: [ocr.Line("*dhamp7de-Mars", (424, 924, 752, 979))])
    phone.uis = [ui(node("", (0, 0, 1080, 2000), cls="android.view.SurfaceView", clickable=False))]
    assert act(Step(do="tap", target="Champ-de-Mars")).startswith('~ 1. tap "*dhamp7de-Mars"')
    assert ("input", "tap", "588", "951") in phone.inputs
