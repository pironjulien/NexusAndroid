"""Sixième et dernière relecture (coordinateur et Codex) : saisie seulement dans un champ, caractères de contrôle
collés ; défilement jugé sur ce que le doigt actionne ; double appui relu sans attente ; IBAN à la longueur du pays ;
groupements de carte réels ; accord lié à la seule identité stable ; garde de la vidéo alimenté par toute lecture
récente. Téléphone simulé, aucun geste réel."""

from __future__ import annotations

import random
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_capture import BANK, inbox, rec  # noqa: F401  (fixture rec)
from test_extras import home  # noqa: F401  (fixture home)
from test_guards import accorded, act, field, node, phone, ui  # noqa: F401  (fixture phone)

from nexus_android import capture, routines, server
from nexus_android import screen as screen_mod
from nexus_android.adb import PhoneError
from nexus_android.messages import CARD, IBAN, mask_codes
from nexus_android.server import Step

WA = "com.whatsapp"
JAVA = Path(__file__).resolve().parents[1] / "phone" / "NexusQuery.java"


def _chat(*extra: str) -> str:
    return ui(node("Alice", (0, 50, 1080, 150), clickable=False, package=WA),
              node("", (0, 200, 1080, 1650), cls="androidx.recyclerview.widget.RecyclerView", clickable=False,
                   package=WA),
              *extra, field("", package=WA),
              node("", (900, 1700, 1080, 1800), rid="com.whatsapp:id/send", desc="Envoyer", package=WA,
                   cls="android.widget.ImageButton"))


# ---------------------------------------------------------------- 1. saisie : champ éditable, contrôles collés


def test_tab_and_space_are_pasted_never_keys(phone, monkeypatch, tmp_path):  # noqa: F811
    pasted: list[str] = []
    monkeypatch.setattr(server.clip, "paste", lambda adb, text: pasted.append(text) or True)
    phone.package = WA
    phone.uis = [_chat()]
    act(Step(do="type", text="ok\t "))
    monkeypatch.setattr(routines, "ROUTINES", tmp_path / "r.json")
    routines.remember("reply", [{"do": "type", "text": "{{m}}"}], WA)
    server._routine("reply", {"m": "ok\t "}, "none")
    assert pasted == ["ok\t ", "ok\t "]
    assert not any(c[:2] == ("input", "text") for c in phone.inputs)  # jamais `input text` avec une tabulation


def test_type_needs_an_editable_focused_field_even_with_confirm(phone):  # noqa: F811
    phone.package = WA
    send = node("", (900, 1700, 1080, 1800), rid="com.whatsapp:id/send", desc="Envoyer", package=WA,
                cls="android.widget.ImageButton", focused=True)
    phone.uis = [ui(node("Alice", (0, 50, 1080, 150), clickable=False, package=WA),
                    field("Salut", focused=False, package=WA), send)]
    report = act(Step(do="key", keys="tab"), Step(do="type", text=" ", confirm=True))
    assert "no active input field" in report and phone.inputs == [("input", "keyevent", "KEYCODE_TAB")]


# ---------------------------------------------------------------- 2, Codex 1-2 : défilement


def test_ordinary_chat_scroll_goes_through(phone):  # noqa: F811
    phone.package = WA
    bubbles = [node(t, (40, y, 900, y + 120), clickable=False, package=WA, cls="android.widget.TextView")
               for t, y in (("Tu peux m'envoyer le doc ?", 600), ("J'ai payé 20 €", 1280))]
    phone.uis = [_chat(*bubbles)]
    assert act(Step(do="scroll", direction="down", amount=3)).startswith("~")
    assert len(phone.inputs) == 3


@pytest.mark.parametrize("direction", ["in", "out"])
def test_scroll_in_or_out_is_refused(phone, direction):  # noqa: F811
    phone.uis = [_chat()]
    assert "direction up, down, left or right" in act(Step(do="scroll", direction=direction))
    assert phone.inputs == []


def test_protected_app_scroll_never_starts_on_a_pay_slider(phone):  # noqa: F811
    phone.package = BANK
    slider = node("Glisser pour payer 50 €", (0, 1280, 1080, 1400), cls="android.widget.SeekBar", clickable=False,
                  package=BANK)
    phone.uis = [ui(node("Comptes", (0, 200, 1080, 1250), cls="android.widget.ListView", clickable=False,
                         package=BANK), slider)]
    for confirm in (False, True):
        report = act(Step(do="scroll", direction="down", confirm=confirm))
        assert "never by the agent" in report and 'accord="' not in report, report
    assert phone.inputs == []
    phone.uis = [ui(node("Comptes", (0, 200, 1080, 1800), cls="android.widget.ListView", clickable=False,
                         package=BANK))]
    assert act(Step(do="scroll", direction="down")).startswith("~")  # lecture ordinaire : permise


