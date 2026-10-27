"""Carte du téléphone : où se règle quoi, et ce qui a changé.

Deux sources, tirées par ADB sans toucher l'écran :
- l'arbre des Paramètres, reconstruit hors ligne depuis l'APK des Paramètres (l'index de recherche du téléphone est
  réservé aux applications système) : titre, chemin « Notifications > … », clé, ouverture directe (activité exportée
  qui affiche l'écran, avec la ligne mise en évidence) ;
- un instantané de l'état : valeurs de `settings` (system, secure, global), applications et versions, permissions
  sensibles accordées, accès spéciaux (rôles par défaut, administrateurs), appareils Bluetooth associés.
Les écrans que l'APK ne décrit pas (applications à écrans dessinés par le code, comme Galaxy Buds) s'ajoutent par une
passe à l'écran : l'agent y navigue, `note` enregistre ce qui est affiché sous le chemin qu'il donne.

Tout reste sur le PC (~/.nexus/nexus-android/map), jamais dans les sources ; valeurs sensibles (jetons, identifiants,
adresses, numéros) masquées avant l'écriture, avec une empreinte courte pour que `diff` voie qu'elles ont changé."""

from __future__ import annotations

import gzip
import hashlib
import ipaddress
import json
import os
import re
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import logs as logs_mod
from .adb import Adb, PhoneError
from .apkres import Apk, Element, FormatError
from .messages import plain

MAP_DIR = Path.home() / ".nexus" / "nexus-android" / "map"
SETTINGS_PACKAGE = "com.android.settings"
ROOT_XML = ("res/xml/sec_top_level_settings.xml", "res/xml/top_level_settings.xml")
HOME = "Paramètres"
HOME_ACTION = "android.settings.SETTINGS"
HIGHLIGHT = ":settings:fragment_args_key"  # extra lu par SettingsActivity : ligne mise en évidence à l'ouverture
FRAGMENT_META = "com.android.settings.FRAGMENT_CLASS"
INDEX_VERSION = 4  # change quand la forme de l'index change : l'ancien cache est reconstruit
MAX_SNAPSHOTS = 30
MASKED = "[masked]"

# Clés dont la valeur identifie l'utilisateur, l'appareil ou un compte : jamais écrites en clair.
_SENSITIVE_KEY = re.compile(
    r"(?i)(token|secret|passw|pwd|\bpin\b|_pin_|android_id|advertising_id|bluetooth_address|bluetooth_name|"
    r"mac_?addr|imei|imsi|iccid|meid|msisdn|serial|phone_?number|line1|account|email|e_mail|gaia|cookie|credential|"
    r"auth|ssid|bssid|uuid|guid|device_name|owner_info|nickname|user_?name|lock_screen_owner|_key$|^key_|cert|"
    r"signature|salt|seed|location_providers|wifi_.*network|vpn|proxy|dns|hotspot|hostname|gateway|netmask|"
    r"ip_?addr|(^|_)ip($|_)|static_ip|specifier|address)")
# Valeur qui est une adresse (IPv4, IPv6, MAC), quelle que soit sa clé.
_MAC = re.compile(r"(?i)\b([0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b")
_IPV4 = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.]*\d)")
_ADDRESS_CANDIDATE = re.compile(r"[0-9A-Za-z:.%\[\]_-]{2,64}")


def has_address(value: str) -> bool:
    """Valeur qui contient une adresse IPv4, IPv6 (abrégée « ::1 », avec zone « fe80::1%wlan0 », entre crochets,
    IPv4 incluse) ou MAC : candidats bornés validés par ipaddress, pas par une expression approximative."""
    if _MAC.search(value):
        return True
    for candidate in _IPV4.findall(value):  # « 192.0.2.1:443 », « http://192.0.2.1:8080/… », « gw:192.0.2.1 »
        try:
            ipaddress.IPv4Address(candidate)
            return True
        except ValueError:
            continue
    bracketed = re.findall(r"\[([^\]]{2,64})\]", value)  # « [2001:db8::7]:443 » : l'adresse sans le port
    for candidate in [*bracketed, *_ADDRESS_CANDIDATE.findall(value)]:
        token = candidate.strip("[]").split("%", 1)[0].rstrip(".")
        if (":" in token or token.count(".") == 3) and len(token) >= 2:
            try:
                ipaddress.ip_address(token)
                return True
            except ValueError:
                continue
    return False


def mask_value(key: str, value: str) -> str:
    """Valeur d'un réglage telle qu'elle est gardée : clé sensible → masquée avec empreinte courte (diff possible sans
    la révéler) ; sinon texte où jetons, e-mails et numéros sont masqués."""
    if value in ("", "null"):
        return value
    if _SENSITIVE_KEY.search(key) or has_address(value):
        return f"{MASKED}#{hashlib.sha256(value.encode()).hexdigest()[:8]}"
    return logs_mod.mask(value)


def _norm(text: str) -> str:
    return plain(text or "").lower()


