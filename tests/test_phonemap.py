"""Carte du téléphone : lecture d'un faux APK des Paramètres fabriqué octet par octet, recherche, instantané masqué,
comparaison, passe à l'écran et outil map avec un adb simulé."""

from __future__ import annotations

import asyncio
import gzip
import io
import json
import struct
import zipfile
from types import SimpleNamespace
from typing import Any

import pytest
from fakeapk import DEEP_FRAGMENT, FRAGMENT, dex, settings_apk
from test_robustness import _NoLock

from nexus_android import phonemap, server
from nexus_android.adb import PhoneError
from nexus_android.apkres import Apk, Dex, FormatError, ResourceTable, StringPool, _uleb, parse_axml


@pytest.fixture
def apk_path(tmp_path):
    path = tmp_path / "SecSettings.apk"
    path.write_bytes(settings_apk())
    return str(path)


@pytest.fixture
def index(apk_path):
    return phonemap.build_index(apk_path)


def entry(index, title):
    return next(e for e in index["entries"] if e["title"] == title)


# ---------------------------------------------------------------- lecture de l'APK


def test_resource_table_prefers_the_phone_language(apk_path):
    apk = Apk(apk_path, "fr", "FR")
    assert apk.table.string(0x7F010001) == "Masquer le contenu"
    assert Apk(apk_path, "en", "US").table.string(0x7F010001) == "Hide content"  # langue absente : défaut
    assert Apk(apk_path, "de", "").table.string(0x7F010001) == "Inhalt ausblenden"
    assert apk.table.file(0x7F020001) == "res/xml/sec_notif.xml"
    assert apk.table.is_type(0x7F020001, "xml") and not apk.table.is_type(0x7F010001, "xml")
    assert apk.table.string(0x01010000) is None  # ressource du système : pas dans la table


def test_dex_constants_link_a_fragment_to_its_screen(apk_path):
    found = Apk(apk_path).constants({FRAGMENT, DEEP_FRAGMENT, "com.absent.Fragment"})
    assert found[FRAGMENT] == [("<clinit>", 0x7F020001), ("getPreferenceScreenResId", 0x7F020001)]
    assert found[DEEP_FRAGMENT] == [("getPreferenceScreenResId", 0x7F020002)]  # 0x0101… ignoré
    assert "com.absent.Fragment" not in found


def test_dex_decodes_instruction_boundaries():
    """Relecture de Codex : un const-wide dont les opérandes ressemblent à « const 0x7f020001 » n'est pas lu comme tel ;
    une table de switch rangée dans le code est sautée."""
    wide: list[int] = [0x0018, 0x1234, 0x0014, 0x0001, 0x7F02, 0x000E]
    switch: list[int] = [0x0100, 1, 0, 0, 0x0014, 0x0001]  # packed-switch, 1 cible : « 0x0014 … » est une donnée
    code = dex({"a.B": {"m": [wide, switch, 0x7F020002]}})
    assert Dex(code).constants({"a.B"}) == {"a.B": [("m", 0x7F020002)]}


