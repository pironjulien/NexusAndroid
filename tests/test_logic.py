"""Tests sans téléphone : analyse des sorties ADB, masquage des codes, garde-fous."""

from nexus_android import policy
from nexus_android.adb import parse_devices, parse_mdns
from nexus_android.messages import mask_codes, normalize, parse_rows, rcs_group_owner
from nexus_android.screen import Element, parse_logical_display, parse_state, parse_ui

UI = """UI hierchary dumped to: /dev/tty<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation="0">
<node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.app" content-desc="" checkable="false"
 checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false"
 long-clickable="false" password="false" selected="false" bounds="[0,0][1080,2000]">
<node index="0" text="Envoyer" resource-id="com.app:id/send_button" class="android.widget.Button" package="com.app"
 content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false"
 scrollable="false" long-clickable="false" password="false" selected="false" bounds="[900,1800][1060,1900]" />
<node index="1" text="" resource-id="com.app:id/pin" class="android.widget.EditText" package="com.app"
 content-desc="Code" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="true"
 scrollable="false" long-clickable="false" password="true" selected="false" bounds="[0,1800][880,1900]" />
<node index="2" text="" resource-id="" class="android.view.View" package="com.app" content-desc="" checkable="false"
 checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false"
 long-clickable="false" password="false" selected="false" bounds="[0,0][10,10]" />
</node></hierarchy>"""


def test_parse_ui_keeps_useful_nodes():
    elements = parse_ui(UI)
    assert [e.text or e.desc for e in elements] == ["Envoyer", "Code"]
    send, pin = elements
    assert send.center == (980, 1850) and send.rid == "send_button" and send.clickable
    assert pin.password and "saisie" in pin.states and "focus" in pin.states
    assert send.line(0.5).endswith("@490,925")


def test_irreversible_buttons_need_confirmation():
    send, pin = parse_ui(UI)
    assert policy.irreversible(send) == "Envoyer"
    assert policy.irreversible(pin) is None
    icon = Element(1, "ImageButton", "", "", "compose_message_send", "x", (0, 0, 1, 1), True)
    assert policy.irreversible(icon)
    assert policy.irreversible(Element(1, "Button", "Envoyé hier", "", "", "x", (0, 0, 1, 1), True)) is None
    assert policy.protected_app("com.revolut.revolut") == "Revolut"


def test_mask_codes_only_in_security_messages():
    assert mask_codes("Votre code de vérification est 482 913.") == "Votre code de vérification est [code masqué]."
    assert mask_codes("G-123456 est votre code Google") == "[code masqué] est votre code Google"
    assert mask_codes("On se voit à 1830 au 12 rue") == "On se voit à 1830 au 12 rue"
    assert mask_codes("Code promo : 20 % sur 1 250,00 €") == "Code promo : 20 % sur 1 250,00 €"


def test_parse_rows_multiline_body():
    out = "Row: 0 _id=92, date=1791871247643, type=1, body=Bonjour, ça va ?\nÀ demain\nRow: 1 _id=91, date=1, type=2, body=Ok\n"
    rows = parse_rows(out, "body")
    assert rows[0]["_id"] == "92" and rows[0]["body"] == "Bonjour, ça va ?\nÀ demain"
    assert rows[1]["body"] == "Ok" and rows[1]["type"] == "2"
    assert parse_rows("No result found.\n", "body") == []


def test_devices_and_mdns():
    out = ("List of devices attached\nRFTEST00001 device product:h8q model:SM_F971B device:h8q transport_id:1\n"
           "192.0.2.20:37099 device model:SM_F971B\nXYZ unauthorized\n")
    devices = parse_devices(out)
    assert [(d.serial, d.transport, d.model) for d in devices[:2]] == [
        ("RFTEST00001", "usb", "SM-F971B"), ("192.0.2.20:37099", "wifi", "SM-F971B")]
    assert devices[2].state == "unauthorized"
    mdns = "List of discovered mdns services\nadb-RFTE-x\t_adb-tls-connect._tcp\t192.0.2.20:37099\n"
    assert parse_mdns(mdns) == ["192.0.2.20:37099"]


