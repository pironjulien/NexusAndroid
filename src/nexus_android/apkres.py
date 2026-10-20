"""Lecture hors ligne d'un APK Android (Python pur) : table de ressources (resources.arsc), XML binaires (AXML) et
constantes de ressources dans le code (dex).

Sert à reconstruire la carte des Paramètres : l'index de recherche du téléphone est réservé aux applications système
(READ_SEARCH_INDEXABLES, refusé à ADB), mais l'APK des Paramètres se lit sans droit particulier et décrit chaque écran
(res/xml/*.xml : titres, clés, sous-écrans) ; le code dit quel fragment affiche quel écran."""

from __future__ import annotations

import struct
import zipfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

RES_STRING_POOL = 0x0001
RES_TABLE = 0x0002
RES_XML = 0x0003
RES_XML_START_ELEMENT = 0x0102
RES_XML_END_ELEMENT = 0x0103
RES_XML_RESOURCE_MAP = 0x0180
RES_TABLE_PACKAGE = 0x0200
RES_TABLE_TYPE = 0x0201

TYPE_REFERENCE = 0x01
TYPE_STRING = 0x03
TYPE_BOOLEAN = 0x12

# Attributs android:* par identifiant (le nom peut manquer d'un AXML optimisé, l'identifiant reste dans la table).
ANDROID_ATTRS = {
    0x01010003: "name", 0x01010021: "targetPackage", 0x0101002F: "targetClass", 0x01010024: "value",
    0x010101E1: "title", 0x010101E8: "key", 0x010101E9: "summary", 0x010102E3: "fragment",
    0x01010001: "label", 0x01010010: "exported", 0x01010202: "targetActivity", 0x01010000: "theme",
}


class FormatError(ValueError):
    """Fichier binaire Android illisible (tronqué, format inconnu)."""


MAX_ENTRY = 256 * 1024 * 1024  # octets décompressés au plus par fichier lu dans l'APK (resources.arsc : ~70 Mo)
_BROKEN = (struct.error, IndexError, ValueError, OverflowError, KeyError, UnicodeError, zipfile.BadZipFile)


@contextmanager
def _format_errors(what: str) -> Iterator[None]:
    """Toute lecture hors limites d'un fichier tronqué ou fabriqué devient une FormatError (jamais un plantage)."""
    try:
        yield
    except FormatError:
        raise
    except _BROKEN as e:
        raise FormatError(f"{what}: unreadable ({type(e).__name__})") from None


class StringPool:
    """Pool de chaînes (UTF-8 ou UTF-16), décodées à la demande : la table des Paramètres en compte des centaines de
    milliers."""

    def __init__(self, data: bytes | memoryview, offset: int) -> None:
        _, header_size, size, count, _, flags, strings_start, _ = struct.unpack_from("<HHIIIIII", data, offset)
        if offset + size > len(data) or header_size + 4 * count > size or strings_start > size:
            raise FormatError("string pool out of bounds")
        self._data = memoryview(data)[offset:offset + size]  # lectures bornées au bloc, jamais au-delà
        self._utf8 = bool(flags & 0x100)
        self._offsets = struct.unpack_from(f"<{count}I", data, offset + header_size)
        self._base = strings_start
        self._cache: dict[int, str] = {}
        self.size = size

    def __len__(self) -> int:
        return len(self._offsets)

    def get(self, index: int) -> str:
        if index < 0 or index >= len(self._offsets):
            return ""
        if (cached := self._cache.get(index)) is not None:
            return cached
        try:
            text = self._decode(index)
        except _BROKEN:
            text = ""  # chaîne hors limites : vide, le reste de la table reste lisible
        self._cache[index] = text
        return text

    def _decode(self, index: int) -> str:
        d, pos = self._data, self._base + self._offsets[index]
        if self._utf8:
            pos += 2 if d[pos] & 0x80 else 1  # longueur en caractères
            n = d[pos]
            if n & 0x80:
                n, pos = ((n & 0x7F) << 8) | d[pos + 1], pos + 2
            else:
                pos += 1
            if pos + n >= len(d) or d[pos + n] != 0:
                raise FormatError("string outside its pool or without end")
            text = bytes(d[pos:pos + n]).decode("utf-8", "replace")
        else:
            n = struct.unpack_from("<H", d, pos)[0]
            if n & 0x8000:
                n, pos = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", d, pos + 2)[0], pos + 4
            else:
                pos += 2
            if pos + 2 * n + 2 > len(d) or d[pos + 2 * n] or d[pos + 2 * n + 1]:
                raise FormatError("string outside its pool or without end")
            text = bytes(d[pos:pos + 2 * n]).decode("utf-16-le", "replace")
        return text


