"""Garde-fous de bout en bout avec un adb simulé : aucun vrai téléphone, aucune commande envoyée."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time

import pytest
from PIL import Image as PILImage

from nexus_android import adb as adb_mod
from nexus_android import policy, screen, server
from nexus_android.adb import PhoneError
from nexus_android.lock import PhoneLock
from nexus_android.messages import mask_codes
from nexus_android.screen import Element, Screen, mask_elements
from nexus_android.server import Step

W, H = 1080, 2000


def node(text="", bounds=(0, 0, 100, 100), rid="", cls="android.widget.Button", package="com.app", desc="",
         clickable=True, focused=False, password=False) -> str:
    x1, y1, x2, y2 = bounds
    a = {"text": text, "resource-id": rid, "class": cls, "package": package, "content-desc": desc,
         "clickable": str(clickable).lower(), "focused": str(focused).lower(), "password": str(password).lower(),
         "bounds": f"[{x1},{y1}][{x2},{y2}]"}
    return "<node " + " ".join(f'{k}="{v}"' for k, v in a.items()) + " />"


def ui(*nodes: str) -> str:
    return ("<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">"
            + "".join(nodes) + "</hierarchy>")


def field(text="", focused=True, password=False, package="com.app", bounds=(0, 1700, 880, 1800)) -> str:
    return node(text, bounds, rid="com.app:id/input", cls="android.widget.EditText", package=package,
                focused=focused, password=password)


class FakePhone:
    """adb simulé : état de l'écran, arbres UI successifs, gestes enregistrés (jamais exécutés)."""

    def __init__(self) -> None:
        self.uis: list[str] = [ui()]
        self.package = "com.app"
        self.lock = "isKeyguardShowing=false"
        self.calls: list[tuple[str, ...]] = []
        self.am = (0, "Starting: Intent { act=android.intent.action.VIEW }", "")
        self.monkey = (0, "Events injected: 1\n## Network stats", "")
        self.png = io.BytesIO()
        PILImage.new("RGB", (W, H), "white").save(self.png, "PNG")

    def state_text(self) -> str:
        focus = f"\n  mCurrentFocus=Window{{abc u0 {self.package}/x.Main}}" if self.package else ""
        return "mWakefulness=Awake\n" + self.lock + focus

    @property
    def inputs(self) -> list[tuple[str, ...]]:
        return [c for c in self.calls if c[0] == "input"]

    def run(self, *argv: str, timeout: float = 30.0) -> tuple[int, str, str]:
        self.calls.append(argv)
        if argv[:2] == ("dumpsys", "power"):
            return 0, "mWakefulness=Awake", ""
        if argv[:2] == ("dumpsys", "window"):
            focus = f"\n  mCurrentFocus=Window{{abc u0 {self.package}/x.Main}}" if self.package else ""
            return 0, self.lock + focus, ""
        if argv[:2] == ("dumpsys", "SurfaceFlinger"):
            return 0, "Display 0", ""
        if argv[0] == "sh" and argv[2] == screen.STATE_SH:  # état en un aller-retour
            return 0, self.state_text(), ""
        if (argv[0] == "sh" and "uiautomator dump" in argv[2]) or argv[0] == "cat":
            current = self.uis.pop(0) if len(self.uis) > 1 else self.uis[0]
            tail = f"\n{screen.STATE_MARK}\n{self.state_text()}" if argv[0] == "sh" else ""
            return ((0, "UI hierchary dumped to: x\n" + current + tail, "") if current else
                    (0, "ERROR: null root node" + tail, ""))
        if argv[0] == "uiautomator":  # ancienne lecture en deux commandes (comparaison avec la version d'avant)
            return 0, "UI hierchary dumped to: x", ""
        if argv[0] == "am":
            return self.am
        if argv[0] == "monkey":
            return self.monkey
        return 0, "", ""

    def shell(self, *argv: str, timeout: float = 30.0, check: bool = True) -> str:
        code, out, err = self.run(*argv, timeout=timeout)
        if code and check:
            raise PhoneError(err or out)
        return out

    def exec_out(self, *argv: str, timeout: float = 30.0) -> bytes:
        return self.png.getvalue()


