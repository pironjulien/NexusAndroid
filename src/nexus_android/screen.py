"""Écran du téléphone : capture de l'affichage actif (Fold : écran intérieur ou extérieur), arbre UI, état."""

from __future__ import annotations

import io
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace

from PIL import Image as PILImage
from PIL import ImageDraw

from .adb import Adb, PhoneError
from .messages import has_keyword, mask_codes

MAX_SIDE = 1280  # côté le plus long de l'image rendue à l'agent
# Zone de l'utilisateur shell, invisible pour les applications ; un fichier par serveur (pid) : les trois agents ont
# chacun le leur, et il est effacé aussitôt lu.
UI_DUMP = f"/data/local/tmp/nexus-android-ui-{os.getpid()}.xml"


@dataclass
class Element:
    index: int
    role: str
    text: str
    desc: str
    rid: str
    package: str
    bounds: tuple[int, int, int, int]
    clickable: bool
    states: list[str] = field(default_factory=list)
    password: bool = False
    focused: bool = False
    editable: bool = False
    masked: bool = False  # un code y a été masqué : zone noircie sur l'image

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2

    @property
    def label(self) -> str:
        return self.text or self.desc

    def line(self, scale: float) -> str:
        x, y = self.center
        parts = [f"[{self.index}] {self.role}"]
        if self.text:
            parts.append(f"« {self.text[:120]} »")
        if self.desc and self.desc != self.text:
            parts.append(f"({self.desc[:120]})")
        if self.rid:
            parts.append(f"#{self.rid}")
        if self.states:
            parts.append(" ".join(self.states))
        parts.append(f"@{round(x * scale)},{round(y * scale)}")
        return " ".join(parts)


_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def parse_ui(xml_text: str) -> list[Element]:
    """Éléments utiles de l'arbre uiautomator : cliquables, saisissables, défilables ou porteurs de texte."""
    start = xml_text.find("<?xml")
    if start < 0:
        start = xml_text.find("<hierarchy")
    if start < 0:
        raise PhoneError("Arbre UI illisible : " + xml_text.strip()[:200])
    root = ET.fromstring(xml_text[start: xml_text.rfind(">") + 1])
    elements: list[Element] = []
    for node in root.iter("node"):
        a = node.attrib
        match = _BOUNDS.match(a.get("bounds", ""))
        if not match:
            continue
        x1, y1, x2, y2 = map(int, match.groups())
        if x2 <= x1 or y2 <= y1:
            continue
        text, desc = a.get("text", "").strip(), a.get("content-desc", "").strip()
        clickable = a.get("clickable") == "true" or a.get("long-clickable") == "true"
        editable = a.get("class", "").endswith("EditText")
        scrollable = a.get("scrollable") == "true"
        if not (text or desc or clickable or editable or scrollable):
            continue
        states = []
        if a.get("checkable") == "true":
            states.append("coché" if a.get("checked") == "true" else "non-coché")
        if a.get("selected") == "true":
            states.append("sélectionné")
        if a.get("focused") == "true":
            states.append("focus")
        if a.get("enabled") == "false":
            states.append("inactif")
        if scrollable:
            states.append("défilable")
        if editable:
            states.append("saisie")
        rid = a.get("resource-id", "")
        elements.append(Element(
            index=len(elements) + 1,
            role=a.get("class", "").rsplit(".", 1)[-1] or "View",
            text=text, desc=desc, rid=rid.split("/", 1)[-1] if rid else "",
            package=a.get("package", ""), bounds=(x1, y1, x2, y2), clickable=clickable or editable,
            states=states, password=a.get("password") == "true", focused=a.get("focused") == "true",
            editable=editable,
        ))
    return elements


def _near(a: Element, b: Element) -> bool:
    """Même bloc à l'écran (titre et corps d'une notification) : colonnes qui se recouvrent, écart vertical court."""
    overlap = min(a.bounds[2], b.bounds[2]) - max(a.bounds[0], b.bounds[0])
    gap = max(a.bounds[1], b.bounds[1]) - min(a.bounds[3], b.bounds[3])
    tall = max(a.bounds[3] - a.bounds[1], b.bounds[3] - b.bounds[1])
    return a.package == b.package and overlap > 0 and gap <= 2 * tall


