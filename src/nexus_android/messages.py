"""SMS et RCS du téléphone (fournisseur de messagerie Android), codes de sécurité masqués, contacts résolus."""

from __future__ import annotations

import base64
import binascii
import math
import re
import secrets
import shlex
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote

from .adb import Adb, PhoneError
from .countries import COUNTRIES
from .query import Table, server_for

# Mots qui annoncent un code, cherchés sur le texte sans accents (« vérification » → « verification ») ; « code » aussi
# dans un mot composé (« Bestätigungscode »), « código », « codice », « clave », « token », « sign in », « log in ».
_KEYWORDS = re.compile(
    r"code|\b(?:codigo|codice|clave\b|contrasena|kennwort|passwort|bestatig|token\b|otp\b|pin\b|passcode|"
    r"mot de passe|password|2fa\b|log[- ]?in\b|login|sign[- ]?in\b|connexion|securit|verif|valid|confirm|authentif|"
    r"authentic|identifiant|usage unique|one[- ]?time)",
    re.I,
)
# Codes chiffrés : 4 chiffres ou plus (espaces ou tirets permis), sans prendre les montants (1 250,00 ; 1 250 €), les
# dates (12/10/2026, 12-10-2026, 2026-10-12), les coordonnées (@1234,2000) ni les numéros de téléphone (10 chiffres ou plus,
# « +33… » : « 06 11 22 33 44 ») ; « 482913, valable » reste un code.
_CODE = re.compile(r"(?<![\d,./$€£+-])(?<!\d[ -])(?!\d{1,2}-\d{1,2}-\d{2,4}(?!\d)|\d{4}-\d{1,2}-\d{1,2}(?!\d))"
                   r"(?:[A-Z]-)?\d(?:[ -]?\d){3,8}(?![ -]?\d|[.,/]\d|\s?(?:€|\$|£|eur\b|EUR\b|%))")
# Codes alphanumériques de 4 à 10 caractères mêlant lettres et chiffres (« A7K9P2 »), près d'un mot-clé.
_ALNUM = re.compile(r"(?<![\w-])(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{4,10}(?![\w-])")
_NEAR = 60  # caractères entre le mot-clé et un code alphanumérique
MASK = "[masked code]"


def plain(text: str) -> str:
    """Texte sans accents et apostrophe droite, même longueur (positions conservées)."""
    out = []
    for c in text or "":
        base = "".join(ch for ch in unicodedata.normalize("NFKD", c) if not unicodedata.combining(ch))
        out.append("'" if c in "’‘ʼ" else base if len(base) == 1 else c)
    return "".join(out)


def has_keyword(*texts: str) -> bool:
    return any(t and _KEYWORDS.search(plain(t)) for t in texts)


# Carte bancaire (13 à 19 chiffres, clé de Luhn) et IBAN (clé modulo 97), espaces ou tirets permis : masqués partout,
# sans mot-clé (un numéro de carte ou de compte n'a pas besoin d'être annoncé pour être sensible).
_CARD = re.compile(r"(?<![\w.])\d(?:[ -]?\d){12,18}(?![\w.])")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b")
CARD, IBAN = "[masked card]", "[masked IBAN]"