@pytest.fixture
def phone(monkeypatch):
    fake = FakePhone()
    scr = Screen(fake)  # type: ignore[arg-type]
    scr.size = (W, H)
    monkeypatch.setattr(server, "ADB", fake)
    monkeypatch.setattr(server, "SCREEN", scr)
    monkeypatch.setattr(server, "MESSAGES", object())
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return fake


def act(*steps: Step) -> str:
    return server._act(list(steps), "none")[0]


def look(fake: FakePhone, *nodes: str) -> None:
    fake.uis = [ui(*nodes)]
    server._view("elements")


# ---------------------------------------------------------------- 1. garde-fou sur toute étape visée


def test_guard_on_every_targeted_step(phone):
    phone.uis = [ui(node("Supprimer définitivement", (0, 0, 500, 200)), field())]
    for step in (Step(do="tap", target="Supprimer définitivement"), Step(do="clear", target="Supprimer définitivement"),
                 Step(do="type", target="Supprimer définitivement", text="x"), Step(do="long_press", target="10,10"),
                 Step(do="swipe", target="10,10", to="400,1000"), Step(do="swipe", target="900,1000", to="100,100"),
                 Step(do="scroll", target="Supprimer définitivement", direction="down")):
        report = act(step)
        assert report.startswith("✗") and "confirm=true" in report, (step, report)
    assert phone.inputs == []
    assert act(Step(do="tap", target="Supprimer définitivement", confirm=True)).startswith("~")
    assert phone.inputs == [("input", "tap", "250", "100")]


def test_swipe_from_sensitive_point_and_on_the_spot(phone):
    phone.uis = [ui(node("Payer 12 €", (0, 0, 500, 200)), node("Liste", (0, 400, 1080, 1600), clickable=False))]
    assert "confirm=true" in act(Step(do="swipe", target="100,100", to="100,1500"))
    assert "nothing readable" in act(Step(do="swipe", target="1000,1900", to="1005,1905"))  # appui déguisé
    assert act(Step(do="swipe", target="500,1500", to="500,500")).startswith("~")
    assert act(Step(do="swipe", target="1000,1900", to="1000,1000")).startswith("~")  # vrai glissé, zone vide
    assert len(phone.inputs) == 2


def test_keys_closed_list(phone):
    for keys in ("enter", "KEYCODE_ENTER", "KEYCODE_DPAD_CENTER", "KEYCODE_CALL", "KEYCODE_ENDCALL", "back enter",
                 "KEYCODE_MEDIA_PLAY"):
        assert "confirm=true" in act(Step(do="key", keys=keys)), keys
    assert phone.inputs == []
    for keys in ("back", "home", "recent", "delete", "ctrl+a", "up down left right", "tab", "volume_up",
                 "KEYCODE_BACK"):
        assert act(Step(do="key", keys=keys)).startswith("~"), keys
    assert act(Step(do="key", keys="enter", confirm=True)).startswith("~")
    assert ("input", "keyevent", "KEYCODE_ENTER") in phone.inputs


def test_newline_in_type_needs_confirmation(phone):
    phone.uis = [ui(field())]
    for text in ("ok\nmerci", "ok\r"):
        assert "Enter key" in act(Step(do="type", text=text))
    assert phone.inputs == []
    act(Step(do="type", text="ok\nmerci", confirm=True))
    assert phone.inputs == [("input", "text", "ok"), ("input", "keyevent", "KEYCODE_ENTER"), ("input", "text", "merci")]


# ---------------------------------------------------------------- 2. cible jugée sur l'écran actuel


def test_index_revalidated_on_current_screen(phone):
    look(phone, node("Archiver", (0, 0, 500, 200)), node("Ouvrir", (0, 300, 500, 500)))
    phone.uis = [ui(node("Supprimer", (0, 0, 500, 200)), node("Ouvrir", (0, 300, 500, 500)))]
    assert "run look again" in act(Step(do="tap", target="[1]"))
    assert phone.inputs == []
    phone.uis = [ui(node("Ouvrir", (0, 310, 500, 510)))]  # même élément, à peine déplacé
    assert act(Step(do="tap", target="[2]")).startswith("~")
    assert phone.inputs == [("input", "tap", "250", "410")]


