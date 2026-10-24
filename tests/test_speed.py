"""Rapidité sans perte de garde-fou : collage par le serveur déjà ouvert pour les gestes, écran ouvert sans image si
l'agent le demande, attente du calme de l'écran à la mesure d'UiAutomator. adb simulé, aucun vrai téléphone."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from test_guards import FakePhone, phone  # noqa: F401  (fixture phone)
from test_quick_wins import _resolving

from nexus_android import clip, screen, server
from nexus_android.adb import PhoneError
from nexus_android.screen import Element


class KeptChannel:
    """Canal du serveur des gestes : presse-papiers de l'utilisateur, collages reçus."""

    def __init__(self, before: str | None = "ancien", fail: bool = False) -> None:
        self.clipboard, self.fail, self.sets = before, fail, []

    def get(self) -> str | None:
        if self.fail:
            raise PhoneError("clipboard: the phone stopped answering")
        return self.clipboard

    def set(self, sequence: int, text: str, paste: bool) -> None:
        self.sets.append((text, paste))
        self.clipboard = text


def test_paste_uses_the_kept_channel_and_gives_the_clipboard_back(monkeypatch):
    monkeypatch.setattr(clip, "_session", lambda *a, **k: pytest.fail("serveur lancé pour rien"))
    monkeypatch.setattr(clip.time, "sleep", lambda s: None)
    kept = KeptChannel()
    assert clip.paste(object(), "café", kept) is True  # type: ignore[arg-type]
    assert kept.sets == [("café", True), ("ancien", False)]


class FakeControl:
    def __init__(self, channel: KeptChannel | None) -> None:
        self.kept, self.closed = channel, False

    def channel(self) -> KeptChannel | None:
        return self.kept

    def broken(self) -> None:
        self.closed = True


def test_a_failed_paste_closes_the_shared_channel(monkeypatch):
    control = FakeControl(KeptChannel(fail=True))
    monkeypatch.setattr(server, "_control", lambda adb: control)
    with pytest.raises(PhoneError, match="stopped answering"):
        server._paste(object(), "été")  # type: ignore[arg-type]
    assert control.closed  # rouvert au prochain usage, jamais réutilisé dans un état douteux


def test_paste_without_gesture_server_opens_its_own(monkeypatch):
    monkeypatch.setattr(server, "_control", lambda adb: FakeControl(None))
    calls = []
    monkeypatch.setattr(clip, "paste", lambda adb, text, kept=None: calls.append(kept) or True)
    assert server._paste(object(), "à") is True  # type: ignore[arg-type]
    assert calls == [None]


def test_launch_can_skip_the_image(phone: FakePhone, monkeypatch):  # noqa: F811
    _resolving(phone, monkeypatch, "priority=1 isDefault=true\ncom.android.settings/.Settings$WifiSettingsActivity\n")
    shown = []
    monkeypatch.setattr(server, "_view", lambda then, notes=None, **k: shown.append(then) or [*(notes or [])])
    server._launch(action="android.settings.WIFI_SETTINGS", then="elements")
    out = server._launch(action="android.settings.WIFI_SETTINGS", then="none")
    assert shown == ["elements"] and "Opened" in out[0]  # none : la note seule, aucune lecture d'écran
    server._launch(action="android.settings.WIFI_SETTINGS")
    assert shown[-1] == "both"  # par défaut : image et éléments, comme avant


def _el(text: str, bounds: tuple[int, int, int, int], path: tuple[tuple[int, int], ...], clickable: bool = True) -> Element:
    return Element(0, "View", text, "", "", "com.android.settings", bounds, clickable, path=path)


def test_a_row_half_hidden_by_a_floating_bar_is_touched_where_it_shows():
    """Vu sur le Fold : « À propos du téléphone » en bas de Réglages, à moitié sous la barre de recherche flottante ;
    son centre touchait la barre (recherche ouverte au lieu de la page)."""
    row = _el("À propos du téléphone", (0, 1600, 1000, 1760), ((0, 0), (1, 5)))
    bar = _el("Rechercher", (0, 1663, 1000, 1837), ((0, 0), (2, 0)))
    x, y = server._visible(row, [row, bar])
    assert 1600 <= y < 1663 and server._front_clickable(x, y, [row, bar]) is row
    hidden = _el("Caché", (0, 1700, 1000, 1800), ((0, 0), (1, 6)))
    with pytest.raises(PhoneError, match="no part of it free of \"Rechercher\""):
        server._visible(hidden, [hidden, bar])


def test_a_thin_visible_strip_is_found():
    """Relecture Codex : seule une bande de 11 px en haut reste visible ; elle est touchée, pas refusée."""
    button = _el("Envoyer", (0, 0, 100, 100), ((0, 0), (1, 0)))
    cover = _el("Fermer", (0, 11, 100, 100), ((0, 0), (2, 0)))
    x, y = server._visible(button, [button, cover])
    assert y < 11 and server._front_clickable(x, y, [button, cover]) is button


def test_draft_accord_is_bound_to_the_point_act_will_touch(monkeypatch):
    """Relecture Codex : le jeton préparé par draft vise le même point que act sur ce bouton (sa partie visible)."""
    button = _el("Envoyer", (0, 0, 100, 100), ((0, 0), (1, 0)))
    cover = _el("Fermer", (0, 40, 100, 100), ((0, 0), (2, 0)))
    fake = SimpleNamespace(last_state=SimpleNamespace(package="com.android.settings"))
    monkeypatch.setattr(server, "_fresh", lambda screen: [button, cover])
    monkeypatch.setattr(server, "_drawn", lambda *a: False)
    monkeypatch.setattr(server, "_height", lambda screen: 2000)
    monkeypatch.setattr(server.policy, "outward", lambda e: "Envoyer" if e is button else None)
    bound = []
    monkeypatch.setattr(server.policy, "screen_action", lambda *a: bound.append(a[-1]) or "act")
    monkeypatch.setattr(server.policy, "accord_propose", lambda act: "jeton")
    monkeypatch.setattr(server.policy.garde_fous, "describe", lambda act: "appui")
    monkeypatch.setattr(server.policy.ACCORDS, "warnings", lambda act: [])
    server._draft_accord(fake)  # type: ignore[arg-type]
    x, y = server._visible(button, [button, cover])
    assert bound == [f"at {x},{y}"] and y < 40


def test_a_paste_whose_clipboard_was_not_given_back_closes_the_shared_channel(monkeypatch):
    """Relecture Codex : réponse de remise perdue → flux douteux ; fermé, jamais réutilisé, collage pas rejoué."""
    control = FakeControl(KeptChannel())
    monkeypatch.setattr(server, "_control", lambda adb: control)
    monkeypatch.setattr(clip, "paste", lambda adb, text, kept=None: False)
    assert server._paste(object(), "été") is False  # type: ignore[arg-type]
    assert control.closed


def test_a_label_inside_its_row_is_aimed_at_its_center():
    row = _el("", (0, 100, 1000, 300), ((0, 0), (1, 0)))
    label = _el("Connexions", (100, 150, 600, 250), ((0, 0), (1, 0), (0, 0)), clickable=False)
    assert server._visible(label, [row, label]) == label.center


def test_settle_quiet_is_uiautomators_and_the_phone_accepts_it():
    assert screen.SETTLE_QUIET_MS == 500
    source = (Path(__file__).parents[1] / "phone" / "NexusQuery.java").read_text(encoding="utf-8")
    assert "QUIET_MS = 1_000" in source  # le lecteur accepte toute attente de 0 à 1000 ms
