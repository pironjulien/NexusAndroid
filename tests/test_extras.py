"""Nouveautés d'octobre 2026 : liaison perdue, pliage, l'utilisateur au téléphone, accents, notifications, fichiers,
position, réglages. adb simulé : aucun vrai téléphone."""

from __future__ import annotations

import socket
import struct
import threading
import time

import pytest
from PIL import Image as PILImage
from test_guards import FakePhone, act, field, node, ui

from nexus_android import adb as adb_mod
from nexus_android import clip, extras, server
from nexus_android.adb import LinkError, PhoneError
from nexus_android.screen import is_black, parse_helpers, parse_posture
from nexus_android.server import Step

# ---------------------------------------------------------------- liaison perdue


def test_link_lost_pattern_is_device_level_only():
    assert adb_mod._LINK_LOST.search("error: device offline")
    assert adb_mod._LINK_LOST.search("adb.EXE: device offline")  # relevé sur le Fold en Wi-Fi
    assert adb_mod._LINK_LOST.search("adb.exe: error: device '192.0.2.67:37477' not found")
    assert adb_mod._LINK_LOST.search("error: no devices/emulators found")
    assert not adb_mod._LINK_LOST.search("/system/bin/sh: foo: not found")


def test_gesture_cut_by_link_loss_is_unknown_not_failed(phone, monkeypatch):
    phone.uis = [ui(node("Bouton", (0, 0, 500, 200)))]
    original = phone.run

    def run(*argv, timeout=30.0):
        if argv[:2] == ("input", "tap"):
            raise LinkError("adb shell : pas de réponse après 30 s.")
        return original(*argv, timeout=timeout)

    monkeypatch.setattr(phone, "run", run)
    out = act(Step(do="tap", target="Bouton"), Step(do="tap", target="Bouton"))
    assert out.startswith("? 1.") and "effet inconnu" in out and "2." not in out


def test_offline_wifi_link_is_reconnected(monkeypatch, tmp_path):
    import subprocess

    monkeypatch.setattr(adb_mod, "WIFI_STATE", tmp_path / "wifi.json")
    (tmp_path / "wifi.json").write_text('{"serialno": "RFTEST00001", "address": "192.0.2.67:37477"}', encoding="utf-8")
    monkeypatch.setattr(adb_mod, "_find_adb", lambda: "adb")
    monkeypatch.setattr(time, "sleep", lambda s: None)
    state = {"link": "offline"}
    calls = []

    def raw(self, args, timeout=30.0, serial=True):
        calls.append(args)
        out = b""
        if args[:2] == ["devices", "-l"]:
            out = f"List of devices attached\n192.0.2.67:37477 {state['link']} model:SM_F971B\n".encode()
        elif args[:2] == ["reconnect", "offline"]:
            state["link"] = "device"
        elif "ro.serialno" in args:
            out = b"RFTEST00001\n"
        elif "boot_id" in " ".join(args):
            out = b"11bd9a22-5d18-4123-9858-51b02a379bdb\n"
        return subprocess.CompletedProcess(args, 0, out, b"")

    monkeypatch.setattr(adb_mod.Adb, "_raw", raw)
    device = adb_mod.Adb().connect()
    assert device.serial == "192.0.2.67:37477" and ["reconnect", "offline"] in calls
    assert not any(a[:2] == ["mdns", "services"] for a in calls)  # trouvé sans chercher ailleurs


# ---------------------------------------------------------------- pliage


def test_posture_parse():
    dump = "  mCommittedState=Optional[DeviceState{identifier=3, name='OPENED', app_accessible=true}]"
    assert parse_posture(dump) == "OPENED"
    assert parse_posture("rien") == ""


def test_coordinates_refused_after_fold_change(phone, monkeypatch):
    phone.uis = [ui(node("Bouton", (0, 0, 500, 200)))]
    server.SCREEN.posture = "CLOSED"  # type: ignore[union-attr]
    monkeypatch.setattr(server.SCREEN, "current_posture", lambda: "OPENED")
    out = act(Step(do="tap", target="100,100"))
    assert out.startswith("✗ 1.") and "plié ou déplié" in out
    assert not phone.inputs
    # Un texte visé est relu sur l'écran actuel : permis.
    assert act(Step(do="tap", target="Bouton")).startswith("~ 1.")


# ---------------------------------------------------------------- l'utilisateur au téléphone


def test_no_gesture_while_owner_uses_phone(phone, monkeypatch):
    monkeypatch.setattr(server, "BUSY_WAIT", 0.0)
    monkeypatch.setattr(server, "_LAST_INPUT", 0.0)
    monkeypatch.setattr(server.awake, "idle_on_phone", lambda adb: 0.5)
    phone.uis = [ui(node("Bouton", (0, 0, 500, 200)))]
    with pytest.raises(PhoneError, match="L'utilisateur se sert du téléphone"):
        act(Step(do="tap", target="Bouton"))
    assert not phone.inputs