def test_point_judged_on_current_screen(phone):
    look(phone, node("Archiver", (0, 0, 500, 200)))
    phone.uis = [ui(node("Payer", (0, 0, 500, 200)))]
    assert "confirm=true" in act(Step(do="tap", target="100,100"))
    phone.uis = [ui(node("Payer", (0, 0, 500, 200)))]
    assert "nothing readable" in act(Step(do="tap", target="900,1900"))
    phone.uis = [""]  # arbre UI illisible
    assert "nothing readable" in act(Step(do="tap", target="100,100"))
    assert phone.inputs == []
    assert act(Step(do="tap", target="100,100", confirm=True)).startswith("~")


def test_clickable_parent_judged(phone):
    phone.uis = [ui(node("", (0, 0, 600, 300), desc="Payer"), node("12,00 €", (50, 50, 300, 150), clickable=False))]
    assert "confirm=true" in act(Step(do="tap", target="12,00 €"))


# ---------------------------------------------------------------- 3. verrou commun aux trois serveurs


def test_lock_between_processes(tmp_path):
    path = tmp_path / "android.lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys; from pathlib import Path; from nexus_android.lock import PhoneLock\n"
         "with PhoneLock(Path(sys.argv[1])):\n    print('pris', flush=True); sys.stdin.read()", str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    try:
        assert holder.stdout and holder.stdout.readline().strip() == "pris"
        with pytest.raises(PhoneError, match=r"busy.*process \d+"):
            with PhoneLock(path, wait=0.3):
                pass
    finally:
        holder.communicate("", timeout=10)
    with PhoneLock(path, wait=1):  # libéré à la sortie de l'autre processus
        with pytest.raises(PhoneError):
            with PhoneLock(path, wait=0.2):  # même processus, autre serveur simulé
                pass


def test_ui_dump_file_per_process_and_removed(phone):
    Screen(phone).dump()  # type: ignore[arg-type]
    command = next(c for c in phone.calls if c[0] == "sh")[2]
    assert f"-{os.getpid()}.xml" in screen.UI_DUMP and f"rm -f {screen.UI_DUMP}" in command


# ---------------------------------------------------------------- 4. identité du téléphone


def fake_adb(monkeypatch, tmp_path, devices: str, ids: dict[str, str], state: dict | None):
    path = tmp_path / "wifi.json"
    if state is not None:
        path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(adb_mod, "WIFI_STATE", path)
    monkeypatch.setattr(adb_mod, "_find_adb", lambda: "adb")
    monkeypatch.setattr(adb_mod, "open_ports", lambda host: [])
    monkeypatch.delenv("NEXUS_ANDROID_SERIAL", raising=False)

    def raw(self, args, timeout=30.0, serial=True):
        out = b""
        if args[0] == "-s" and args[3:] == ["getprop", "ro.serialno"]:
            out = ids.get(args[1], "").encode()
        elif args[0] == "-s" and args[3:] == ["ip", "-4", "addr"]:
            out = b"    inet 192.0.2.50/24 brd 192.0.2.255 scope global wlan0\n"
        elif args[0] == "-s" and "/proc/net/tcp6" in args:
            out = (b"  sl  local rem st\n   1: 0000:8211 0000:0000 0A 00000000:00000000 00:00000000 00000000  2000 0 2\n")
        elif args[0] == "devices":
            out = ("List of devices attached\n" + devices).encode()
        return subprocess.CompletedProcess(args, 0, out, b"")

    monkeypatch.setattr(adb_mod.Adb, "_raw", raw)
    return path


def test_wifi_ip_prefers_wlan0_then_hotspot():
    lo = "    inet 127.0.0.1/8 scope host lo\n"
    cell = "    inet 203.0.113.2/29 brd 203.0.113.7 scope global rmnet_data7\n"
    hotspot = "    inet 192.0.2.160/24 brd 192.0.2.255 scope global ap_br_swlan0\n"
    wlan = "    inet 198.51.100.9/24 brd 198.51.100.255 scope global wlan0\n"
    assert adb_mod.wifi_ip(lo + cell + hotspot) == "192.0.2.160"  # partage de connexion, PC sur le Wi-Fi du téléphone
    assert adb_mod.wifi_ip(lo + hotspot + wlan) == "198.51.100.9"
    assert adb_mod.wifi_ip(lo + cell) is None  # données mobiles seules : rien à joindre depuis le PC


