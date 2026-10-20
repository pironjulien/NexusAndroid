"""Relecture finale de la 0.9.0 (Claude, Codex) : chaque faille reproduite (preuves poc1-3 et 11 reproductions de
Codex) devient un test qui échoue sur la 0.9.0 et passe après correction. Téléphone simulé, aucun geste réel."""

from __future__ import annotations

import io
import json
import socket
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from PIL import Image as PILImage
from PIL import ImageDraw
from test_capture import BANK, inbox, rec  # noqa: F401  (fixture rec)
from test_extras import home  # noqa: F401  (fixture home)
from test_guards import FakePhone, accorded, act, field, node, phone, ui  # noqa: F401  (fixture phone)

from nexus_android import (
    capture,
    clip,
    control,
    extras,
    functions,
    logs,
    ocr,
    phonemap,
    policy,
    routines,
    screen,
    server,
    web,
)
from nexus_android.adb import PhoneError
from nexus_android.messages import CARD, IBAN, MASK, mask_codes
from nexus_android.server import Step

BOURSO = "com.boursorama.android.clients"


def _wait(condition, seconds: float = 3.0) -> bool:
    """Attente réelle (time.sleep est simulé par les fixtures) d'une condition remplie par un autre fil."""
    pause = threading.Event()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        pause.wait(0.01)
    return condition()


# ---------------------------------------------------------------- 1. appli protégée, arbre illisible (poc1)


def test_protected_app_with_unreadable_tree_refuses_every_aimed_gesture(phone):  # noqa: F811
    phone.package = BOURSO
    phone.uis = [ui(node("Valider le virement", (0, 900, 1080, 1100), package=BOURSO))]
    assert "never by the agent" in act(Step(do="tap", target="540,1000", confirm=True))
    phone.uis = [""]  # arbre illisible (null root node) : avant, l'appui par coordonnées partait
    for step in (Step(do="tap", target="540,1000", confirm=True), Step(do="long_press", target="540,1000", confirm=True),
                 Step(do="swipe", target="60,1000", to="1040,1000", confirm=True),
                 Step(do="scroll", target="540,1000", direction="down")):
        report = act(step)
        assert report.startswith("✗") and "UI tree unreadable" in report, (step, report)
    assert phone.inputs == []
    assert act(Step(do="key", keys="back")).startswith("~")  # quitter reste permis


# ---------------------------------------------------------------- 2. touches (poc2, Codex 1, 2, 4)


def test_password_field_refuses_typing_keys_even_with_accord(phone):  # noqa: F811
    phone.uis = [ui(field(password=True))]
    for keys in ("KEYCODE_A", "delete", "ctrl+a", "KEYCODE_PASTE", "enter", "KEYCODE_5"):
        report = act(Step(do="key", keys=keys, confirm=True))
        assert "password field" in report and "accord" not in report, (keys, report)
    assert phone.inputs == []
    assert act(Step(do="key", keys="tab")).startswith("~")  # navigation : permise


def test_protected_app_refuses_enter_even_with_confirm_and_never_offers_an_accord(phone):  # noqa: F811
    phone.package = BOURSO
    phone.uis = [ui(field("150", package=BOURSO), node("Valider le virement", (0, 1850, 1080, 1950), package=BOURSO))]
    for keys in ("enter", "KEYCODE_DPAD_CENTER", "tab enter", "KEYCODE_NUMPAD_ENTER"):
        report = act(Step(do="key", keys=keys, confirm=True))
        assert "never by the agent" in report and 'accord="' not in report, (keys, report)
    report = act(Step(do="type", text="150\n", confirm=True))  # saut de ligne = Entrée
    assert "never by the agent" in report
    assert phone.inputs == []
    assert act(Step(do="key", keys="tab", confirm=True)).startswith("~")  # navigation avec confirm : permise


def test_accord_binds_the_whole_key_sequence(phone):  # noqa: F811
    phone.uis = [ui(field("approved"))]
    token = accorded(act(Step(do="key", keys="enter", confirm=True)))
    for keys in ("tab enter", "delete enter"):
        report = act(Step(do="key", keys=keys, confirm=True, accord=token))
        assert report.startswith("✗") and phone.inputs == [], (keys, report)
        token = accorded(report)  # nouveau jeton, pour cette séquence-là
    sequence = accorded(act(Step(do="key", keys="tab enter", confirm=True)))
    assert act(Step(do="key", keys="tab enter", confirm=True, accord=sequence)).startswith("~")
    assert phone.inputs[-2:] == [("input", "keyevent", "KEYCODE_TAB"), ("input", "keyevent", "KEYCODE_ENTER")]


