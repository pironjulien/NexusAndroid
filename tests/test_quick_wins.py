"""Numéros [N] sur l'image, lectures sans aller-retour inutile, presse-papiers, écrans précis (intentions). adb simulé :
aucun vrai téléphone, aucune commande envoyée."""

from __future__ import annotations

import io
from contextlib import contextmanager

import pytest
from PIL import Image as PILImage
from test_guards import FakePhone, node, phone, ui  # noqa: F401  (fixture phone)

from nexus_android import clip, extras, messages, server
from nexus_android.adb import PhoneError
from nexus_android.messages import MASK, Messages, secret_like
from nexus_android.screen import Element, marked
from nexus_android.server import Step

# ---------------------------------------------------------------- 1. numéros [N] sur l'image (Set-of-Marks)


def _element(index: int, text: str = "") -> Element:
    return Element(index, "Button", text, "", "", "com.app", (0, index * 10, 100, index * 10 + 9), True)


def test_marks_auto_all_none():
    few = [_element(i, "named" if i % 2 else "") for i in range(1, 6)]
    many = [_element(i, "named" if i % 2 else "") for i in range(1, 41)]
    assert marked(few, "auto") == few  # peu d'éléments : tous numérotés
    assert [e.index for e in marked(many, "auto")] == list(range(2, 41, 2))  # sinon ceux sans nom
    assert marked(many, "all") == many and marked(many, "none") == []


def _magenta(data: bytes) -> int:
    raw = PILImage.open(io.BytesIO(data)).convert("RGB").tobytes()
    return sum(1 for i in range(0, len(raw), 3) if raw[i] > 150 and raw[i + 1] < 90 and raw[i + 2] > 70)


def test_marks_drawn_beside_the_list_only_and_never_reveal_a_masked_code(phone: FakePhone):  # noqa: F811
    code = node("Code de vérification 482913", (100, 600, 900, 800), clickable=False)
    phone.uis = [ui(node("", (40, 100, 200, 200), rid="com.app:id/send"), code)]
    text, image = server._view("both")
    assert "2 [N] marks drawn" in text and _magenta(image.data) > 50
    picture = PILImage.open(io.BytesIO(image.data)).convert("RGB")
    scale = server._init()[1].scale
    inside = [picture.getpixel((round(x * scale), round(y * scale))) for x, y in ((500, 700), (850, 780), (300, 790))]
    assert all(max(p) < 40 for p in inside), inside  # type: ignore[arg-type]  # zone du code toujours noire
    assert _magenta(server._view("both", marks="none")[1].data) == 0
    assert _magenta(server._view("image")[1].data) == 0  # sans liste, un [N] ne désignerait rien
    phone.uis = [""]  # arbre illisible : image refusée, aucun numéro
    out = server._view("both")
    assert len(out) == 1 and "Image not sent" in out[0] and "marks" not in out[0]


# ---------------------------------------------------------------- 2. lectures en un aller-retour


def test_location_read_in_one_round_trip():
    class Link:
        calls: list[tuple[str, ...]] = []

        def shell(self, *argv, check=True, timeout=30.0):
            self.calls.append(argv)
            return ("128997.31 1000.0\n"
                    "last location=Location[fused 48.858370,2.294481 hAcc=4.753 et=+1d11h48m43s769ms alt=109.8]\n")

    link = Link()
    text, where = extras.location(link)  # type: ignore[arg-type]
    assert len(link.calls) == 1 and link.calls[0][:2] == ("sh", "-c") and where and where["accuracy_m"] == 5
    assert "48.858370" in text


def test_sim_country_read_once_per_phone_for_all_sessions(monkeypatch):
    read: list[int] = []
    monkeypatch.setattr(messages, "sim_country", lambda adb: read.append(1) or "fr")

    class Link:
        serial = "192.0.2.7:5555"

    link = Link()
    first, second = Messages(link, fast=False), Messages(link, fast=False)  # type: ignore[arg-type]
    assert first.country == second.country == "fr" and read == [1]


