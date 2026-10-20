"""Cinquième relecture (coordinateur et Codex) : le contrat est porté à la frontière de chaque injection physique
(chaque appui, glissé de défilement, touche d'envoi seule dans son étape), l'identité approuvée est simplifiée
(appli, textes et sceaux dans l'ordre du document, cible complète), et les serrages qui gênaient l'usage sont levés
sans rien céder sur la sécurité. Téléphone simulé, aucun geste réel."""

from __future__ import annotations

import random
from types import SimpleNamespace

import pytest
from test_capture import BANK, inbox, rec  # noqa: F401  (fixture rec)
from test_extras import home  # noqa: F401  (fixture home)
from test_guards import accorded, act, field, node, phone, ui  # noqa: F401  (fixture phone)
from test_review_final import _code_on_a_surface

from nexus_android import capture, server
from nexus_android import screen as screen_mod
from nexus_android.adb import PhoneError
from nexus_android.messages import CARD, IBAN, mask_codes
from nexus_android.server import Step

SMS = "com.google.android.apps.messaging"
WHATSAPP = "com.whatsapp"


def _slider(top: int, bottom: int, text: str = "Glisser vers le haut pour envoyer") -> str:
    return node(text, (0, top, 1080, bottom), cls="android.widget.SeekBar", clickable=False, package=SMS)


# ---------------------------------------------------------------- B1, B2, point 5 : défilement à la frontière


def test_scroll_is_judged_where_the_finger_lands(phone):  # noqa: F811
    phone.package = SMS
    phone.uis = [ui(node("Conversation", (0, 200, 1080, 1250), cls="android.widget.ListView", clickable=False,
                         package=SMS), _slider(1280, 1400))]
    report = act(Step(do="scroll", direction="down"))  # doigt posé à 540,1333 : sur le curseur d'envoi
    assert report.startswith("✗") and "irreversible" in report and phone.inputs == []


def test_one_accord_never_covers_several_scrolls(phone):  # noqa: F811
    phone.package = SMS
    phone.uis = [ui(node("Alice", (0, 50, 1080, 150), clickable=False, package=SMS), _slider(900, 1100))]
    down = Step(do="scroll", target="540,700", direction="down", confirm=True)  # départ à 540,1033 : sur le curseur
    token = accorded(act(down))
    for amount in (3, 10):
        assert "one scroll per step" in act(down.model_copy(update={"amount": amount, "accord": token}))
    assert phone.inputs == []
    up = act(Step(do="scroll", target="540,1367", direction="up", confirm=True, accord=token))  # autre glissé
    assert up.startswith("✗") and phone.inputs == []
    assert act(down.model_copy(update={"accord": accorded(up)})).startswith("✗")  # jeton d'un autre geste
    token = accorded(act(down))
    assert act(down.model_copy(update={"accord": token})).startswith("~") and len(phone.inputs) == 1


# ---------------------------------------------------------------- point 4 : chaque appui d'un double appui


def test_second_tap_of_a_double_tap_is_judged_again(phone, monkeypatch):  # noqa: F811
    """La relecture entre les deux appuis passe par le chemin rapide (lecteur d'écran du téléphone, simulé ici)."""
    phone.package = SMS
    phone.uis = [ui(node("Photo", (0, 300, 500, 700), package=SMS))]
    send = {"text": "Envoyer", "bounds": "[0,300][500,700]", "clickable": "true", "package": SMS,
            "class": "android.widget.Button"}  # le 1er appui fait apparaître Envoyer sous le doigt
    snap = screen_mod.Snapshot(screen_mod.State(True, False, SMS), "", (1080, 2000), [send])
    monkeypatch.setattr(server.SCREEN, "_server", lambda: SimpleNamespace(release=lambda: None))  # lecteur rapide du téléphone présent
    reads = iter([None, snap])  # 1re lecture : arbre (Photo) ; entre les appuis : chemin rapide (Envoyer)
    monkeypatch.setattr(server.SCREEN, "snapshot", lambda *a, **k: next(reads, snap))
    report = act(Step(do="double_tap", target="250,500"))
    assert report.startswith("✗") and phone.inputs == [("input", "tap", "250", "500")]


