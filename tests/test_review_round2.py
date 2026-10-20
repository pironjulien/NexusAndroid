"""Contre-relecture de la 0.9.1 par Codex (« Accord : non », 8 trouvailles) : chaque trouvaille reproduite devient
un test qui échoue sur la 0.9.1 et passe après correction. Téléphone simulé, aucun geste réel."""

from __future__ import annotations

import io
import time

import pytest
from PIL import Image as PILImage
from test_capture import inbox, rec  # noqa: F401  (fixture rec)
from test_extras import home  # noqa: F401  (fixture home)
from test_guards import accorded, act, field, node, phone, ui  # noqa: F401  (fixture phone)
from test_review_final import _code_on_a_surface

from nexus_android import capture, ocr, server
from nexus_android.adb import PhoneError
from nexus_android.messages import CARD, IBAN, mask_codes
from nexus_android.server import Step

SMS = "com.google.android.apps.messaging"


# ---------------------------------------------------------------- 1. accord lié au contenu brut des textes masqués


@pytest.mark.parametrize("first,second", [
    ("FR76 3000 6000 0112 3456 7890 189", "GB82 WEST 1234 5698 7654 32"),
    ("4111 1111 1111 1111", "5555 5555 5555 4444"),
])
def test_accord_tells_two_masked_numbers_apart(phone, first, second):  # noqa: F811
    def draft(number: str) -> None:
        phone.uis = [ui(field(f"Mon RIB : {number}", package=SMS),
                        node("Envoyer", (900, 1700, 1080, 1800), package=SMS))]
    phone.package = SMS
    draft(first)
    request = act(Step(do="tap", target="Envoyer", confirm=True))
    assert first not in request and first.split()[1] not in request  # jamais révélé dans le résumé
    draft(second)
    report = act(Step(do="tap", target="Envoyer", confirm=True, accord=accorded(request)))
    assert report.startswith("✗") and phone.inputs == []
    draft(first)  # même contenu qu'à l'accord : accepté
    assert act(Step(do="tap", target="Envoyer", confirm=True, accord=accorded(request))).startswith("✗")  # jeton usé
    request = act(Step(do="tap", target="Envoyer", confirm=True))
    assert act(Step(do="tap", target="Envoyer", confirm=True, accord=accorded(request))).startswith("~")


# ---------------------------------------------------------------- 2. focus relu après chaque touche qui le déplace


def test_key_after_tab_is_judged_on_the_new_focus(phone):  # noqa: F811
    phone.uis = [ui(field("alice")), ui(field(password=True))]  # tab amène le focus sur le mot de passe
    report = act(Step(do="key", keys="tab delete"))
    assert "password field" in report and phone.inputs == [("input", "keyevent", "KEYCODE_TAB")]


def test_tab_then_a_typing_key_stops_at_a_password(phone):  # noqa: F811
    phone.uis = [ui(field("alice"))]
    assert "alone in its step" in act(Step(do="key", keys="tab KEYCODE_5", confirm=True))
    phone.uis = [ui(field("alice")), ui(field(password=True))]
    report = act(Step(do="key", keys="tab ctrl+a"))
    assert "password field" in report and phone.inputs == [("input", "keyevent", "KEYCODE_TAB")]


# ---------------------------------------------------------------- 3. glissé : départ et arrivée sur le même écran


def test_swipe_judged_on_one_reading_and_bound_to_its_conversation(phone):  # noqa: F811
    def screen(who: str) -> str:
        return ui(node(who, (0, 50, 1080, 150), clickable=False, package=SMS),
                  node("Glisser pour envoyer", (0, 900, 1080, 1100), cls="android.widget.SeekBar", clickable=False,
                       package=SMS))
    phone.package = SMS
    phone.uis = [screen("Alice")]
    swipe = Step(do="swipe", target="60,1000", to="1040,1000", confirm=True)
    token = accorded(act(swipe))
    phone.uis = [screen("Bob")]  # autre conversation au moment du geste : refusé
    assert act(swipe.model_copy(update={"accord": token})).startswith("✗") and phone.inputs == []
    phone.uis = [screen("Alice"), screen("Bob")]  # une seule lecture : départ et arrivée jugés sur Alice
    token = accorded(act(swipe))
    phone.uis = [screen("Alice"), screen("Bob")]
    assert act(swipe.model_copy(update={"accord": token})).startswith("~")


# ---------------------------------------------------------------- 4, 5. OCR : surfaces et échec de l'OCR


def _red_at_code(data: bytes, scale: float) -> bool:
    image = PILImage.open(io.BytesIO(data)).convert("RGB")
    pixel = image.getpixel((round(400 * scale), round(230 * scale)))
    return max(pixel) > 150 and pixel[1] < 100  # type: ignore[index, arg-type]