_STOP = {"le", "la", "les", "de", "des", "du", "un", "une", "et", "ou", "sur", "pour", "par", "dans", "en", "au",
         "aux", "a", "mes", "mon", "ma", "ses", "son", "sa", "ce", "cette", "the", "of", "on", "to", "in", "for", "my",
         "and", "or", "est", "comment", "se", "regle", "reglage", "where", "how", "set"}


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^0-9a-z]+", _norm(text)) if w]


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word[-1] in "sx" else word


def query_terms(query: str) -> list[str]:
    return list(dict.fromkeys(_stem(w) for w in _words(query) if w not in _STOP and len(w) > 1))


def _hit(term: str, words: Iterable[str]) -> bool:
    for w in words:
        w = _stem(w)
        if w == term or (len(term) >= 4 and w.startswith(term)) or (len(w) >= 4 and term.startswith(w)):
            return True
    return False


# ---------------------------------------------------------------- arbre des Paramètres (APK)


@dataclass
class Screen:
    xml: str
    fragment: str = ""
    title: str = ""
    path: tuple[str, ...] = ()  # titres des écrans à traverser depuis l'accueil, celui-ci compris
    component: str = ""  # activité exportée qui l'ouvre directement
    action: str = ""
    reached: bool = True  # atteint depuis l'accueil des Paramètres (sinon : ouvert seulement par son activité)
    parent: str = ""  # écran d'où l'on vient


def _text(apk: Apk, el: Element, name: str) -> str:
    return (apk.table.text(el.get(name)) or "").strip()


def _raw(el: Element, name: str) -> str:
    v = el.get(name)
    return (v.raw or "").strip() if v is not None and v.raw else ""


def _activities(apk: Apk) -> tuple[dict[str, tuple[str, str, str]], dict[str, tuple[str, str]]]:
    """Manifeste : fragment → (activité exportée, action publique, libellé) ; activité → (action, libellé)."""
    by_fragment: dict[str, tuple[str, str, str]] = {}
    by_name: dict[str, tuple[str, str]] = {}
    package = _raw(apk.manifest(), "package") or SETTINGS_PACKAGE
    for el in apk.manifest().walk():
        if el.tag not in ("activity", "activity-alias"):
            continue
        name = _raw(el, "name")
        if name.startswith("."):
            name = package + name
        exported = el.get("exported")
        filters = [c for c in el.children if c.tag == "intent-filter"]
        if exported is not None and not exported.data or (exported is None and not filters):
            continue
        actions = [_raw(a, "name") for f in filters for a in f.children if a.tag == "action"]
        public = next((a for a in actions if a and a != "android.intent.action.MAIN"), "")
        label = _text(apk, el, "label")
        by_name[name] = (public, label)
        for meta in (c for c in el.children if c.tag == "meta-data"):
            if _raw(meta, "name") == FRAGMENT_META and (fragment := _raw(meta, "value")):
                current = by_fragment.get(fragment)
                if current is None or (public and not current[1]):  # une action publique est plus stable
                    by_fragment[fragment] = (f"{package}/{name}", public, label)
    return by_fragment, by_name


def _resolve_intent(apk: Apk, el: Element) -> tuple[str, str]:
    for child in el.children:
        if child.tag == "intent":
            pkg = _raw(child, "targetPackage") or apk.table.text(child.get("targetPackage")) or ""
            cls = _raw(child, "targetClass") or apk.table.text(child.get("targetClass")) or ""
            return _raw(child, "action"), f"{pkg}/{cls}" if pkg and cls else ""
    return "", ""


_LEAF_TAGS = re.compile(r"(?i)(preference|switch|checkbox|seekbar|list|radio|dropdown|slider|button)")
# Lignes d'explication (pied de page, légende, illustration) : du texte, pas un réglage.
_INFO_KEYS = re.compile(r"(?i)(description|caption(?!ing)|footer|_desc$|_guide|_notice|_info_text|disclaimer)")
_INFO_TAGS = re.compile(r"(?i)(footer|description|caption|illustration|layoutpreference|divider|banner|tips?pref|"
                        r"summarypref|animation|lottie|preview|inset|blank|space|relativelink|contextualcard)")