def _luhn(digits: str) -> bool:
    total = 0
    for i, c in enumerate(reversed(digits)):
        n = int(c) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def _card(m: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", m[0])
    return CARD if 13 <= len(digits) <= 19 and len(set(digits)) > 1 and _luhn(digits) else m[0]


def _iban(m: re.Match[str]) -> str:
    raw = m[0].replace(" ", "")
    moved = raw[4:] + raw[:4]
    number = "".join(str(int(c, 36)) for c in moved)
    return IBAN if 15 <= len(raw) <= 34 and int(number) % 97 == 1 else m[0]


def mask_codes(text: str, context: bool = False) -> str:
    """Masquage unique de toutes les sorties texte (écran, notifications, SMS, presse-papiers, agenda, journaux, web,
    carte) : numéros de carte (Luhn) et IBAN (modulo 97) toujours ; codes de validation des textes qui en parlent.
    L'agent ne les voit jamais. context=True : le mot-clé est ailleurs (titre de la notification, nom du champ), tout
    code du texte est masqué."""
    if not text:
        return text
    text = _CARD.sub(_card, _IBAN.sub(_iban, text))
    keywords = [m.span() for m in _KEYWORDS.finditer(plain(text))]
    if not keywords and not context:
        return text

    def alnum(m: re.Match[str]) -> str:
        near = context or any(m.start() - _NEAR <= end and start <= m.end() + _NEAR for start, end in keywords)
        return MASK if near else m.group(0)

    return _CODE.sub(MASK, _ALNUM.sub(alnum, text))  # alphanumériques d'abord : positions des mots-clés intactes


_BARE_CODE = re.compile(r"(?:[A-Z]-)?\d(?:[ -]?\d){3,7}")  # 4 à 8 chiffres seuls : code copié depuis un SMS
_LINK_OR_MAIL = re.compile(r"(?i)[a-z][a-z0-9+.-]*://\S|(?:mailto|tel|geo|sms|smsto|market):\S|www\.|[^@\s:/]+@[^@\s]+\.[a-z]{2,}$")


# Jetons connus par leur préfixe (OpenAI, Anthropic, GitHub, GitLab, AWS, Slack, Google, Stripe, Hugging Face, npm,
# PyPI, SendGrid, Shopify, OAuth Google).
_KEY_PREFIX = re.compile(r"(?:sk-|sk_(?:live|test)_|rk_(?:live|test)_|gh[pousr]_|github_pat_|glpat-|AKIA|ASIA|"
                         r"xox[abposr]-|AIza|hf_|npm_|pypi-|SG\.|shpat_|ya29\.)[A-Za-z0-9._\-]{12,}")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*")
_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]+-----")
_HEX = re.compile(r"[0-9a-fA-F]{24,}")
_B64 = re.compile(r"[A-Za-z0-9+/_-]{24,}={0,2}")
# Ponctuation de mot de passe ; « . », « - » et « _ » n'en sont pas (fichiers, versions, modèles, plaques, hôtes),
# ni l'apostrophe (aujourd'hui).
_PW_SYMBOLS = set(r"!@#$%^&*+=?~|\<>{}[]")
_SEPARATORS = re.compile(r"[._-]")
_HANDLE = re.compile(r"[@#][\w.]+")  # @pseudo, #mot-dièse
_FILE_NAME = re.compile(r"[\w.-]+\.[A-Za-z0-9]{1,5}")


def _entropy(value: str) -> float:
    """Bits par caractère (Shannon) : un jeton aléatoire en a beaucoup, un mot ou une répétition peu."""
    counts = {c: value.count(c) for c in set(value)}
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())


def _classes(value: str) -> int:
    return sum((any(c.islower() for c in value), any(c.isupper() for c in value), any(c.isdigit() for c in value),
                any(c in _PW_SYMBOLS for c in value)))


def secret_like(text: str) -> bool:
    """Texte copié qui ressemble à un secret sans mot-clé autour : code seul (« 482913 »), clé privée (PEM), jeton
    (préfixe connu, JWT, longue suite hexadécimale ou base64 au hasard), ou mot de passe (un seul mot court mêlant
    des lettres à une ponctuation de mot de passe, ou trois sortes de caractères qu'aucun découpage aux séparateurs
    . - _ n'explique). Lien, adresse e-mail, nom de fichier, version, modèle, plaque ou hôte ne le sont pas."""
    value = (text or "").strip()
    if _PEM.search(value):
        return True
    if _BARE_CODE.fullmatch(value):
        return True
    if not value or any(c.isspace() for c in value) or len(value) > 8192 or _LINK_OR_MAIL.match(value):
        return False
    if _KEY_PREFIX.fullmatch(value) or _JWT.fullmatch(value):
        return True
    if _HEX.fullmatch(value):
        return _entropy(value) >= 3.0
    if _B64.fullmatch(value):
        core = value.rstrip("=")
        # Jeton tiré au hasard : minuscules, majuscules et chiffres mêlés, très peu de - et _ (un nom-avec-des-tirets
        # en a un tous les quelques caractères), entropie haute.
        mixed = all((any(c.islower() for c in core), any(c.isupper() for c in core), any(c.isdigit() for c in core)))
        if mixed and sum(c in "-_" for c in core) * 10 <= len(core) and _entropy(core) >= 3.5:
            return True
    if not 6 <= len(value) <= 64 or _HANDLE.fullmatch(value):
        return False
    letters = any(c.isalpha() for c in value)
    # « Merci! » : ponctuation de fin de phrase ; « hunter2! » : un chiffre dit qu'elle fait partie du mot.
    core = value if any(c.isdigit() for c in value) else value.rstrip("!?")
    if letters and any(c in _PW_SYMBOLS for c in core):
        return True
    if _classes(core) < 3:
        return False
    parts = [x for x in _SEPARATORS.split(value) if x]
    if len(parts) > 1 and all(_classes(x) <= 2 for x in parts):
        return False  # SM-F956B, Release-2026.Q4 : morceaux ordinaires reliés par des séparateurs
    return not (_FILE_NAME.fullmatch(value) and _classes(value.rsplit(".", 1)[0]) <= 2)


FORGED = ("read refused: the output of `content` does not split into safe rows (a text, an SMS for example, "
          "imitates a \"Row:\" line); nothing is shown, so no dubious message is displayed. Try again in a minute "
          "(the phone's read server)")