def test_display_and_state():
    dump = '  mDisplayId=0\n    mBaseDisplayInfo=DisplayInfo{"Écran intégré", displayId 0, uniqueId "local:1234567890123456789", app}'
    assert parse_logical_display(dump) == "1234567890123456789"
    primary = "  mDisplayId=0\n    mPrimaryDisplayDevice=Écran intégré(local:1234567890123456789)\n    mIsEnabled=true"
    assert parse_logical_display(primary) == "1234567890123456789"
    state = parse_state("mWakefulness=Awake", "isKeyguardShowing=false\n  mCurrentFocus=Window{abc u0 com.whatsapp/com.whatsapp.Main}")
    assert state.awake and not state.locked and state.package == "com.whatsapp"


def test_normalize():
    assert normalize("+33 6 12 34 56 78") == "0612345678"


def test_adbd_port_from_proc_net():
    from nexus_android.adb import parse_adbd_port
    proc = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
            "   0: 00000000000000000000000000000000:AE65 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000 10119 0 1\n"
            "   1: 00000000000000000000000000000000:8211 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000  2000 0 2\n")
    assert parse_adbd_port(proc) == 33297
    assert parse_adbd_port("") is None


def test_contact_name_drops_extra_columns():
    import re
    assert re.sub(r", \w+=.*", "", "Alice, number=+33 6 00 00 00 00", flags=re.S) == "Alice"


def test_rcs_group_owner():
    group = "d4vtgmzwgezdgnbvgy3tqhzqgezdgnbvgy3tqolbmjrwizlggaytemzugu3doobzmfrggzdfmy======@rcs.google.com"
    assert rcs_group_owner(group) == "+33612345678"
    assert rcs_group_owner("+33600000001") is None
    assert rcs_group_owner("pas-du-base32!@rcs.google.com") is None
    assert rcs_group_owner("mv4gk3lqnrsv6x2bkrpv64tcnuxgo33pm5ptwx2flbcu2ucmivptwxzdgaydambqga======@bot.rcs.google.com") is None


def test_connect_survives_stale_wifi_port(monkeypatch, tmp_path):
    """Port du débogage sans fil changé : l'ancienne adresse ne répond plus, la nouvelle (mDNS) doit être prise."""
    import json
    import subprocess

    from nexus_android import adb as adb_mod

    state = tmp_path / "wifi.json"
    state.write_text(json.dumps({"usb": "RFTE", "address": "192.0.2.67:33915"}), encoding="utf-8")
    monkeypatch.setattr(adb_mod, "WIFI_STATE", state)
    monkeypatch.setattr(adb_mod, "_find_adb", lambda: "adb")
    monkeypatch.setattr(adb_mod, "open_ports", lambda host: [])  # jamais de vrai balayage réseau en test
    connected: list[str] = []

    def raw(self, args, timeout=30.0, serial=True):
        out = b""
        if args[0] == "-s" and args[3:] == ["getprop", "ro.serialno"]:
            out = b"RFTE\n" if args[1] in connected else b""
        elif args[0] == "devices":
            out = ("List of devices attached\n" + "".join(f"{a} device model:SM_F971B\n" for a in connected)).encode()
        elif args[0] == "mdns":
            out = b"adb-RFTE-x\t_adb-tls-connect._tcp\t192.0.2.67:42879\n"
        elif args[0] == "connect":
            if args[1].endswith(":33915"):
                raise adb_mod.PhoneError("pas de réponse après 8 s.")
            connected.append(args[1])
            out = f"connected to {args[1]}".encode()
        return subprocess.CompletedProcess(args, 0, out, b"")

    monkeypatch.setattr(adb_mod.Adb, "_raw", raw)
    assert adb_mod.Adb().connect().serial == "192.0.2.67:42879"
    assert json.loads(state.read_text(encoding="utf-8"))["address"] == "192.0.2.67:42879"