def build_index(apk_path: str, language: str = "fr", country: str = "FR") -> dict[str, Any]:
    """Arbre des Paramètres tiré de l'APK : écrans (chemin, ouverture directe) et lignes (titre, résumé, clé)."""
    apk = Apk(apk_path, language, country)
    try:
        by_fragment, by_name = _activities(apk)
        screens_xml: dict[str, list[dict[str, Any]]] = {}
        fragments: set[str] = set(by_fragment)
        for name in sorted(n for n in apk.names if n.startswith("res/xml/") and n.endswith(".xml")):
            try:
                root = apk.xml(name)
            except (FormatError, KeyError, ValueError, IndexError):
                continue
            if "PreferenceScreen" not in root.tag and "Preference" not in root.tag:
                continue
            rows: list[dict[str, Any]] = []
            _rows(apk, root, "", rows)
            screens_xml[name] = rows
            fragments.update(r["fragment"] for r in rows if r.get("fragment"))
        constants = apk.constants(fragments)
        fragment_xml: dict[str, str] = {}
        for fragment, found in constants.items():
            xmls = [(m, apk.table.file(v)) for m, v in found if apk.table.is_type(v, "xml")]
            xmls = [(m, f) for m, f in xmls if f in screens_xml]
            # getPreferenceScreenResId peut rendre plusieurs écrans selon l'état (page d'attente, vraie page) : celui
            # que l'index de recherche du fragment (<clinit>) nomme aussi, sinon le plus fourni.
            own = [f for m, f in xmls if m == "getPreferenceScreenResId"]
            indexed = {f for m, f in xmls if m == "<clinit>"}
            best = (next((f for f in own if f in indexed), None)
                    or max(own, key=lambda f: len(screens_xml[f]), default=None)
                    or (xmls[0][1] if xmls else None))
            if best:
                fragment_xml[fragment] = best
        screens = _tree(screens_xml, fragment_xml, by_fragment)
        entries: list[dict[str, Any]] = []
        for name, rows in screens_xml.items():
            screen = screens.get(name)
            if screen is None:  # écran que le code ne relie à rien (code optimisé, dex absent) : gardé, place inconnue
                screen = Screen(name, title=name.removeprefix("res/xml/").removesuffix(".xml"), reached=False)
                screen.path = (f"[{screen.title}]",)
                orphan = True
            else:
                orphan = False
            for row in rows:
                entry = {k: v for k, v in row.items() if v}
                entry["screen"] = name.removeprefix("res/xml/").removesuffix(".xml")
                entry["path"] = list(screen.path)
                if not (screen.component or screen.action):
                    via, taps = _via(screens, screen)
                    if via:
                        entry["via"], entry["taps"] = via, taps
                if orphan:
                    entry["orphan"] = True
                elif not screen.reached:
                    entry["direct"] = True  # écran sans entrée connue depuis l'accueil : ouvert par son activité
                if screen.component:
                    entry["open"] = screen.component
                if screen.action:
                    entry["action"] = screen.action
                child = fragment_xml.get(row.get("fragment", ""))
                if child and (sub := screens.get(child)) and not row.get("target"):
                    if sub.action:  # ligne qui ouvre un sous-écran : sa propre ouverture directe
                        entry["target_action"] = sub.action
                    elif sub.component:
                        entry["target"] = sub.component
                entries.append(entry)
        activities = [{"component": f"{SETTINGS_PACKAGE}/{n}", "action": a, "title": label}
                      for n, (a, label) in sorted(by_name.items()) if label]
        return {"version": INDEX_VERSION, "language": language, "entries": entries, "activities": activities,
                "screens": len(screens), "built": datetime.now().isoformat(timespec="seconds")}
    finally:
        apk.close()


def _via(screens: dict[str, Screen], screen: Screen) -> tuple[dict[str, str], list[str]]:
    """Écran sans ouverture directe : l'écran ouvrable le plus proche au-dessus, et les titres à toucher ensuite."""
    taps: list[str] = []
    current: Screen | None = screen
    seen: set[str] = set()
    while current is not None and current.xml not in seen:
        seen.add(current.xml)
        if current.component or current.action:
            return ({"action": current.action} if current.action else {"open": current.component}), taps
        taps.insert(0, current.title)
        current = screens.get(current.parent)
    return {}, []


def _rows(apk: Apk, el: Element, section: str, out: list[dict[str, Any]]) -> None:
    for child in el.children:
        if child.tag in ("intent", "extra", "meta-data"):
            continue
        title = _text(apk, child, "title")
        tag = child.tag.rsplit(".", 1)[-1]
        if "Category" in tag:
            _rows(apk, child, title or section, out)
            continue
        key = _raw(child, "key")
        if title and _LEAF_TAGS.search(tag) and not _INFO_TAGS.search(tag) and not (_INFO_KEYS.search(key) and not _raw(child, "fragment")):
            action, component = _resolve_intent(apk, child)
            english = (apk.english.text(child.get("title")) or "").strip() if apk.english else ""
            out.append({"title": title, "english": english if english != title else "",
                        "summary": _text(apk, child, "summary"), "key": key,
                        "section": section, "fragment": _raw(child, "fragment"), "intent_action": action,
                        "target": component, "keywords": _text(apk, child, "keywords"),
                        "kind": "switch" if re.search(r"(?i)switch|checkbox", tag) else ""})
        if child.children:
            _rows(apk, child, section, out)