def test_own_recent_gesture_is_not_owner(phone, monkeypatch):
    monkeypatch.setattr(server, "BUSY_WAIT", 0.0)
    monkeypatch.setattr(server, "_LAST_INPUT", time.time())
    monkeypatch.setattr(server.awake, "idle_on_phone", lambda adb: 0.5)
    phone.uis = [ui(node("Bouton", (0, 0, 500, 200)))]
    assert act(Step(do="tap", target="Bouton")).startswith("~ 1.")


# ---------------------------------------------------------------- accents


def test_accented_text_is_pasted(phone, monkeypatch):
    pasted = []
    monkeypatch.setattr(clip, "paste", lambda adb, text: pasted.append(text) or True)
    phone.uis = [ui(field()), ui(field("Réponse été 🧪"))]
    out = act(Step(do="type", text="Réponse été 🧪"))
    assert pasted == ["Réponse été 🧪"] and out.startswith("✓ 1.") and "non remis" not in out
    assert not [c for c in phone.inputs if c[1] == "text"]


def test_pasted_but_clipboard_not_restored_is_not_a_failure(phone, monkeypatch):
    monkeypatch.setattr(clip, "paste", lambda adb, text: False)
    phone.uis = [ui(field()), ui(field("été"))]
    out = act(Step(do="type", text="été"))
    assert out.startswith("✓ 1.") and "presse-papiers de l'utilisateur non remis" in out


def test_link_lost_while_typing_ascii_stays_unknown(phone, monkeypatch):
    original = phone.run

    def run(*argv, timeout=30.0):
        if argv[:2] == ("input", "text"):
            raise LinkError("liaison perdue avec le téléphone (adb.EXE: device offline)")
        return original(*argv, timeout=timeout)

    monkeypatch.setattr(phone, "run", run)
    phone.uis = [ui(field())]
    assert act(Step(do="type", text="bonjour")).startswith("? 1.")


def test_password_field_still_refused_for_accents(phone, monkeypatch):
    monkeypatch.setattr(clip, "paste", lambda adb, text: pytest.fail("collé dans un mot de passe"))
    phone.uis = [ui(field(password=True))]
    assert "mot de passe" in act(Step(do="type", text="été"))


def _serve(handler):
    a, b = socket.socketpair()
    thread = threading.Thread(target=handler, args=(b,), daemon=True)
    thread.start()
    a.settimeout(5)
    return clip.Channel(a), thread