# ---------------------------------------------------------------- 4, Codex 8 : double appui


def test_double_tap_without_quick_reader_is_refused_before_any_tap(phone):  # noqa: F811
    phone.uis = [ui(node("Carte", (0, 200, 1080, 1800), clickable=False, cls="android.view.View"))]
    report = act(Step(do="double_tap", target="540,1000"))
    assert "quick screen reader" in report and phone.inputs == []


@pytest.mark.parametrize("quick", [None, "no-tree"])
def test_second_tap_never_sent_without_a_usable_quick_reading(phone, monkeypatch, quick):  # noqa: F811
    phone.uis = [ui(node("Photo", (0, 300, 500, 700)))]
    monkeypatch.setattr(server.SCREEN, "_server", lambda: SimpleNamespace(release=lambda: None))
    snap = None if quick is None else screen_mod.Snapshot(screen_mod.State(True, False, "com.app"), "", (1080, 2000),
                                                          None)
    monkeypatch.setattr(server.SCREEN, "snapshot", lambda *a, **k: snap if k.get("settle") is False else None)
    report = act(Step(do="double_tap", target="250,500"))
    assert "the second tap was not sent (the first one was)" in report and len(phone.inputs) == 1


def test_double_tap_refused_when_the_quick_check_is_too_slow(phone, monkeypatch):  # noqa: F811
    phone.uis = [ui(node("Photo", (0, 300, 500, 700)))]
    clock = [0.0]
    monkeypatch.setattr(server.time, "monotonic", lambda: clock[0])
    asked = []

    def slow(*a, **k):
        asked.append(k)
        if k.get("settle") is False:
            clock[0] += 1.0  # relecture lente (écran qui attend le calme)
            return screen_mod.Snapshot(screen_mod.State(True, False, "com.app"), "", (1080, 2000), [])
        return None
    monkeypatch.setattr(server.SCREEN, "snapshot", slow)
    monkeypatch.setattr(server.SCREEN, "_server", lambda: SimpleNamespace(release=lambda: None))
    report = act(Step(do="double_tap", target="250,500"))
    assert "could not be checked again" in report and len(phone.inputs) == 1
    assert {"settle": False} in asked  # relecture de l'instant, sans attente d'inactivité


def test_quick_snapshot_asks_the_phone_not_to_wait(monkeypatch):
    queries = []
    fake = SimpleNamespace(run=lambda q: queries.append(q[0]) or None)
    s = screen_mod.Screen(SimpleNamespace())  # type: ignore[arg-type]
    monkeypatch.setattr(s, "_server", lambda: fake)
    s.snapshot(settle=False)
    s.snapshot()
    assert queries[0][-2:] == ["--quiet", "0"] and "--quiet" not in queries[1]
    source = JAVA.read_text(encoding="utf-8")
    assert 'case "--quiet"' in source and "if (quietMs > 0)" in source  # le lecteur du téléphone le comprend


# ---------------------------------------------------------------- 3, 5 : IBAN et cartes


def test_iban_needs_its_country_length():
    rng = random.Random(3)
    words = "depart porte arrivee bagage retard heure terminal siege rang avion vol embarquement prevu demain".split()
    for _ in range(1000):
        code = rng.choice(["AF", "EZ", "LH", "BA", "KL", "TO", "U2", "RN", "AB", "CD"]) + str(rng.randint(10, 9999))
        sentence = f"Vol {code} " + " ".join(rng.choice(words) for _ in range(5))
        assert mask_codes(sentence) == sentence
    for text in ("IBAN FR76 3000 6000\n0112 3456 7890 189", "IBAN FR76  3000  6000  0112  3456  7890  189",
                 "IBAN FR76.3000.6000.0112.3456.7890.189", "FR76 3000 6000 0112 3456 7890 189EUR"):
        assert IBAN in mask_codes(text) and "3456" not in mask_codes(text), text
    assert mask_codes("CH93 0076 2011 6238 5295 7 FR76 3000 6000 0112 3456 7890 189") == f"{IBAN} {IBAN}"


@pytest.mark.parametrize("text", ["Carte 4111  1111  1111  1111", "Carte 4111 1111\n1111 1111",
                                  "Carte 4111.1111.1111.1111", "x4111 1111 1111 1111", "Visa 4222 222 222 222",
                                  "UnionPay 6211 1111 1111 1111 9", "Carte 6211 1111 1111 1111 12",
                                  "IMEI 356938035643809"])
def test_card_groupings_are_masked(text):
    out = mask_codes(text)
    assert CARD in out and "1111" not in out.replace(" ", "") and "2222" not in out.replace(" ", ""), out