def mask_elements(elements: list[Element]) -> list[Element]:
    """Codes de sécurité masqués à la source, avant tout usage (liste, query, wait, cible texte) : l'agent ne peut
    pas en deviner un par sa présence ou son absence. Le mot-clé peut être dans l'élément (texte, description, nom
    de ressource) ou dans un voisin du même bloc (titre d'une notification au-dessus du code seul)."""
    keyed = [e for e in elements if has_keyword(e.text, e.desc, e.rid)]
    out = []
    for e in elements:
        context = e in keyed or any(k is not e and _near(k, e) for k in keyed)
        text, desc = mask_codes(e.text, context), mask_codes(e.desc, context)
        out.append(replace(e, text=text, desc=desc, masked=(text, desc) != (e.text, e.desc)))
    return out


def blackout(image: PILImage.Image, elements: list[Element]) -> PILImage.Image:
    """Noircit sur l'image (pixels écran) la zone des éléments dont un code a été masqué."""
    hidden = [e.bounds for e in elements if e.masked]
    if not hidden:
        return image
    image = image.convert("RGB")
    draw = ImageDraw.Draw(image)
    for box in hidden:
        draw.rectangle(box, fill=(0, 0, 0))
    return image


def parse_logical_display(dumpsys_display: str) -> str | None:
    """Identifiant physique (SurfaceFlinger) de l'affichage logique 0, celui que voit l'utilisateur."""
    match = re.search(r"mDisplayId=0\s*\n\s*mPrimaryDisplayDevice=[^\n]*\(local:(\d+)\)", dumpsys_display) or re.search(
        r"mDisplayId=0\s*\n\s*mBaseDisplayInfo=DisplayInfo\{.*?uniqueId \"local:(\d+)\"", dumpsys_display)
    return match.group(1) if match else None


@dataclass
class State:
    awake: bool
    locked: bool
    package: str
    lock_known: bool = True  # False : dumpsys ne dit pas si l'écran de verrouillage est affiché


_KEYGUARD = re.compile(r"(?:isKeyguardShowing|mShowingLockscreen|KeyguardShowing|mIsShowing)=(true|false)")


def parse_state(power: str, window: str) -> State:
    awake = "mWakefulness=Awake" in power
    flags = _KEYGUARD.findall(window)
    focus = re.search(r"mCurrentFocus=Window\{\S+ \S+ ([\w.]+)/", window) or re.search(
        r"mFocusedApp=.*? ([\w.]+)/", window)
    return State(awake, "true" in flags, focus.group(1) if focus else "", lock_known=bool(flags))


def parse_posture(device_state: str) -> str:
    """Pliage du Fold (CLOSED, HALF_OPENED, OPENED…) d'après `dumpsys device_state` ; "" si illisible."""
    match = re.search(r"mCommittedState=Optional\[DeviceState\{identifier=\d+, name='(\w+)'", device_state)
    return match.group(1) if match else ""


def is_black(image: PILImage.Image) -> bool:
    """Capture entièrement noire : écran protégé (FLAG_SECURE), éteint ou en transition."""
    small = image.convert("L").resize((64, 64))
    return (small.getextrema()[1] or 0) <= 12  # type: ignore[index, operator]


# Programmes d'aide d'autres outils de pilotage, qui gardent la main sur l'accessibilité ou l'injection d'entrées et
# rendent l'arbre UI illisible (relevé avec mobile-mcp) ; le nôtre (nexus-scrcpy) n'en fait pas partie.
_HELPERS = re.compile(r"mobilecli|DeviceServer|appium|uiautomator2|io\.github\.uiautomator|shizuku|scrcpy", re.I)