def test_keys_other_than_navigation_need_a_readable_tree(phone):  # noqa: F811
    phone.uis = [""]
    assert "active field unreadable" in act(Step(do="key", keys="delete"))
    assert phone.inputs == []
    assert act(Step(do="key", keys="back")).startswith("~")


# ---------------------------------------------------------------- 3. glissé « pour envoyer » (poc3)


def test_slide_to_send_needs_the_accord_like_a_tap(phone):  # noqa: F811
    phone.uis = [ui(node("Glisser pour envoyer 200 EUR", (0, 900, 1080, 1100), cls="android.widget.SeekBar",
                         clickable=False))]
    assert "irreversible" in act(Step(do="swipe", target="60,1000", to="1040,1000"))
    report = act(Step(do="swipe", target="60,1000", to="1040,1000", confirm=True))
    assert "confirm=true alone is not enough" in report and phone.inputs == []
    assert act(Step(do="swipe", target="60,1000", to="1040,1000", confirm=True,
                    accord=accorded(report))).startswith("~")
    assert phone.inputs == [("input", "swipe", "60", "1000", "1040", "1000", "400")]


# ---------------------------------------------------------------- 4. accord : destinataire, pièces jointes (Codex 3)


def test_accord_for_one_number_does_not_call_another(phone):  # noqa: F811
    app = "com.samsung.android.dialer"
    phone.package = app

    def dial(number: str) -> None:  # numéro sous les 18 % du haut de l'écran (y 300..450 sur 2000)
        phone.uis = [ui(node("", (440, 1700, 640, 1900), package=app), node(number, (0, 300, 1080, 450),
                                                                             clickable=False, package=app))]
    dial("0611223344")
    request = act(Step(do="tap", target="540,1800", confirm=True))
    assert "0611223344" in request
    dial("0655667788")
    report = act(Step(do="tap", target="540,1800", confirm=True, accord=accorded(request)))
    assert report.startswith("✗") and "changed" in report and phone.inputs == []


def test_attachment_and_body_are_part_of_the_action():
    def mail(attachment: str, preview: str = "Hello") -> list[screen.Element]:
        return [screen.Element(1, "Text", "Alice", "", "", "com.mail", (0, 0, 400, 100), False),
                screen.Element(2, "Text", preview, "", "", "com.mail", (0, 500, 800, 600), False),
                screen.Element(3, "Text", attachment, "", "", "com.mail", (0, 700, 800, 800), False)]
    a = policy.screen_action("tap", "Send", "com.mail", mail("photo-a.jpg"), 2000)
    assert a["attachments"] == ["photo-a.jpg"]
    assert a != policy.screen_action("tap", "Send", "com.mail", mail("photo-b.jpg"), 2000)
    assert a != policy.screen_action("tap", "Send", "com.mail", mail("photo-a.jpg", "Goodbye"), 2000)
    shown = [screen.Element(1, "Text", "To: bob@example.com", "", "", "com.mail", (0, 900, 800, 1000), False)]
    assert "bob@example.com" in policy.screen_action("tap", "Send", "com.mail", shown, 2000)["recipients"]


def test_no_accord_on_an_unreadable_outward_screen(phone):  # noqa: F811
    phone.package = "com.whatsapp"
    phone.uis = [""]
    report = act(Step(do="tap", target="980,1750", confirm=True))
    assert "cannot be read" in report and 'accord="' not in report and phone.inputs == []


# ---------------------------------------------------------------- 5. vidéo (Codex 6, opérationnel 1)