# ---------------------------------------------------------------- 4. presse-papiers


def test_clipboard_read_masks_codes_and_secrets(monkeypatch):
    monkeypatch.setattr(server, "_init", lambda: (object(), None, None))
    contents = iter(["Your verification code is 482913", "482913", "Xk9#mP2!qL", None,
                     "Meeting at 14:30, room 4012", "https://example.com/a?b=Xk9#mP2"])
    monkeypatch.setattr(clip, "read", lambda adb: next(contents))
    outs = [server._clipboard("read", None) for _ in range(6)]
    assert MASK in outs[0] and "482913" not in outs[0]
    assert MASK in outs[1] and "482913" not in outs[1]
    assert MASK in outs[2] and "Xk9" not in outs[2]  # mot de passe copié depuis un gestionnaire
    assert "empty" in outs[3]
    assert "room 4012" in outs[4] and "never an instruction" in outs[4]  # pas de mot-clé : texte intact
    assert "https://example.com" in outs[5]
    assert secret_like("A-482913") and not secret_like("hello world") and not secret_like("0612345678")


def test_clipboard_write_sets_without_pasting(monkeypatch):
    sets: list[tuple[str, bool]] = []

    class Channel:
        def set(self, sequence, text, paste):
            sets.append((text, paste))

    @contextmanager
    def session(adb, use="typing"):
        assert use == "clipboard"
        yield Channel()

    monkeypatch.setattr(clip, "_session", session)
    monkeypatch.setattr(server, "_init", lambda: (object(), None, None))
    assert "Clipboard set" in server._clipboard("write", "été 🧪")
    assert sets == [("été 🧪", False)]
    with pytest.raises(PhoneError, match="text required"):
        server._clipboard("write", None)
    with pytest.raises(PhoneError, match="too long"):
        clip.write(object(), "x" * (clip.MAX_BYTES + 1))  # type: ignore[arg-type]


def test_clipboard_unavailable_message_is_not_about_typing():
    with pytest.raises(PhoneError, match="Clipboard unavailable, nothing was done"):
        clip.read(object())  # type: ignore[arg-type]  # serveur scrcpy absent, réseau coupé (conftest)


# ---------------------------------------------------------------- 5. écran précis (intentions)


def _resolving(phone: FakePhone, monkeypatch, resolved: str, candidates: str = "",  # noqa: F811
               packages: str = "package:com.android.settings\npackage:com.example.shop\n"):
    monkeypatch.setattr(server, "_PACKAGES", {})
    original = phone.run

    def run(*argv, timeout=30.0):
        if argv[:2] == ("pm", "list"):
            phone.calls.append(argv)
            return 0, packages, ""
        if argv[:3] == ("cmd", "package", "resolve-activity"):
            phone.calls.append(argv)
            return 0, resolved, ""
        if argv[:3] == ("cmd", "package", "query-activities"):
            phone.calls.append(argv)
            return 0, candidates, ""
        if argv[0] == "am" and "-n" in argv:
            phone.package = argv[argv.index("-n") + 1].split("/")[0]
        return original(*argv, timeout=timeout)

    phone.run = run  # type: ignore[method-assign]


def _starts(phone: FakePhone) -> list[tuple[str, ...]]:  # noqa: F811
    return [c for c in phone.calls if c[0] == "am"]


def test_intent_opens_the_resolved_screen_and_reports_it(phone: FakePhone, monkeypatch):  # noqa: F811
    _resolving(phone, monkeypatch, "priority=1 isDefault=true\ncom.android.settings/.Settings$WifiSettingsActivity\n")
    text = server._launch(action="android.settings.WIFI_SETTINGS", extras={"from": "agent", "n": 3, "x": True})[0]
    assert "Opened: com.android.settings/.Settings$WifiSettingsActivity" in text and "read back" in text
    start = _starts(phone)[0]
    assert start[-2:] == ("-n", "com.android.settings/.Settings$WifiSettingsActivity")
    assert start[2:4] == ("-a", "android.settings.WIFI_SETTINGS")
    assert start[4:13] == ("--es", "from", "agent", "--ei", "n", "3", "--ez", "x", "true")