def test_connect_refuses_other_devices(monkeypatch, tmp_path):
    state = {"serialno": "RFTEST00001", "usb": "RFTEST00001", "address": "192.0.2.67:33915"}
    path = fake_adb(monkeypatch, tmp_path, "emulator-5554 device model:sdk\n192.0.2.30:5555 device model:TV\n",
                    {"emulator-5554": "EMULATOR", "192.0.2.30:5555": "TVBOX"}, state)
    with pytest.raises(PhoneError, match="RFTEST00001.*not found"):
        adb_mod.Adb().connect()
    assert json.loads(path.read_text(encoding="utf-8")) == state


def test_connect_skips_emulator_and_keeps_identity(monkeypatch, tmp_path):
    path = fake_adb(monkeypatch, tmp_path, "emulator-5554 device model:sdk\nRFTEST00001 device model:SM_F971B\n",
                    {"emulator-5554": "EMULATOR", "RFTEST00001": "RFTEST00001"}, None)
    assert adb_mod.Adb().connect().serial == "RFTEST00001"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["serialno"] == "RFTEST00001" and saved["address"] == "192.0.2.50:33297"


def test_connect_wifi_only_if_same_phone(monkeypatch, tmp_path):
    state = {"serialno": "RFTEST00001", "address": "192.0.2.67:33915"}
    path = fake_adb(monkeypatch, tmp_path, "192.0.2.67:40000 device model:SM_F971B\n",
                    {"192.0.2.67:40000": "AUTRE"}, state)
    with pytest.raises(PhoneError, match="not found"):
        adb_mod.Adb().connect()
    assert json.loads(path.read_text(encoding="utf-8")) == state
    path = fake_adb(monkeypatch, tmp_path, "192.0.2.67:40000 device model:SM_F971B\n",
                    {"192.0.2.67:40000": "RFTEST00001"}, state)
    assert adb_mod.Adb().connect().serial == "192.0.2.67:40000"
    assert json.loads(path.read_text(encoding="utf-8"))["address"] == "192.0.2.67:40000"


def test_connect_first_pairing_needs_usb(monkeypatch, tmp_path):
    fake_adb(monkeypatch, tmp_path, "192.0.2.30:5555 device model:TV\n", {"192.0.2.30:5555": "TVBOX"}, None)
    with pytest.raises(PhoneError, match="not registered on this PC yet"):
        adb_mod.Adb().connect()


# ---------------------------------------------------------------- 5. applications protégées


def test_protected_app_of_targeted_element(phone):
    phone.uis = [ui(node("Virement", (0, 0, 500, 200), package="com.revolut.revolut"), node("Notes", (600, 0, 1000, 200)))]
    assert act(Step(do="tap", target="Notes")).startswith("~")  # autre moitié de l'écran partagé : permis
    phone.calls.clear()
    report = act(Step(do="tap", target="100,100", confirm=True))
    assert "Revolut" in report and "never by the agent" in report and phone.inputs == []


def test_unknown_foreground_app(phone):
    phone.package = ""
    phone.uis = [ui(node("Ouvrir", (0, 0, 500, 200)))]
    assert "foreground app unknown" in act(Step(do="tap", target="Ouvrir"))
    assert act(Step(do="key", keys="home")).startswith("~")
    assert act(Step(do="tap", target="Ouvrir", confirm=True)).startswith("~")


def test_protected_packages_and_pattern():
    for package in ("com.paypal.android.p2pmobile", "com.lydia.app", "com.binance.dev", "com.coinbase.android",
                    "com.samsung.android.samsungpass", "fr.bnpp.digitalbanking", "eu.qonto.qonto", "io.metamask",
                    "com.authy.authy", "fr.cnamts.it.activity", "fr.gouv.finances.smartphone.android",
                    "com.example.mybank", "fr.credit.banque", "com.foo.wallet", "com.foo.payment", "com.x.auth"):
        assert policy.protected_app(package), package
    for package in ("com.whatsapp", "com.android.settings", "com.google.android.apps.messaging", "com.android.vending",
                    "com.sec.android.app.launcher", "com.android.systemui", ""):
        assert policy.protected_app(package) is None, package


# ---------------------------------------------------------------- 6. codes masqués partout