def test_protected_app_seen_by_the_watch_taints_the_video_for_good(rec, home, monkeypatch):  # noqa: F811
    monkeypatch.setattr(capture, "WATCH_EVERY", 0.01)
    capture.start(rec, None, "bug", None, False)  # type: ignore[arg-type]
    recording = capture._CURRENT
    assert recording is not None
    rec.package = BANK  # la banque passe devant…
    assert _wait(lambda: recording.tainted)
    rec.package = "com.app"  # … puis repart avant le stop : la vidéo la contient quand même
    remote = rec.spawned[0].split()[-2]
    assert ("rm", "-f", remote) in rec.calls
    with pytest.raises(capture.Discarded, match="recording discarded"):
        capture.stop(rec)  # type: ignore[arg-type]
    assert rec.pulls == [] and inbox(home) == []


def test_unwatched_recording_is_discarded(rec, home):  # noqa: F811
    capture.start(rec, None, "bug", None, False)  # type: ignore[arg-type]
    recording = capture._CURRENT
    assert recording is not None
    recording.last_seen -= 60  # une minute sans lecture du premier plan (liaison perdue)
    with pytest.raises(capture.Discarded, match="could not be checked"):
        capture.stop(rec)  # type: ignore[arg-type]
    assert rec.pulls == [] and inbox(home) == []


def test_previous_video_is_not_collected_before_the_checks(rec, home):  # noqa: F811
    capture.start(rec, 1, None, None, False)  # type: ignore[arg-type]
    previous = capture._CURRENT
    rec.procs[0].code = 0  # finie seule
    rec.package = BANK
    with pytest.raises(PhoneError, match="no screen recording"):
        capture.begin(rec, 1, None, None, False)  # type: ignore[arg-type]
    assert rec.pulls == [] and capture._CURRENT is previous  # rien rapatrié, poignée gardée
    rec.package = "com.app"
    text = capture.start(rec, 1, None, None, False)  # type: ignore[arg-type]
    assert "Previous recording, which had ended, collected" in text and len(rec.pulls) == 1


def test_failed_previous_collection_keeps_the_handle_and_starts_nothing(rec, home):  # noqa: F811
    capture.start(rec, 1, None, None, False)  # type: ignore[arg-type]
    previous = capture._CURRENT
    rec.procs[0].code = 0
    rec.pull_fails = True
    with pytest.raises(PhoneError, match="nothing new was started"):
        capture.start(rec, 1, None, None, False)  # type: ignore[arg-type]
    assert capture._CURRENT is previous and len(rec.spawned) == 1
    rec.pull_fails = False
    assert "Screen recording saved on the PC" in capture.stop(rec)  # type: ignore[arg-type]


def test_shutdown_goes_through_the_same_check(rec, home):  # noqa: F811
    capture.start(rec, None, "bug", None, False)  # type: ignore[arg-type]
    rec.package = BANK
    capture.shutdown()
    assert rec.pulls == [] and inbox(home) == [] and capture._CURRENT is None


# ---------------------------------------------------------------- 6. OCR : pixels noircis (Codex 5)


def _code_on_a_surface(fake: FakePhone, monkeypatch) -> None:
    image = PILImage.new("RGB", (1080, 2000), "white")
    ImageDraw.Draw(image).rectangle((100, 200, 700, 260), fill="red")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    fake.png = buffer
    fake.uis = [ui(node("", (0, 0, 1080, 2000), cls="android.view.SurfaceView", clickable=False))]
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "read", lambda image, factor=1.0: [ocr.Line("Votre code : 123456", (100, 200, 700, 260))])


@pytest.mark.parametrize("show", ["both", "image"])
def test_ocr_code_is_blacked_out_on_the_returned_image(phone, monkeypatch, show):  # noqa: F811
    _code_on_a_surface(phone, monkeypatch)
    out = server._view(show)
    assert "123456" not in out[0]
    returned = PILImage.open(io.BytesIO(out[1].data)).convert("RGB")
    scale = server.SCREEN.scale  # type: ignore[union-attr]
    assert max(returned.getpixel((round(400 * scale), round(230 * scale)))) < 60  # type: ignore[arg-type]
    assert min(returned.getpixel((round(400 * scale), round(1000 * scale)))) > 200  # type: ignore[arg-type]


def test_ocr_code_on_a_drawn_surface_beside_text_is_blacked_out(phone, monkeypatch):  # noqa: F811
    _code_on_a_surface(phone, monkeypatch)
    phone.uis = [ui(node("Lecteur", (0, 0, 1080, 100), clickable=False),
                    node("", (0, 150, 1080, 1500), cls="android.view.TextureView", clickable=False))]
    out = server._view("image")
    returned = PILImage.open(io.BytesIO(out[1].data)).convert("RGB")
    scale = server.SCREEN.scale  # type: ignore[union-attr]
    assert max(returned.getpixel((round(400 * scale), round(230 * scale)))) < 60  # type: ignore[arg-type]