def _loose_fields(chunk: str, last: str) -> dict[str, str]:
    """Colonnes d'une ligne dont l'ordre diffère de la projection : « nom=valeur, » ; `last` prend tout le reste."""
    head, sep, tail = chunk.partition(f"{last}=")
    row = {m.group(1): m.group(2).strip() for m in re.finditer(r"(\w+)=([^,]*)(?:, |$)", head)}
    if sep:
        row[last] = tail.rstrip("\r\n")
    return row


def _fields(chunk: str, projection: list[str]) -> dict[str, str]:
    """Colonnes d'une ligne, dans l'ordre de la projection : une valeur va jusqu'à « , <colonne suivante>= »
    (« Dupont, Jean » reste entier), la dernière jusqu'à la fin de la ligne (texte libre, retours à la ligne permis)."""
    if not chunk.startswith(f"{projection[0]}="):
        return _loose_fields(chunk, projection[-1])
    starts = [len(projection[0]) + 1]
    for column in projection[1:]:
        at = chunk.find(f", {column}=", starts[-1])
        if at < 0:
            return _loose_fields(chunk, projection[-1])
        starts.append(at + len(column) + 3)
    ends = [start - len(column) - 3 for start, column in zip(starts[1:], projection[1:], strict=True)] + [len(chunk)]
    row = {column: chunk[start:end] for column, start, end in zip(projection, starts, ends, strict=True)}
    row[projection[-1]] = row[projection[-1]].rstrip("\r\n")
    return row


def parse_rows(output: str, projection: list[str], limit: int | None = None) -> list[dict[str, str]]:
    """Sortie texte de `content query` (repli par adb shell). ValueError si les numéros « Row: N » ne se suivent pas
    strictement depuis 0 ou dépassent `limit` : un texte libre qui contient « \\nRow: 1 _id=… » fabriquerait sinon
    une ligne (faux message sortant)."""
    parts = re.split(r"(?m)^Row: (\d+) ", output)
    numbers, chunks = parts[1::2], parts[2::2]
    if parts[0].strip() not in ("", "No result found.") or numbers != [str(i) for i in range(len(numbers))] or (
            limit is not None and len(numbers) > limit):
        raise ValueError(FORGED)
    return [_fields(chunk, projection) for chunk in chunks]


def shell_table(output: str, projection: list[str], limit: int | None = None, count: int | None = None) -> Table:
    """Sortie de `content query` → Table ; `count` : nombre de lignes rendu par la même lecture réduite à _id (aucun
    texte libre), qui doit être égal à celui des lignes lues, sinon la lecture est refusée."""
    text = output.strip()
    if text and not text.startswith(("Row: ", "No result found.")):
        return Table(error=text[:300])  # fournisseur en erreur
    try:
        rows = parse_rows(output, projection, limit)
    except ValueError as error:
        return Table(error=str(error))
    if count is not None and len(rows) != count:
        return Table(error=FORGED)
    return Table(rows)


def rcs_group_owner(address: str) -> str | None:
    """Adresse de groupe RCS de Google Messages : base32 de « \x1f<mon numéro>\x1f<identifiant du groupe> »
    suivi de @rcs.google.com. Renvoie mon numéro, ou None si ce n'est pas une adresse de groupe."""
    local, _, domain = (address or "").partition("@")
    if not domain.endswith("rcs.google.com"):
        return None
    try:
        fields = base64.b32decode(local.upper()).decode("utf-8").split("")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return fields[1] if len(fields) >= 3 and fields[1] else None


def sim_country(adb: Adb) -> str:
    """Pays de la carte SIM (code ISO en minuscules), sinon du réseau ; « » s'il est inconnu. Double SIM : « ,fr »."""
    out = adb.shell("sh", "-c", "getprop gsm.sim.operator.iso-country; getprop gsm.operator.iso-country", check=False)
    for line in out.splitlines():
        if code := next((c for c in re.split(r"[,\s]+", line.strip().lower()) if c in COUNTRIES), ""):
            return code
    return ""


def normalize(number: str, country: str = "") -> str:
    """Numéro comparable : chiffres seuls, l'indicatif du pays de la SIM ramené au format national (« +33 6… » →
    « 06… » en France) ; pays inconnu : chiffres et « + » seulement."""
    digits = re.sub(r"[^\d+]", "", number or "")
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if country in COUNTRIES:
        code, prefix = COUNTRIES[country]
        if digits.startswith(f"+{code}"):
            digits = prefix + digits[len(str(code)) + 1:]
    return digits