def _tree(screens_xml: dict[str, list[dict[str, Any]]], fragment_xml: dict[str, str],
          by_fragment: dict[str, tuple[str, str, str]]) -> dict[str, Screen]:
    """Parcours en largeur depuis l'accueil : chaque écran reçoit son chemin le plus court et son ouverture directe ;
    les écrans hors arbre mais ouvrables par une activité deviennent des racines à leur libellé."""
    xml_fragment = {x: f for f, x in fragment_xml.items()}
    screens: dict[str, Screen] = {}
    root = next((x for x in ROOT_XML if x in screens_xml), None)
    queue: list[str] = []
    if root:
        screens[root] = Screen(root, title=HOME, path=(), action=HOME_ACTION)
        queue.append(root)
    while queue:
        name = queue.pop(0)
        parent = screens[name]
        for row in screens_xml.get(name, ()):
            child = fragment_xml.get(row.get("fragment", ""))
            if child and child not in screens:
                component, action, _ = by_fragment.get(row["fragment"], ("", "", ""))
                screens[child] = Screen(child, row["fragment"], row["title"], (*parent.path, row["title"]),
                                        component, action, parent=name)
                queue.append(child)
    for fragment, (component, action, label) in sorted(by_fragment.items()):
        name = fragment_xml.get(fragment)
        if name and name not in screens:
            title = label or name.removeprefix("res/xml/").removesuffix(".xml")
            screens[name] = Screen(name, fragment, title, (title,), component, action, reached=False)
            queue.append(name)
            while queue:  # ses sous-écrans à leur tour
                current = screens[queue.pop(0)]
                for row in screens_xml.get(current.xml, ()):
                    child = fragment_xml.get(row.get("fragment", ""))
                    if child and child not in screens:
                        c, a, _ = by_fragment.get(row["fragment"], ("", "", ""))
                        screens[child] = Screen(child, row["fragment"], row["title"], (*current.path, row["title"]),
                                                c, a, reached=False, parent=current.xml)
                        queue.append(child)
    for name, screen in screens.items():
        screen.fragment = screen.fragment or xml_fragment.get(name, "")
    return screens


# ---------------------------------------------------------------- état du téléphone (instantané)


MARK = "@@NEXUS-MAP@@"
SENSITIVE_PERMISSIONS = ("CAMERA", "RECORD_AUDIO", "ACCESS_FINE_LOCATION", "ACCESS_COARSE_LOCATION",
                         "ACCESS_BACKGROUND_LOCATION", "READ_SMS", "RECEIVE_SMS", "SEND_SMS", "READ_CONTACTS",
                         "WRITE_CONTACTS", "READ_CALL_LOG", "CALL_PHONE", "READ_PHONE_NUMBERS", "BODY_SENSORS",
                         "READ_CALENDAR", "ACTIVITY_RECOGNITION", "READ_MEDIA_IMAGES", "READ_MEDIA_VIDEO",
                         "READ_EXTERNAL_STORAGE", "NEARBY_WIFI_DEVICES", "BLUETOOTH_SCAN")
ROLES = ("SMS", "DIALER", "BROWSER", "HOME", "ASSISTANT", "WALLET")
STATE_SH = " ; ".join([
    "getprop ro.product.model", f"echo {MARK}", "getprop ro.build.fingerprint", f"echo {MARK}",
    "getprop ro.build.display.id", f"echo {MARK}", "getprop ro.build.version.security_patch", f"echo {MARK}",
    "settings list system", f"echo {MARK}", "settings list secure", f"echo {MARK}", "settings list global",
    f"echo {MARK}", "pm list packages -i -U --show-versioncode", f"echo {MARK}", "pm list packages -d",
    f"echo {MARK}", "pm list packages -3", f"echo {MARK}",
    "for r in " + " ".join(ROLES) + "; do echo \"$r=$(cmd role get-role-holders android.app.role.$r)\"; done",
    f"echo {MARK}", "dumpsys device_policy | grep -a -o 'ComponentInfo{[^}]*}' | sort -u", f"echo {MARK}",
    "dumpsys bluetooth_manager | grep -a -A40 'Bonded devices' | grep -a -E '^    [0-9A-Fa-fX]{2}:'",
    f"echo {MARK}",
    "dumpsys package packages | grep -a -E '^  Package \\[|android.permission.(" + "|".join(SENSITIVE_PERMISSIONS)
    + "): granted=true'",
])
_SETTING = re.compile(r"^([^=\s]+)=(.*)$")
_PACKAGE = re.compile(r"^package:(\S+)")
_PACKAGE_FIELD = re.compile(r"\b(versionCode|installer|uid)[:=](\S+)")
# Gestionnaires de mots de passe (ni regard, ni capture) : jamais notés.
PASSWORD_MANAGERS = re.compile(r"(?i)(bitwarden|onepassword|agilebits|keepass|dashlane|nordpass|roboform|lastpass|"
                               r"proton\.android\.pass|samsungpass|enpass|keepersecurity|passwordmanager)")
_BT_NAME = re.compile(r"(?:\]\s*)([^\[\]]+?)\s*$")