def test_mask_codes_more_cases():
    assert mask_codes("Votre code est 482913, valable 10 min") == "Votre code est [masked code], valable 10 min"
    assert mask_codes("Code de validation : A7K9P2") == "Code de validation : [masked code]"
    assert mask_codes("Votre OTP : 123456789") == "Votre OTP : [masked code]"
    assert "4829" not in mask_codes("PIN 4829")
    assert "482913" not in mask_codes("Utilisez 482913 pour la vérification de connexion")
    assert "77AB12" not in mask_codes("security code 77AB12")
    assert mask_codes("Code promo : 20 % sur 1 250,00 €") == "Code promo : 20 % sur 1 250,00 €"
    assert mask_codes("Rendez-vous à 1830 près du A12") == "Rendez-vous à 1830 près du A12"
    assert mask_codes("482913", context=True) == "[masked code]"


def test_notification_body_masked_by_title():
    elements = mask_elements([
        Element(1, "TextView", "Code de vérification Google", "", "title", "com.android.systemui", (0, 100, 900, 160), False),
        Element(2, "TextView", "482913", "", "text", "com.android.systemui", (0, 170, 900, 230), False),
        Element(3, "TextView", "Batterie 2048 mAh", "", "", "com.android.systemui", (0, 1500, 900, 1560), False),
    ])
    assert elements[1].text == "[masked code]" and elements[1].masked
    assert elements[2].text == "Batterie 2048 mAh" and not elements[2].masked


def test_mask_before_query_wait_target_and_image(phone):
    title = node("Code de vérification", (0, 100, 900, 160), clickable=False)
    body = node("482913", (0, 170, 900, 230), clickable=False)
    phone.uis = [ui(title, body)]
    text = server._view("elements", query="482913")[0]
    assert "482913" not in text and "no readable element" in text
    assert "absent" in act(Step(do="wait", text="482913", timeout=0.05))
    assert "not found" in act(Step(do="tap", target="482913"))
    image = PILImage.open(io.BytesIO(server._view("image")[1].data)).convert("RGB")
    scale = image.width / W

    def pixel(x: int, y: int) -> tuple[int, ...]:
        value = image.getpixel((round(x * scale), round(y * scale)))
        assert isinstance(value, tuple)
        return value

    assert max(pixel(450, 200)) < 60  # zone du code noircie
    assert min(pixel(450, 1000)) > 200  # reste de l'écran intact


# ---------------------------------------------------------------- 7. champ actif et verrouillage


def test_type_checks_focused_field(phone):
    phone.uis = [ui(field(password=True))]
    assert "password field" in act(Step(do="type", text="secret"))
    phone.uis = [ui(field(focused=False))]
    assert "no active input field" in act(Step(do="type", text="abc"))
    phone.uis = [ui(field(focused=False), field(password=True, focused=True, bounds=(0, 0, 500, 100)))]
    assert "password field" in act(Step(do="type", text="abc", confirm=True))
    assert phone.inputs == []


def test_unknown_lock_state_refuses_typing(phone):
    phone.lock = "rien de lisible"
    phone.uis = [ui(field())]
    assert "lock state unreadable" in act(Step(do="type", text="abc", confirm=True))
    assert "lock state unreadable" in act(Step(do="tap", target="0,1750"))
    phone.lock = "mIsShowing=true"
    assert "Phone locked" in act(Step(do="tap", target="0,1750", confirm=True))
    assert phone.inputs == []
    assert act(Step(do="key", keys="home")).startswith("~")


# ---------------------------------------------------------------- 8. saisie de % selon AOSP


def aosp_input_text(arg: str) -> str:
    """Ce que tape `input text` (InputShellCommand.sendText) : « %s » → espace, rien d'autre."""
    buff, escape, i = list(arg), False, 0
    while i < len(buff):
        if escape:
            escape = False
            if buff[i] == "s":
                buff[i] = " "
                del buff[i - 1]
                i -= 1
        if buff[i] == "%":
            escape = True
        i += 1
    return "".join(buff)


@pytest.mark.parametrize("text", ["100%", "100% sur", "a%sb", "%s", "%%s", "% s", "50 % de 20%", "x\\%y", "s%"])
def test_percent_typed_as_written(text):
    chunks = server._input_chunks(text)
    assert "".join(aosp_input_text(arg) for arg, _ in chunks) == text
    assert sum(n for _, n in chunks) == len(text)


