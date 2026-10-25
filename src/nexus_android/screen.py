"""Écran du téléphone : capture de l'affichage actif (Fold : écran intérieur ou extérieur), arbre UI, état."""

from __future__ import annotations

import hashlib
import hmac
import io
import math
import os
import re
import secrets
import struct
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from PIL import Image as PILImage
from PIL import ImageChops, ImageDraw, ImageFont

from . import ocr, query
from .adb import STATE_WAIT, Adb, PhoneError
from .messages import has_keyword, mask_codes

# Image rendue à l'agent (règle commune des Nexus, NexusHarness/NexusShared/images.md) : côté long ≤ 1568 px et ≤ 1,15 Mpx
# (limite standard de Claude, sans réduction ; GPT-5.x et Gemini 3 aussi), WebP sans perte, qualité 85 puis 70 au-delà
# de 200 Ko, jamais plus de 500 Ko (au-delà, Antigravity rejette la requête).
MAX_SIDE = 1568
MAX_PIXELS = 1_150_000
LOSSLESS_MAX = 200 * 1024
IMAGE_MAX = 500 * 1024


def fit(width: float, height: float) -> float:
    """Facteur de réduction d'une image de cette taille pour l'agent (1.0 : gardée telle quelle)."""
    if width <= 0 or height <= 0:
        return 1.0
    return min(1.0, MAX_SIDE / max(width, height), (MAX_PIXELS / (width * height)) ** 0.5)


def sized(width: float, height: float) -> tuple[int, int]:
    """Taille d'une image de cette taille pour l'agent : réduite par fit, arrondie vers le bas (jamais au-dessus des
    plafonds : 1234×932 dépasserait 1,15 Mpx de 88 pixels)."""
    factor = fit(width, height)
    if factor >= 1.0:
        return int(width), int(height)
    return max(1, int(width * factor)), max(1, int(height * factor))


def within(size: tuple[int, int]) -> bool:
    return max(size) <= MAX_SIDE and size[0] * size[1] <= MAX_PIXELS


