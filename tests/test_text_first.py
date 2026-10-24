"""Texte d'abord (NexusHarness/shared/images.md § 2) : par défaut la liste seule ; l'image rejoint la liste quand un
effet n'a pas été relu, quand l'écran n'a aucun élément lisible, ou sur demande. adb simulé (test_guards)."""

from __future__ import annotations

from test_guards import node, phone, ui  # noqa: F401  (fixture phone)

from nexus_android import server
from nexus_android.server import Image, Step

A = node("Inbox", (0, 0, 500, 100), rid="com.app:id/inbox")
B = node("Archive", (0, 200, 500, 300), rid="com.app:id/archive")


def images(out: list) -> int:
    return sum(isinstance(part, Image) for part in out)


def test_look_by_default_sends_the_list_without_the_image(phone):  # noqa: F811
    phone.uis = [ui(A, B)]
    out = server._view("auto")
    assert images(out) == 0 and '"Inbox"' in out[0] and '"Archive"' in out[0]


def test_asking_for_the_image_still_gets_it(phone):  # noqa: F811
    phone.uis = [ui(A, B)]
    assert images(server._view("both")) == 1
    phone.uis = [ui(A, B)]
    assert images(server._view("auto", region=[0, 0, 200, 200])) == 1  # zoom : c'est une image qu'on demande


def test_a_screen_without_readable_element_joins_the_image(phone):  # noqa: F811
    phone.uis = [ui()]  # canevas, jeu, vidéo : l'arbre n'a rien
    out = server._view("auto")
    assert images(out) == 1


def test_a_screen_of_unnamed_buttons_joins_the_image(phone, monkeypatch):  # noqa: F811
    """Relecture Codex : un arbre non vide mais sans aucun libellé (appli dessinée, OCR absent) ne dit rien."""
    monkeypatch.setattr(server, "_ocr_elements", lambda screen, image: None)
    phone.uis = [ui(node("", (0, 0, 1080, 2000)))]
    out = server._view("auto")
    assert images(out) == 1


def test_the_status_bar_alone_does_not_count_as_readable(phone, monkeypatch):  # noqa: F811
    monkeypatch.setattr(server, "_ocr_elements", lambda screen, image: None)
    clock = node("10:42", (0, 0, 200, 40), package="com.android.systemui", clickable=False)
    phone.uis = [ui(clock, node("", (0, 40, 1080, 2000)))]  # jeu au premier plan (com.app), barre d'état lisible
    assert images(server._view("auto")) == 1


def test_an_unreadable_tree_follows_the_same_guard_as_both(phone):  # noqa: F811
    """Relecture Codex : arbre en erreur, l'image n'est envoyée qu'avec confirm=true (codes non masqués)."""
    phone.uis = [""]  # ERROR: null root node
    refused = server._view("auto")
    assert images(refused) == 0 and "look(confirm=true)" in refused[0]
    phone.uis = [""]
    agreed = server._view("auto", confirm=True)
    assert images(agreed) == 1 and "without code masking (confirm=true)" in agreed[0]


def test_an_unverified_effect_joins_the_image(phone, monkeypatch):  # noqa: F811
    phone.uis = [ui(A, B)]
    monkeypatch.setattr(server, "_step", lambda adb, screen, step: ("~", "sent, not read back"))
    assert images(server._act([Step(do="key", keys="back")], "auto")) == 1


def test_a_verified_effect_keeps_text_only(phone, monkeypatch):  # noqa: F811
    phone.uis = [ui(A, B)]
    monkeypatch.setattr(server, "_step", lambda adb, screen, step: ("✓", "done, read back"))
    out = server._act([Step(do="key", keys="back")], "auto")
    assert images(out) == 0 and '"Inbox"' in out[0]


def test_a_failed_step_joins_the_image(phone, monkeypatch):  # noqa: F811
    phone.uis = [ui(A, B)]

    def refused(adb, screen, step):
        raise server.PhoneError("target not found")

    monkeypatch.setattr(server, "_step", refused)
    assert images(server._act([Step(do="tap", target="Nowhere")], "auto")) == 1


def test_explicit_elements_never_join_the_image(phone, monkeypatch):  # noqa: F811
    phone.uis = [ui(A, B)]
    monkeypatch.setattr(server, "_step", lambda adb, screen, step: ("~", "sent, not read back"))
    assert images(server._act([Step(do="key", keys="back")], "elements")) == 0  # choix explicite de l'agent
