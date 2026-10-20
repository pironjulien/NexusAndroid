"""Quatrième relecture (Codex) : une injection sortante = une seule injection, juste après sa propre relecture comparée
à l'identité approuvée, qui inclut la CIBLE du geste ; rien n'est lié de ce que l'arbre ne décrit pas (OCR) ; captures
et vidéos jugées sur toutes leurs lectures. Chaque cas échoue sans la correction. Téléphone simulé, aucun geste réel."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from PIL import Image as PILImage
from test_capture import BANK, inbox, rec  # noqa: F401  (fixture rec)
from test_extras import home  # noqa: F401  (fixture home)
from test_guards import accorded, act, field, node, phone, ui  # noqa: F401  (fixture phone)
from test_review_final import _code_on_a_surface

from nexus_android import capture, ocr, server
from nexus_android import screen as screen_mod
from nexus_android.adb import PhoneError
from nexus_android.messages import CARD, IBAN, mask_codes
from nexus_android.server import Step

SMS = "com.google.android.apps.messaging"


def _wait(condition, seconds: float = 3.0) -> bool:
    pause, end = threading.Event(), time.monotonic() + seconds
    while time.monotonic() < end and not condition():
        pause.wait(0.01)
    return condition()


# ---------------------------------------------------------------- 1. une injection sortante, une relecture


def test_no_enter_hidden_in_typed_text(phone, monkeypatch):  # noqa: F811
    monkeypatch.setattr(server.clip, "paste", lambda adb, text: True)
    phone.package = SMS
    phone.uis = [ui(node("Alice", (0, 50, 1080, 150), clickable=False, package=SMS), field("", package=SMS))]
    act(Step(do="type", text="Salut\n"))
    assert all("KEYCODE_ENTER" not in c for c in phone.inputs)  # collé, jamais envoyé par Entrée


def test_no_double_tap_on_an_outward_button(phone, monkeypatch):  # noqa: F811
    phone.package = SMS
    phone.uis = [ui(node("Envoyer", (900, 1700, 1080, 1800), package=SMS))]
    report = act(Step(do="double_tap", target="Envoyer", confirm=True, accord="deadbeef"))
    assert "no double tap" in report and phone.inputs == []
    # Rien de sortant, relecture rapide réussie dans le délai : vrai double appui.
    photo = {"text": "Photo", "bounds": "[0,300][500,700]", "clickable": "true", "package": SMS,
             "class": "android.widget.Button"}
    quick = screen_mod.Snapshot(screen_mod.State(True, False, SMS), "", (1080, 2000), [photo])
    monkeypatch.setattr(server.SCREEN, "_server", lambda: SimpleNamespace(release=lambda: None))
    monkeypatch.setattr(server.SCREEN, "snapshot", lambda *a, **k: quick if k.get("settle") is False else None)
    phone.uis = [ui(node("Photo", (0, 300, 500, 700), package=SMS))]
    assert act(Step(do="double_tap", target="Photo")).startswith("~") and len(phone.inputs) == 2


# ---------------------------------------------------------------- 2. la cible effective fait partie de l'action


def _two_buttons() -> str:
    return ui(node("Envoyer", (150, 500, 350, 650), rid="com.app:id/send_one", package=SMS),
              node("Envoyer", (650, 500, 850, 650), rid="com.app:id/send_two", package=SMS))


def test_accord_for_one_button_does_not_tap_the_other(phone):  # noqa: F811
    phone.package = SMS
    phone.uis = [_two_buttons()]
    token = accorded(act(Step(do="tap", target="250,575", confirm=True)))
    report = act(Step(do="tap", target="750,575", confirm=True, accord=token))
    assert report.startswith("✗") and "target" in report and phone.inputs == []


def test_accord_for_a_swipe_binds_its_end_point(phone):  # noqa: F811
    phone.package = SMS
    phone.uis = [ui(node("Glisser pour envoyer", (0, 900, 1080, 1100), cls="android.widget.SeekBar", clickable=False,
                         package=SMS))]
    token = accorded(act(Step(do="swipe", target="60,1000", to="500,1000", confirm=True)))
    report = act(Step(do="swipe", target="60,1000", to="1040,1000", confirm=True, accord=token))
    assert report.startswith("✗") and phone.inputs == []


def test_accord_for_enter_binds_the_focused_field(phone):  # noqa: F811
    def form(focus_first: bool) -> str:
        return ui(field("pour Alice", focused=focus_first, package=SMS, bounds=(0, 300, 1080, 400)),
                  field("pour Bob", focused=not focus_first, package=SMS, bounds=(0, 500, 1080, 600)))
    phone.package = SMS
    phone.uis = [form(True)]
    token = accorded(act(Step(do="key", keys="enter", confirm=True)))
    phone.uis = [form(False)]  # même écran, focus permuté
    report = act(Step(do="key", keys="enter", confirm=True, accord=token))
    assert report.startswith("✗") and phone.inputs == []


def _stacked(first: str, second: str) -> str:
    """Deux boutons superposés : seul l'ordre de dessin dit lequel reçoit l'appui."""
    def button(rid: str, order: int) -> str:
        return (f'<node text="Envoyer" resource-id="com.app:id/{rid}" class="android.widget.Button" package="{SMS}" '
                f'content-desc="" clickable="true" focused="false" password="false" drawing-order="{order}" '
                'bounds="[600,1600][1080,1800]" />')
    return ui(button("send_a", 1 if first == "a" else 2), button("send_b", 1 if second == "a" else 2))


def test_accord_binds_the_drawing_order_of_stacked_buttons(phone):  # noqa: F811
    phone.package = SMS
    phone.uis = [_stacked("a", "b")]
    token = accorded(act(Step(do="tap", target="840,1700", confirm=True)))
    phone.uis = [_stacked("b", "a")]
    report = act(Step(do="tap", target="840,1700", confirm=True, accord=token))
    assert report.startswith("✗") and phone.inputs == []


# ---------------------------------------------------------------- 3. rien n'est lié de ce que l'arbre ne décrit pas


def test_no_accord_when_the_action_depends_on_drawn_text(phone, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "read", lambda image, factor=1.0: [ocr.Line("Alice", (0, 50, 500, 150)),
                                                               ocr.Line("Glisser pour envoyer", (0, 950, 1080, 1050))])
    phone.uis = [ui(node("", (0, 0, 1080, 2000), cls="android.view.SurfaceView", clickable=False))]
    report = act(Step(do="swipe", target="Glisser pour envoyer", to="1040,1000", confirm=True))
    assert "drawn outside the UI tree" in report and 'accord="' not in report and phone.inputs == []
    # Bouton décrit par l'arbre, mais destinataire dessiné sur une grande surface sans texte : pas lié non plus.
    phone.uis = [ui(node("", (0, 0, 1080, 1500), cls="android.view.SurfaceView", clickable=False),
                    node("Envoyer", (900, 1700, 1080, 1800)))]
    report = act(Step(do="tap", target="Envoyer", confirm=True))
    assert "drawn outside the UI tree" in report and phone.inputs == []


# ---------------------------------------------------------------- 4, 6. capture : toutes les lectures comptent


def test_screenshot_ocr_when_the_second_tree_needs_it(rec, home, monkeypatch):  # noqa: F811
    _code_on_a_surface(rec, monkeypatch)
    rec.uis = [ui(node("Lecteur", (0, 1600, 1080, 1700), clickable=False)), ui()]  # 2e arbre devenu vide
    capture.screenshot(rec, "jeu", None, False)  # type: ignore[arg-type]
    image = PILImage.open(home / "Downloads" / "Telephone" / "jeu.png").convert("RGB")
    assert max(image.getpixel((400, 230))) < 60  # type: ignore[arg-type]


def test_screenshot_refused_when_a_bank_shows_up_during_the_capture(rec, home):  # noqa: F811
    rec.uis = [ui(node("Accueil", (0, 100, 500, 200))), ui(node("Solde", (0, 100, 500, 200), package=BANK))]
    with pytest.raises(PhoneError, match="banking, payment or identity app shown during the capture"):
        capture.screenshot(rec, "x", None, False)  # type: ignore[arg-type]
    original, dumps = rec.run, []

    def run(*argv: str, timeout: float = 30.0):
        out = original(*argv, timeout=timeout)
        if argv[0] == "sh" and "uiautomator dump" in argv[2]:
            dumps.append(1)
            rec.package = BANK  # premier plan relu avec le second arbre : la banque
        return out
    rec.run = run  # type: ignore[method-assign]
    rec.uis = [ui(node("Accueil", (0, 100, 500, 200)))]
    with pytest.raises(PhoneError, match="shown during the capture"):
        capture.screenshot(rec, "y", None, False)  # type: ignore[arg-type]
    assert inbox(home) == []


# ---------------------------------------------------------------- 7, 8. vidéo : preuves et fin


def test_partial_reading_naming_a_bank_still_taints_the_video(rec, home):  # noqa: F811
    capture.start(rec, None, "bug", None, False)  # type: ignore[arg-type]
    recording = capture._CURRENT
    assert recording is not None
    rec.state_text = lambda: f"  mCurrentFocus=Window{{abc u0 {BANK}/x.Main}}"  # type: ignore[method-assign]
    capture._observe(recording)  # sortie tronquée : pas de mWakefulness
    assert recording.tainted


def test_observation_after_the_end_does_not_taint_a_sound_video(rec, home, monkeypatch):  # noqa: F811
    monkeypatch.setattr(capture, "WATCH_EVERY", 0.01)
    capture.start(rec, 1, "bug", None, False)  # type: ignore[arg-type]
    recording = capture._CURRENT
    assert recording is not None
    rec.procs[0].code = 0  # fini seul
    rec.package = BANK  # ouvert APRÈS la fin : pas dans la vidéo
    capture._observe(recording)
    assert _wait(lambda: recording.ended is not None)
    assert not recording.tainted
    assert "Screen recording saved on the PC" in capture.stop(rec)  # type: ignore[arg-type]


# ---------------------------------------------------------------- 5. masquage : union de tous les débuts valides


@pytest.mark.parametrize("text,masked", [
    ("CH93 0076 2011 6238 5295 7 FR76 3000 6000 0112 3456 7890 189", f"{IBAN} {IBAN}"),  # longueur du pays
    ("3782 822463 10005 3566 0020 2036 0505", f"{CARD} {CARD}"),
    ("Cartes 3782 822463 10005 et 3566 0020 2036 0505.", f"Cartes {CARD} et {CARD}."),
])
def test_every_valid_start_is_masked(text, masked):
    out = mask_codes(text)
    assert out == masked and not any(c.isdigit() for c in out)  # Amex 4-6-5 puis JCB 4-4-4-4