def encode(image: PILImage.Image, photo: bool = False) -> tuple[bytes, tuple[int, int]]:
    """WebP pour l'agent et sa taille finale : sans perte (méthode 0, la plus rapide) s'il tient en 200 Ko, sinon
    qualité 85 puis 70 ; réduit encore si même 70 dépasse 500 Ko (photo très chargée) — l'appelant recale alors son
    échelle sur la taille rendue. photo : une photo dépasse toujours 200 Ko sans perte, l'essai est sauté."""
    image = image.convert("RGB")
    if not photo:
        out = io.BytesIO()
        image.save(out, "WEBP", lossless=True, method=0)
        if out.tell() <= LOSSLESS_MAX:
            return out.getvalue(), image.size
    while True:
        tries = []
        for quality in (85, 70):
            out = io.BytesIO()
            image.save(out, "WEBP", quality=quality, method=2)
            if out.tell() <= LOSSLESS_MAX:
                return out.getvalue(), image.size
            tries.append(out.getvalue())
        # Même 70 dépasse 200 Ko : la meilleure qualité qui tient sous le plafond (85 si possible).
        if fitting := [data for data in tries if len(data) <= IMAGE_MAX]:
            return fitting[0], image.size
        image = image.resize((max(1, image.width * 3 // 4), max(1, image.height * 3 // 4)), PILImage.Resampling.LANCZOS)


UNCHANGED = True  # même image que la dernière envoyée : pas renvoyée (False : tests qui relisent le même écran simulé)
THUMB = 64  # côté de l'empreinte d'une image envoyée (en couleur : rouge et vert ont souvent le même gris)
THUMB_SLACK = 2  # écart toléré par canal et par pixel (bruit d'encodage), au-delà l'écran a changé


def thumb(image: PILImage.Image) -> PILImage.Image:
    return image.convert("RGB").resize((THUMB, THUMB), PILImage.Resampling.BILINEAR)


def same_thumb(a: PILImage.Image | None, b: PILImage.Image) -> bool:
    if a is None or a.size != b.size:
        return False
    return all(high <= THUMB_SLACK for _, high in ImageChops.difference(a, b).getextrema())  # type: ignore[misc]
# Zone de l'utilisateur shell, invisible pour les applications ; un fichier par serveur (pid) : les trois agents ont
# chacun le leur, et il est effacé aussitôt lu.
UI_DUMP = f"/data/local/tmp/nexus-android-ui-{os.getpid()}.xml"
# Lecture rapide par le serveur gardé en vie (query.py, phone/NexusQuery.java) : arbre, capture et état en un échange.
# False : toujours uiautomator dump et screencap (tests sur un téléphone simulé).
FAST = True
PHONE_QUALITY = 90  # JPEG fait par le téléphone, réencodé à 80 par render après le noircissement et les numéros
# ms sans événement d'accessibilité avant de lire l'arbre : le calme qu'UiAutomator attend avant chacune de ses actions
# (QueryController, 500 ms) ; l'attente s'allonge tant que l'écran bouge (jusqu'à 3 s, puis arbre de l'instant).
SETTLE_QUIET_MS = 500


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
    # Sceau du texte brut d'un élément masqué (HMAC à clé secrète propre au processus, jamais montré tel quel ni
    # réversible) : l'accord d'un geste sortant est lié au vrai contenu, pas à « [masked IBAN] ».
    sealed: str = ""
    raw: tuple[str, str] = ("", "")  # texte et description bruts (espaces de bord compris), jamais affichés
    scrollable: bool = False  # liste ou zone défilable (un glissé dedans n'est pas un appui)
    # Place dans l'arbre : (ordre de dessin, rang) de chaque nœud depuis la racine. Un ancêtre en est un préfixe ; le
    # plus grand chemin est dessiné devant. () : inconnue.
    path: tuple[tuple[int, int], ...] = ()

    def holds(self, other: Element) -> bool:
        """Lui-même ou un ancêtre de other (chemins connus)."""
        return bool(self.path) and other.path[:len(self.path)] == self.path

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
            parts.append(f"\"{self.text[:120]}\"")
        if self.desc and self.desc != self.text:
            parts.append(f"({self.desc[:120]})")
        if self.rid:
            parts.append(f"#{self.rid}")
        if self.states:
            parts.append(" ".join(self.states))
        parts.append(f"@{round(x * scale)},{round(y * scale)}")
        return " ".join(parts)


_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


# Surfaces dessinées sans arbre (vidéo, jeu, carte, aperçu de caméra) : leur texte n'est visible que sur l'image.
OPAQUE = re.compile(r"(?:Surface|Texture|GLSurface)View$")
Box = tuple[int, int, int, int]


def parse_ui(xml_text: str, surfaces: list[Box] | None = None) -> list[Element]:
    """Éléments utiles de l'arbre uiautomator : cliquables, saisissables, défilables ou porteurs de texte (surfaces :
    reçoit les limites des surfaces dessinées sans texte, voir elements_of)."""
    start = xml_text.find("<?xml")
    if start < 0:
        start = xml_text.find("<hierarchy")
    if start < 0:
        raise PhoneError("UI tree unreadable: " + xml_text.strip()[:200])
    root = ET.fromstring(xml_text[start: xml_text.rfind(">") + 1])

    def walk(parent: ET.Element, path: tuple[tuple[int, int], ...]) -> Iterable[Mapping[str, Any]]:
        for rank, node in enumerate(parent.findall("node")):
            here = (*path, (_number(node.get("drawing-order")), rank))
            yield {**node.attrib, PATH: here}
            yield from walk(node, here)

    return elements_of(walk(root, ()), surfaces)


PATH = "nexus-path"  # clé ajoutée aux attributs d'un nœud : sa place dans l'arbre (Element.path)


def _number(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def with_paths(nodes: list[dict[str, Any]], depths: list[int], orders: list[int]) -> None:
    """Chemins (Element.path) des nœuds rendus par le serveur du téléphone dans l'ordre du document, d'après la
    profondeur et l'ordre de dessin de chacun."""
    stack: list[tuple[tuple[tuple[int, int], ...], list[int]]] = []  # chemin du nœud ouvert, rangs déjà donnés
    for node, depth, order in zip(nodes, depths, orders, strict=True):
        del stack[max(depth, 0):]
        if len(stack) < depth:  # profondeur sautée (arbre tronqué) : chemins inconnus pour la suite
            return
        parent, ranks = stack[-1] if stack else ((), [0])
        rank = ranks[0]
        ranks[0] += 1
        here = (*parent, (order, rank))
        node[PATH] = here
        stack.append((here, [0]))


_EDIT_CLASS = re.compile(r"(EditText|AutoCompleteTextView|SearchAutoComplete|SearchEditText)$")


def elements_of(nodes: Iterable[Mapping[str, Any]], surfaces: list[Box] | None = None) -> list[Element]:
    """Éléments utiles d'après les attributs des nœuds (ceux du XML de uiautomator, ou rendus par le serveur dans les
    mêmes termes), dans l'ordre du document : une seule règle de tri et de numérotation pour les deux lectures.
    surfaces : reçoit les limites des surfaces dessinées (SurfaceView, TextureView), avec ou sans libellé
    accessible, lues par OCR pour y noircir un code."""
    elements: list[Element] = []
    for a in nodes:
        match = _BOUNDS.match(a.get("bounds", ""))
        if not match:
            continue
        x1, y1, x2, y2 = map(int, match.groups())
        if x2 <= x1 or y2 <= y1:
            continue
        raw = (a.get("text", ""), a.get("content-desc", ""))  # brut : identité et sceau d'un accord
        text, desc = raw[0].strip(), raw[1].strip()  # pour l'affichage et la recherche
        clickable = a.get("clickable") == "true" or a.get("long-clickable") == "true"
        # Drapeau d'Android (isEditable, envoyé par le lecteur d'écran) ; `uiautomator dump` ne le donne pas : classes de
        # saisie connues, sous-classes comprises (AutoCompleteTextView de la recherche des Paramètres, SearchView).
        editable = a.get("editable") == "true" or bool(_EDIT_CLASS.search(a.get("class", "")))
        scrollable = a.get("scrollable") == "true"
        if surfaces is not None and OPAQUE.search(a.get("class", "")):
            surfaces.append((x1, y1, x2, y2))  # libellée ou non : un libellé ne dit rien du texte dessiné
        if not (text or desc or clickable or editable or scrollable):
            continue
        states = []
        if a.get("checkable") == "true":
            states.append("checked" if a.get("checked") == "true" else "unchecked")
        if a.get("selected") == "true":
            states.append("selected")
        if a.get("focused") == "true":
            states.append("focused")
        if a.get("enabled") == "false":
            states.append("disabled")
        if scrollable:
            states.append("scrollable")
        if editable:
            states.append("editable")
        rid = a.get("resource-id", "")
        elements.append(Element(
            index=len(elements) + 1,
            role=a.get("class", "").rsplit(".", 1)[-1] or "View",
            text=text, desc=desc, rid=rid.split("/", 1)[-1] if rid else "",
            package=a.get("package", ""), bounds=(x1, y1, x2, y2), clickable=clickable or editable,
            states=states, password=a.get("password") == "true", focused=a.get("focused") == "true", raw=raw,
            editable=editable, scrollable=scrollable, path=tuple(a.get(PATH, ())),
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
        masked = (text, desc) != (e.text, e.desc)
        sealed = seal(*(e.raw if any(e.raw) else (e.text, e.desc))) if masked else ""
        out.append(replace(e, text=text, desc=desc, masked=masked, sealed=sealed, raw=("", "") if masked else e.raw))
    return out


_SEAL_KEY = secrets.token_bytes(32)  # propre au processus, comme les accords qu'il sert à lier


def seal(*raw: str) -> str:
    """Empreinte d'un texte brut masqué : deux codes, cartes ou IBAN différents donnent deux sceaux différents ; sans la
    clé (jamais exposée), le sceau ne permet pas de retrouver le texte, même court (code à 6 chiffres)."""
    return hmac.new(_SEAL_KEY, "\0".join(raw).encode("utf-8"), hashlib.sha256).hexdigest()[:20]


MOVED = 10  # pixels écran : écart des limites en deçà duquel un élément n'a pas bougé (rapport des changements)
SAME_PLACE = 40  # pixels écran : même endroit, contenu changé (champ dont le texte change, compteur, bascule)
DIFF_FULL = 0.6  # part d'éléments différents au-delà de laquelle la liste complète est rendue


@dataclass
class Changes:
    """Écran actuel comparé à la dernière liste montrée : elements porte les numéros à jour (ceux de la liste
    précédente pour les éléments retrouvés, des numéros neufs pour les autres)."""

    elements: list[Element]
    added: list[Element]
    removed: list[Element]
    changed: list[tuple[Element, list[str]]]
    unchanged: int
    ratio: float  # part d'éléments différents (0 : rien n'a changé, 1 : tout)


def _gap(a: Element, b: Element) -> int:
    return max(abs(p - q) for p, q in zip(a.bounds, b.bounds, strict=True))


def diff_elements(old: list[Element], new: list[Element]) -> Changes:
    """Rapprochement en deux passes, au plus près d'abord : même contenu (rôle, texte, description, ressource,
    application) où qu'il soit, puis même rôle, ressource et application au même endroit (contenu changé). Les deux
    listes sont déjà masquées : un code qui change sous son masque ne se voit pas."""
    match: dict[int, int] = {}  # position dans new → position dans old
    used: set[int] = set()

    def pair(key, limit: int | None) -> None:
        groups: dict[tuple, list[int]] = {}
        for i, o in enumerate(old):
            if i not in used:
                groups.setdefault(key(o), []).append(i)
        candidates = sorted((_gap(old[i], n), j, i) for j, n in enumerate(new) if j not in match
                            for i in groups.get(key(n), []) if limit is None or _gap(old[i], n) <= limit)
        for _, j, i in candidates:
            if j not in match and i not in used:
                match[j] = i
                used.add(i)

    pair(lambda e: (e.role, e.text, e.desc, e.rid, e.package), None)
    pair(lambda e: (e.role, e.rid, e.package), SAME_PLACE)
    following = max((e.index for e in old), default=0)
    elements, added, changed, unchanged = [], [], [], 0
    for j, e in enumerate(new):
        if j in match:
            before = old[match[j]]
            e = replace(e, index=before.index)
            why = [name for name, differs in (("text", (e.text, e.desc) != (before.text, before.desc)),
                                              ("state", e.states != before.states),
                                              ("moved", _gap(e, before) > MOVED)) if differs]
            if why:
                changed.append((e, why))
            else:
                unchanged += 1
        else:
            following += 1
            e = replace(e, index=following)
            added.append(e)
        elements.append(e)
    removed = [o for i, o in enumerate(old) if i not in used]
    ratio = 1 - unchanged / max(len(old), len(new), 1)
    return Changes(elements, added, removed, changed, unchanged, ratio)


def blackout(image: PILImage.Image, elements: list[Element], shrink: float = 1.0) -> PILImage.Image:
    """Noircit sur l'image la zone des éléments dont un code a été masqué et des champs de mot de passe (limites en
    pixels écran ; shrink : image réduite par le téléphone, pixels image = pixels écran × shrink, zone arrondie vers
    l'extérieur)."""
    hidden = [e.bounds for e in elements if e.masked or e.password]
    if not hidden:
        return image
    image = image.convert("RGB")
    draw = ImageDraw.Draw(image)
    for x1, y1, x2, y2 in hidden:
        if shrink == 1.0:
            draw.rectangle((x1, y1, x2, y2), fill=(0, 0, 0))
        else:
            draw.rectangle((math.floor(x1 * shrink), math.floor(y1 * shrink), math.ceil(x2 * shrink),
                            math.ceil(y2 * shrink)), fill=(0, 0, 0))
    return image


# Toute lecture de l'état, par n'importe quel Screen (session, capture...) : (appli au premier plan, début de la
# lecture). Branché par capture : une appli protégée vue pendant une vidéo la fait jeter aussitôt.
ON_STATE: Callable[[str, float], None] | None = None


def stamp() -> float:
    """Horodatage des lectures et des enregistrements vidéo, comparés entre eux (preuve prise pendant ou après une
    vidéo) : horloge fine. time.monotonic n'a qu'environ 16 ms de résolution sous Windows avant Python 3.13 : une
    lecture faite juste après la fin d'une vidéo y recevait la même date que cette fin et passait pour prise
    pendant."""
    return time.perf_counter()
OPAQUE_SHARE = 0.25  # part de l'écran au-delà de laquelle une surface sans texte fait lire l'image par OCR


def needs_ocr(elements: list[Element], size: tuple[int, int] | None, surfaces: Iterable[Box] = (),
              strict: bool = True) -> bool:
    """Texte que l'arbre ne donne pas : arbre sans aucun texte (jeu, Flutter, dessin), ou grande surface dessinée
    (SurfaceView, TextureView : surfaces, ou élément de ce rôle), même avec un libellé accessible posé dessus (un
    « Fermer » ne dit rien du texte dessiné). Seule l'OCR y repère un code."""
    if not any(e.text or e.desc for e in elements):
        return True
    width, height = size or (1080, 2000)
    boxes = list(surfaces) + [e.bounds for e in elements if OPAQUE.search(e.role)]
    big = [b for b in boxes if (b[2] - b[0]) * (b[3] - b[1]) >= OPAQUE_SHARE * width * height]
    if strict:  # capture enregistrée : toute grande surface, libellée ou non
        return bool(big)
    # look (latence) : seulement une grande surface sans texte de l'arbre posé dessus (ni libellé, ni texte dedans).
    return any(not any((e.text or e.desc) and x1 <= e.center[0] <= x2 and y1 <= e.center[1] <= y2 for e in elements)
               for x1, y1, x2, y2 in big)


def ocr_elements(image: PILImage.Image, width: int, package: str) -> list[Element] | None:
    """Texte vu sur l'image (OCR local de Windows) en éléments « OCR » (pixels écran ; width : largeur de l'écran),
    codes masqués comme ceux de l'arbre : un élément masqué est noirci sur l'image comme un élément de l'arbre.
    None : OCR indisponible ou en échec (les codes dessinés n'ont pas pu être cherchés), à ne pas confondre avec []."""
    if not ocr.available():
        return None
    factor = width / image.width if image.width else 1.0
    try:
        lines = ocr.read(image, factor)
    except ocr.OcrError:
        return None
    return mask_elements([Element(index=0, role="OCR", text=line.text, desc="", rid="", package=package,
                                  bounds=line.box, clickable=True) for line in lines])


MARK_FEW = 21  # marks="auto" : jusqu'à ce nombre d'éléments, tous numérotés ; au-delà, seulement ceux sans nom
MARK_SIZE = 18  # px du numéro sur l'image rendue (côté long MAX_SIDE)
MARK_FILL = (200, 30, 120, 215)  # pastille (comme nexus-windows), chiffres blancs
MARK_EDGE = (200, 30, 120, 150)  # contour fin des limites de l'élément
_FONT: list[ImageFont.FreeTypeFont | ImageFont.ImageFont] = []


def marked(elements: list[Element], marks: str = "auto") -> list[Element]:
    """Éléments dont le numéro [N] est dessiné sur l'image : auto = tous s'ils sont peu nombreux, sinon ceux sans nom
    (icônes sans texte ni description, que la liste seule ne permet pas de situer) ; all ; none."""
    if marks == "none":
        return []
    if marks == "all" or len(elements) <= MARK_FEW:
        return list(elements)
    return [e for e in elements if not e.label]


def _font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if not _FONT:
        _FONT.append(ImageFont.load_default(size=MARK_SIZE))
    return _FONT[0]


def draw_marks(image: PILImage.Image, elements: list[Element], factor: float,
               origin: tuple[float, float] = (0.0, 0.0)) -> PILImage.Image:
    """Numéros [N] et contour fin de chaque élément, sur l'image déjà réduite (pixel image = (pixel écran - origin) ×
    factor). Dessinés après le noircissement des codes : une pastille ne fait que recouvrir, jamais révéler."""
    if not elements:
        return image
    image = image.convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    font = _font()
    ox, oy = origin
    for e in elements:
        x1, y1, x2, y2 = ((e.bounds[0] - ox) * factor, (e.bounds[1] - oy) * factor,
                          (e.bounds[2] - ox) * factor, (e.bounds[3] - oy) * factor)
        if x2 <= 0 or y2 <= 0 or x1 >= image.width or y1 >= image.height:
            continue  # hors de la zone agrandie (region)
        draw.rectangle((x1, y1, x2 - 1, y2 - 1), outline=MARK_EDGE, width=1)
        label = str(e.index)
        box = draw.textbbox((0, 0), label, font=font)
        tw, th = box[2] - box[0] + 6, box[3] - box[1] + 4
        x = min(max(0, round(x1)), image.width - tw)
        y = min(max(0, round(y1)), image.height - th)
        draw.rectangle((x, y, x + tw, y + th), fill=MARK_FILL)
        draw.text((x + 3, y + 2 - box[1]), label, font=font, fill=(255, 255, 255, 255))
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
    # Activité au premier plan, jeton compris (« 57396375 com.android.settings/.SubSettings ») ; "" si illisible. Une
    # autre page de l'appli (activité) change le jeton ; un compteur ou une horloge qui tourne, jamais.
    activity: str = ""


# État (allumé, verrouillé, premier plan) en une commande : sorties de dumpsys filtrées sur le téléphone.
STATE_SH = ("dumpsys power | grep mWakefulness=; { dumpsys window displays; dumpsys window policy; } | grep -E "
            "'mCurrentFocus=|mFocusedApp=|isKeyguardShowing=|mShowingLockscreen=|KeyguardShowing=|mIsShowing='; true")
STATE_MARK = "@@NEXUS-STATE@@"
_KEYGUARD = re.compile(r"(?:isKeyguardShowing|mShowingLockscreen|KeyguardShowing|mIsShowing)=(true|false)")


def parse_state(power: str, window: str) -> State:
    awake = "mWakefulness=Awake" in power
    flags = _KEYGUARD.findall(window)
    focus = re.search(r"mCurrentFocus=Window\{\S+ \S+ ([\w.]+)/", window) or re.search(
        r"mFocusedApp=.*? ([\w.]+)/", window)
    record = re.search(r"mFocusedApp=ActivityRecord\{(\w+) \S+ ([\w.]+/[\w.$]+)", window)
    return State(awake, "true" in flags, focus.group(1) if focus else "", lock_known=bool(flags),
                 activity=f"{record.group(1)} {record.group(2)}" if record else "")


# Android tient-il le téléphone déverrouillé malgré l'écran de verrouillage (Extend Unlock : lieu ou appareil de
# confiance) ? Ligne de l'utilisateur courant de `dumpsys trust`, deviceLocked=0.
TRUST_SH = "dumpsys trust | grep -F '(current):'; true"
_TRUSTED = re.compile(r"\(current\):.*\bdeviceLocked=0\b")
LIFT_WAIT = 2.0  # s au plus pour que l'écran de verrouillage se lève
LIFTED = ("Lock screen lifted: Android keeps the phone unlocked (trusted place or device, deviceLocked=0); no code "
          "typed.")


def parse_trusted(dumpsys_trust: str) -> bool:
    return bool(_TRUSTED.search(dumpsys_trust))


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


# Serveurs d'autres outils qui gardent la connexion UiAutomation, unique sur le téléphone (vu le 19/10 : celui de
# mobile-mcp, resté 30 min en vie) : tant qu'il tourne, ni notre lecteur ni `uiautomator dump` ne lisent l'arbre.
_HOLDERS = re.compile(r"mobilecli|io\.appium\.uiautomator2|com\.github\.uiautomator|atx-agent|uiautomator2|"
                      r"androidx\.test\.runner|UiAutomatorStub|uiautomator|(?:^|[\s/])app_process", re.I)
# Les nôtres (NexusQuery, scrcpy pour le collage) et des app_process qui ne prennent pas l'accessibilité (Shizuku).
# Les commandes d'Android (am, pm, input, settings, wm…) tournent elles aussi dans app_process
# (com.android.commands.*) sans garder l'accessibilité, sauf uiautomator.
_NOT_HOLDERS = re.compile(r"NexusQuery|nexus-scrcpy|com\.genymobile\.scrcpy|shizuku|"
                          r"com\.android\.commands\.(?!uiautomator\b)", re.I)


def parse_holders(ps: str) -> list[tuple[str, str]]:
    """(nom, pid) des serveurs d'autres outils de pilotage qui peuvent garder UiAutomation, d'après
    `ps -A -o PID,NAME,ARGS` ; un `uiautomator dump` ponctuel (autre session) n'en est pas un : il la rend tout seul."""
    found = []
    for line in ps.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        pid, args = parts[0], parts[2].strip()
        if not _HOLDERS.search(args) or _NOT_HOLDERS.search(args) or re.search(r"uiautomator\S*\s+dump", args):
            continue
        words = [w for w in args.split() if "=" not in w or w.startswith("-")]  # sans CLASSPATH=… en tête
        name = (words[0] if words else args).rsplit("/", 1)[-1]
        if name in ("app_process", "app_process64"):
            # app_process <dossier> <classe> … : la classe dit l'outil.
            name = next((w for w in words[1:] if not w.startswith(("/", "-"))), name)
        found.append((name[:80], pid))
    return sorted(set(found))


def holders_message(holders: list[tuple[str, str]]) -> str:
    named = "; ".join(f"{name}, pid {pid}" for name, pid in holders)
    tool = "Another automation tool is" if len(holders) == 1 else "Other automation tools are"
    return f"{tool} using the phone's accessibility connection ({named}): close it or stop it, then retry."


NODE_FLAGS = ("checkable", "checked", "clickable", "enabled", "focusable", "focused", "scrollable", "long-clickable",
              "password", "selected", "editable")  # bits des drapeaux d'un nœud, dans l'ordre de NexusQuery.walk
POSTURE_MARK = "@@NEXUS-POSTURE@@"
MAX_NODES = 20_000


def fingerprint(elements: Iterable[Element], package: str) -> str:
    """Empreinte d'un écran : textes, descriptions, identifiants et positions (à 16 px près) de ses éléments, sans la
    barre d'état (son horloge change chaque minute) ni le focus (un appui sans effet le déplace). Deux écrans identiques
    pour l'agent ont la même empreinte."""
    parts = [package] + sorted(
        f"{e.text}|{e.desc}|{e.rid}|{e.bounds[0] // 16},{e.bounds[1] // 16},{e.bounds[2] // 16},{e.bounds[3] // 16}|"
        f"{','.join(s for s in e.states if s != 'focused')}" for e in elements if e.package != "com.android.systemui")
    return hashlib.sha1("\n".join(parts).encode()).hexdigest()[:16]


def content_print(elements: Iterable[Element], package: str) -> str:
    """Empreinte complète d'un écran pour « image inchangée » : tous les éléments, barre système comprise (une tuile ou
    une notification qui change d'état), limites et états exacts."""
    parts = [package] + sorted(f"{e.package}|{e.text}|{e.desc}|{e.rid}|{e.bounds}|{','.join(e.states)}|{e.masked}"
                               for e in elements)
    return hashlib.sha1("\n".join(parts).encode()).hexdigest()[:16]


LOOP_REPEATS = 3  # même lot de gestes sur le même écran, sans effet : l'agent tourne en rond
LOOP_STILL = 3  # lots de gestes d'affilée sans aucun changement de l'écran


class LoopWatch:
    """Repère un agent qui tourne en rond : mêmes gestes sur le même écran sans effet, ou écran figé malgré les gestes.
    Une vue par session MCP (chaque agent la sienne)."""

    def __init__(self) -> None:
        self.last: tuple[str, str] | None = None  # (gestes, écran avant) du lot précédent
        self.repeats = 0
        self.still = 0

    def record(self, gestures: str, before: str | None, after: str | None) -> str | None:
        if before is None or after is None:
            return None
        changed = before != after
        self.still = 0 if changed else self.still + 1
        same = self.last == (gestures, before) and not changed
        self.repeats = self.repeats + 1 if same else (1 if not changed else 0)
        self.last = (gestures, before)
        if self.repeats >= LOOP_REPEATS:
            return (f"⚠ Going in circles: the same gestures on the same screen {self.repeats} times, nothing changed. "
                    "Stop repeating them: map(query=…) says where a setting is, look re-reads the screen (the target "
                    "may be elsewhere or need a scroll), try another target or way, or ask the user.")
        if self.still >= LOOP_STILL:
            return (f"⚠ The screen has not changed after the last {self.still} batches of gestures: they have no "
                    "effect here (wrong target, disabled element, popup elsewhere?). Look again and change approach "
                    "rather than repeating.")
        return None


@dataclass
class Snapshot:
    """Lecture d'écran du serveur : état (sortie de STATE_SH), pliage, taille de l'affichage, nœuds (None : arbre
    illisible, tree_error dit pourquoi), image (JPEG du téléphone, éventuellement réduite) et taille de la capture."""

    state: State | None
    posture: str
    size: tuple[int, int]
    nodes: list[dict[str, Any]] | None
    tree_error: str = ""
    image: PILImage.Image | None = None
    shot: tuple[int, int] = (0, 0)
    image_error: str = ""


def parse_snapshot(data: bytes) -> Snapshot:
    """Réponse binaire de la requête `screen` (protocole décrit en tête de phone/NexusQuery.java) ; ValueError si elle
    est tronquée ou d'un autre protocole."""
    reader = query._Reader(data, 0)
    status = reader.text()
    state, posture = None, ""
    if status:
        head, _, tail = status.rpartition(POSTURE_MARK) if POSTURE_MARK in status else (status, "", "")
        state, posture = parse_state(head, head), parse_posture(tail)
    _, width, height = reader.number(), reader.number(), reader.number()
    nodes: list[dict[str, Any]] | None = None
    tree_error = ""
    kind = reader.number()
    if kind == 0:
        count = reader.number()
        if not 0 <= count <= MAX_NODES:
            raise ValueError("response from another protocol")
        nodes = []
        depths, orders = [], []
        for _ in range(count):
            text, rid, cls, package, desc = (reader.text() or "" for _ in range(5))
            flags = reader.number()
            x1, y1, x2, y2 = (reader.number() for _ in range(4))
            depths.append(reader.number())
            orders.append(reader.number())
            node: dict[str, Any] = {"text": text, "resource-id": rid, "class": cls, "package": package,
                                    "content-desc": desc, "bounds": f"[{x1},{y1}][{x2},{y2}]"}
            node.update({name: "true" if flags >> bit & 1 else "false" for bit, name in enumerate(NODE_FLAGS)})
            nodes.append(node)
        with_paths(nodes, depths, orders)
    elif kind == 1:
        tree_error = reader.text() or "UI tree unavailable"
    else:
        raise ValueError("response from another protocol")
    image, shot, image_error = None, (0, 0), ""
    kind = reader.number()
    if kind == 0:
        shot = (reader.number(), reader.number())
        picture = PILImage.open(io.BytesIO(reader.blob(reader.number())))
        picture.load()
        image = picture
    elif kind == 1:
        image_error = reader.text() or "no screenshot"
    elif kind != 2:
        raise ValueError("response from another protocol")
    return Snapshot(state, posture, (width, height), nodes, tree_error, image, shot, image_error)


@dataclass
class Reading:
    """Ce que look montre : état, image (None : non demandée), éléments ou l'erreur de l'arbre."""

    state: State
    image: PILImage.Image | None = None
    elements: list[Element] = field(default_factory=list)
    error: PhoneError | None = None


class Screen:
    def __init__(self, adb: Adb, fast: bool = True) -> None:
        self.adb = adb
        self.fast = fast
        self.scale = 1.0  # pixels image = pixels écran × scale
        self.shrink = 1.0  # pixels de la dernière capture = pixels écran × shrink (réduite par le téléphone : < 1)
        self.elements: list[Element] = []  # dernière liste montrée à l'agent (look) : ce que désigne [N]
        self.listed_package = ""  # premier plan au moment de cette liste (rapport des changements)
        self.size: tuple[int, int] | None = None
        self.posture = ""  # pliage au moment de la dernière image (les coordonnées "x,y" en dépendent)
        self.last_state: State | None = None  # état lu avec la dernière lecture d'écran (state ou dump)
        self.last_seen_at = 0.0  # début (stamp()) de la lecture qui a donné last_state
        self.busy = ""  # message de la dernière lecture d'arbre bloquée par un autre outil (UiAutomation prise)
        self.fingerprint: str | None = None  # empreinte du dernier écran lu (tourner en rond)
        self.rendered: PILImage.Image | None = None  # empreinte (thumb) de la dernière image rendue
        # Dernière image vraiment envoyée : (clé, empreinte, échelle, taille envoyée) ; la même ne repart pas.
        self.sent: tuple[Any, ...] | None = None
        self.last_size: tuple[int, int] = (0, 0)  # taille de la dernière image vraiment encodée pour l'agent
        self.loops = LoopWatch()
        self.surfaces: list[Box] = []  # surfaces dessinées sans texte de la dernière lecture de l'arbre (OCR)

    def _server(self) -> query.QueryServer | None:
        return query.server_for(self.adb) if FAST and self.fast else None

    def _observed(self, state: State, since: float) -> None:
        """État lu par une lecture commencée à `since` (stamp()) : gardé, et signalé à ON_STATE (garde de la
        vidéo en cours), quel que soit l'outil ou la session qui l'a lu."""
        self.last_state, self.last_seen_at = state, since
        if ON_STATE is not None:
            ON_STATE(state.package, since)

    def snapshot(self, image: str = "none", settle: bool = True) -> Snapshot | None:
        """Arbre, état, pliage et, au besoin, capture (image : none, scaled, full) en un échange avec le serveur gardé
        en vie ; None s'il est indisponible ou répond autre chose (l'appelant reprend la méthode d'avant). Liaison
        perdue : LinkError, comme toute lecture. settle=False : arbre de l'instant, sans attendre que l'écran se calme
        (relecture entre les deux appuis d'un double appui)."""
        server = self._server()
        if server is None:
            return None
        since = stamp()
        tables = server.run([["screen", "--image", image, "--max", str(MAX_SIDE), "--quality", str(PHONE_QUALITY),
                              "--quiet", str(SETTLE_QUIET_MS if settle else 0)]])
        if not tables or not tables[0].raw:
            return None
        try:
            snap = parse_snapshot(tables[0].raw)
        except (ValueError, struct.error, OSError):  # OSError : JPEG illisible (PIL)
            return None
        if snap.state is not None:
            self._observed(snap.state, since)
        return snap

    def release(self) -> None:
        """Connexion d'accessibilité du serveur rendue (s'il tourne) : `uiautomator dump` ne peut pas la partager."""
        if (server := self._server()) is not None:
            server.release()

    def read(self, image: bool, tree: bool, full: bool = False) -> Reading:
        """État, image et arbre (codes masqués) en un échange avec le serveur quand il répond ; chaque partie manquante
        relue comme avant (dumpsys, screencap, uiautomator dump). full : image en pleine résolution (region)."""
        snap = self.snapshot("full" if full else "scaled" if image else "none") if (image or tree) else None
        state = snap.state if snap and snap.state else self.state()
        reading = Reading(state)
        if not image and snap and snap.size[0] > 0 and snap.size[1] > 0:
            # Échelle des coordonnées @x,y même sans image jointe, relue à chaque lecture : rotation ou pliage depuis la
            # dernière image (défilement, zoom et coordonnées de la liste suivent l'écran actuel).
            if self.size != snap.size:  # même géométrie : l'échelle reste celle de la dernière image envoyée
                self.scale = fit(*snap.size)
            self.size = snap.size
            self.posture = snap.posture or self.posture
        if image:
            if snap and snap.image is not None and snap.shot[0] > 0:
                reading.image = snap.image
                self.size = snap.shot
                self.shrink = snap.image.width / snap.shot[0]
                self.posture = snap.posture or self.current_posture()
            else:
                reading.image = self.capture()
        if tree:
            if snap and snap.nodes is not None:
                self.surfaces = []
                reading.elements = mask_elements(elements_of(snap.nodes, self.surfaces))
            else:
                try:
                    ps = self._holders_check(snap.tree_error) if snap else None
                    reading.elements = self.dump(fast=False, ps=ps)
                except PhoneError as e:
                    reading.error = e
        return reading

    def current_posture(self) -> str:
        return parse_posture(self.adb.shell("dumpsys", "device_state", check=False, timeout=STATE_WAIT))

    def _ps(self) -> str:
        return self.adb.shell("ps", "-A", "-o", "PID,NAME,ARGS", check=False)

    def helpers(self) -> list[str]:
        """Programmes d'aide d'autres outils de pilotage en cours sur le téléphone."""
        return parse_helpers(self._ps())

    def _holders_check(self, error: str) -> str | None:
        """Arbre illisible : un seul `ps` ; PhoneError tout de suite (sans les essais de `uiautomator dump`, voués à
        l'échec) si un autre outil garde UiAutomation. Rend la sortie de `ps` (reprise pour le message final), ou None
        sans la lire quand la connexion a été obtenue (racine vide : écran en mouvement, pas un autre outil)."""
        if "null root node" in error:
            return None
        ps = self._ps()
        if holders := parse_holders(ps):
            self.busy = holders_message(holders)
            raise PhoneError(self.busy)
        return ps

    def state(self) -> State:
        """État lu en un seul aller-retour (trois dumpsys filtrés sur le téléphone)."""
        since = stamp()
        out = self.adb.shell("sh", "-c", STATE_SH, check=False, timeout=STATE_WAIT)
        self._observed(parse_state(out, out), since)
        assert self.last_state is not None
        return self.last_state

    def lift_keyguard(self, state: State) -> tuple[State, bool]:
        """Écran de verrouillage affiché alors qu'Android tient le téléphone déverrouillé (deviceLocked=0) : réveil et
        levée (KEYCODE_WAKEUP, wm dismiss-keyguard), état relu. Jamais de code tapé : verrouillé pour de bon, rien
        n'est envoyé et l'état reste celui lu. Rend l'état et True si l'écran de verrouillage a été levé."""
        if not state.locked:
            return state, False
        if not parse_trusted(self.adb.shell("sh", "-c", TRUST_SH, check=False, timeout=STATE_WAIT)):
            return state, False
        self.adb.shell("input", "keyevent", "KEYCODE_WAKEUP", check=False)
        self.adb.shell("wm", "dismiss-keyguard", check=False)
        deadline = time.monotonic() + LIFT_WAIT
        while True:
            time.sleep(0.2)
            state = self.state()
            lifted = state.lock_known and not state.locked  # état illisible : jamais pris pour une levée
            if lifted or time.monotonic() >= deadline:
                return state, lifted

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
                    raise PhoneError("Screenshot unreadable (three attempts): "
                                     + raw[:60].decode("utf-8", "replace")) from e
                time.sleep(0.5)
        assert image is not None
        self.size = image.size
        self.shrink = 1.0
        self.posture = self.current_posture()
        return image

    def prepare(self, image: PILImage.Image, region: list[float] | None = None,
                marks: list[Element] | None = None) -> PILImage.Image:
        """Image pour l'agent, pas encore encodée (taille par sized, jamais agrandie) ; region [x1,y1,x2,y2] en pixels
        de l'image complète ; marks : éléments dont le numéro [N] est dessiné (draw_marks). Empreinte dans self.rendered."""
        origin = (0.0, 0.0)
        # Capture réduite par le téléphone (look sans region) : pixels image = pixels écran × shrink.
        shrink = self.shrink if self.size and self.shrink != 1.0 and round(self.size[0] * self.shrink) == image.width \
            else 1.0
        if region:
            x1, y1, x2, y2 = (v / self.scale for v in region)
            box = (int(x1), int(y1), int(x2), int(y2))
            image = image.crop((round(box[0] * shrink), round(box[1] * shrink), round(box[2] * shrink),
                                round(box[3] * shrink)) if shrink != 1.0 else box)
            origin = (float(box[0]), float(box[1]))
            width, height = box[2] - box[0], box[3] - box[1]
            target = sized(width, height)
        else:
            width, height = self.size if shrink != 1.0 and self.size else image.size
            target = sized(width, height)
            if image.width < target[0]:  # jamais agrandie : capture réduite par le téléphone plus que nécessaire
                target = (image.width, min(image.height, int(height * image.width / width)))
            if abs(image.width - target[0]) <= 1 and abs(image.height - target[1]) <= 1 and within(image.size):
                target = image.size  # déjà à la taille (réduite par le téléphone) : pas de rééchantillonnage
            self.scale = target[0] / width
        factor = target[0] / width
        if image.size != target:
            image = image.resize(target, PILImage.Resampling.LANCZOS)
        if marks:
            image = draw_marks(image, marks, factor, origin)
        self.rendered = thumb(image)
        return image

    def send(self, image: PILImage.Image, region: list[float] | None = None) -> bytes:
        """Encode l'image préparée ; si elle a dû être réduite encore (plus de 500 Ko), l'échelle des coordonnées
        suit la taille vraiment envoyée."""
        data, size = encode(image)
        if size != image.size and not region:
            self.scale *= size[0] / image.width
        self.last_size = size
        return data

    def render(self, image: PILImage.Image, region: list[float] | None = None,
               marks: list[Element] | None = None) -> bytes:
        return self.send(self.prepare(image, region, marks), region)

    def dump(self, fast: bool = True, ps: str | None = None) -> list[Element]:
        """Arbre UI de l'écran actuel, codes masqués ; ne change pas la liste du dernier look. Par le serveur gardé en
        vie quand il répond, sinon uiautomator dump (après lui avoir fait rendre sa connexion d'accessibilité). Arrêt
        immédiat si un autre outil garde UiAutomation (un seul `ps` ; ps : sa sortie, déjà lue par l'appelant)."""
        self.busy = ""
        if fast and (snap := self.snapshot()) is not None:
            if snap.nodes is not None:
                self.surfaces = []
                return mask_elements(elements_of(snap.nodes, self.surfaces))
            ps = self._holders_check(snap.tree_error)
        self.release()
        last = ""
        for _ in range(3):
            since = stamp()
            out = self.adb.shell("sh", "-c", f"uiautomator dump {UI_DUMP} 2>&1; cat {UI_DUMP} 2>/dev/null; "
                                 f"rm -f {UI_DUMP}; echo; echo {STATE_MARK}; {STATE_SH}", timeout=20, check=False)
            # La dernière marque est la nôtre (un texte de l'écran qui la recopierait est avant, dans l'arbre).
            tree, _, status = out.rpartition(STATE_MARK) if STATE_MARK in out else (out, "", "")
            if status.strip():  # état lu dans le même aller-retour que l'arbre : jugé sur le même instant
                self._observed(parse_state(status, status), since)
            if "<hierarchy" in tree:
                self.surfaces = []
                return mask_elements(parse_ui(tree, self.surfaces))
            last = out.strip()
            if ps is None and (ps := self._holders_check(last)) is None:
                ps = ""  # racine vide : connexion obtenue, aucun `ps` de plus
            time.sleep(0.6)
        helpers = parse_helpers(ps) if ps else self.helpers()
        culprit = ("; another automation tool is running on the phone and may block reading: "
                   + ", ".join(helpers) + " (close it or restart the phone)") if helpers else ""
        raise PhoneError("UI tree unavailable (screen moving or protected): " + last[:200] + culprit)

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        return round(x / self.scale), round(y / self.scale)