def parse_state(out: str) -> dict[str, Any]:
    parts = [p.strip("\n") for p in out.split(MARK)]
    parts += [""] * (15 - len(parts))
    model, fingerprint, display, patch, system, secure, global_, pkgs, disabled, third, roles, admins, bt, perms = \
        parts[:14]
    settings: dict[str, dict[str, str]] = {}
    for ns, text in (("system", system), ("secure", secure), ("global", global_)):
        values: dict[str, str] = {}
        for line in text.splitlines():
            if m := _SETTING.match(line.strip()):
                values[m.group(1)] = mask_value(m.group(1), m.group(2))
        settings[ns] = values
    disabled_set = {ln.strip().removeprefix("package:") for ln in disabled.splitlines() if ln.strip()}
    third_set = {ln.strip().removeprefix("package:") for ln in third.splitlines() if ln.strip()}
    packages: dict[str, dict[str, Any]] = {}
    for line in pkgs.splitlines():
        if m := _PACKAGE.match(line.strip()):
            name = m.group(1)
            fields = dict(_PACKAGE_FIELD.findall(line))
            version = fields.get("versionCode", "")
            packages[name] = {"version": int(version) if version.isdigit() else 0,
                              "installer": fields.get("installer", "?"),
                              **({"third_party": True} if name in third_set else {}),
                              **({"disabled": True} if name in disabled_set else {})}
    permissions: dict[str, list[str]] = {}
    current = ""
    for line in perms.splitlines():
        if m := re.match(r"^\s*Package \[([^\]]+)\]", line):
            current = m.group(1)
        elif current and (m := re.search(r"android\.permission\.(\w+): granted=true", line)) \
                and m.group(1) in SENSITIVE_PERMISSIONS:
            permissions.setdefault(m.group(1), [])
            if current not in permissions[m.group(1)]:
                permissions[m.group(1)].append(current)
    role_map = {}
    for line in roles.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            role_map[k.strip()] = v.strip()
    bluetooth = sorted({m.group(1).strip() for line in bt.splitlines() if (m := _BT_NAME.search(line))})
    return {
        "device": {"model": model.strip(), "build": display.strip(), "security_patch": patch.strip(),
                   "fingerprint": fingerprint.strip()},
        "settings": settings,
        "packages": packages,
        "permissions": {k: sorted(v) for k, v in sorted(permissions.items())},
        "roles": role_map,
        "device_admins": sorted({ln.strip() for ln in admins.splitlines() if ln.strip()}),
        "bluetooth": [logs_mod.mask(n) for n in bluetooth],
    }


def read_state(adb: Adb) -> dict[str, Any]:
    out = adb.shell("sh", "-c", STATE_SH, check=False, timeout=120)
    if out.count(MARK) < 13:
        raise PhoneError("phone state unreadable (output cut): try again")
    state = parse_state(out)
    state["taken"] = datetime.now().isoformat(timespec="seconds")
    return state


# ---------------------------------------------------------------- stockage sur le PC


def _phone_dir(adb: Adb) -> Path:
    if not adb.serial:
        adb.connect()
    ident = adb.identity(adb.serial or "") or adb.serial or "phone"
    return MAP_DIR / hashlib.sha256(ident.encode()).hexdigest()[:12]


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
            f.write(gzip.compress(raw) if path.suffix == ".gz" else raw)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> Any:
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)


def _order(path: Path) -> tuple[str, int]:
    """Ordre chronologique : horodatage, puis numéro de la prise dans la même seconde (« …-2 » après « … »)."""
    stem = path.name.removesuffix(".json.gz")
    stamp, _, seq = stem[15:].partition("-")
    return stem[:15], int(seq) if seq.isdigit() else 0


def snapshots(folder: Path) -> list[Path]:
    return sorted((folder / "snapshots").glob("*.json.gz"), key=_order)


def save_snapshot(folder: Path, state: dict[str, Any]) -> Path:
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    path = folder / "snapshots" / f"{stamp}.json.gz"
    n = 1
    while path.exists():  # deux instantanés dans la même seconde : aucun n'écrase l'autre
        path = folder / "snapshots" / f"{stamp}-{n}.json.gz"
        n += 1
    _write_json(path, state)
    for old in snapshots(folder)[:-MAX_SNAPSHOTS]:
        old.unlink(missing_ok=True)
    return path


def _apk_stamp(adb: Adb) -> tuple[str, str, str]:
    """(chemin de l'APK des Paramètres sur le téléphone, empreinte taille+date+build+langue, langue) : l'index se
    refait seulement quand l'APK ou la langue du téléphone change (mise à jour du système ou des Paramètres)."""
    path = adb.shell("pm", "path", SETTINGS_PACKAGE).strip().splitlines()[0].removeprefix("package:").strip()
    stat = adb.shell("stat", "-c", "%s:%Y", path, check=False).strip()
    build = adb.shell("getprop", "ro.build.fingerprint", check=False).strip()
    # Langue choisie par l'utilisateur, sinon celle d'usine (persist.sys.locale vide tant qu'elle n'a pas été changée).
    locale = (adb.shell("getprop", "persist.sys.locale", check=False).strip()
              or adb.shell("getprop", "ro.product.locale", check=False).strip() or "fr-FR")
    key = f"{path}|{stat}|{build}|{locale}|{INDEX_VERSION}"
    return path, hashlib.sha256(key.encode()).hexdigest()[:12], locale