def test_type_reports_only_what_was_sent(phone):
    phone.uis = [ui(field())]
    original = phone.run

    def failing(*argv, timeout=30.0):
        if argv[:2] == ("input", "text") and argv[2] == "sb":
            return 1, "", "input: erreur simulée"
        return original(*argv, timeout=timeout)

    phone.run = failing  # type: ignore[method-assign]
    report = act(Step(do="type", text="a%sb"))
    assert report.startswith("✗") and "2 of 4 character(s) typed" in report


def test_type_verified_only_when_reread(phone):
    phone.uis = [ui(field()), ui(field("bonjour"))]
    assert act(Step(do="type", text="bonjour")).startswith("✓")
    phone.uis = [ui(field()), ui(field(""))]
    assert act(Step(do="type", text="bonjour")).startswith("~ 1. 7 characters sent, not read back")


# ---------------------------------------------------------------- 9. faux succès


def test_failed_launch_and_open_url_reported(phone):
    phone.calls.clear()
    phone.monkey = (0, "** No activities found to run, monkey aborted.", "")
    original = phone.run
    phone.run = lambda *a, timeout=30.0: (0, "package:com.whatsapp\n", "") if a[:2] == ("pm", "list") else original(  # type: ignore[method-assign]
        *a, timeout=timeout)
    with pytest.raises(PhoneError, match="not launched.*No activities"):
        server._launch("com.whatsapp")
    phone.am = (0, "", "Error: Activity not started, unable to resolve Intent")
    assert act(Step(do="open_url", text="foo://x")).startswith("✗")
    phone.am = (0, "Warning: Activity not started, its current task has been brought to the front", "")
    assert act(Step(do="open_url", text="https://x")).startswith("✗")


def test_draft_failure_not_reported_as_ready(phone, monkeypatch):
    class Book:
        def name(self, number):
            return number

        def thread(self, n, limit):
            return ["Alice (0611223344)"], []

    monkeypatch.setattr(server, "MESSAGES", Book())
    phone.am = (0, "", "Error: Activity not started, unable to resolve Intent")
    with pytest.raises(PhoneError, match="Android opened nothing"):
        server._draft("0611223344", "Salut", "sms")
    phone.am = (0, "Starting: Intent", "")
    phone.uis = [ui(field("Salut Alice"))]
    assert "Draft ready" in server._draft("0611223344", "Salut Alice", "sms")[0]
    phone.uis = [ui()]
    assert "not read back" in server._draft("0611223344", "Salut Alice", "sms")[0]


# ---------------------------------------------------------------- 10. [N] explicite


def test_digits_are_text_or_number_not_index(phone, monkeypatch):
    look(phone, *[node(f"Item {i}", (0, i * 100, 500, i * 100 + 90)) for i in range(1, 13)], node("12", (600, 0, 700, 90)))
    phone.uis = [ui(node("12", (600, 0, 700, 90)))]
    act(Step(do="tap", target="12"))
    assert phone.inputs == [("input", "tap", "650", "45")]

    class Book:
        def name(self, number):
            return number

        def thread(self, n, limit):
            assert n == 12
            return ["Alice (0611223344)"], []

    monkeypatch.setattr(server, "MESSAGES", Book())
    server._draft("36179", "STOP", "sms")
    server._draft("[12]", "Salut", "sms")
    starts = [c for c in phone.calls if c[0] == "am"]
    assert "smsto:36179" in starts[0] and "smsto:0611223344" in starts[1]


# ---------------------------------------------------------------- 11. libellés dangereux


def test_irreversible_labels_anywhere():
    def el(text):
        return Element(1, "Button", text, "", "", "com.app", (0, 0, 1, 1), True)

    for text in ("Supprimer définitivement", "Oui, envoyer", "Vider la corbeille", "Passer la commande",
                 "Résilier l'abonnement", "Transférer de l’argent", "Buy now", "Désinstaller", "PAYER", "Appeler Alice",
                 "S'abonner"):
        assert policy.irreversible(el(text)), text
    # Accord du 18/10 : supprimer (corbeille), accepter, partager, appeler, valider et autoriser sont libres.
    for text in ("Envoyé hier", "La Poste", "Paramètres", "Commandes passées", "Callback", "Postal", "Tout supprimer",
                 "Déplacer vers la corbeille", "J’accepte", "Autoriser", "Journal d'appels", "Partager", "Soumettre",
                 "VALIDER", "Boîte d'envoi", "Éléments envoyés"):
        assert policy.irreversible(el(text)) is None, text