def test_ocr_code_is_blacked_out_in_the_saved_screenshot(rec, home, monkeypatch):  # noqa: F811
    _code_on_a_surface(rec, monkeypatch)
    text = capture.screenshot(rec, "jeu", None, False)  # type: ignore[arg-type]
    assert "blacked out" in text
    image = PILImage.open(home / "Downloads" / "Telephone" / "jeu.png").convert("RGB")
    assert max(image.getpixel((400, 230))) < 60  # type: ignore[arg-type]
    assert min(image.getpixel((400, 1000))) > 200  # type: ignore[arg-type]


# ---------------------------------------------------------------- 7. masquage unique : cartes et IBAN partout


CARD_NUMBER = "4111 1111 1111 1111"  # numéro de test public (Luhn valide)
IBAN_NUMBER = "FR76 3000 6000 0112 3456 7890 189"


def test_one_masking_for_every_text_output(monkeypatch):
    text = f"Carte {CARD_NUMBER}, compte {IBAN_NUMBER}"
    for out in (mask_codes(text), logs.mask(text), web._clean(text), functions.clean(text),
                phonemap.mask_value("name", text),
                extras.Notice("com.bank", None, "Paiement", text, False).line()):
        assert CARD in out and IBAN in out and "4111" not in out and "3000 6000" not in out, out
    masked = screen.mask_elements([screen.Element(1, "Text", text, "", "", "com.app", (0, 0, 10, 10), False)])
    assert masked[0].masked and "4111" not in masked[0].text
    monkeypatch.setattr(clip, "read", lambda adb: text)
    monkeypatch.setattr(server, "ADB", object())
    monkeypatch.setattr(server, "SCREEN", object())
    monkeypatch.setattr(server, "MESSAGES", object())
    assert CARD in server._clipboard("read", None)
    rows = [{"begin": "1760000000000", "end": "1760003600000", "allDay": "0", "title": f"Virement {IBAN_NUMBER}",
             "eventLocation": f"carte {CARD_NUMBER}"}]
    agenda, data = extras.calendar(SimpleNamespace(_query=lambda *a, **k: rows), 7)  # type: ignore[arg-type]
    assert IBAN in agenda and CARD in agenda and "4111" not in json.dumps(data)
    assert mask_codes("Commande 1234567890123 du 12/10/2026") == "Commande 1234567890123 du 12/10/2026"
    assert MASK in mask_codes("Votre code : 482913")


# ---------------------------------------------------------------- 8. web : URL réellement lue (Codex 7)


@pytest.mark.parametrize("url", ["https://bank.example/account", ""])
def test_web_rechecks_the_url_actually_read(monkeypatch, url):
    @contextmanager
    def fake_forward(*a):
        yield 1

    class WS:
        def __init__(self, *a): pass
        def send(self, *a): pass
        def close(self): pass

        def receive(self):
            return json.dumps({"id": 1, "result": {"result": {"value": {
                "url": url, "title": "Account", "text": "Confidential transaction description", "links": []}}}})
    monkeypatch.setattr(web, "forward", fake_forward)
    monkeypatch.setattr(web, "WebSocket", WS)
    tab = web.Tab("fake", "com.android.chrome", "1", "Old", "https://example.org", "/page")
    with pytest.raises(PhoneError) as error:
        web.read(SimpleNamespace(), tab)  # type: ignore[arg-type]
    assert "Confidential" not in str(error.value)


# ---------------------------------------------------------------- 9. canal scrcpy : délai après un échec (Codex 9)


def test_failed_gesture_channel_waits_before_retrying(monkeypatch):
    adb = SimpleNamespace(serial="fake", links=1)
    channel = control.Control(adb)  # type: ignore[arg-type]
    calls = []

    def unavailable():
        calls.append(1)
        raise PhoneError("offline")
    monkeypatch.setattr(channel, "_open", unavailable)
    assert channel.fingers((1080, 2000)) is None and channel.fingers((1080, 2000)) is None
    assert len(calls) == 1 and channel.failed == "offline"
    adb.links = 2  # reconnexion : retenté tout de suite
    assert channel.fingers((1080, 2000)) is None and len(calls) == 2