# ---------------------------------------------------------------- R3, points 1-2 : une touche d'envoi, seule


def test_enter_enter_is_refused_and_enter_is_judged_on_its_own_reading(phone):  # noqa: F811
    phone.package = WHATSAPP
    phone.uis = [ui(node("Alice", (0, 50, 1080, 150), clickable=False, package=WHATSAPP),
                    field("Salut", package=WHATSAPP))]
    assert "alone in its step" in act(Step(do="key", keys="enter enter", confirm=True))
    token = accorded(act(Step(do="key", keys="enter", confirm=True)))
    # Une surface dessinée sans texte apparaît sur l'en-tête avant la touche : rejugée à SA relecture, refusée.
    phone.uis = [ui(node("", (0, 0, 1080, 200), cls="android.view.SurfaceView", clickable=False, package=WHATSAPP),
                    field("Salut", package=WHATSAPP))]
    assert act(Step(do="key", keys="enter", confirm=True, accord=token)).startswith("✗") and phone.inputs == []


# ---------------------------------------------------------------- R1, R2 : identité simplifiée, une lecture


def test_accord_survives_a_layout_shift_and_a_ticking_timer(phone):  # noqa: F811
    phone.package = SMS

    def chat(dy: int) -> str:
        return ui(node("Alice", (0, 50, 1080, 150), clickable=False, package=SMS),
                  node("Bonjour", (0, 600 - dy, 1080, 700 - dy), clickable=False, package=SMS),
                  field("Salut", package=SMS), node("Envoyer", (900, 1700, 1080, 1800), package=SMS))
    phone.uis = [chat(0)]
    token = accorded(act(Step(do="tap", target="Envoyer", confirm=True)))
    phone.uis = [chat(4)]  # la liste remonte de 4 px
    assert act(Step(do="tap", target="Envoyer", confirm=True, accord=token)).startswith("~")
    phone.package = BANK

    def account(t: str) -> str:
        return ui(node(f"Session : {t}", (0, 50, 1080, 150), clickable=False, package=BANK),
                  node("Compte courant", (0, 400, 1080, 900), cls="android.widget.HorizontalScrollView",
                       clickable=False, package=BANK))
    phone.uis = [account("4:59"), account("4:58")]  # minuterie : une seule lecture pour le glissé
    assert act(Step(do="swipe", target="900,600", to="100,600", confirm=True)).startswith("~")


# ---------------------------------------------------------------- R4, point 3 : surfaces dessinées et accord


def test_video_preview_beside_a_described_button_gets_an_accord(phone):  # noqa: F811
    phone.package = WHATSAPP
    phone.uis = [ui(node("Alice", (0, 50, 1080, 150), clickable=False, package=WHATSAPP),
                    node("", (0, 200, 1080, 1500), cls="android.view.TextureView", clickable=False, package=WHATSAPP,
                         desc="Aperçu vidéo"),
                    node("", (900, 1700, 1080, 1800), desc="Envoyer", package=WHATSAPP))]
    token = accorded(act(Step(do="tap", target="Envoyer", confirm=True)))
    assert act(Step(do="tap", target="Envoyer", confirm=True, accord=token)).startswith("~")


def test_small_header_surface_with_a_drawn_recipient_gets_no_accord(phone):  # noqa: F811
    phone.package = WHATSAPP
    phone.uis = [ui(node("", (0, 0, 1080, 200), cls="android.view.SurfaceView", clickable=False, package=WHATSAPP),
                    node("Bonjour", (0, 600, 1080, 700), clickable=False, package=WHATSAPP),
                    node("", (900, 1700, 1080, 1800), desc="Envoyer", package=WHATSAPP))]
    report = act(Step(do="tap", target="Envoyer", confirm=True))
    assert "drawn outside the UI tree" in report and 'accord="' not in report and phone.inputs == []


# ---------------------------------------------------------------- R5, R6 : look sans OCR, texte multiligne