def international(number: str, country: str) -> str:
    """Numéro au format international sans « + » (wa.me) : « +44 7… » tel quel, « 06 12… » avec l'indicatif du pays de
    la SIM. Pays inconnu et numéro national : PhoneError (jamais un indicatif deviné)."""
    digits = re.sub(r"[^\d+]", "", number or "")
    if digits.startswith("+"):
        return digits[1:]
    if digits.startswith("00"):
        return digits[2:]
    if country not in COUNTRIES:
        raise PhoneError(f"{number}: number without country code and SIM card country unknown; give the number in "
                         "international format (+33 6…, +44 7…)")
    code, prefix = COUNTRIES[country]
    return f"{code}{digits[len(prefix):] if prefix and digits.startswith(prefix) else digits}"


@dataclass
class Message:
    when: datetime
    outgoing: bool
    text: str
    kind: str  # "SMS" ou "RCS/MMS"
    read: bool = True
    attachments: list[tuple[str, str]] = field(default_factory=list)  # (identifiant de part MMS, type MIME)
    labels: list[str] = field(default_factory=list)  # repères affichés, ex. « [image 2] »
    sender: str = ""  # expéditeur d'un message reçu dans un groupe

    def line(self) -> str:
        who = "me" if self.outgoing else self.sender or "them"
        flag = "" if self.read or self.outgoing else " (unread)"
        text = " ".join([mask_codes(self.text)] + self.labels).strip()
        return f"{self.when:%d/%m %H:%M} {who}{flag} : {text}"


# Colonne archived du fournisseur : l'application SMS par défaut (Google Messages) la met à 1 pour une conversation
# retirée de sa liste principale, archivée ou dans sa corbeille (les deux ne se distinguent pas hors de l'application).
HIDDEN = " · hidden in the app (archived or in the trash)"


# Noms des contacts, communs à toutes les sessions du démon : (instant de la lecture, nom). Relus après NAMES_TTL
# secondes, pour qu'un contact ajouté ou renommé apparaisse sans redémarrer le serveur.
_NAMES: dict[str, tuple[float, str]] = {}
_COUNTRIES: dict[str, str] = {}  # numéro adb → pays de la SIM, commun aux sessions du démon
NAMES_TTL = 600.0
_PARALLEL = 32  # requêtes lancées ensemble sur le téléphone (une machine Java chacune ; 30 en 1,5 s sur le Fold)
_IDS = "sed -n 's/^Row: [0-9]* _id=//p' | paste -sd, -"  # « Row: 0 _id=12 » … → « 12,11 »


def query_args(uri: str, projection: list[str], where: str | None = None, sort: str | None = None) -> list[str]:
    """Arguments de la commande `content` pour une lecture."""
    argv = ["query", "--uri", uri, "--projection", ":".join(projection)]
    if where:
        argv += ["--where", where]
    if sort:
        argv += ["--sort", sort]
    return argv


def shell_join(argv: list[str]) -> str:
    return " ".join(shlex.quote(a) for a in ["content", *argv])


def query_command(uri: str, projection: list[str], where: str | None = None, sort: str | None = None) -> str:
    return shell_join(query_args(uri, projection, where, sort))


def chained(ids_command: str, uri: str, projection: list[str], column: str) -> str:
    """Deux requêtes enchaînées sur le téléphone (le fournisseur refuse les sous-requêtes SELECT) : les _id rendus
    par la première deviennent le filtre « column IN (…) » de la seconde, sans aller-retour avec le PC. Sortie vide
    si la première ne rend rien."""
    second = query_command(uri, projection)
    return f'ids=$({ids_command} | {_IDS}); [ -z "$ids" ] || {second} --where "{column} IN ($ids)"'


@dataclass(frozen=True)
class Chain:
    """Deux lectures enchaînées : les _id rendus par `ids` deviennent le filtre « column IN (…) » de la seconde."""
    ids: list[str]
    uri: str
    projection: list[str]
    column: str

    def shell(self) -> str:
        return chained(shell_join(self.ids), self.uri, self.projection, self.column)

    def second(self, ids: Table) -> list[str] | None:
        """Seconde lecture d'après le résultat de la première ; None si celle-ci n'a rien rendu."""
        found = ",".join(r["_id"] for r in ids.rows if r.get("_id", "").isdigit()) if not ids.error else ""
        return query_args(self.uri, self.projection, f"{self.column} IN ({found})") if found else None


type Query = list[str] | Chain  # arguments de `content`, ou deux lectures enchaînées


def _mms_before(before_ms: int | None) -> str:
    """Filtre « avant » des MMS/RCS, datés en secondes : date*1000 < before_ms, soit date < ⌈before_ms / 1000⌉."""
    return "" if before_ms is None else f" AND date<{-(-int(before_ms) // 1000)}"


MAX_TEXT = 60_000  # caractères au plus d'une réponse texte (messages, notifications, fichiers)


def fit(lines: list[str], budget: int = MAX_TEXT) -> int:
    """Nombre de lignes, prises dans l'ordre, qui tiennent dans `budget` caractères (une au moins, coupée par
    l'appelant si elle déborde seule)."""
    used = 0
    for i, line in enumerate(lines):
        used += len(line) + 1
        if used > budget:
            return max(1, i)
    return len(lines)