def parse_helpers(ps: str) -> list[str]:
    found = []
    for line in ps.splitlines():
        if _HELPERS.search(line) and "nexus-scrcpy" not in line:
            name = (line.split(None, 2)[2] if len(line.split(None, 2)) > 2 else line).strip()
            found.append(name[:80])
    return sorted(set(found))


class Screen:
    def __init__(self, adb: Adb) -> None:
        self.adb = adb
        self.scale = 1.0  # pixels image = pixels écran × scale
        self.elements: list[Element] = []  # dernière liste montrée à l'agent (look) : ce que désigne [N]
        self.size: tuple[int, int] | None = None
        self.posture = ""  # pliage au moment de la dernière image (les coordonnées "x,y" en dépendent)

    def current_posture(self) -> str:
        return parse_posture(self.adb.shell("dumpsys", "device_state", check=False))

    def helpers(self) -> list[str]:
        """Programmes d'aide d'autres outils de pilotage en cours sur le téléphone."""
        return parse_helpers(self.adb.shell("ps", "-A", "-o", "PID,NAME,ARGS", check=False))

    def state(self) -> State:
        power = self.adb.shell("dumpsys", "power", check=False)
        window = self.adb.shell("dumpsys", "window", "displays", check=False)
        window += self.adb.shell("dumpsys", "window", "policy", check=False)
        return parse_state(power, window)

    def _display_id(self) -> str | None:
        ids = self.adb.shell("dumpsys", "SurfaceFlinger", "--display-id", check=False)
        if len(re.findall(r"^Display \d+", ids, re.M)) <= 1:
            return None
        return parse_logical_display(self.adb.shell("dumpsys", "display", check=False))

    def capture(self) -> PILImage.Image:
        display = self._display_id()
        args = ["screencap", "-p"] + (["-d", display] if display else [])
        image = None
        for attempt in range(3):  # en Wi-Fi, une capture arrive parfois tronquée : la refaire
            raw = self.adb.exec_out(*args)
            try:
                image = PILImage.open(io.BytesIO(raw))
                image.load()
                break
            except Exception as e:
                if attempt == 2:
                    raise PhoneError("Capture d'écran illisible (trois essais) : "
                                     + raw[:60].decode("utf-8", "replace")) from e
                time.sleep(0.5)
        assert image is not None
        self.size = image.size
        self.posture = self.current_posture()
        return image

    def render(self, image: PILImage.Image, region: list[float] | None = None) -> bytes:
        """JPEG pour l'agent, côté max MAX_SIDE ; region [x1,y1,x2,y2] en pixels de l'image complète."""
        if region:
            x1, y1, x2, y2 = (v / self.scale for v in region)
            image = image.crop((int(x1), int(y1), int(x2), int(y2)))
            factor = min(1.0, MAX_SIDE / max(image.size))
        else:
            factor = min(1.0, MAX_SIDE / max(image.size))
            self.scale = factor
        if factor < 1.0:
            image = image.resize((round(image.width * factor), round(image.height * factor)), PILImage.Resampling.LANCZOS)
        out = io.BytesIO()
        image.convert("RGB").save(out, "JPEG", quality=80)
        return out.getvalue()

    def dump(self) -> list[Element]:
        """Arbre UI de l'écran actuel, codes masqués ; ne change pas la liste du dernier look."""
        last = ""
        for _ in range(3):
            out = self.adb.shell("sh", "-c", f"uiautomator dump {UI_DUMP} 2>&1; cat {UI_DUMP} 2>/dev/null; "
                                 f"rm -f {UI_DUMP}", timeout=20, check=False)
            if "<hierarchy" in out:
                return mask_elements(parse_ui(out))
            last = out.strip()
            time.sleep(0.6)
        helpers = self.helpers()
        culprit = (" ; autre outil de pilotage actif sur le téléphone, qui peut bloquer la lecture : "
                   + ", ".join(helpers) + " (le fermer ou redémarrer le téléphone)") if helpers else ""
        raise PhoneError("Arbre UI indisponible (écran en mouvement ou protégé) : " + last[:200] + culprit)

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        return round(x / self.scale), round(y / self.scale)