def test_look_returns_the_image_of_a_map_without_ocr(phone):  # noqa: F811
    maps = "com.google.android.apps.maps"
    phone.package = maps
    phone.uis = [ui(node("Rechercher ici", (0, 50, 1080, 150), package=maps),
                    node("", (0, 0, 1080, 2000), cls="android.view.SurfaceView", clickable=False, package=maps))]
    assert len(server._view("both")) == 2  # surface libellée : pas d'OCR sur look (latence), image rendue
    phone.uis = [ui(node("Rechercher ici", (0, 50, 1080, 150), package=maps),
                    node("", (0, 200, 1080, 2000), cls="android.view.SurfaceView", clickable=False, package=maps))]
    out = server._view("both")  # surface sans texte de l'arbre, OCR absent : image rendue, avertissement clair
    assert len(out) == 2 and "OCR unavailable" in out[0]


def test_screenshot_still_refused_without_ocr(rec, home, monkeypatch):  # noqa: F811
    _code_on_a_surface(rec, monkeypatch)
    monkeypatch.setattr(server.ocr_mod, "available", lambda: False)
    with pytest.raises(PhoneError, match="could not be read by OCR"):
        capture.screenshot(rec, "carte", None, False)  # type: ignore[arg-type]
    assert inbox(home) == []


def test_multiline_note_is_pasted(phone, monkeypatch):  # noqa: F811
    pasted = []
    monkeypatch.setattr(server.clip, "paste", lambda adb, text: pasted.append(text) or True)
    keep = "com.google.android.keep"
    phone.package = keep
    phone.uis = [ui(field("", package=keep))]
    act(Step(do="type", text="Lait\nOeufs"))
    assert pasted == ["Lait\nOeufs"] and all("KEYCODE_ENTER" not in c for c in phone.inputs)


# ---------------------------------------------------------------- R7, R8, points 6-7 : masquage


def _luhn_card(rng: random.Random, first: str) -> str:
    body = first + "".join(str(rng.randint(0, 9)) for _ in range(14))
    for c in "0123456789":
        digits = body + c
        total = sum((int(d) * 2 - 9 if int(d) * 2 > 9 else int(d) * 2) if i % 2 else int(d)
                    for i, d in enumerate(reversed(digits)))
        if total % 10 == 0:
            return digits
    raise AssertionError


def test_card_masking_keeps_calendars_draws_and_tables():
    rng = random.Random(7)
    calendars = [" ".join(str(d) for d in range(1 + m, 15 + m)) for m in range(1, 13)]
    draws = [" ".join(str(rng.randint(1, 49)) for _ in range(10)) for _ in range(300)]
    tables = [" ".join(str(rng.randint(10, 9999)) for _ in range(rng.randint(4, 8))) for _ in range(300)]
    assert all(mask_codes(t) == t for t in calendars + draws)
    assert sum(mask_codes(t) != t for t in tables) <= 45  # 98 / 300 avant : seuls les vrais groupements de carte
    for _ in range(100):
        card = _luhn_card(rng, rng.choice("23456"))
        grouped = " ".join(card[i:i + 4] for i in range(0, 16, 4))
        for text in (f"Carte {grouped} exp 10/26", f"{grouped} 123", f"n° {grouped}, cvv 123",
                     grouped.replace(" ", " "), grouped.replace(" ", " "), grouped.replace(" ", "-"), card):
            assert card[-4:] not in mask_codes(text).replace(" ", "").replace(" ", ""), text


@pytest.mark.parametrize("text,masked", [
    ("Montant 12.50 4111 1111 1111 1111", f"Montant 12.50 {CARD}"),
    ("FR76 3000 6000 0112 3456 7890 189EUR", f"{IBAN}EUR"),
    ("fr76 3000 6000 0112 3456 7890 189", IBAN),
    ("IBAN : FR76 3000 6000 0112 3456 7890 189", f"IBAN : {IBAN}"),
    ("IBAN FR76-3000-6000-0112-3456-7890-189", f"IBAN {IBAN}"),
])
def test_separators_case_and_glued_words(text, masked):
    assert mask_codes(text) == masked