def test_label_on_a_drawn_surface_does_not_skip_the_ocr_of_a_saved_screenshot(rec, home, monkeypatch):  # noqa: F811
    _code_on_a_surface(rec, monkeypatch)
    tree = ui(node("", (0, 0, 1080, 1500), cls="android.view.TextureView", clickable=False),
              node("Fermer", (900, 1300, 1060, 1400)))  # libellé posé sur la surface
    rec.uis = [tree, tree]
    capture.screenshot(rec, "film", None, False)  # type: ignore[arg-type]
    image = PILImage.open(home / "Downloads" / "Telephone" / "film.png").convert("RGB")
    assert max(image.getpixel((400, 230))) < 60  # type: ignore[arg-type]


def test_screenshot_keeps_the_surfaces_of_both_readings(rec, home, monkeypatch):  # noqa: F811
    _code_on_a_surface(rec, monkeypatch)
    label = node("Lecteur", (0, 1600, 1080, 1700), clickable=False)
    surface = node("", (0, 0, 1080, 1500), cls="android.view.SurfaceView", clickable=False)
    rec.uis = [ui(label, surface), ui(label)]  # surface vue à la première lecture seulement
    capture.screenshot(rec, "jeu", None, False)  # type: ignore[arg-type]
    image = PILImage.open(home / "Downloads" / "Telephone" / "jeu.png").convert("RGB")
    assert max(image.getpixel((400, 230))) < 60  # type: ignore[arg-type]


def _failing_ocr(monkeypatch) -> None:
    def broken(image, factor=1.0):
        raise ocr.OcrError("engine crashed")
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "read", broken)


def test_failed_ocr_still_returns_the_look_image_with_a_warning(phone, monkeypatch):  # noqa: F811
    _code_on_a_surface(phone, monkeypatch)
    _failing_ocr(monkeypatch)
    out = server._view("both")
    assert len(out) == 2 and "not masked on this image (OCR unavailable)" in out[0]
    monkeypatch.setattr(ocr, "available", lambda: False)  # moteur absent (autre système) : même règle
    assert len(server._view("image")) == 2


def test_failed_ocr_refuses_the_screenshot_unless_unmasked(rec, home, monkeypatch):  # noqa: F811
    _code_on_a_surface(rec, monkeypatch)
    _failing_ocr(monkeypatch)
    with pytest.raises(PhoneError, match="could not be read by OCR"):
        capture.screenshot(rec, "jeu", None, False)  # type: ignore[arg-type]
    assert inbox(home) == []
    assert "without masking of the drawn text" in capture.screenshot(rec, "jeu", None, False, unmasked=True)  # type: ignore[arg-type]


# ---------------------------------------------------------------- 6, 7. vidéo : surveillance


def test_unreadable_foreground_does_not_count_as_watched(rec, home):  # noqa: F811
    capture.start(rec, None, "bug", None, False)  # type: ignore[arg-type]
    recording = capture._CURRENT
    assert recording is not None
    recording.last_seen -= 60
    before = recording.last_seen
    rec.package = ""  # écran allumé, premier plan illisible
    capture._observe(recording)
    assert recording.last_seen == before
    with pytest.raises(capture.Discarded, match="could not be checked"):
        capture.stop(rec)  # type: ignore[arg-type]


def test_retried_collection_does_not_erase_a_sound_video(rec, home, monkeypatch):  # noqa: F811
    capture.start(rec, 1, "bug", None, False)  # type: ignore[arg-type]
    rec.procs[0].code = 0  # fini seul, avant que la surveillance ne le voie
    rec.pull_fails = True
    with pytest.raises(PhoneError, match="not copied"):
        capture.stop(rec)  # type: ignore[arg-type]
    real = time.monotonic
    monkeypatch.setattr(capture.time, "monotonic", lambda: real() + 30)  # stop retenté 30 s plus tard
    rec.pull_fails = False
    assert "Screen recording saved on the PC" in capture.stop(rec)  # type: ignore[arg-type]
    assert len(inbox(home)) == 1


# ---------------------------------------------------------------- 8. bornes des cartes et IBAN


def test_card_before_a_full_stop_and_iban_before_a_word_are_masked():
    assert mask_codes("Carte 4111 1111 1111 1111.") == f"Carte {CARD}."
    assert mask_codes("DE89 3704 0044 0532 0130 00 TEST") == f"{IBAN} TEST"
    assert mask_codes("version 4111.1111111111111") == "version 4111.1111111111111"  # nombre décimal : pas une carte
