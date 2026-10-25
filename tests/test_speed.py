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
    assert shown[-1] == "auto"  # par défaut : texte d'abord (image jointe seulement si rien n'est lisible)


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


# ---------------------------------------------------------------- 0.9.6 : attente de l'utilisateur, photos MMS


def test_owner_wait_is_checked_every_quarter_second(monkeypatch):
    # L'utilisateur touche encore le téléphone : relu toutes les 0,25 s (au plus), et l'agent repart dès les
    # BUSY_QUIET s de calme atteintes, sans attendre une seconde de plus.
    idles = iter([0.5, 1.0, 2.9, server.BUSY_QUIET])
    sleeps: list[float] = []
    monkeypatch.setattr(server.awake, "ENABLED", False)
    monkeypatch.setattr(server, "_LAST_INPUT", 0.0)
    monkeypatch.setattr(server.awake, "idle_on_phone", lambda adb: next(idles))
    monkeypatch.setattr(server.time, "sleep", sleeps.append)
    assert server._wait_for_owner(object(), SimpleNamespace(awake=True)) == ""  # type: ignore[arg-type]
    assert sleeps[:2] == [0.25, 0.25] and sleeps[2] == pytest.approx(server.BUSY_QUIET - 2.9)
    assert len(sleeps) == 3


def _photo(size: tuple[int, int], orientation: int = 1) -> bytes:
    import io

    from PIL import Image as PILImage

    picture = PILImage.new("RGB", size, (200, 30, 30))
    exif = PILImage.Exif()
    exif[0x0112] = orientation  # Orientation
    out = io.BytesIO()
    picture.save(out, "JPEG", exif=exif.tobytes())
    return out.getvalue()


class PhotoBook:
    """Conversation simulée : trois photos MMS, dont une absente de la lecture groupée et une illisible."""

    def __init__(self) -> None:
        from datetime import datetime

        from nexus_android.messages import Message

        self.message = Message(datetime(2026, 10, 24, 12, 0), False, "photos", "RCS/MMS",
                               attachments=[("11", "image/jpeg"), ("12", "image/jpeg"), ("13", "image/png"),
                                            ("14", "video/mp4")])
        self.batched: list[list[str]] = []
        self.alone: list[str] = []

    def thread(self, conversation: int, limit: int, before_ms: int | None = None):
        return ["Alice (0611223344)"], [self.message]

    def parts(self, ids: list[str]) -> dict[str, bytes]:
        self.batched.append(list(ids))
        return {"11": _photo((40, 20), orientation=6), "13": b"not an image"}

    def part(self, part_id: str) -> bytes:
        self.alone.append(part_id)
        return _photo((30, 30))

    def hidden(self, conversation: int) -> bool:
        return False


def test_mms_photos_are_read_together_then_alone_if_missing(monkeypatch):
    import io

    from PIL import Image as PILImage

    book = PhotoBook()
    monkeypatch.setattr(server, "ADB", object())
    monkeypatch.setattr(server, "SCREEN", object())
    monkeypatch.setattr(server, "MESSAGES", book)
    out = server._messages(7, None, 15, 4)
    assert book.batched == [["11", "12", "13"]]  # images seulement, en une lecture groupée
    assert book.alone == ["12"]  # seule la photo absente de la lecture groupée est relue à part
    text = out[0]
    assert "[image 1]" in text and "[image 2]" in text and "[unreadable image]" in text
    assert "[video not shown]" in text or "not shown]" in text
    pictures = [PILImage.open(io.BytesIO(block.data)) for block in out[1:]]
    assert len(pictures) == 2
    # Orientation EXIF appliquée : la photo 40×20 marquée « tournée de 90° » arrive debout.
    assert any(p.height > p.width for p in pictures)