# ---------------------------------------------------------------- 10. géométrie relue sans image (Codex 8)


def test_geometry_follows_rotation_without_image(monkeypatch):
    s = screen.Screen(SimpleNamespace())  # type: ignore[arg-type]
    s.size, s.posture = (1080, 2400), "folded"
    snap = screen.Snapshot(screen.State(True, False, "com.app"), "open", (2400, 1080), [])
    monkeypatch.setattr(s, "snapshot", lambda *a, **k: snap)
    s.read(image=False, tree=True)
    assert s.size == (2400, 1080) and s.posture == "open"


# ---------------------------------------------------------------- 11. premier plan : échéance réelle (Codex 10)


def test_foreground_wait_is_a_real_deadline(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(server.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(server.time, "sleep", lambda d: clock.__setitem__(0, clock[0] + d))

    class Slow:
        def state(self):
            clock[0] += 4.9  # lecture lente
            return SimpleNamespace(package="elsewhere")
    server._foreground(Slow(), "wanted", 5)  # type: ignore[arg-type]
    assert clock[0] <= 5 + 4.9 + server.POLL


# ---------------------------------------------------------------- 12. mineurs


def test_no_blind_typing_even_with_confirm(phone):  # noqa: F811
    phone.uis = [""]
    report = act(Step(do="type", text="bonjour", confirm=True))
    assert "active field unreadable" in report and phone.inputs == []


def test_inactive_owner_check_is_said(phone, monkeypatch):  # noqa: F811
    phone.uis = [ui(node("Ouvrir", (0, 300, 500, 500)))]
    assert "lastUserActivityTime" in act(Step(do="tap", target="Ouvrir"))  # le faux téléphone ne la donne pas
    monkeypatch.setattr(server.awake, "idle_on_phone", lambda adb: 60.0)
    assert "lastUserActivityTime" not in act(Step(do="tap", target="Ouvrir"))


def test_settings_map_language_falls_back_to_the_factory_locale():
    answers = {("pm", "path"): "package:/system/priv-app/Settings/Settings.apk\n", ("stat", "-c"): "1:2\n",
               ("getprop", "ro.build.fingerprint"): "x/y:17/z\n", ("getprop", "persist.sys.locale"): "\n",
               ("getprop", "ro.product.locale"): "de-DE\n"}
    adb = SimpleNamespace(shell=lambda *a, **k: answers.get(a[:2], ""))
    assert phonemap._apk_stamp(adb)[2] == "de-DE"  # type: ignore[arg-type]


@pytest.mark.parametrize("code", [0, 255])
def test_xiaomi_inject_events_refusal_gives_the_setting(phone, code):  # noqa: F811
    original = phone.run
    refusal = "java.lang.SecurityException: Injecting to another application requires INJECT_EVENTS permission"
    phone.run = lambda *a, timeout=30.0: (code, refusal, "") if a[0] == "input" else original(*a, timeout=timeout)
    phone.uis = [ui(node("Ouvrir", (0, 300, 500, 500)))]
    sent = server._SENT
    report = act(Step(do="tap", target="Ouvrir"))
    assert report.startswith("✗") and "USB debugging (Security settings)" in report and server._SENT == sent


def test_clipboard_silence_mid_answer_is_a_clean_error():
    a, b = socket.socketpair()
    try:
        a.settimeout(0.2)
        with pytest.raises(PhoneError, match="stopped answering"):
            clip.Channel(a).set(1, "x", paste=False)  # le téléphone ne répond jamais
    finally:
        a.close()
        b.close()


def test_look_reads_the_routines_file_once(phone, monkeypatch):  # noqa: F811
    data = {f"r{i}": {"package": "com.app", "steps": []} for i in range(5)}
    loads = []
    monkeypatch.setattr(routines, "_load", lambda: loads.append(1) or data)
    phone.uis = [ui(node("Ouvrir", (0, 300, 500, 500)))]
    assert "Routines for this app: r0, r1, r2, r3, r4" in server._view("elements")[0]
    assert len(loads) == 1