@pytest.mark.parametrize("text", ["FR42 1234567890123456789 4111 1111 1111 1111",
                                  "IT60 X054 2811 1010 0000 0123 456 4000 0000 0000 0002"])
def test_iban_and_card_found_on_the_original_text(text):
    out = mask_codes(text)
    assert not any(c.isdigit() for c in out.replace("IT60", "").replace("FR42", "")), out


# ---------------------------------------------------------------- capture et vidéo (points 8-10, mineurs)


def test_home_screen_with_the_wallet_handle_is_saved(rec, home):  # noqa: F811
    launcher = "com.sec.android.app.launcher"
    rec.package = launcher
    tree = ui(node("Téléphone", (0, 1700, 200, 1900), package=launcher),
              node("", (440, 1950, 640, 2000), desc="Samsung Pay", package="com.samsung.android.spay"))
    rec.uis = [tree, tree]
    assert "Screenshot saved" in capture.screenshot(rec, "accueil", None, False)  # type: ignore[arg-type]


def test_bank_read_with_a_failed_tree_is_never_saved(rec, home):  # noqa: F811
    original = rec.run

    def run(*argv: str, timeout: float = 30.0):
        if argv[0] == "sh" and "uiautomator dump" in argv[2] and rec.uis[0] == "":
            rec.package = BANK  # arbre illisible, mais le premier plan lu avec lui est la banque
        return original(*argv, timeout=timeout)
    rec.run = run  # type: ignore[method-assign]
    rec.uis = [ui(node("Accueil", (0, 100, 500, 200))), ""]
    with pytest.raises(PhoneError, match="shown during the capture"):
        capture.screenshot(rec, "x", None, False, unmasked=True)  # type: ignore[arg-type]
    assert inbox(home) == []


def test_evidence_sampled_during_the_recording_counts_even_if_delivered_after(rec, home):  # noqa: F811
    capture.start(rec, None, "bug", None, False)  # type: ignore[arg-type]
    recording = capture._CURRENT
    assert recording is not None
    original = rec.run

    def run(*argv: str, timeout: float = 30.0):
        if argv[0] == "sh" and argv[2] == capture.STATE_SH:
            rec.package = BANK
            rec.procs[0].code = 0  # screenrecord finit pendant la lecture
        return original(*argv, timeout=timeout)
    rec.run = run  # type: ignore[method-assign]
    capture._observe(recording)
    assert recording.tainted


def test_tool_guard_ignores_a_finished_video(rec, home):  # noqa: F811
    capture.start(rec, 1, "bug", None, False)  # type: ignore[arg-type]
    recording = capture._CURRENT
    assert recording is not None
    rec.procs[0].code = 0
    capture.guard(rec, BANK)  # type: ignore[arg-type]
    assert not recording.tainted
    assert "Screen recording saved on the PC" in capture.stop(rec)  # type: ignore[arg-type]


@pytest.mark.parametrize("display,counted", [("DOZE\n  Display State=OFF", True), ("ON", False)])
def test_always_on_display_counts_as_screen_off(rec, home, display, counted):  # noqa: F811
    capture.start(rec, None, "bug", None, False)  # type: ignore[arg-type]
    recording = capture._CURRENT
    assert recording is not None
    recording.last_seen -= 60
    before = recording.last_seen
    rec.state_text = lambda: "mWakefulness=Dozing\nisKeyguardShowing=true"  # type: ignore[method-assign]
    original = rec.run
    rec.run = lambda *a, timeout=30.0: (0, f"  Display State={display}\n", "") if (  # type: ignore[method-assign]
        a[0] == "sh" and a[2] == capture.DISPLAY_SH) else original(*a, timeout=timeout)
    capture._observe(recording)
    assert (recording.last_seen != before) is counted
    capture.shutdown()


def test_launch_accord_shows_the_resolved_package(phone, monkeypatch):  # noqa: F811
    monkeypatch.setattr(server, "_packages", lambda adb, fresh=False: ["com.whatsapp"])
    with pytest.raises(PhoneError) as refusal:
        server._launch(app="whats", action="android.intent.action.CALL", data="tel:0611223344", confirm=True)
    assert "com.whatsapp" in str(refusal.value)