def load_index(adb: Adb, folder: Path, rebuild: bool = False) -> tuple[dict[str, Any], bool]:
    """Index des Paramètres en cache, reconstruit (APK tiré, ~10 s en Wi-Fi) s'il manque ou si l'APK a changé."""
    apk_path, stamp, language = _apk_stamp(adb)
    cache = folder / f"settings-{stamp}.json.gz"
    if cache.exists() and not rebuild:
        return _read_json(cache), False
    folder.mkdir(parents=True, exist_ok=True)
    fd, local = tempfile.mkstemp(dir=folder, suffix=".apk")
    os.close(fd)
    try:
        try:  # compressé : l'APK garde sa table de ressources non compressée (120 Mo tirés en 9 s au lieu de 23)
            adb.host("pull", "-z", "zstd", apk_path, local, timeout=600)
        except PhoneError:
            adb.host("pull", apk_path, local, timeout=600)
        lang, _, country = language.replace("_", "-").partition("-")
        try:
            index = build_index(local, lang or "fr", country or "")
        except FormatError as e:
            raise PhoneError(f"Settings app unreadable, map not built: {e}") from None
    finally:
        Path(local).unlink(missing_ok=True)
    for old in folder.glob("settings-*.json.gz"):
        old.unlink(missing_ok=True)
    _write_json(cache, index)
    return index, True


def load_screens(folder: Path) -> dict[str, Any]:
    path = folder / "screens.json"
    return _read_json(path) if path.exists() else {}


def save_screens(folder: Path, screens: dict[str, Any]) -> None:
    _write_json(folder / "screens.json", screens)


# ---------------------------------------------------------------- recherche


@dataclass
class Hit:
    score: float
    matched: int
    entry: dict[str, Any]
    source: str  # settings, screen (passe à l'écran), activity, value


def _score(terms: list[str], fields: list[tuple[str, float]]) -> tuple[int, float]:
    matched, score = 0, 0.0
    for term in terms:
        best = max((weight for text, weight in fields if text and _hit(term, _words(text))), default=0.0)
        if best:
            matched += 1
            score += best
    return matched, score