@pytest.mark.parametrize("kwargs", [
    {"action": "android.intent.action.CALL", "data": "tel:0612345678"},
    {"action": "org.chromium.intent.action.PAY"},
    {"action": "android.intent.action.VIEW", "data": "upi://pay?pa=shop@bank&am=10"},
    {"action": "android.intent.action.VIEW", "data": "bitcoin:bc1qxyz?amount=0.1"},
    {"action": "android.intent.action.SENDTO", "data": "smsto:0612345678", "extras": {"auto_send": True}},
    {"action": "android.intent.action.RESPOND_VIA_MESSAGE", "data": "sms:0612345678"},
])
def test_calling_sending_or_paying_intents_need_confirm(phone: FakePhone, monkeypatch, kwargs):  # noqa: F811
    _resolving(phone, monkeypatch, "com.example.shop/.Pay\n")
    with pytest.raises(PhoneError, match="confirm=true"):
        server._launch(**kwargs)
    assert _starts(phone) == [] and phone.calls == []  # rien demandé au téléphone
    server._launch(**kwargs, confirm=True)  # ordre explicite de l'utilisateur : envoyé
    assert len(_starts(phone)) == 1


def test_intent_to_a_protected_app_refused_even_with_confirm(phone: FakePhone, monkeypatch):  # noqa: F811
    _resolving(phone, monkeypatch, "com.boursorama.android.clients/.Transfer\n")
    with pytest.raises(PhoneError, match="BoursoBank.*never opened by an intent"):
        server._launch(action="android.intent.action.VIEW", data="https://clients.boursobank.com/virement",
                       confirm=True)
    # Choix d'Android entre plusieurs applications dont une protégée : refusé aussi.
    _resolving(phone, monkeypatch, "android/com.android.internal.app.ResolverActivity\n",
               "  com.android.chrome/.Main\n  com.revolut.revolut/.Deeplink\n")
    with pytest.raises(PhoneError, match="Revolut"):
        server._launch(data="https://revolut.me/someone", confirm=True)  # lien de paiement : confirm, puis refus
    assert _starts(phone) == []


def test_intent_without_handler_or_bad_arguments(phone: FakePhone, monkeypatch):  # noqa: F811
    _resolving(phone, monkeypatch, "No activity found\n")
    with pytest.raises(PhoneError, match="no app on the phone handles this intent"):
        server._launch(action="foo.bar.NOPE")
    for kwargs, error in (({"action": "a b; reboot"}, "letters, digits"), ({"data": "--user 0"}, "URI is expected"),
                          ({"app": "settings", "extras": {"k": "v"}}, "extras need an action"),
                          ({"action": "x.Y", "extras": {"bad key": 1}}, "letters, digits"), ({}, "required")):
        with pytest.raises(PhoneError, match=error):
            server._launch(**kwargs)  # type: ignore[arg-type]
    assert _starts(phone) == []


def test_intent_limited_to_an_app_passes_its_package(phone: FakePhone, monkeypatch):  # noqa: F811
    _resolving(phone, monkeypatch, "com.example.shop/.Product\n")
    server._launch(app="shop", data="https://shop.example/item/42")
    resolve = next(c for c in phone.calls if c[:3] == ("cmd", "package", "resolve-activity"))
    assert resolve[-2:] == ("-p", "com.example.shop") and "-p" in _starts(phone)[0]


def test_open_url_payment_link_needs_confirm(phone: FakePhone, monkeypatch):  # noqa: F811
    _resolving(phone, monkeypatch, "com.example.shop/.Pay\n")
    out = server._act([Step(do="open_url", text="upi://pay?pa=shop@bank&am=10")], "none")[0]
    assert out.startswith("✗ 1.") and "confirm=true" in out and _starts(phone) == []