def _read(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        data += sock.recv(n - len(data))
    return data


def test_clipboard_channel_protocol():
    seen = []

    def phone_side(sock):
        assert _read(sock, 2) == bytes([clip.GET_CLIPBOARD, 0])
        text = b"ancien"
        sock.sendall(bytes([clip.CLIPBOARD]) + struct.pack(">I", len(text)) + text)
        for _ in range(2):
            kind, sequence, paste, size = struct.unpack(">BQBI", _read(sock, 14))
            seen.append((kind, paste, _read(sock, size).decode()))
            sock.sendall(bytes([clip.ACK_CLIPBOARD]) + struct.pack(">Q", sequence))

    channel, thread = _serve(phone_side)
    before = channel.get()
    channel.set(7, "été 🧪", paste=True)
    channel.set(8, before or "", paste=False)
    thread.join(2)
    assert before == "ancien"
    assert seen == [(clip.SET_CLIPBOARD, 1, "été 🧪"), (clip.SET_CLIPBOARD, 0, "ancien")]


def test_empty_clipboard_reads_as_none():
    def phone_side(sock):
        _read(sock, 2)  # le serveur ne répond rien quand le presse-papiers est vide
        time.sleep(0.5)

    channel, _ = _serve(phone_side)
    assert channel.get(wait=0.2) is None


# ---------------------------------------------------------------- écran


def test_black_capture_and_helpers():
    assert is_black(PILImage.new("RGB", (200, 300), "black"))
    assert not is_black(PILImage.new("RGB", (200, 300), "white"))
    ps = ("PID NAME ARGS\n123 app_process app_process / com.mobilenext.mobilecli.DeviceServer\n"
          "456 app_process CLASSPATH=/data/local/tmp/nexus-scrcpy.jar app_process / com.genymobile.scrcpy.Server\n"
          "789 com.whatsapp com.whatsapp")
    assert parse_helpers(ps) == ["app_process / com.mobilenext.mobilecli.DeviceServer"]


# ---------------------------------------------------------------- notifications

DUMP = """Current Notification Manager state:
  Notification List:
    NotificationRecord(0x1: pkg=com.google.android.apps.messaging user=UserHandle{0} id=3 flags=AUTO_CANCEL)
      notification=
            when=1792260271059/1792260271059
            extras={
                android.title=String (Banque Exemple)
                android.text=String (Votre code de validation : 482913)
                android.bigText=String (Votre code de validation : 482913
valable 10 minutes (ne le communiquez pas))
            }
    NotificationRecord(0x2: pkg=com.microsoft.appmanager user=UserHandle{0} id=101 flags=ONGOING_EVENT|NO_CLEAR)
      notification=
            when=1792260271000/1792260271000
            extras={
                android.title=String (Vos appareils sont connectés.)
                android.text=String (Connecté à PC-BUREAU.)
            }
  TimeoutPendingIntent:
  mArchive=Archive (100 notifications)
    StatusBarNotification(pkg=com.whatsapp user=UserHandle{0} id=1)
    StatusBarNotification(pkg=com.whatsapp user=UserHandle{0} id=2)
  Snoozed notifications:
"""


def test_notifications_parse_mask_and_filter():
    notices, dismissed = extras.parse_notifications(DUMP)
    assert [n.package for n in notices] == ["com.google.android.apps.messaging", "com.microsoft.appmanager"]
    assert notices[0].text.endswith("(ne le communiquez pas)")
    assert "482913" not in notices[0].line() and "[code masqué]" in notices[0].line()
    assert notices[1].ongoing and not notices[0].ongoing
    assert dismissed == ["com.whatsapp", "com.whatsapp"]

    class Fake:
        def shell(self, *argv, **kw):
            return DUMP

    out = extras.notifications(Fake(), all_=False)  # type: ignore[arg-type]
    assert "PC-BUREAU" not in out and "1 permanentes masquées" in out and "com.whatsapp ×2" in out
    assert "PC-BUREAU" in extras.notifications(Fake(), all_=True)  # type: ignore[arg-type]


# ---------------------------------------------------------------- fichiers, position, réglages


def test_phone_path_stays_in_shared_storage():
    assert extras.phone_path("DCIM/Camera") == "/sdcard/DCIM/Camera"
    assert extras.phone_path("/storage/emulated/0/Download/a.pdf") == "/sdcard/Download/a.pdf"
    assert extras.phone_path(None) == "/sdcard/Download"
    for bad in ("/data/data/com.whatsapp", "/sdcard/../data", "/system"):
        with pytest.raises(PhoneError):
            extras.phone_path(bad)


def test_push_never_overwrites(tmp_path):
    local = tmp_path / "photo.jpg"
    local.write_bytes(b"x")

    class Fake:
        pushed = False

        def run(self, *argv, **kw):
            return (0, "", "") if argv[:2] == ("test", "-e") else (1, "", "")

        def shell(self, *argv, **kw):
            return ""

        def host(self, *argv, **kw):
            Fake.pushed = True
            return ""

    with pytest.raises(PhoneError, match="existe déjà"):
        extras.push(Fake(), str(local))  # type: ignore[arg-type]
    assert not Fake.pushed


def test_location_picks_fresh_precise_fix():
    dump = ("last location=Location[network 48.857500,2.295100 hAcc=300.0 et=+1d11h48m57s291ms]\n"
            "last location=Location[fused 48.858370,2.294481 hAcc=4.753 et=+1d11h48m43s769ms alt=109.8]")
    uptime = extras._elapsed("+1d11h50m00s")
    lat, lon, acc, age = extras.parse_location(dump, uptime)  # type: ignore[misc]
    assert (lat, lon) == (48.858370, 2.294481) and acc < 5 and 70 < age < 80


def test_wifi_off_refused_on_wifi_link():
    class Fake:
        calls: list = []

        def shell(self, *argv, **kw):
            Fake.calls.append(argv)
            return "1"

    with pytest.raises(PhoneError, match="couperait la liaison"):
        extras.setting(Fake(), "wifi", "off", "wifi")  # type: ignore[arg-type]
    assert not [c for c in Fake.calls if c[0] == "svc"]


def test_volume_parse():
    assert extras._volume("volume is 5 in range [0..15]") == (0, 15, "33 % (médias)")


@pytest.fixture
def phone(monkeypatch):
    fake = FakePhone()
    scr = server.Screen(fake)  # type: ignore[arg-type]
    scr.size = (1080, 2000)
    monkeypatch.setattr(server, "ADB", fake)
    monkeypatch.setattr(server, "SCREEN", scr)
    monkeypatch.setattr(server, "MESSAGES", object())
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return fake