def search(query: str, index: dict[str, Any], screens: dict[str, Any], values: dict[str, dict[str, str]],
           limit: int = 8) -> list[Hit]:
    """Lignes qui couvrent au moins la moitié des mots de la demande, les plus complètes d'abord ; aucune : les
    meilleures correspondances partielles (Hit.matched dit combien de mots chacune couvre)."""
    terms = query_terms(query)
    if not terms:
        return []
    found = _search(terms, index, screens, values, max(1, (len(terms) + 1) // 2), limit)
    return found or _search(terms, index, screens, values, 1, limit)


def _search(terms: list[str], index: dict[str, Any], screens: dict[str, Any], values: dict[str, dict[str, str]],
            need: int, limit: int) -> list[Hit]:
    hits: list[Hit] = []

    def add(entry: dict[str, Any], source: str, fields: list[tuple[str, float]], bonus: float = 0.0) -> None:
        matched, score = _score(terms, fields)
        if matched >= need:
            hits.append(Hit(score + bonus, matched, entry, source))

    for e in index.get("entries", ()):
        title = e.get("title", "")
        # Phrase d'explication (titre long) et écran hors de l'accueil : derrière les vrais réglages.
        bonus = (-1.5 if len(title) > 70 else 0) + (-1 if e.get("direct") else 0) + (-2 if e.get("orphan") else 0)
        add(e, "settings", [(title, 3), (e.get("english", ""), 2.5), (" ".join(e.get("path", ())), 2),
                            (e.get("section", ""), 1.5), (e.get("summary", ""), 1), (e.get("keywords", ""), 1.5),
                            (e.get("key", "").replace("_", " "), 0.5)], bonus)
    for path, screen in screens.items():
        for item in screen.get("items", ()):
            entry = {"title": item["text"], "path": path.split(" > "), "state": item.get("state", ""),
                     "open": screen.get("open", ""), "noted": screen.get("noted", "")}
            add(entry, "screen", [(item["text"], 3), (path, 2)], bonus=0.5)  # relevé à l'écran : plus sûr
    for a in index.get("activities", ()):
        add(a, "activity", [(a.get("title", ""), 2.5), (a.get("action", "").replace("_", " "), 1)])
    for ns, kv in values.items():
        for key in kv:
            add({"namespace": ns, "key": key}, "value", [(key.replace("_", " ").replace(".", " "), 1)])
    hits.sort(key=lambda h: (-h.matched, -h.score, len(h.entry.get("path", ()))))
    seen: set[tuple[str, ...]] = set()
    unique: list[Hit] = []
    for h in hits:
        sig = (h.source, str(h.entry.get("title", h.entry.get("key", ""))), *map(str, h.entry.get("path", ())))
        if sig not in seen:
            seen.add(sig)
            unique.append(h)
    return unique[:limit]


def known_targets(index: dict[str, Any], screens: dict[str, Any]) -> set[str]:
    """Activités que la carte propose d'ouvrir : seules celles-là sont ouvertes par map(action="open")."""
    found = {a["component"] for a in index.get("activities", ())}
    for e in index.get("entries", ()):
        found.update(v for v in (e.get("open"), e.get("target"), (e.get("via") or {}).get("open")) if v)
    found.update(s["open"] for s in screens.values() if s.get("open"))
    return found


def value_of(values: dict[str, dict[str, str]], key: str) -> str | None:
    for ns in ("secure", "system", "global"):
        if key and key in values.get(ns, {}):
            return f"{ns}/{key}={values[ns][key]}"
    return None


def describe(hit: Hit, values: dict[str, dict[str, str]]) -> str:
    e = hit.entry
    if hit.source == "value":
        return f"value {e['namespace']}/{e['key']} = {values[e['namespace']][e['key']]}"
    if hit.source == "activity":
        how = f"launch(action=\"{e['action']}\")" if e.get("action") else f"map(action=\"open\", target=\"{e['component']}\")"
        return f"{e['title']} (screen) → {how}"
    if hit.source == "settings" and e.get("orphan"):
        path = f"Settings screen {' > '.join(e.get('path', ()))} (place in the menus unknown)"
    elif hit.source == "settings" and e.get("direct"):
        path = " > ".join(e.get("path", ())) + " (screen opened directly, not from the Settings home)"
    elif hit.source == "settings":
        path = " > ".join([HOME, *e.get("path", ())])
    else:
        path = " > ".join(e.get("path", ()))
    line = f"{path} > {e['title']}" + (f" (EN: {e['english']})" if e.get("english") else "")
    if e.get("section"):
        line += f"  (section « {e['section']} »)"
    if e.get("summary"):
        line += f" — {e['summary'][:120]}"
    if hit.source == "screen":
        line += f"  [{e['state']}]" if e.get("state") else ""
        line += f"  (noted on screen {e['noted']})" if e.get("noted") else ""
    if (current := value_of(values, e.get("key", ""))) is not None:
        line += f"\n    now: {current}"
    opener = e.get("target") or e.get("open")
    if e.get("target_action"):
        line += f"\n    open: launch(action=\"{e['target_action']}\")"
    elif hit.source == "settings" and e.get("action") and not e.get("target") and not (e.get("key") and e.get("open")):
        line += f"\n    open: launch(action=\"{e['action']}\")"
    elif opener:  # avec la clé : la ligne mise en évidence (extra que launch n'accepte pas)
        line += f"\n    open: map(action=\"open\", target=\"{opener}\"" + (
            f", key=\"{e['key']}\")" if e.get("key") and not e.get("target") else ")")
    elif hit.source == "settings" and e.get("via"):
        via = e["via"]
        first = (f"launch(action=\"{via['action']}\")" if via.get("action") else
                 f"map(action=\"open\", target=\"{via['open']}\")")
        line += f"\n    open: {first}, then tap {' > '.join(e.get('taps', ()))}"
    elif hit.source == "settings":
        line += "\n    open: follow the path from Settings (no direct entry)"
    return line


# ---------------------------------------------------------------- comparaison de deux instantanés


def _changed(a: dict[str, Any], b: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    added = sorted(k for k in b if k not in a)
    removed = sorted(k for k in a if k not in b)
    changed = sorted(k for k in a if k in b and a[k] != b[k])
    return added, removed, changed


def _show(value: str) -> str:
    return MASKED if value.startswith(MASKED) else value


def diff(old: dict[str, Any], new: dict[str, Any], limit: int = 80) -> str:
    lines: list[str] = [f"From {old.get('taken', '?')} to {new.get('taken', '?')}:"]
    od, nd = old.get("device", {}), new.get("device", {})
    for k in ("build", "security_patch"):
        if od.get(k) != nd.get(k):
            lines.append(f"System {k.replace('_', ' ')}: {od.get(k)} → {nd.get(k)}")
    count = 0
    for ns in ("system", "secure", "global"):
        a, b = old.get("settings", {}).get(ns, {}), new.get("settings", {}).get(ns, {})
        added, removed, changed = _changed(a, b)
        for k in changed:
            lines.append(f"  {ns}/{k}: {_show(a[k])} → {_show(b[k])}")
        for k in added:
            lines.append(f"  {ns}/{k}: (new) {_show(b[k])}")
        for k in removed:
            lines.append(f"  {ns}/{k}: removed (was {_show(a[k])})")
        count += len(added) + len(removed) + len(changed)
    if count:
        lines.insert(len(lines) - count, f"Settings values changed: {count}")
    pa, pb = old.get("packages", {}), new.get("packages", {})
    added, removed, changed = _changed(pa, pb)
    updated = [k for k in changed if pa[k].get("version") != pb[k].get("version")]
    for k in added:
        lines.append(f"App installed: {k} (version {pb[k].get('version')}, from {pb[k].get('installer')})")
    for k in removed:
        lines.append(f"App removed: {k}")
    for k in updated:
        lines.append(f"App updated: {k} {pa[k].get('version')} → {pb[k].get('version')}")
    for k in changed:
        if pa[k].get("disabled") != pb[k].get("disabled"):
            lines.append(f"App {'disabled' if pb[k].get('disabled') else 'enabled'}: {k}")
    qa, qb = old.get("permissions", {}), new.get("permissions", {})
    for perm in sorted(set(qa) | set(qb)):
        got = sorted(set(qb.get(perm, ())) - set(qa.get(perm, ())))
        lost = sorted(set(qa.get(perm, ())) - set(qb.get(perm, ())))
        if got:
            lines.append(f"Permission {perm} granted to: {', '.join(got)}")
        if lost:
            lines.append(f"Permission {perm} withdrawn from: {', '.join(lost)}")
    for k in sorted(set(old.get("roles", {})) | set(new.get("roles", {}))):
        if old.get("roles", {}).get(k) != new.get("roles", {}).get(k):
            lines.append(f"Default {k.lower()} app: {old.get('roles', {}).get(k) or '-'} → "
                         f"{new.get('roles', {}).get(k) or '-'}")
    for label, key in (("Device admin", "device_admins"), ("Bluetooth device", "bluetooth")):
        a_set, b_set = set(old.get(key, ())), set(new.get(key, ()))
        lines += [f"{label} added: {x}" for x in sorted(b_set - a_set)]
        lines += [f"{label} removed: {x}" for x in sorted(a_set - b_set)]
    if len(lines) == 1:
        return lines[0] + " nothing changed."
    if len(lines) > limit:
        lines = lines[:limit] + [f"… {len(lines) - limit} more lines"]
    return "\n".join(lines)


def pick_snapshot(paths: list[Path], since: str | None) -> Path | None:
    """Instantané de référence : le dernier pris avant `since` (2h, 3d, 13:05, 2026-10-19 13:05), sinon le plus
    récent."""
    if not paths:
        return None
    if not since:
        return paths[-1]
    start = logs_mod.since_stamp(since, datetime.now())  # « 2026-10-19 13:05:00.000 »
    stamp = start[:19].replace("-", "").replace(":", "").replace(" ", "-")
    before = [p for p in paths if p.name[:15] <= stamp]
    return before[-1] if before else paths[0]


def age(path: Path) -> str:
    seconds = time.time() - path.stat().st_mtime
    if seconds < 3600:
        return f"{seconds / 60:.0f} min ago"
    if seconds < 86400 * 2:
        return f"{seconds / 3600:.0f} h ago"
    return f"{seconds / 86400:.0f} days ago"


# ---------------------------------------------------------------- passe à l'écran (écrans que l'APK ne décrit pas)


def screen_items(elements: Iterable[Any]) -> list[dict[str, str]]:
    """Lignes d'un écran relevé : textes dans l'ordre, l'état d'une bascule sans texte (interrupteur) rattaché à la
    ligne de texte de la même bande horizontale. Codes déjà masqués par la lecture de l'écran ; jetons, e-mails et
    numéros masqués ici."""
    items: list[dict[str, str]] = []
    bands: list[tuple[int, int]] = []
    for e in elements:
        if getattr(e, "password", False):
            continue  # champ de mot de passe : jamais relevé, même masqué
        state = next((s for s in e.states if s in ("checked", "unchecked")), "")
        state = {"checked": "on", "unchecked": "off"}.get(state, "")
        text = logs_mod.mask((e.text or "").strip())
        if text and len(text) <= 200:
            items.append({"text": text, **({"state": state} if state else {})})
            bands.append((e.bounds[1], e.bounds[3]))
        elif state and items:
            top, bottom = e.bounds[1], e.bounds[3]
            for i in range(len(items) - 1, -1, -1):
                if bands[i][0] < bottom and top < bands[i][1] + (bottom - top):
                    items[i].setdefault("state", state)
                    break
    seen: set[str] = set()
    unique = []
    for item in items:
        if item["text"] not in seen:
            seen.add(item["text"])
            unique.append(item)
    return unique


def note_screen(screens: dict[str, Any], path: str, items: list[dict[str, str]], component: str,
                append: bool) -> dict[str, Any]:
    """Écran relevé enregistré sous son chemin (« Paramètres > Galaxy Buds4 Pro > Notifications ») ; append : lignes
    ajoutées à celles déjà notées (écran long relevé en plusieurs fois après défilement)."""
    path = " > ".join(p.strip() for p in path.split(">") if p.strip())
    if not path:
        raise PhoneError("note: path required, e.g. \"Settings > Galaxy Buds4 Pro > Notifications\"")
    old = screens.get(path, {})
    if append and old:
        known = {i["text"] for i in old.get("items", ())}
        items = old["items"] + [i for i in items if i["text"] not in known]
    screens[path] = {"items": items, "open": component or old.get("open", ""),
                     "noted": datetime.now().isoformat(timespec="minutes")}
    return screens[path]