def _projection(argv: list[str]) -> list[str]:
    return argv[argv.index("--projection") + 1].split(":") if "--projection" in argv else []


def _limit(argv: list[str]) -> int | None:
    sort = argv[argv.index("--sort") + 1] if "--sort" in argv else ""
    found = re.search(r"\bLIMIT (\d+)\s*$", sort, re.I)
    return int(found.group(1)) if found else None


def _count_command(query: Query) -> str | None:
    """Même lecture réduite à _id (aucun texte libre), comptée sur le téléphone : contrôle du découpage du texte."""
    if isinstance(query, Chain):
        if query.projection == ["_id"]:
            return None
        command = Chain(query.ids, query.uri, ["_id"], query.column).shell()
    else:
        if _projection(query) == ["_id"]:
            return None
        argv = list(query)
        if "--projection" in argv:
            argv[argv.index("--projection") + 1] = "_id"
        else:
            argv += ["--projection", "_id"]
        command = shell_join(argv)
    return f"( {command} ) | grep -c '^Row: '"


def split_batch(output: str, token: str, count: int) -> list[str]:
    """Sortie d'un lot : chaque commande précédée de « \\n@@<jeton> <i>\\n » (jeton aléatoire : un SMS ne peut pas
    imiter le séparateur)."""
    chunks = [""] * count
    parts = re.split(rf"\n@@{token} (\d+)\n", "\n" + output)
    for index, text in zip(parts[1::2], parts[2::2], strict=True):
        if int(index) < count:
            chunks[int(index)] = text
    return chunks


def rows_of(table: Table) -> list[dict[str, str]]:
    if table.error:
        raise RuntimeError(table.error.strip()[:300])
    return table.rows


def split_parts(output: bytes, token: str) -> dict[str, bytes]:
    """Sortie binaire de parts() : « @@<jeton> <id> <taille>\\n » suivi des octets, pour chaque pièce."""
    found: dict[str, bytes] = {}
    head = re.compile(rb"@@" + token.encode() + rb" (\d+) (\d+)\n")
    pos = 0
    while match := head.match(output, pos):
        size = int(match.group(2))
        data = output[match.end():match.end() + size]
        if size and len(data) == size:
            found[match.group(1).decode()] = data
        pos = match.end() + size
    return found