@dataclass
class Value:
    type: int
    data: int
    raw: str | None = None  # chaîne brute (AXML) ou chaîne résolue (table)

    @property
    def ref(self) -> int | None:
        return self.data if self.type == TYPE_REFERENCE and self.data else None


@dataclass
class Element:
    tag: str
    attrs: dict[str, Value]
    children: list[Element] = field(default_factory=list)

    def get(self, name: str) -> Value | None:
        return self.attrs.get(name)

    def walk(self) -> Iterator[Element]:
        yield self
        for child in self.children:
            yield from child.walk()


def parse_axml(data: bytes) -> Element:
    """XML binaire compilé (AndroidManifest.xml, res/xml/*.xml) → arbre d'éléments ; attributs par nom court."""
    with _format_errors("binary XML"):
        return _parse_axml(data)


def _parse_axml(data: bytes) -> Element:
    if len(data) < 8 or struct.unpack_from("<H", data, 0)[0] != RES_XML:
        raise FormatError("not a binary XML")
    if struct.unpack_from("<I", data, 4)[0] != len(data):
        raise FormatError("binary XML truncated")  # un arbre partiel passerait pour un écran complet
    pos = struct.unpack_from("<H", data, 2)[0]
    pool: StringPool | None = None
    resmap: list[int] = []
    root = Element("#root", {})
    stack = [root]
    while pos + 8 <= len(data):
        ctype, header, size = struct.unpack_from("<HHI", data, pos)
        if size < 8 or pos + size > len(data):
            raise FormatError("bad chunk size")
        if ctype == RES_STRING_POOL:
            pool = StringPool(data, pos)
        elif ctype == RES_XML_RESOURCE_MAP:
            resmap = list(struct.unpack_from(f"<{(size - header) // 4}I", data, pos + header))
        elif ctype == RES_XML_START_ELEMENT and pool is not None:
            body = pos + header
            _, name_i, attr_start, attr_size, attr_count = struct.unpack_from("<iIHHH", data, body)
            attrs: dict[str, Value] = {}
            for k in range(attr_count):
                a = body + attr_start + k * attr_size
                _, aname, raw, _, _, vtype, vdata = struct.unpack_from("<iIiHBBI", data, a)
                key = ANDROID_ATTRS.get(resmap[aname]) if aname < len(resmap) else None
                key = key or pool.get(aname)
                attrs[key] = Value(vtype, vdata, pool.get(raw) if raw >= 0 else None)
            el = Element(pool.get(name_i), attrs)
            stack[-1].children.append(el)
            stack.append(el)
        elif ctype == RES_XML_END_ELEMENT and pool is not None:
            name_i = struct.unpack_from("<I", data, pos + header + 4)[0]
            if len(stack) < 2 or pool.get(name_i) != stack[-1].tag:
                raise FormatError("binary XML: closing tag does not match")
            stack.pop()
        pos += size
    if pos != len(data) or len(stack) != 1 or len(root.children) != 1:
        raise FormatError("binary XML incomplete (open element, trailing bytes or no root)")
    return root.children[0]


def _language(config: bytes | memoryview) -> str:
    """Langue d'une configuration de ressources (« fr », « » pour la valeur par défaut)."""
    if len(config) < 12:
        return ""
    lang = bytes(config[8:10])
    if lang[0] & 0x80:  # code à trois lettres empaqueté : ignoré
        return "?"
    return lang.rstrip(b"\0").decode("ascii", "replace")


def _country(config: bytes | memoryview) -> str:
    return bytes(config[10:12]).rstrip(b"\0").decode("ascii", "replace") if len(config) >= 12 else ""