def test_siret_and_ean13_are_not_cards():
    assert mask_codes("SIRET : 73282932000074") == "SIRET : 73282932000074"  # 14 chiffres collés
    assert mask_codes("EAN 4006381333931") == "EAN 4006381333931"  # 13 chiffres collés


# ---------------------------------------------------------------- Codex 6 : accord lié à l'identité stable


def test_two_pixel_shift_across_the_header_line_keeps_the_accord(phone):  # noqa: F811
    phone.package = WA

    def screen(top: int) -> str:  # en-tête : 18 % de 2000 = 360 px ; « Alice » passe de l'en-tête au contexte
        return ui(node("Alice", (0, top, 1080, top + 59), clickable=False, package=WA), field("Salut", package=WA),
                  node("", (900, 1700, 1080, 1800), rid="com.whatsapp:id/send", desc="Envoyer", package=WA))
    phone.uis = [screen(300)]
    token = accorded(act(Step(do="tap", target="desc:Envoyer", confirm=True)))
    phone.uis = [screen(302)]
    assert act(Step(do="tap", target="desc:Envoyer", confirm=True, accord=token)).startswith("~")


# ---------------------------------------------------------------- Codex 3, 4, 7 : capture et garde de la vidéo


def test_screenshot_rereads_the_foreground_even_after_a_failed_tree(rec, home):  # noqa: F811
    rec.uis = ["", "", "", ui(node("Accueil", (0, 100, 500, 200)))]  # 1re lecture : trois échecs
    original = rec.exec_out

    def shot(*argv: str, timeout: float = 30.0) -> bytes:
        rec.package = BANK  # banque apparue pendant screencap
        return original(*argv, timeout=timeout)
    rec.exec_out = shot  # type: ignore[method-assign]
    with pytest.raises(PhoneError, match="shown during the capture"):
        capture.screenshot(rec, "x", None, False, unmasked=True)  # type: ignore[arg-type]
    assert inbox(home) == []


def test_any_fresh_reading_of_a_bank_taints_the_running_video(rec, home):  # noqa: F811
    capture.start(rec, None, "bug", None, False)  # type: ignore[arg-type]
    recording = capture._CURRENT
    assert recording is not None
    rec.package = BANK
    with pytest.raises(PhoneError):
        capture.screenshot(rec, "x", None, False)  # type: ignore[arg-type]  # lu par la capture, pas par la session
    assert recording.tainted


def test_an_old_bank_reading_does_not_discard_a_new_video(rec, home):  # noqa: F811
    server.SCREEN.last_state = screen_mod.State(True, False, BANK)  # type: ignore[union-attr]
    server.SCREEN.last_seen_at = 0.0  # type: ignore[union-attr]  # lue bien avant la vidéo
    capture.start(rec, None, "bug", None, False)  # type: ignore[arg-type]
    recording = capture._CURRENT
    assert recording is not None
    server._video_guard()
    assert not recording.tainted
    capture.shutdown()



# ---------------------------------------------------------------- dernier passage (coordinateur)


def test_dated_bank_reading_taints_even_if_the_video_ended_since(rec, home):  # noqa: F811
    capture.start(rec, None, "bug", None, False)  # type: ignore[arg-type]
    recording = capture._CURRENT
    assert recording is not None
    during = recording.started + 0.5  # lecture commencée pendant l'enregistrement
    rec.procs[0].code = 0  # … qui s'est arrêté avant que la preuve n'arrive
    capture.guard(rec, BANK, during)  # type: ignore[arg-type]
    assert recording.tainted


def test_bank_seen_while_measuring_the_display_stops_the_recording_start(rec, home, monkeypatch):  # noqa: F811
    def display(adb, screen):
        screen.last_state = screen_mod.State(True, False, BANK)  # premier plan relu par la mesure de l'affichage
        return 1080, 2000
    monkeypatch.setattr(capture, "_display", display)
    with pytest.raises(PhoneError, match="no screen recording"):
        capture.start(rec, None, "bug", None, False)  # type: ignore[arg-type]
    assert rec.spawned == []


def test_crlf_separates_card_groups():
    assert "1111" not in mask_codes("Carte 4111 1111\r\n1111 1111")


def test_identity_keeps_raw_text_and_selection():
    from nexus_android import policy

    def tab(text: str, selected: bool) -> list:
        node_ = {"text": text, "bounds": "[0,0][500,100]", "clickable": "true", "package": "com.app",
                 "class": "android.widget.Button", "selected": str(selected).lower()}
        return screen_mod.mask_elements(screen_mod.elements_of([node_]))
    assert policy.outward_identity(tab("Alice", True), "com.app") != policy.outward_identity(tab("Alice ", True),
                                                                                             "com.app")
    assert policy.outward_identity(tab("Alice", True), "com.app") != policy.outward_identity(tab("Alice", False),
                                                                                             "com.app")
