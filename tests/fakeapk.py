"""Faux APK des Paramètres fabriqué octet par octet (table de ressources, XML binaires, dex), pour tester la carte du
téléphone sans téléphone ni outil Android."""

from __future__ import annotations

import io
import struct
import zipfile
from dataclasses import dataclass, field

ANDROID_IDS = {"title": 0x010101E1, "key": 0x010101E8, "summary": 0x010101E9, "fragment": 0x010102E3,
               "name": 0x01010003, "value": 0x01010024, "exported": 0x01010010, "label": 0x01010001,
               "targetPackage": 0x01010021, "targetClass": 0x0101002F}


def _pad4(data: bytes) -> bytes:
    return data + b"\0" * (-len(data) % 4)


def string_pool(strings: list[str]) -> bytes:
    body = b""
    offsets = []
    for s in strings:
        offsets.append(len(body))
        encoded = s.encode("utf-16-le")
        body += struct.pack("<H", len(encoded) // 2) + encoded + b"\0\0"
    body = _pad4(body)
    header = 28
    start = header + 4 * len(strings)
    size = start + len(body)
    return (struct.pack("<HHIIIIII", 0x0001, header, size, len(strings), 0, 0, start, 0)
            + struct.pack(f"<{len(strings)}I", *offsets) + body)


@dataclass
class Node:
    tag: str
    attrs: dict[str, str | int | bool] = field(default_factory=dict)  # str : chaîne brute, int : référence @0x…
    children: list[Node] = field(default_factory=list)


def axml(root: Node) -> bytes:
    """XML binaire : attributs android:* connus déclarés dans la table des identifiants (comme aapt2)."""
    names: list[str] = list(ANDROID_IDS)
    strings: list[str] = list(names)

    def idx(s: str) -> int:
        if s not in strings:
            strings.append(s)
        return strings.index(s)

    chunks: list[bytes] = []

    def emit(node: Node) -> None:
        attrs = b""
        for name, value in node.attrs.items():
            n = idx(name)
            if isinstance(value, bool):
                attrs += struct.pack("<iIiHBBI", -1, n, -1, 8, 0, 0x12, 0xFFFFFFFF if value else 0)
            elif isinstance(value, int):
                attrs += struct.pack("<iIiHBBI", -1, n, -1, 8, 0, 0x01, value)
            else:
                v = idx(value)
                attrs += struct.pack("<iIiHBBI", -1, n, v, 8, 0, 0x03, v)
        body = struct.pack("<iIHHHHHH", -1, idx(node.tag), 20, 20, len(node.attrs), 0, 0, 0) + attrs
        chunks.append(struct.pack("<HHIIi", 0x0102, 16, 16 + len(body), 1, -1) + body)
        for child in node.children:
            emit(child)
        chunks.append(struct.pack("<HHIIiiI", 0x0103, 16, 24, 1, -1, -1, idx(node.tag)))

    emit(root)
    pool = string_pool(strings)
    resmap = struct.pack("<HHI", 0x0180, 8, 8 + 4 * len(names)) + struct.pack(
        f"<{len(names)}I", *(ANDROID_IDS[n] for n in names))
    payload = pool + resmap + b"".join(chunks)
    return struct.pack("<HHI", 0x0003, 8, 8 + len(payload)) + payload


def _config(language: str) -> bytes:
    cfg = bytearray(64)
    struct.pack_into("<I", cfg, 0, 64)
    cfg[8:10] = language.encode().ljust(2, b"\0") if language else b"\0\0"
    return bytes(cfg)


def arsc(strings: dict[str, list[tuple[str, str]]], xmls: list[tuple[str, str]]) -> bytes:
    """Table de ressources : type 1 « string » (une valeur par langue, "" = défaut), type 2 « xml » (chemins).
    strings : langue → [(nom d'entrée, texte)] dans l'ordre des identifiants 0x7f01xxxx."""
    values: list[str] = []
    keys: list[str] = []

    def v(s: str) -> int:
        values.append(s)
        return len(values) - 1

    def k(s: str) -> int:
        if s not in keys:
            keys.append(s)
        return keys.index(s)

    def type_chunk(type_id: int, language: str, entries: list[tuple[str, str]]) -> bytes:
        header = 20 + 64
        offsets, data = [], b""
        for name, text in entries:
            offsets.append(len(data))
            data += struct.pack("<HHI", 8, 0, k(name)) + struct.pack("<HBBI", 8, 0, 0x03, v(text))
        start = header + 4 * len(entries)
        head = struct.pack("<HHIBBHII", 0x0201, header, start + len(data), type_id, 0, 0, len(entries), start)
        return head + _config(language) + struct.pack(f"<{len(entries)}I", *offsets) + data

    types = b"".join(type_chunk(1, lang, rows) for lang, rows in strings.items()) + type_chunk(2, "", xmls)
    type_names = string_pool(["string", "xml"])
    key_pool = string_pool(keys)
    header = 288
    name = "com.android.settings".encode("utf-16-le").ljust(256, b"\0")
    package_head = struct.pack("<HHII", 0x0200, header, header + len(type_names) + len(key_pool) + len(types), 0x7F)
    package_head += name + struct.pack("<IIIII", header, 2, header + len(type_names), len(keys), 0)
    package = package_head + type_names + key_pool + types
    pool = string_pool(values)
    return struct.pack("<HHII", 0x0002, 12, 12 + len(pool) + len(package), 1) + pool + package


def _uleb(value: int) -> bytes:
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        out.append(b | (0x80 if value else 0))
        if not value:
            return bytes(out)


def dex(classes: dict[str, dict[str, list[int | list[int]]]]) -> bytes:
    """Dex minimal : classe → {méthode : constantes chargées par « const v0, #… », ou unités brutes (liste)} (puis
    return)."""
    strings: list[str] = []
    for cls, methods in classes.items():
        strings.append("L" + cls.replace(".", "/") + ";")
        strings.extend(m for m in methods if m not in strings)
    type_idx = {cls: strings.index("L" + cls.replace(".", "/") + ";") for cls in classes}
    methods = [(cls, m) for cls, ms in classes.items() for m in ms]
    head = 0x70
    string_ids_off = head
    type_ids_off = string_ids_off + 4 * len(strings)
    method_ids_off = type_ids_off + 4 * len(classes)
    class_defs_off = method_ids_off + 8 * len(methods)
    data_off = class_defs_off + 32 * len(classes)
    data = bytearray()
    string_offsets = []
    for s in strings:
        string_offsets.append(data_off + len(data))
        data += _uleb(len(s)) + s.encode() + b"\0"
    while len(data) % 4:
        data += b"\0"
    code_offsets = []
    for cls, m in methods:
        code_offsets.append(data_off + len(data))
        insns: list[int] = []
        for value in classes[cls][m]:
            insns += value if isinstance(value, list) else [0x0014, value & 0xFFFF, value >> 16]
        insns.append(0x000F)
        data += struct.pack("<HHHHII", 1, 1, 0, 0, 0, len(insns)) + struct.pack(f"<{len(insns)}H", *insns)
        while len(data) % 4:
            data += b"\0"
    class_data_offsets = []
    order = list(classes)
    for cls in order:
        class_data_offsets.append(data_off + len(data))
        own = [i for i, (c, _) in enumerate(methods) if c == cls]
        data += _uleb(0) + _uleb(0) + _uleb(0) + _uleb(len(own))
        previous = 0
        for i in own:
            data += _uleb(i - previous) + _uleb(1) + _uleb(code_offsets[i])
            previous = i
    out = bytearray(b"dex\n035\0" + b"\0" * (head - 8))
    struct.pack_into("<IIII", out, 0x38, len(strings), string_ids_off, len(classes), type_ids_off)
    struct.pack_into("<IIII", out, 0x58, len(methods), method_ids_off, len(classes), class_defs_off)
    out += struct.pack(f"<{len(strings)}I", *string_offsets)
    out += struct.pack(f"<{len(classes)}I", *(type_idx[c] for c in order))
    for cls, m in methods:
        out += struct.pack("<HHI", order.index(cls), 0, strings.index(m))
    for n, cls in enumerate(order):
        out += struct.pack("<IIIIIIII", order.index(cls), 1, 0, 0, 0, 0, class_data_offsets[n], 0)
    return bytes(out + data)


STR_NOTIF, STR_HIDE, STR_LABEL = 0x7F010000, 0x7F010001, 0x7F010002
XML_TOP, XML_NOTIF, XML_DEEP = 0x7F020000, 0x7F020001, 0x7F020002
FRAGMENT = "com.android.settings.notification.ConfigureNotificationSettings"
DEEP_FRAGMENT = "com.samsung.android.settings.notification.Advanced"


def settings_apk() -> bytes:
    """Accueil > Notifications (activité exportée) > Paramètres avancés (sans activité) ; Galaxy Buds vers une autre
    application ; une bascule « Masquer le contenu » ; un pied de page à ignorer."""
    table = arsc({
        "": [("notifications", "Notifications"), ("hide", "Hide content"), ("label", "Notifications")],
        "fr": [("notifications", "Notifications"), ("hide", "Masquer le contenu"), ("label", "Notifications")],
        "de": [("notifications", "Benachrichtigungen"), ("hide", "Inhalt ausblenden"), ("label", "x")],
    }, [("sec_top_level_settings", "res/xml/sec_top_level_settings.xml"),
        ("sec_notif", "res/xml/sec_notif.xml"), ("sec_deep", "res/xml/sec_deep.xml")])
    top = Node("PreferenceScreen", {"key": "top"}, [
        Node("com.android.settings.widget.HomepagePreference",
             {"title": STR_NOTIF, "key": "top_level_notifications", "fragment": FRAGMENT}),
        Node("com.android.settings.widget.HomepagePreference", {"title": "Galaxy Buds", "key": "top_level_buds"},
             [Node("intent", {"targetPackage": "com.samsung.accessory.budsunitemgr",
                              "targetClass": "com.samsung.accessory.hearablemgr.WelcomeActivity"})]),
    ])
    notif = Node("PreferenceScreen", {}, [
        Node("PreferenceCategory", {"title": "Écran de verrouillage"}, [
            Node("SwitchPreferenceCompat", {"title": STR_HIDE, "key": "lock_screen_show_notifications",
                                            "summary": "Sur l'écran verrouillé"}),
        ]),
        Node("Preference", {"title": "Paramètres avancés", "key": "advanced", "fragment": DEEP_FRAGMENT}),
        Node("com.android.settingslib.widget.FooterPreference", {"title": "Texte d'explication sur les notifications"}),
    ])
    deep = Node("PreferenceScreen", {}, [
        Node("SwitchPreference", {"title": "Rappels de notification", "key": "notification_reminder"}),
    ])
    manifest = Node("manifest", {"package": "com.android.settings"}, [Node("application", {}, [
        Node("activity", {"name": "com.android.settings.Settings$ConfigureNotificationSettingsActivity",
                          "exported": True, "label": STR_LABEL}, [
            Node("intent-filter", {}, [Node("action", {"name": "android.settings.NOTIFICATION_SETTINGS"})]),
            Node("meta-data", {"name": "com.android.settings.FRAGMENT_CLASS", "value": FRAGMENT}),
        ]),
        Node("activity", {"name": "com.android.settings.Settings$HiddenActivity", "exported": False,
                          "label": "Caché"}),
    ])])
    code = dex({FRAGMENT: {"<clinit>": [XML_NOTIF], "getPreferenceScreenResId": [XML_NOTIF]},
                DEEP_FRAGMENT: {"getPreferenceScreenResId": [0x01010000, XML_DEEP]}})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("resources.arsc", table)
        z.writestr("AndroidManifest.xml", axml(manifest))
        z.writestr("res/xml/sec_top_level_settings.xml", axml(top))
        z.writestr("res/xml/sec_notif.xml", axml(notif))
        z.writestr("res/xml/sec_deep.xml", axml(deep))
        z.writestr("res/xml/not_a_screen.xml", b"garbage")
        z.writestr("classes.dex", code)
    return buf.getvalue()