class ResourceTable:
    """resources.arsc : résout un identifiant 0x7fTTEEEE en chaîne (langue voulue, sinon par défaut) ou en nom de
    fichier (res/xml/...). Seuls les types demandés sont lus (string, xml) : la table des Paramètres fait 70 Mo."""

    def __init__(self, data: bytes, language: str = "fr", country: str = "FR",
                 types: Iterable[str] = ("string", "xml")) -> None:
        with _format_errors("resource table"):
            self._load(data, language, country, types)

    def _load(self, data: bytes, language: str, country: str, types: Iterable[str]) -> None:
        if struct.unpack_from("<H", data, 0)[0] != RES_TABLE:
            raise FormatError("not a resource table")
        self.language, self.country = language, country
        wanted = set(types)
        view = memoryview(data)
        header = struct.unpack_from("<H", data, 2)[0]
        pos = header
        self.values: dict[int, tuple[int, int, int]] = {}  # id → (rang de langue, type, donnée)
        self.names: dict[int, str] = {}  # id → nom d'entrée (« sec_display_settings »)
        self.type_ids: dict[str, int] = {}
        globals_pool: StringPool | None = None
        while pos + 8 <= len(data):
            ctype, _, size = struct.unpack_from("<HHI", data, pos)
            if size < 8:
                raise FormatError("bad chunk size")
            if ctype == RES_STRING_POOL and globals_pool is None:
                globals_pool = StringPool(view, pos)
            elif ctype == RES_TABLE_PACKAGE:
                self._package(view, pos, size, wanted)
            pos += size
        if globals_pool is None:
            raise FormatError("resource table without strings")
        self.strings = globals_pool

    def _rank(self, config: bytes | memoryview) -> int:
        lang = _language(config)
        if lang == self.language:
            return 3 if _country(config) == self.country else 2
        return 1 if lang == "" else 0

    def _package(self, d: memoryview, start: int, size: int, wanted: set[str]) -> None:
        header = struct.unpack_from("<H", d, start + 2)[0]
        pkg_id = struct.unpack_from("<I", d, start + 8)[0]
        type_off, _, key_off = struct.unpack_from("<III", d, start + 268)
        type_names = StringPool(d, start + type_off)
        keys = StringPool(d, start + key_off)
        ids = {type_names.get(i): i + 1 for i in range(len(type_names))}
        self.type_ids.update({n: (pkg_id << 24) | (t << 16) for n, t in ids.items()})
        keep = {ids[n] for n in wanted if n in ids}
        pos, end = start + header, start + size
        while pos + 8 <= end:
            ctype, theader, csize = struct.unpack_from("<HHI", d, pos)
            if csize < 8:
                raise FormatError("bad chunk size")
            if ctype == RES_TABLE_TYPE and d[pos + 8] in keep:
                self._type(d, pos, theader, pkg_id, keys)
            pos += csize

    def _type(self, d: memoryview, pos: int, header: int, pkg_id: int, keys: StringPool) -> None:
        type_id, flags = d[pos + 8], d[pos + 9]
        count, entries_start = struct.unpack_from("<II", d, pos + 12)
        config_size = struct.unpack_from("<I", d, pos + 20)[0]
        rank = self._rank(d[pos + 20:pos + 20 + config_size])
        if rank == 0:
            return
        index_pos = pos + header
        if flags & 0x01:  # FLAG_SPARSE : paires (index, décalage/4)
            pairs = struct.unpack_from(f"<{2 * count}H", d, index_pos)
            slots = [(pairs[i], pairs[i + 1] * 4) for i in range(0, len(pairs), 2)]
        elif flags & 0x02:  # FLAG_OFFSET16 : décalages/4 sur 16 bits, 0xffff = absent
            offs16 = struct.unpack_from(f"<{count}H", d, index_pos)
            slots = [(i, o * 4) for i, o in enumerate(offs16) if o != 0xFFFF]
        else:
            offs32 = struct.unpack_from(f"<{count}I", d, index_pos)
            slots = [(i, o) for i, o in enumerate(offs32) if o != 0xFFFFFFFF]
        base = pos + entries_start
        for index, off in slots:
            e = base + off
            res_id = (pkg_id << 24) | (type_id << 16) | index
            esize, eflags = struct.unpack_from("<HH", d, e)
            if eflags & 0x0008:  # FLAG_COMPACT : clé sur 16 bits, type dans l'octet haut des drapeaux
                key, vtype, vdata = esize, eflags >> 8, struct.unpack_from("<I", d, e + 4)[0]
            elif eflags & 0x0001:  # valeur complexe (tableau, style) : sans intérêt ici
                continue
            else:
                key = struct.unpack_from("<I", d, e + 4)[0]
                vtype, vdata = d[e + esize + 3], struct.unpack_from("<I", d, e + esize + 4)[0]
            if res_id not in self.names:
                self.names[res_id] = keys.get(key)
            old = self.values.get(res_id)
            if old is None or rank > old[0]:
                self.values[res_id] = (rank, vtype, vdata)

    def string(self, res_id: int | None, depth: int = 0) -> str | None:
        """Texte d'une ressource chaîne (références suivies) ; None si absente ou d'un autre paquet."""
        if res_id is None or depth > 4:
            return None
        found = self.values.get(res_id)
        if found is None:
            return None
        _, vtype, vdata = found
        if vtype == TYPE_STRING:
            return self.strings.get(vdata)
        if vtype == TYPE_REFERENCE:
            return self.string(vdata, depth + 1)
        return None

    def file(self, res_id: int) -> str | None:
        """Chemin du fichier d'une ressource fichier (« res/xml/sec_display_settings.xml »)."""
        found = self.values.get(res_id)
        return self.strings.get(found[2]) if found and found[1] == TYPE_STRING else None

    def is_type(self, res_id: int, name: str) -> bool:
        base = self.type_ids.get(name)
        return base is not None and (res_id & 0xFFFF0000) == base

    def text(self, value: Value | None) -> str | None:
        """Texte d'un attribut : chaîne brute, ou ressource résolue."""
        if value is None:
            return None
        if value.type == TYPE_STRING:
            return value.raw
        return self.string(value.ref) if value.ref else None


