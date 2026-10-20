"""Troisième relecture (Codex, sur la copie de travail après la 0.9.1) : une seule identité canonique de l'écran
approuvé, revérifiée avant chaque injection sortante, et masquage des cartes et IBAN par balayage de candidats. Chaque
cas reproduit échoue sans la correction et passe avec. Téléphone simulé, aucun geste réel."""

from __future__ import annotations

import pytest
from PIL import Image as PILImage
from test_capture import rec  # noqa: F401  (fixture rec)
from test_extras import home  # noqa: F401  (fixture home)
from test_guards import accorded, act, field, node, phone, ui  # noqa: F401  (fixture phone)
from test_review_final import _code_on_a_surface

from nexus_android import capture, ocr, screen
from nexus_android.messages import CARD, IBAN, mask_codes
from nexus_android.server import Step

WHATSAPP = "com.whatsapp"
SMS = "com.google.android.apps.messaging"


def slider(package: str = "com.app") -> str:
    return node("Glisser pour envoyer", (0, 900, 1080, 1100), cls="android.widget.SeekBar", clickable=False,
                package=package)


# ---------------------------------------------------------------- 1. Enter après Tab : identité revérifiée


def test_enter_after_tab_is_its_own_step_on_its_own_reading(phone):  # noqa: F811
    def chat(who: str) -> str:
        return ui(node(who, (0, 50, 1080, 150), clickable=False, package=WHATSAPP),
                  field("Salut", package=WHATSAPP))
    phone.package = WHATSAPP
    phone.uis = [chat("Alice")]
    assert "alone in its step" in act(Step(do="key", keys="tab enter", confirm=True))
    token = accorded(act(Step(do="key", keys="enter", confirm=True)))
    phone.uis = [chat("Bob")]  # brouillon passé à Bob avant l'Entrée : refusé
    assert act(Step(do="key", keys="enter", confirm=True, accord=token)).startswith("✗")
    assert phone.inputs == []


# ---------------------------------------------------------------- 2, 3. glissé : identité des deux bouts


def test_swipe_refused_when_two_fields_swap_their_ibans(phone):  # noqa: F811
    a, b = "FR76 3000 6000 0112 3456 7890 189", "GB82 WEST 1234 5698 7654 32"

    def form(first: str, second: str) -> str:
        return ui(field(first, package=SMS, bounds=(0, 300, 1080, 400)),
                  field(second, focused=False, package=SMS, bounds=(0, 500, 1080, 600)),
                  slider(SMS))
    phone.package = SMS
    phone.uis = [form(a, b)]
    swipe = Step(do="swipe", target="60,1000", to="1040,1000", confirm=True)
    token = accorded(act(swipe))
    phone.uis = [form(b, a)]  # les deux champs ont échangé leurs valeurs depuis l'accord
    report = act(swipe.model_copy(update={"accord": token}))
    assert report.startswith("✗") and phone.inputs == []


def test_swipe_refused_when_the_foreground_app_changes(phone):  # noqa: F811
    phone.package = SMS
    phone.uis = [ui(slider())]
    swipe = Step(do="swipe", target="60,1000", to="1040,1000", confirm=True)
    token = accorded(act(swipe))
    phone.package = "org.telegram.messenger"  # mêmes textes et positions, autre appli au premier plan
    report = act(swipe.model_copy(update={"accord": token}))
    assert report.startswith("✗") and phone.inputs == []


# ---------------------------------------------------------------- 8. cible visée par OCR : même identité


def test_drag_aimed_by_ocr_on_a_stable_screen_goes_through(phone, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "read", lambda image, factor=1.0: [ocr.Line("Deplacer", (100, 900, 400, 980))])
    phone.uis = [ui(node("", (0, 0, 1080, 2000), cls="android.view.SurfaceView", clickable=False))]
    report = act(Step(do="drag", target="Deplacer", to="800,1000", confirm=True))
    assert report.startswith("~"), report
    assert phone.inputs and phone.inputs[-1][1] == "draganddrop"


# ---------------------------------------------------------------- 4. surface libellée apparue à la 2e lecture


def test_screenshot_keeps_a_labelled_surface_seen_by_the_second_reading(rec, home, monkeypatch):  # noqa: F811
    _code_on_a_surface(rec, monkeypatch)
    label = node("Lecteur", (0, 1600, 1080, 1700), clickable=False)
    player = node("Video player", (0, 0, 1080, 1500), cls="android.view.TextureView", clickable=False)
    rec.uis = [ui(label), ui(label, player)]
    capture.screenshot(rec, "film", None, False)  # type: ignore[arg-type]
    image = PILImage.open(home / "Downloads" / "Telephone" / "film.png").convert("RGB")
    assert max(image.getpixel((400, 230))) < 60  # type: ignore[arg-type]


def test_labelled_surface_is_kept_among_the_surfaces():
    found: list = []
    screen.parse_ui(ui(node("Video player", (0, 0, 1080, 1500), cls="android.view.TextureView", clickable=False)),
                    found)
    assert found == [(0, 0, 1080, 1500)]


# ---------------------------------------------------------------- 5. veille d'écran : pas une preuve d'écran éteint


@pytest.mark.parametrize("wakefulness,counted", [("Dreaming", False), ("Dozing", False), ("Asleep", True)])
def test_only_asleep_counts_as_watched_without_a_foreground(rec, home, wakefulness, counted):  # noqa: F811
    capture.start(rec, None, "bug", None, False)  # type: ignore[arg-type]
    recording = capture._CURRENT
    assert recording is not None
    recording.last_seen -= 60
    before = recording.last_seen
    rec.state_text = lambda: f"mWakefulness={wakefulness}\nisKeyguardShowing=false"  # type: ignore[method-assign]
    capture._observe(recording)
    assert (recording.last_seen != before) is counted
    capture.shutdown()


# ---------------------------------------------------------------- 6, 7. balayage des cartes et IBAN


@pytest.mark.parametrize("text,masked", [
    ("Carte 4111 1111 1111 1111 10/26", f"Carte {CARD} 10/26"),
    ("Carte 4111 1111 1111 1111 123", f"Carte {CARD} 123"),
    ("Carte 4111-1111-1111-1111.", f"Carte {CARD}."),
    ("FR76 3000 6000 0112 3456 7890 189 ET GB82 WEST 1234 5698 7654 32", f"{IBAN} ET {IBAN}"),
    ("DE89 3704 0044 0532 0130 00 TEST", f"{IBAN} TEST"),
    ("IBAN FR7630006000011234567890189, merci", f"IBAN {IBAN}, merci"),
    ("FR00 3000 6000 0112 3456 7890 189", "FR00 3000 6000 0112 3456 7890 189"),  # clé fausse
    ("version 4111.1111111111111", "version 4111.1111111111111"),
    ("Commande 1234567890123 du 12/10/2026", "Commande 1234567890123 du 12/10/2026"),
])
def test_cards_and_ibans_found_by_candidate_scan(text, masked):
    assert mask_codes(text) == masked