# ---------------------------------------------------------------- glissé court dans un bouton


def test_swipe_inside_one_clickable_element_is_a_tap(phone):
    """Départ et arrivée dans le même élément cliquable : un appui déguisé, jugé comme tel (icône sans nom)."""
    phone.package = "com.whatsapp"
    send = node("", (900, 1700, 1060, 1860), rid="com.whatsapp:id/btn_x", package="com.whatsapp")
    row = node("Alice", (0, 300, 1080, 500), package="com.whatsapp")
    scroller = node("", (0, 600, 1080, 1600), cls="androidx.recyclerview.widget.RecyclerView", package="com.whatsapp")
    phone.uis = [ui(send, row, scroller.replace('clickable="true"', 'clickable="true" scrollable="true"'))]
    report = act(Step(do="swipe", target="910,1710", to="1050,1850"))
    assert report.startswith("✗") and "unnamed icon" in report and phone.inputs == []
    assert act(Step(do="swipe", target="910,1710", to="1050,1850", confirm=True)).startswith("~")
    assert act(Step(do="swipe", target="100,400", to="1000,400")).startswith("~")  # ligne nommée : glissé permis
    assert act(Step(do="swipe", target="500,1500", to="500,700")).startswith("~")  # liste défilable : vrai glissé
    assert act(Step(do="swipe", target="1000,1800", to="500,1000")).startswith("~")  # sort du bouton : vrai glissé
    assert len(phone.inputs) == 4


# ---------------------------------------------------------------- brouillon WhatsApp : indicatif de la SIM


def test_whatsapp_draft_uses_the_sim_country(phone, monkeypatch):
    class Book:
        country = "gb"

        def name(self, number):
            return number

    monkeypatch.setattr(server, "MESSAGES", Book())
    phone.uis = [ui(field("Hello"))]
    server._draft("07700 900123", "Hello", "whatsapp")
    server._draft("+33 6 11 22 33 44", "Hello", "whatsapp")
    Book.country = ""
    with pytest.raises(PhoneError, match="international format"):
        server._draft("0611223344", "Hello", "whatsapp")
    server._draft("+33611223344", "Hello", "whatsapp")  # déjà international : pays inutile
    urls = [c[-1] for c in phone.calls if c[0] == "am"]
    assert urls == ["https://wa.me/447700900123?text=Hello", "https://wa.me/33611223344?text=Hello",
                    "https://wa.me/33611223344?text=Hello"]


# ---------------------------------------------------------------- l'utilisateur reprend le téléphone pendant un lot


def test_batch_stops_when_owner_touches_the_phone(phone, monkeypatch):
    reads: list[float] = []
    touched = {"after": 99}  # nombre de gestes après lequel l'utilisateur touche l'écran

    def idle(adb):
        reads.append(1)
        # Avant le lot : téléphone posé depuis longtemps ; ensuite, toucher il y a 1 s, 4 s après notre geste.
        return 1.0 if len(phone.inputs) >= touched["after"] else 100.0

    monkeypatch.setattr(server.awake, "idle_on_phone", idle)
    monkeypatch.setattr(server, "_LAST_INPUT", 0.0)
    monkeypatch.setattr(server, "_gestures_done", lambda: setattr(server, "_LAST_INPUT", time.time() - 5))
    phone.uis = [ui(node("Un", (0, 0, 500, 200)), node("Deux", (0, 300, 500, 500)), node("Trois", (0, 600, 500, 800)))]
    touched["after"] = 1
    report = act(Step(do="tap", target="Un"), Step(do="wait", text="Deux"), Step(do="tap", target="Deux"),
                 Step(do="tap", target="Trois"))
    assert report.startswith("~ 1.") and "✓ 2." in report and "✗ 3." in report and "took over the phone" in report
    assert "4." not in report and len(phone.inputs) == 1  # arrêt net : ni l'étape 3 ni la 4
    phone.calls.clear()
    reads.clear()
    touched["after"] = 99
    report = act(Step(do="tap", target="Un"), Step(do="tap", target="Deux"))
    assert "✗" not in report and len(phone.inputs) == 2 and len(reads) == 2  # une lecture avant chaque geste
    reads.clear()
    act(Step(do="tap", target="Un"))
    assert len(reads) == 1  # un seul geste : seulement la vérification d'avant le lot