class Messages:
    """Lectures par vagues de requêtes parallèles, servies par le serveur gardé en vie sur le téléphone (quelques
    dizaines de ms). À défaut, chaque `content query` démarre une machine Java (environ 1 s) ; lancées ensemble dans un
    seul adb shell, dix coûtent alors autant qu'une."""

    def __init__(self, adb: Adb, fast: bool = True) -> None:
        self.adb = adb
        self.fast = server_for(adb) if fast else None
        self._archived: dict[int, bool] = {}  # conversation lue par thread() → masquée dans l'application
        self._country: str | None = None

    @property
    def country(self) -> str:
        """Pays de la carte SIM, lu une fois par téléphone (commun aux sessions du démon) : numéros comparés au format
        national."""
        if self._country is None:
            key = getattr(self.adb, "serial", None) or ""
            if key not in _COUNTRIES:
                _COUNTRIES[key] = sim_country(self.adb)
            self._country = _COUNTRIES[key]
        return self._country

    def _batch(self, queries: list[Query]) -> list[Table]:
        """Lectures exécutées en parallèle sur le téléphone ; résultats dans l'ordre."""
        if not queries:
            return []
        if self.fast and (tables := self._served(queries)) is not None:
            return tables
        return self._shell_tables(queries)

    def _served(self, queries: list[Query]) -> list[Table] | None:
        """Par le serveur du téléphone, en deux échanges au plus (second : lectures enchaînées) ; None s'il manque."""
        assert self.fast
        tables = self.fast.run([q.ids if isinstance(q, Chain) else q for q in queries])
        if tables is None:
            return None
        second: dict[int, list[str]] = {}
        for i, q in enumerate(queries):
            if isinstance(q, Chain):
                follow = q.second(tables[i])
                tables[i] = Table()
                if follow:
                    second[i] = follow
        if second:
            more = self.fast.run(list(second.values()))
            if more is None:
                return None
            for i, table in zip(second, more, strict=True):
                tables[i] = table
        return tables

    def _shell_tables(self, queries: list[Query]) -> list[Table]:
        """Par adb shell (serveur du téléphone indisponible) : chaque lecture à texte libre est doublée de la même
        lecture réduite à _id, comptée ; un nombre de lignes différent révèle un texte qui imite une ligne."""
        counts = [_count_command(q) for q in queries]
        extra = [c for c in counts if c]
        outputs = self._shell_batch([q.shell() if isinstance(q, Chain) else shell_join(q) for q in queries] + extra)
        tallies = iter(outputs[len(queries):])
        tables = []
        for q, out, count in zip(queries, outputs[:len(queries)], counts, strict=True):
            expected = None
            if count:
                tally = next(tallies).strip()
                expected = int(tally) if tally.isdigit() else -1
            projection = q.projection if isinstance(q, Chain) else _projection(q)
            tables.append(shell_table(out, projection, None if isinstance(q, Chain) else _limit(q), expected))
        return tables

    def _shell_batch(self, commands: list[str]) -> list[str]:
        """Commandes shell exécutées en parallèle sur le téléphone, en un seul adb shell ; sorties dans l'ordre."""
        token = secrets.token_hex(8)
        folder = f"/data/local/tmp/nexus-q-{token}"
        lines = [f"mkdir -p {folder}"]
        for i, command in enumerate(commands):
            lines.append(f"( {command} ) >{folder}/{i} 2>&1 &")
            if (i + 1) % _PARALLEL == 0:
                lines.append("wait")
        lines += ["wait", f"for i in {' '.join(map(str, range(len(commands))))}; do "
                          f"printf '\\n@@{token} %s\\n' $i; cat {folder}/$i; done", f"rm -rf {folder}"]
        out = self.adb.shell("sh", "-c", "\n".join(lines), check=False, timeout=60)
        return split_batch(out, token, len(commands))

    def _query(self, uri: str, projection: list[str], where: str | None = None,
               sort: str | None = None) -> list[dict[str, str]]:
        return rows_of(self._batch([query_args(uri, projection, where, sort)])[0])

    def resolve(self, addresses: list[str], extra: list[Query] | None = None) -> list[Table]:
        """Noms du répertoire de ces adresses : celles absentes du cache sont lues ensemble, en une vague, avec les
        lectures `extra` dont les résultats sont rendus."""
        now = time.monotonic()
        todo = list(dict.fromkeys(a for a in addresses if a and (a not in _NAMES or now - _NAMES[a][0] >= NAMES_TTL)))
        queries = [query_args(f"content://com.android.contacts/phone_lookup/{quote(a, safe='')}", ["display_name"])
                    for a in todo]
        outputs = self._batch((extra or []) + queries)
        for address, out in zip(todo, outputs[len(extra or []):], strict=True):
            rows = rows_of(out)
            # Le fournisseur ajoute ses colonnes après celles demandées : ne garder que le nom.
            name = rows[0].get("display_name", "") if rows else ""
            _NAMES[address] = (now, re.sub(r", \w+=.*", "", name, flags=re.S))
        return outputs[:len(extra or [])]

    def name(self, address: str) -> str:
        if not address:
            return "?"
        self.resolve([address])
        name = _NAMES[address][1]
        return f"{name} ({address})" if name else address

    @staticmethod
    def _canonical(table: Table) -> dict[str, str]:
        return {r.get("_id", ""): r.get("address", "") for r in rows_of(table)}

    @staticmethod
    def _addresses(recipient_ids: str, canonical: dict[str, str]) -> list[str]:
        return [canonical.get(i, "") for i in recipient_ids.split() if i.isdigit()]

    def _members(self, addresses: list[str], addr_rows: list[dict[str, str]], top: set[str] | None = None) -> list[str] | None:
        """Membres d'un groupe RCS, qui n'a qu'une adresse codée : adresses des derniers messages du fil (ceux de
        `top` si donné), moi exclu. None si ce n'est pas un groupe."""
        owners = {normalize(o, self.country) for o in map(rcs_group_owner, addresses) if o}
        if not owners:
            return None
        members: dict[str, str] = {}
        for r in addr_rows:
            address = r.get("address", "")
            if top is not None and r.get("msg_id") not in top:
                continue
            if address and normalize(address, self.country) not in owners and "insert-address-token" not in address:
                members.setdefault(normalize(address, self.country), address)
        return list(members.values())

    @staticmethod
    def _recent_addr(thread_id: int, count: int = 30, before_ms: int | None = None) -> Chain:
        """Adresses (expéditeur et destinataires) des `count` derniers MMS/RCS du fil (antérieurs à before_ms)."""
        return Chain(query_args("content://mms", ["_id"], f"thread_id={int(thread_id)}{_mms_before(before_ms)}",
                                f"date DESC LIMIT {count}"),
                     "content://mms/addr", ["msg_id", "type", "address"], "msg_id")

    def conversations(self, limit: int = 15, before_ms: int | None = None) -> list[str]:
        return [line for line, _ in self.conversation_page(limit, before_ms)]

    def conversation_page(self, limit: int = 15, before_ms: int | None = None) -> list[tuple[str, int]]:
        """Conversations (plus récentes d'abord, antérieures à before_ms) : (ligne affichée, date en ms)."""
        projection = ["_id", "date", "message_count", "read", "archived", "recipient_ids", "snippet"]
        where = "message_count>0" + (f" AND date<{int(before_ms)}" if before_ms is not None else "")
        threads_out, canonical_out = self._batch([
            query_args("content://mms-sms/conversations?simple=true", projection, where,
                          f"date DESC LIMIT {int(limit)}"),
            query_args("content://mms-sms/canonical-addresses", ["_id", "address"])])
        threads = [t for t in rows_of(threads_out)
                   if t.get("message_count", "0") not in ("0", "")][:limit]  # brouillons vides
        canonical = self._canonical(canonical_out)
        addresses = {t["_id"]: self._addresses(t.get("recipient_ids", ""), canonical) for t in threads}
        groups = [i for i, a in addresses.items() if any(map(rcs_group_owner, a))]
        # Vague 2 : noms des participants et adresses récentes des groupes ; vague 3 : noms des membres des groupes,
        # seulement s'ils manquent au cache.
        group_out = self.resolve([a for i, a_list in addresses.items() if i not in groups for a in a_list],
                                 [self._recent_addr(int(g)) for g in groups])
        members = {g: self._members(addresses[g], rows_of(out)) or []
                   for g, out in zip(groups, group_out, strict=True)}
        self.resolve([a for m in members.values() for a in m])
        lines = []
        for t in threads:
            i = t["_id"]
            names = ["RCS group"] + [self.name(a) for a in members[i]] if i in members else [
                self.name(a) for a in addresses[i]]
            people = ", ".join(names) or "?"
            when = datetime.fromtimestamp(int(t.get("date", "0") or 0) / 1000)
            unread = "" if t.get("read") == "1" else " · UNREAD"
            unread += HIDDEN if t.get("archived") == "1" else ""
            snippet = mask_codes((t.get("snippet") or "").replace("\n", " "))[:100]  # masqué avant la coupe
            lines.append((f"[{t.get('_id')}] {people} · {when:%d/%m %H:%M} · {t.get('message_count')} msg{unread}"
                          + (f"\n     \"{snippet}\"" if snippet and snippet != "NULL" else ""),
                          int(t.get("date", "0") or 0) if str(t.get("date", "")).isdigit() else 0))
        return lines

    def thread(self, thread_id: int, limit: int = 20, before_ms: int | None = None) -> tuple[list[str], list[Message]]:
        """Participants et `limit` derniers messages du fil, antérieurs à before_ms (date en ms) si donné."""
        tid = int(thread_id)
        last = f"date DESC LIMIT {int(limit)}"
        sms_where = f"thread_id={tid}" + (f" AND date<{int(before_ms)}" if before_ms is not None else "")
        mms_where = f"thread_id={tid}{_mms_before(before_ms)}"
        sms_p, mms_p = ["_id", "date", "type", "read", "body"], ["_id", "date", "msg_box", "read", "m_type"]
        part_p, thread_p = ["_id", "mid", "ct", "text"], ["_id", "archived", "recipient_ids"]
        sms_out, mms_out, part_out, addr_out, thread_out, canonical_out = self._batch([
            query_args("content://sms", sms_p, sms_where, last),
            query_args("content://mms", mms_p, mms_where, last),
            Chain(query_args("content://mms", ["_id"], mms_where, last), "content://mms/part", part_p, "mid"),
            self._recent_addr(tid, max(int(limit), 30), before_ms),
            query_args("content://mms-sms/conversations?simple=true", thread_p, f"_id={tid}"),
            query_args("content://mms-sms/canonical-addresses", ["_id", "address"])])
        sms = rows_of(sms_out)[:limit]
        messages = [Message(datetime.fromtimestamp(int(r["date"]) / 1000), r.get("type") == "2", r.get("body", ""),
                            "SMS", r.get("read") != "0") for r in sms if r.get("date", "").isdigit()]
        mms_all = rows_of(mms_out)
        mms = mms_all[:limit]
        addr_rows = rows_of(addr_out)
        if mms:
            texts: dict[str, list[str]] = {}
            files: dict[str, list[tuple[str, str]]] = {}
            for p in rows_of(part_out):
                if p.get("ct") == "text/plain":
                    texts.setdefault(p.get("mid", ""), []).append(p.get("text", ""))
                elif p.get("ct", "").startswith(("image/", "video/", "audio/", "application/", "text/x-vcard")) and p.get(
                        "ct") != "application/smil":  # SMIL : mise en page MMS, pas une pièce jointe
                    files.setdefault(p.get("mid", ""), []).append((p.get("_id", ""), p["ct"]))
            senders = {r.get("msg_id", ""): r.get("address", "") for r in addr_rows if r.get("type") == "137"}
            for r in mms:
                if r.get("date", "").isdigit():
                    attachments = files.get(r["_id"], [])
                    messages.append(Message(datetime.fromtimestamp(int(r["date"])), r.get("msg_box") == "2",
                                            " ".join(texts.get(r["_id"], [])) or ("" if attachments else "[no text]"),
                                            "RCS/MMS", r.get("read") != "0", attachments))
                    if r.get("msg_box") != "2" and senders.get(r["_id"]):
                        messages[-1].sender = senders[r["_id"]]
        messages.sort(key=lambda m: m.when)
        messages = messages[-limit:]
        thread = rows_of(thread_out)
        self._archived[tid] = bool(thread) and thread[0].get("archived") == "1"
        addresses = self._addresses(thread[0].get("recipient_ids", ""), self._canonical(canonical_out)) if thread else []
        # Membres d'un groupe : adresses des 30 derniers MMS/RCS (toutes lues quand limit < 30).
        top = {r.get("_id", "") for r in mms_all[:30]} if limit >= 30 else None
        members = self._members(addresses, addr_rows, top)
        senders_shown = [m.sender for m in messages if m.sender] if members is not None else []
        self.resolve((addresses if members is None else members) + senders_shown)
        people = [self.name(a) for a in addresses] if members is None else ["RCS group"] + [
            self.name(a) for a in members]
        for m in messages:  # expéditeur nommé seulement dans un groupe : à deux, « lui/elle » suffit
            m.sender = self.name(m.sender) if members is not None and m.sender else ""
        return people, messages

    def hidden(self, thread_id: int) -> bool:
        if int(thread_id) in self._archived:  # relu à l'instant par thread()
            return self._archived.pop(int(thread_id))
        rows = self._query("content://mms-sms/conversations?simple=true", ["_id", "archived"],
                           where=f"_id={int(thread_id)}")
        return bool(rows) and rows[0].get("archived") == "1"

    def part(self, part_id: str) -> bytes:
        """Contenu brut d'une pièce jointe MMS/RCS (image, vidéo…)."""
        return self.adb.exec_out("content", "read", "--uri", f"content://mms/part/{int(part_id)}", timeout=60)

    def parts(self, part_ids: list[str]) -> dict[str, bytes]:
        """Plusieurs pièces jointes lues en parallèle, en un seul échange ; une pièce illisible est absente."""
        ids = list(dict.fromkeys(str(int(i)) for i in part_ids))
        if not ids:
            return {}
        token = secrets.token_hex(8)
        folder = f"/data/local/tmp/nexus-p-{token}"
        lines = [f"mkdir -p {folder}"]
        lines += [f"content read --uri content://mms/part/{i} >{folder}/{i} 2>/dev/null &" for i in ids]
        lines += ["wait", f"for i in {' '.join(ids)}; do printf '@@{token} %s %s\\n' $i $(wc -c <{folder}/$i); "
                          f"cat {folder}/$i; done", f"rm -rf {folder}"]
        return split_parts(self.adb.exec_out("sh", "-c", "\n".join(lines), timeout=120), token)

    @staticmethod
    def _find_command(name: str) -> list[str]:
        return query_args(f"content://com.android.contacts/data/phones/filter/{quote(name, safe='')}",
                             ["display_name", "data1"])

    def _found(self, table: Table) -> list[tuple[str, str]]:
        seen, out = set(), []
        for r in rows_of(table):
            number = r.get("data1", "")
            if number and normalize(number, self.country) not in seen:
                seen.add(normalize(number, self.country))
                out.append((r.get("display_name", ""), number))
        return out

    def find(self, name: str) -> list[tuple[str, str]]:
        """Contacts dont le nom contient `name` : (nom, numéro), numéros en double retirés."""
        return self._found(self._batch([self._find_command(name)])[0])

    def search(self, name: str) -> tuple[list[tuple[str, str]], list[int]]:
        """Contacts dont le nom contient `name`, et conversations à deux (pas de groupe) avec l'un de leurs numéros,
        plus récentes d'abord : contacts et conversations lus dans la même vague."""
        threads_p = ["_id", "message_count", "recipient_ids"]
        found_out, canonical_out, threads_out = self._batch([
            self._find_command(name),
            query_args("content://mms-sms/canonical-addresses", ["_id", "address"]),
            query_args("content://mms-sms/conversations?simple=true", threads_p, sort="date DESC")])
        found = self._found(found_out)
        wanted = {normalize(n, self.country) for _, n in found}
        ids = {i for i, a in self._canonical(canonical_out).items() if normalize(a, self.country) in wanted}
        return found, [int(t["_id"]) for t in rows_of(threads_out)
                       if t.get("recipient_ids", "").strip() in ids and t.get("message_count", "0") != "0"]