def _uleb(d: bytes | memoryview, pos: int) -> tuple[int, int]:
    """Entier ULEB128 d'un dex (32 bits : 5 octets au plus)."""
    result = 0
    for shift in range(0, 35, 7):
        b = d[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if b < 0x80:
            return result, pos
    raise FormatError("ULEB128 longer than 5 bytes")


def _sizes() -> list[int]:
    """Taille en unités de 16 bits de chaque instruction Dalvik, par code d'opération (formats de la spécification
    « Dalvik bytecode ») ; 0x00 (nop) peut porter une table (switch, tableau), mesurée à part."""
    size = [1] * 256
    two = [0x02, 0x05, 0x08, 0x13, 0x15, 0x16, 0x19, 0x1A, 0x1C, 0x1F, 0x20, 0x22, 0x23, 0x29, 0xFE, 0xFF,
           *range(0x2D, 0x3E), *range(0x44, 0x6E), *range(0x90, 0xB0), *range(0xD0, 0xE3)]
    three = [0x03, 0x06, 0x09, 0x14, 0x17, 0x1B, 0x24, 0x25, 0x26, 0x2A, 0x2B, 0x2C, 0xFC, 0xFD,
             *range(0x6E, 0x73), *range(0x74, 0x79)]
    for op in two:
        size[op] = 2
    for op in three:
        size[op] = 3
    size[0x18] = 5  # const-wide
    size[0xFA] = size[0xFB] = 4  # invoke-polymorphic
    return size


_SIZES = _sizes()


def _payload(insns: tuple[int, ...], i: int) -> int:
    """Taille d'une table rangée dans le code (packed-switch, sparse-switch, fill-array-data), sinon d'un nop."""
    ident = insns[i]
    if ident == 0x0100:
        return insns[i + 1] * 2 + 4
    if ident == 0x0200:
        return insns[i + 1] * 4 + 2
    if ident == 0x0300:
        width, count = insns[i + 1], insns[i + 2] | (insns[i + 3] << 16)
        return (count * width + 1) // 2 + 4
    return 1


class Dex:
    """Fichier dex : pour quelques classes choisies, les constantes 0x7f…… chargées par leur code (const, const/high16).
    Suffit à savoir quel écran XML un fragment affiche (getPreferenceScreenResId, addPreferencesFromResource)."""

    def __init__(self, data: bytes) -> None:
        if not data.startswith(b"dex\n") or len(data) < 0x70:
            raise FormatError("not a dex file")
        self.d = memoryview(data)
        (self.string_count, self.string_off, self.type_count, self.type_off) = struct.unpack_from("<IIII", data, 0x38)
        self.method_count, self.method_off, self.class_count, self.class_off = struct.unpack_from("<IIII", data, 0x58)
        if self.class_off + 32 * self.class_count > len(data):
            raise FormatError("dex class table out of bounds")
        self._strings: dict[int, str] = {}

    def string(self, index: int) -> str:
        if (s := self._strings.get(index)) is not None:
            return s
        off = struct.unpack_from("<I", self.d, self.string_off + 4 * index)[0]
        _, pos = _uleb(self.d, off)
        end = bytes(self.d[pos:pos + 65536]).find(b"\0")
        if end < 0:
            raise FormatError("dex string without end")
        s = bytes(self.d[pos:pos + end]).decode("utf-8", "replace")
        self._strings[index] = s
        return s

    def type_name(self, index: int) -> str:
        desc = self.string(struct.unpack_from("<I", self.d, self.type_off + 4 * index)[0])
        return desc[1:-1].replace("/", ".") if desc.startswith("L") else desc

    def method_name(self, index: int) -> str:
        return self.string(struct.unpack_from("<I", self.d, self.method_off + 8 * index + 4)[0])

    def constants(self, classes: set[str]) -> dict[str, list[tuple[str, int]]]:
        """Classe (nom pointé) → [(méthode, constante 0x7f……)] dans l'ordre du code, pour les classes demandées."""
        with _format_errors("dex"):
            return self._constants(classes)

    def _constants(self, classes: set[str]) -> dict[str, list[tuple[str, int]]]:
        found: dict[str, list[tuple[str, int]]] = {}
        d = self.d
        for c in range(self.class_count):
            item = self.class_off + 32 * c
            name = self.type_name(struct.unpack_from("<I", d, item)[0])
            if name not in classes:
                continue
            data_off = struct.unpack_from("<I", d, item + 24)[0]
            if not data_off:
                continue
            sf, pos = _uleb(d, data_off)
            inf, pos = _uleb(d, pos)
            dm, pos = _uleb(d, pos)
            vm, pos = _uleb(d, pos)
            for _ in range(2 * (sf + inf)):
                _, pos = _uleb(d, pos)
            out: list[tuple[str, int]] = []
            for group in (dm, vm):
                method = 0
                for _ in range(group):
                    diff, pos = _uleb(d, pos)
                    _, pos = _uleb(d, pos)
                    code, pos = _uleb(d, pos)
                    method += diff
                    if code:
                        out.extend((self.method_name(method), v) for v in self._code_constants(code))
            found[name] = out
        return found

    def _code_constants(self, code: int) -> list[int]:
        size = struct.unpack_from("<I", self.d, code + 12)[0]
        if code + 16 + 2 * size > len(self.d):
            raise FormatError("dex code out of bounds")
        insns = struct.unpack_from(f"<{size}H", self.d, code + 16)
        # Décodage aux frontières des instructions (tailles de la spécification, tables comprises) : un opérande
        # d'une autre instruction n'est jamais pris pour un « const ». Seuls const (0x14) et const/high16 (0x15).
        found: list[int] = []
        i = 0
        while i < size:
            op = insns[i] & 0xFF
            if op == 0x14 and i + 2 < size:
                value = insns[i + 1] | (insns[i + 2] << 16)
                if value >> 24 == 0x7F:
                    found.append(value)
            elif op == 0x15 and i + 1 < size:
                value = insns[i + 1] << 16
                if value >> 24 == 0x7F:
                    found.append(value)
            i += _payload(insns, i) if op == 0 else _SIZES[op]
        return found


class Apk:
    """Un APK ouvert : table de ressources, XML binaires, dex. Toute partie illisible lève FormatError."""

    def __init__(self, path: str, language: str = "fr", country: str = "FR") -> None:
        try:
            self.zip = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as e:
            raise FormatError(f"not an APK ({e})") from None
        try:
            self.names = set(self.zip.namelist())
            data = self.read("resources.arsc")
            self.table = ResourceTable(data, language, country)
            # Titres anglais en plus (noms que les agents connaissent : « Extend Unlock »), quand la langue diffère.
            self.english = ResourceTable(data, "en", "US") if language != "en" else None
        except BaseException:
            self.zip.close()
            raise

    def read(self, name: str) -> bytes:
        """Fichier de l'APK, taille décompressée bornée (MAX_ENTRY) avant et pendant la lecture."""
        with _format_errors(name):
            info = self.zip.getinfo(name)
            if info.file_size > MAX_ENTRY:
                raise FormatError(f"{name}: {info.file_size} bytes, over the {MAX_ENTRY} limit")
            with self.zip.open(info) as f:
                data = f.read(MAX_ENTRY + 1)
            if len(data) > MAX_ENTRY:
                raise FormatError(f"{name}: over the {MAX_ENTRY} limit")
            return data

    def xml(self, name: str) -> Element:
        return parse_axml(self.read(name))

    def manifest(self) -> Element:
        return self.xml("AndroidManifest.xml")

    def dex_files(self) -> list[str]:
        return sorted((n for n in self.names if n.startswith("classes") and n.endswith(".dex")),
                      key=lambda n: (len(n), n))

    def constants(self, classes: set[str]) -> dict[str, list[tuple[str, int]]]:
        found: dict[str, list[tuple[str, int]]] = {}
        for name in self.dex_files():
            if not classes - found.keys():
                break
            found.update(Dex(self.read(name)).constants(classes - found.keys()))
        return found

    def close(self) -> None:
        self.zip.close()