def test_bad_binary_files_are_refused():
    with pytest.raises(FormatError):
        parse_axml(b"garbage!")
    with pytest.raises(FormatError):
        parse_axml(b"\x03\x00\x08\x00\x08\x00\x00\x00")  # XML vide
    with zipfile.ZipFile(io.BytesIO(settings_apk())) as z:
        top = z.read("res/xml/sec_top_level_settings.xml")
    for cut in (12, 40, len(top) // 2, len(top) - 3):  # tronqué n'importe où : FormatError, jamais struct.error
        with pytest.raises(FormatError):
            parse_axml(top[:cut])
    with pytest.raises(FormatError):
        ResourceTable(b"")
    # Relecture de Codex, tour 2 : chaîne placée après la fin de son bloc, arbre sans fermeture de la racine
    pool = bytearray(struct.pack("<HHIIIIII", 0x0001, 28, 32, 1, 0, 0x100, 32, 0) + struct.pack("<I", 0))
    pool += b"\x07\x07OUTSIDE\x00"  # au-delà des 32 octets déclarés
    assert StringPool(bytes(pool), 0).get(0) == ""
    unclosed = top[:-24]  # dernière fermeture (celle de la racine) retirée, taille totale ajustée
    unclosed = unclosed[:4] + struct.pack("<I", len(unclosed)) + unclosed[8:]
    with pytest.raises(FormatError):
        parse_axml(unclosed)
    with pytest.raises(FormatError):
        parse_axml(top + b"\0\0\0\0")  # octets de fin qui ne forment pas un bloc
    with pytest.raises(FormatError):
        _uleb(b"\xff" * 20 + b"\x01", 0)


def test_truncated_optional_screen_is_skipped_and_broken_apk_refused(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(settings_apk())) as src, zipfile.ZipFile(buf, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            dst.writestr(name, data[:30] if name == "res/xml/sec_deep.xml" else data)
    path = tmp_path / "cut.apk"
    path.write_bytes(buf.getvalue())
    index = phonemap.build_index(str(path))
    assert any(e["title"] == "Masquer le contenu" for e in index["entries"])
    assert not any(e["title"] == "Rappels de notification" for e in index["entries"])
    bad = tmp_path / "bad.apk"
    bad.write_bytes(b"PK\x03\x04 not really")
    with pytest.raises(FormatError):
        Apk(str(bad))


# ---------------------------------------------------------------- arbre des Paramètres


def test_tree_paths_sections_and_direct_opening(index):
    hide = entry(index, "Masquer le contenu")
    assert hide["path"] == ["Notifications"] and hide["section"] == "Écran de verrouillage"
    assert hide["action"] == "android.settings.NOTIFICATION_SETTINGS"
    assert hide["key"] == "lock_screen_show_notifications" and hide["kind"] == "switch"
    assert hide["open"].endswith("Settings$ConfigureNotificationSettingsActivity")
    top = entry(index, "Notifications")
    assert top["path"] == [] and top["target_action"] == "android.settings.NOTIFICATION_SETTINGS"
    buds = entry(index, "Galaxy Buds")
    assert buds["target"] == "com.samsung.accessory.budsunitemgr/com.samsung.accessory.hearablemgr.WelcomeActivity"
    deep = entry(index, "Rappels de notification")  # écran sans activité : ouvrir le parent puis toucher
    assert deep["path"] == ["Notifications", "Paramètres avancés"]
    assert deep["via"] == {"action": "android.settings.NOTIFICATION_SETTINGS"} and deep["taps"] == ["Paramètres avancés"]
    assert not any("explication" in e["title"] for e in index["entries"])  # pied de page : pas un réglage
    assert [a["title"] for a in index["activities"]] == ["Notifications"]  # activité non exportée ignorée
    assert index["screens"] == 3


def test_search_ranks_the_setting_and_shows_value_and_opening(index):
    values = {"secure": {"lock_screen_show_notifications": "0"}, "system": {}, "global": {}}
    hits = phonemap.search("masquer le contenu des notifications", index, {}, values)
    assert hits[0].entry["title"] == "Masquer le contenu"
    text = phonemap.describe(hits[0], values)
    assert text.startswith("Paramètres > Notifications > Masquer le contenu (EN: Hide content)  (section « Écran de verrouillage »)")
    assert phonemap.search("hide content", index, {}, values)[0].entry["title"] == "Masquer le contenu"
    assert "now: secure/lock_screen_show_notifications=0" in text
    assert ('map(action="open", target="com.android.settings/com.android.settings.Settings$'
            'ConfigureNotificationSettingsActivity", key="lock_screen_show_notifications")') in text
    deep = phonemap.search("rappels", index, {}, values)[0]
    assert "then tap Paramètres avancés" in phonemap.describe(deep, values)
    assert phonemap.search("le la les", index, {}, values) == []


def test_search_accents_plurals_and_partial_fallback(index):
    assert phonemap.query_terms("Écran VERROUILLÉ, notifications") == ["ecran", "verrouille", "notification"]
    hits = phonemap.search("ecran verrouillage", index, {}, {})
    assert hits and hits[0].entry["title"] == "Masquer le contenu"  # section « Écran de verrouillage »
    partial = phonemap.search("lire notifications montre connectée", index, {}, {})
    assert partial and all(h.matched == 1 for h in partial)


def test_noted_screen_answers_without_searching(index):
    screens: dict = {}
    phonemap.note_screen(screens, "Paramètres > Galaxy Buds4 Pro > Notifications ",
                         [{"text": "Lire les notifications à voix haute", "state": "on"}],
                         "com.samsung.accessory.budsunitemgr/.NotificationActivity", append=False)
    hits = phonemap.search("lire les notifications sur les Buds", index, screens, {})
    assert hits[0].source == "screen"
    text = phonemap.describe(hits[0], {})
    assert text.startswith("Paramètres > Galaxy Buds4 Pro > Notifications > Lire les notifications à voix haute  [on]")
    assert 'map(action="open", target="com.samsung.accessory.budsunitemgr/.NotificationActivity")' in text


# ---------------------------------------------------------------- passe à l'écran


BUDS = "com.samsung.accessory.budsunitemgr"


def el(text, top, states=(), bottom=None, package=BUDS):
    return SimpleNamespace(text=text, states=list(states), bounds=(0, top, 100, bottom or top + 50), package=package)


def test_screen_items_attach_switch_state_and_mask():
    items = phonemap.screen_items([
        el("Notifications", 0), el("Lire à voix haute", 100), el("", 105, ["checked"], 145),
        el("Code : rien", 200), el("", 300, ["unchecked"], 340), el("Contact pierre@example.com", 400),
        el("Lire à voix haute", 500),  # doublon (en-tête collant) : gardé une fois
    ])
    assert items[0] == {"text": "Notifications"}
    assert items[1] == {"text": "Lire à voix haute", "state": "on"}
    assert "state" not in items[2]  # interrupteur trop loin : pas rattaché au hasard
    assert "pierre@example.com" not in items[3]["text"]
    assert len(items) == 4


def test_note_appends_after_scroll_and_requires_a_path():
    screens: dict = {}
    phonemap.note_screen(screens, "A > B", [{"text": "un"}], "p/.X", append=False)
    phonemap.note_screen(screens, "A>B", [{"text": "un"}, {"text": "deux"}], "", append=True)
    assert [i["text"] for i in screens["A > B"]["items"]] == ["un", "deux"] and screens["A > B"]["open"] == "p/.X"
    phonemap.note_screen(screens, "A > B", [{"text": "trois"}], "", append=False)
    assert [i["text"] for i in screens["A > B"]["items"]] == ["trois"]
    with pytest.raises(PhoneError):
        phonemap.note_screen(screens, " > ", [], "", append=False)


# ---------------------------------------------------------------- instantané et comparaison


def state_output(**over) -> str:
    parts = {
        "model": "SM-F971B", "fingerprint": "samsung/x:17/1", "display": "F971BXXU1AAAA", "patch": "2026-10-01",
        "system": "screen_brightness=120\nfont_scale=1.0",
        "secure": "android_id=abcdef0123456789\nenabled_notification_listeners=com.a/.L\nbluetooth_name=Fold de Alex",
        "global": "adb_wifi_enabled=1\nwifi_ssid_hint=MaBox",
        "packages": ("package:com.a versionCode:10  installer=com.android.vending uid:10100\n"
                     "package:com.b versionCode:3  installer=null uid:10101\n"
                     "package:com.android.phone versionCode:37  installer=null uid:1001"),
        "disabled": "package:com.b", "third": "package:com.a\npackage:com.b",
        "roles": "SMS=com.google.android.apps.messaging\nDIALER=com.samsung.android.dialer",
        "admins": "ComponentInfo{com.google.android.gms/com.google.android.gms.mdm.receivers.MdmDeviceAdminReceiver}",
        "bt": ("    XX:XX:XX:XX:FD:57(Public ) => XX:XX:XX:XX:FD:57(Public ) [ DUAL ] [0x244404] [ACL BR/EDR:N LE:N] "
               "Buds4 Pro de Alex"),
        "perms": ("  Package [com.a] (1):\n      android.permission.CAMERA: granted=true\n"
                  "      android.permission.INTERNET: granted=true\n  Package [com.b] (2):\n"
                  "      android.permission.ACCESS_FINE_LOCATION: granted=true"),
    }
    parts.update(over)
    return f"\n{phonemap.MARK}\n".join(parts.values())


def test_state_parsed_and_sensitive_values_masked():
    state = phonemap.parse_state(state_output())
    secure = state["settings"]["secure"]
    assert secure["android_id"].startswith(phonemap.MASKED + "#") and "abcdef" not in json.dumps(state)
    assert secure["bluetooth_name"].startswith(phonemap.MASKED) and "Alex" not in secure["bluetooth_name"]
    assert state["settings"]["global"]["wifi_ssid_hint"].startswith(phonemap.MASKED)
    assert secure["enabled_notification_listeners"] == "com.a/.L"
    assert state["packages"]["com.a"] == {"version": 10, "installer": "com.android.vending", "third_party": True}
    assert state["packages"]["com.b"]["disabled"] is True
    assert state["permissions"] == {"ACCESS_FINE_LOCATION": ["com.b"], "CAMERA": ["com.a"]}  # INTERNET : pas sensible
    assert state["roles"]["SMS"] == "com.google.android.apps.messaging"
    assert state["bluetooth"] == ["Buds4 Pro de Alex"] and "FD:57" not in json.dumps(state["bluetooth"])
    assert state["device"]["build"] == "F971BXXU1AAAA"


def test_mask_value_keeps_plain_settings_and_hides_identifiers(tmp_path):
    assert phonemap.mask_value("screen_brightness", "120") == "120"
    assert phonemap.mask_value("lock_screen_owner_info", "Alex 0612345678").startswith(phonemap.MASKED)
    assert phonemap.mask_value("some_note", "call 06 12 34 56 78") != "call 06 12 34 56 78"  # numéro masqué
    assert phonemap.mask_value("android_id", "x") != phonemap.mask_value("android_id", "y")  # diff possible
    assert phonemap.mask_value("android_id", "null") == "null"
    # Relecture de Codex : identifiant de profil DNS et adresses réseau
    assert phonemap.mask_value("private_dns_specifier", "abc123.dns.nextdns.io").startswith(phonemap.MASKED)
    assert phonemap.mask_value("wifi_static_ip", "192.0.2.15").startswith(phonemap.MASKED)
    assert phonemap.mask_value("anything", "gw 192.0.2.1").startswith(phonemap.MASKED)
    assert phonemap.mask_value("anything", "aa:bb:cc:dd:ee:ff").startswith(phonemap.MASKED)
    assert phonemap.mask_value("font_scale", "1.0") == "1.0"
    # Relecture de Codex, tour 2 : IPv6 abrégées, avec zone, entre crochets, IPv4 incluse
    for value in ("::1", "fe80::1", "fe80::abcd%wlan0", "[2001:db8::7]:443", "::ffff:192.0.2.1", "x 10.0.0.1."):
        assert phonemap.mask_value("anything", value).startswith(phonemap.MASKED), value
    # Tour 3 : IPv4 avec port, dans une URL, collée à un libellé ; jusqu'au fichier écrit sur le disque
    for value in ("192.0.2.1:443", "http://192.0.2.1:8080", "gw:192.0.2.1"):
        assert phonemap.mask_value("endpoint", value).startswith(phonemap.MASKED), value
    state = phonemap.parse_state(state_output(**{"global": "captive_portal_http_url=http://192.0.2.1:8080/generate_204"}))
    saved = phonemap.save_snapshot(tmp_path, state)
    assert "192.0.2.1" not in gzip.decompress(saved.read_bytes()).decode()
    for value in ("1.0", "12:30", "1.2.3.4.5","com.a/.L", "content://media/32?title=Dudum", "a:b", "0", "2026-10-20 01:02:03"):
        assert phonemap.mask_value("anything", value) == value, value


def test_diff_lists_what_changed_without_revealing_masked_values():
    old = phonemap.parse_state(state_output())
    new = phonemap.parse_state(state_output(
        display="F971BXXS4BZK1", system="screen_brightness=200\nfont_scale=1.0\nnew_setting=1",
        secure="android_id=ffffffff00000000\nenabled_notification_listeners=com.a/.L\nbluetooth_name=Fold de Alex",
        packages=("package:com.a versionCode:11  installer=com.android.vending uid:10100\n"
                  "package:com.c versionCode:1  installer=null uid:10102\n"
                  "package:com.android.phone versionCode:37  installer=null uid:1001"),
        disabled="", third="package:com.a\npackage:com.c", roles="SMS=com.samsung.android.messaging",
        perms="  Package [com.a] (1):\n      android.permission.RECORD_AUDIO: granted=true", bt=""))
    old["taken"], new["taken"] = "2026-10-19T10:00:00", "2026-10-20T10:00:00"
    text = phonemap.diff(old, new)
    assert "System build: F971BXXU1AAAA → F971BXXS4BZK1" in text
    assert "system/screen_brightness: 120 → 200" in text and "system/new_setting: (new) 1" in text
    assert f"secure/android_id: {phonemap.MASKED} → {phonemap.MASKED}" in text and "ffff" not in text
    assert "App installed: com.c" in text and "App removed: com.b" in text and "App updated: com.a 10 → 11" in text
    assert "Permission RECORD_AUDIO granted to: com.a" in text and "Permission CAMERA withdrawn from: com.a" in text
    assert "Permission ACCESS_FINE_LOCATION withdrawn from: com.b" in text
    assert "Default sms app: com.google.android.apps.messaging → com.samsung.android.messaging" in text
    assert "Default dialer app: com.samsung.android.dialer → -" in text
    assert "Bluetooth device removed: Buds4 Pro de Alex" in text
    assert phonemap.diff(old, old).endswith("nothing changed.")


def test_snapshots_kept_and_picked_by_date(tmp_path, monkeypatch):
    names = ["20261018-090000", "20261019-120000", "20261020-080000"]
    for n in names:
        (tmp_path / "snapshots").mkdir(exist_ok=True)
        (tmp_path / "snapshots" / f"{n}.json.gz").write_bytes(gzip.compress(b"{}"))
    paths = phonemap.snapshots(tmp_path)
    assert phonemap.pick_snapshot(paths, None) == paths[-1]
    assert phonemap.pick_snapshot(paths, "2026-10-19 13:00") == paths[1]
    assert phonemap.pick_snapshot(paths, "2026-01-01 00:00") == paths[0]  # rien d'avant : le plus ancien
    assert phonemap.pick_snapshot([], "2h") is None
    with pytest.raises(PhoneError):
        phonemap.pick_snapshot(paths, "hier soir")
    same = tmp_path / "same"
    (same / "snapshots").mkdir(parents=True)
    for n in ["20261020-010203", *(f"20261020-010203-{i}" for i in range(1, 12))]:
        (same / "snapshots" / f"{n}.json.gz").write_bytes(gzip.compress(b"{}"))
    ordered = [p.name for p in phonemap.snapshots(same)]
    assert ordered[0] == "20261020-010203.json.gz" and ordered[-1] == "20261020-010203-11.json.gz"
    assert ordered[2] == "20261020-010203-2.json.gz"  # numérique, pas lexical
    monkeypatch.setattr(phonemap, "MAX_SNAPSHOTS", 2)
    phonemap.save_snapshot(tmp_path, {"taken": "x"})
    assert len(phonemap.snapshots(tmp_path)) == 2


# ---------------------------------------------------------------- outil map (adb simulé)


class MapPhone:
    serial = "192.0.2.67:5555"

    def __init__(self, apk: bytes) -> None:
        self.apk = apk
        self.calls: list[tuple[str, ...]] = []
        self.pulls = 0
        self.top = "com.samsung.accessory.budsunitemgr/com.samsung.accessory.hearablemgr.NotificationActivity"

    def identity(self, serial: str) -> str:
        return "R5CX123"

    def connect(self):
        return None

    def shell(self, *argv: str, timeout: float = 30.0, check: bool = True) -> str:
        self.calls.append(argv)
        if argv[:2] == ("pm", "path"):
            return "package:/system/priv-app/SecSettings/SecSettings.apk\n"
        if argv[0] == "stat":
            return "118848221:1230768000\n"
        if argv[:2] == ("getprop", "ro.build.fingerprint"):
            return "samsung/x:17/1\n"
        if argv[:2] == ("getprop", "persist.sys.locale"):
            return "fr-FR\n"
        if argv[0] == "sh" and "topResumedActivity" in argv[2]:
            return f"  topResumedActivity=ActivityRecord{{1a2b u0 {self.top} t12}}\n"
        if argv[0] == "sh" and "settings list system; echo" in argv[2] and "pm list" not in argv[2]:
            return f"font_scale=1.0\n{phonemap.MARK}\nlock_screen_show_notifications=1\n{phonemap.MARK}\nx=1\n"
        if argv[0] == "sh" and "pm list packages" in argv[2]:
            return state_output()
        return ""

    def host(self, *args: str, timeout: float = 60.0) -> str:
        self.calls.append(args)
        if args[0] == "pull":
            self.pulls += 1
            with open(args[-1], "wb") as f:
                f.write(self.apk)
        return ""


class MapScreen:
    def __init__(self) -> None:
        self.package = BUDS
        self.elements = [el("Notifications", 0), el("Lire les notifications à voix haute", 100),
                         el("", 105, ["checked"], 145)]

    def read(self, image: bool, tree: bool, full: bool = False):
        return SimpleNamespace(error=None, state=SimpleNamespace(locked=False, package=self.package),
                               elements=self.elements)


@pytest.fixture
def phone(monkeypatch, tmp_path):
    fake = MapPhone(settings_apk())
    monkeypatch.setattr(phonemap, "MAP_DIR", tmp_path / "map")
    monkeypatch.setattr(server, "ADB", fake)
    monkeypatch.setattr(server, "SCREEN", MapScreen())
    monkeypatch.setattr(server, "MESSAGES", object())
    monkeypatch.setattr(server, "PhoneLock", _NoLock)
    return fake


def run(**kw) -> str:
    result = asyncio.run(server.phone_map(**kw))
    assert isinstance(result, str)
    return result


def test_find_builds_the_map_once_and_reads_values_live(phone):
    text = run(query="masquer le contenu")
    assert "(map rebuilt from the Settings app first)" in text and phone.pulls == 1
    assert ("zstd" in phone.calls[[c[0] for c in phone.calls].index("pull")])  # tiré compressé
    assert "now: secure/lock_screen_show_notifications=1" in text
    again = run(query="masquer le contenu")
    assert phone.pulls == 1 and "rebuilt" not in again  # cache : même APK, même build
    assert not list((phonemap.MAP_DIR).rglob("*.apk"))  # APK tiré effacé


def test_find_rebuilds_after_a_system_update(phone):
    run(query="notifications")
    phone.shell = (lambda orig: lambda *a, **k: "samsung/x:17/2\n" if a[:2] == ("getprop", "ro.build.fingerprint")
                   else orig(*a, **k))(phone.shell)
    assert "rebuilt" in run(query="notifications") and phone.pulls == 2
    assert len(list(phonemap.MAP_DIR.rglob("settings-*.json.gz"))) == 1  # ancien cache retiré


def test_note_then_find_answers_the_buds_question(phone):
    text = run(action="note", path="Paramètres > Galaxy Buds4 Pro > Notifications")
    assert text.startswith("Noted 2 lines under « Paramètres > Galaxy Buds4 Pro > Notifications »")
    assert "Lire les notifications à voix haute [on]" in text
    found = run(query="lire les notifications sur les Buds")
    assert "1. Paramètres > Galaxy Buds4 Pro > Notifications > Lire les notifications à voix haute  [on]" in found
    assert f'target="{phone.top}"' in found


def test_note_refuses_a_banking_app(phone):
    phone.top = "fr.creditagricole.androidapp/.Main"
    with pytest.raises(Exception, match="banking"):
        run(action="note", path="Banque > Comptes")


def test_note_checks_every_source_of_the_screen(phone):
    """Relecture de Codex : arbre lu dans une appli protégée puis premier plan bénin, ou bulle protégée par-dessus."""
    screen: Any = server.SCREEN
    screen.package = "com.revolut.revolut"
    phone.top = "com.android.settings/.Settings"
    with pytest.raises(Exception, match="banking"):
        run(action="note", path="A > B")
    screen.package = "com.android.settings"
    screen.elements = [el("Solde 1 234 €", 0, package="com.revolut.revolut")]
    with pytest.raises(Exception, match="banking"):
        run(action="note", path="A > B")
    screen.elements = [el("Wi-Fi", 0, package="com.android.settings")]
    phone.top = BUDS + "/.Main"  # premier plan changé entre la lecture et l'activité
    with pytest.raises(Exception, match="foreground changed"):
        run(action="note", path="A > B")
    assert not list(phonemap.MAP_DIR.rglob("screens.json"))  # rien d'écrit


def test_open_needs_a_component_and_highlights_the_row(phone, monkeypatch):
    seen = {}
    monkeypatch.setattr(server, "_open_intent", lambda adb, screen, package, intent: seen.setdefault("i", intent))
    known = "com.android.settings/com.android.settings.Settings$ConfigureNotificationSettingsActivity"
    asyncio.run(server.phone_map(action="open", target=known, key="lock_screen_show_notifications"))
    assert seen["i"] == ["-n", known, "--es", ":settings:fragment_args_key", "lock_screen_show_notifications"]
    with pytest.raises(Exception, match="package/activity"):
        run(action="open", target="am start -a x; reboot")
    # Relecture d'Antigravity : seulement un écran que la carte donne, jamais une activité quelconque
    with pytest.raises(Exception, match="only a screen the map gives"):
        run(action="open", target="com.android.settings/com.android.settings.MasterClear")


def test_note_skips_password_fields_and_password_managers(phone):
    screen: Any = server.SCREEN
    field = el("hunter2", 200, package=BUDS)
    field.password = True
    screen.elements = [el("Compte", 0), field]
    text = run(action="note", path="A > B")
    assert "hunter2" not in text and "Compte" in text
    phone.top = "com.x8bit.bitwarden/.MainActivity"
    screen.package = "com.x8bit.bitwarden"
    with pytest.raises(Exception, match="password manager"):
        run(action="note", path="Coffre > Comptes")


def test_package_fields_in_any_order_and_orphan_screens_kept(tmp_path):
    state = phonemap.parse_state(state_output(packages=(
        "package:com.a uid:10100 versionCode:10 installer=com.android.vending\n"
        "package:com.b installer=null uid:10101 versionCode:3")))
    assert state["packages"]["com.a"]["version"] == 10 and state["packages"]["com.b"]["installer"] == "null"
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(settings_apk())) as src, zipfile.ZipFile(buf, "w") as dst:
        for name in src.namelist():
            if name != "classes.dex":  # code absent (APK pré-optimisé) : écrans gardés, place inconnue
                dst.writestr(name, src.read(name))
    path = tmp_path / "nodex.apk"
    path.write_bytes(buf.getvalue())
    index = phonemap.build_index(str(path))
    hide = entry(index, "Masquer le contenu")
    assert hide.get("orphan") and hide["path"] == ["[sec_notif]"]
    hit = phonemap.search("masquer le contenu", index, {}, {})[0]
    assert phonemap.describe(hit, {}).startswith("Settings screen [sec_notif] (place in the menus unknown)")


def test_snapshot_then_diff(phone):
    first = run(action="diff")
    assert "First snapshot of this phone" in first and "settings map 5 lines" in first
    phone.shell = (lambda orig: lambda *a, **k: state_output(system="screen_brightness=50\nfont_scale=1.0")
                   if a[0] == "sh" and "pm list packages" in a[2] else orig(*a, **k))(phone.shell)
    second = run(action="diff")
    assert "system/screen_brightness: 120 → 50" in second
    assert len(phonemap.snapshots(next(phonemap.MAP_DIR.iterdir()))) == 2


def test_find_without_query_is_refused(phone):
    with pytest.raises(Exception, match="query required"):
        run()
