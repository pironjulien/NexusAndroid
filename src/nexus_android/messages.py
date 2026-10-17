"""SMS et RCS du téléphone (fournisseur de messagerie Android), codes de sécurité masqués, contacts résolus."""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote

from .adb import Adb

# Mots qui annoncent un code, cherchés sur le texte sans accents (« vérification » → « verification »).
_KEYWORDS = re.compile(
    r"\b(?:code|otp\b|pin\b|passcode|mot de passe|password|2fa\b|login|connexion|securit|verif|valid|confirm|"
    r"authentif|authentic|identifiant|usage unique|one[- ]?time)",
    re.I,
)
# Codes chiffrés : 4 chiffres ou plus (espaces ou tirets permis), sans prendre les montants (1 250,00) ni les
# coordonnées (@1234,2000) ; « 482913, valable » reste un code.
_CODE = re.compile(r"(?<![\d,.])(?:[A-Z]-)?\d(?:[ -]?\d){3,}(?!\d|[.,]\d)")
# Codes alphanumériques de 4 à 10 caractères mêlant lettres et chiffres (« A7K9P2 »), près d'un mot-clé.
_ALNUM = re.compile(r"(?<![\w-])(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{4,10}(?![\w-])")
_NEAR = 60  # caractères entre le mot-clé et un code alphanumérique
MASK = "[code masqué]"


def plain(text: str) -> str:
    """Texte sans accents et apostrophe droite, même longueur (positions conservées)."""
    out = []
    for c in text or "":
        base = "".join(ch for ch in unicodedata.normalize("NFKD", c) if not unicodedata.combining(ch))
        out.append("'" if c in "’‘ʼ" else base if len(base) == 1 else c)
    return "".join(out)


def has_keyword(*texts: str) -> bool:
    return any(t and _KEYWORDS.search(plain(t)) for t in texts)


def mask_codes(text: str, context: bool = False) -> str:
    """Masque les codes de validation des textes qui en parlent ; l'agent ne les voit jamais. context=True : le
    mot-clé est ailleurs (titre de la notification, nom du champ), tout code du texte est masqué."""
    if not text:
        return text
    keywords = [m.span() for m in _KEYWORDS.finditer(plain(text))]
    if not keywords and not context:
        return text

    def alnum(m: re.Match[str]) -> str:
        near = context or any(m.start() - _NEAR <= end and start <= m.end() + _NEAR for start, end in keywords)
        return MASK if near else m.group(0)

    return _CODE.sub(MASK, _ALNUM.sub(alnum, text))  # alphanumériques d'abord : positions des mots-clés intactes


def parse_rows(output: str, last: str) -> list[dict[str, str]]:
    """Sortie de `content query`. La colonne `last` (texte libre) doit être la dernière de la projection : elle peut
    contenir virgules et retours à la ligne, tout ce qui la suit jusqu'à la ligne « Row: » suivante lui appartient."""
    rows: list[dict[str, str]] = []
    chunks = re.split(r"(?m)^Row: \d+ ", output)
    for chunk in chunks[1:]:
        head, sep, tail = chunk.partition(f"{last}=")
        row: dict[str, str] = {}
        for match in re.finditer(r"(\w+)=([^,]*)(?:, |$)", head):
            row[match.group(1)] = match.group(2).strip()
        if sep:
            row[last] = tail.rstrip("\r\n")
        rows.append(row)
    return rows


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


def normalize(number: str) -> str:
    digits = re.sub(r"[^\d+]", "", number or "")
    if digits.startswith("+33"):
        digits = "0" + digits[3:]
    return digits


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
        who = "moi" if self.outgoing else self.sender or "lui/elle"
        flag = "" if self.read or self.outgoing else " (non lu)"
        text = " ".join([mask_codes(self.text)] + self.labels).strip()
        return f"{self.when:%d/%m %H:%M} {who}{flag} : {text}"


# Colonne archived du fournisseur : l'application SMS par défaut (Google Messages) la met à 1 pour une conversation
# retirée de sa liste principale, archivée ou dans sa corbeille (les deux ne se distinguent pas hors de l'application).
HIDDEN = " · masquée dans l'application (archivée ou corbeille)"


class Messages:
    def __init__(self, adb: Adb) -> None:
        self.adb = adb
        self._names: dict[str, str] = {}

    def _query(self, uri: str, projection: list[str], where: str | None = None, sort: str | None = None) -> list[dict]:
        argv = ["content", "query", "--uri", uri, "--projection", ":".join(projection)]
        if where:
            argv += ["--where", where]
        if sort:
            argv += ["--sort", sort]
        out = self.adb.shell(*argv, check=False)
        if "Error" in out[:200] or "Exception" in out[:200]:
            raise RuntimeError(out.strip()[:300])
        return parse_rows(out, projection[-1])

    def name(self, address: str) -> str:
        if not address:
            return "?"
        if address not in self._names:
            rows = self._query(f"content://com.android.contacts/phone_lookup/{quote(address, safe='')}",
                               ["display_name"])
            # Le fournisseur ajoute ses colonnes après celles demandées : ne garder que le nom.
            name = rows[0].get("display_name", "") if rows else ""
            self._names[address] = re.sub(r", \w+=.*", "", name, flags=re.S)
        name = self._names[address]
        return f"{name} ({address})" if name else address

    def _thread_addresses(self, recipient_ids: str) -> list[str]:
        ids = [i for i in recipient_ids.split() if i.isdigit()]
        if not ids:
            return []
        rows = self._query("content://mms-sms/canonical-addresses", ["_id", "address"],
                           where=f"_id IN ({','.join(ids)})")
        return [r.get("address", "") for r in rows]

    def _people(self, thread_id: int, recipient_ids: str) -> list[str]:
        """Participants nommés. Un groupe RCS n'a qu'une adresse codée : ses membres viennent des adresses des
        derniers messages du fil, moi exclu."""
        addresses = self._thread_addresses(recipient_ids)
        owners = {normalize(o) for o in map(rcs_group_owner, addresses) if o}
        if not owners:
            return [self.name(a) for a in addresses]
        ids = [r["_id"] for r in self._query("content://mms", ["_id"], where=f"thread_id={int(thread_id)}",
                                             sort="date DESC")[:30] if r.get("_id", "").isdigit()]
        members: dict[str, str] = {}
        if ids:
            for r in self._query("content://mms/addr", ["msg_id", "address"], where=f"msg_id IN ({','.join(ids)})"):
                address = r.get("address", "")
                if address and normalize(address) not in owners and "insert-address-token" not in address:
                    members.setdefault(normalize(address), address)
        return ["groupe RCS"] + [self.name(a) for a in members.values()]

    def conversations(self, limit: int = 15) -> list[str]:
        threads = self._query("content://mms-sms/conversations?simple=true",
                              ["_id", "date", "message_count", "read", "archived", "recipient_ids", "snippet"],
                              sort="date DESC")
        threads = [t for t in threads if t.get("message_count", "0") not in ("0", "")][:limit]  # brouillons vides
        lines = []
        for t in threads:
            people = ", ".join(self._people(int(t["_id"]), t.get("recipient_ids", ""))) or "?"
            when = datetime.fromtimestamp(int(t.get("date", "0") or 0) / 1000)
            unread = "" if t.get("read") == "1" else " · NON LU"
            unread += HIDDEN if t.get("archived") == "1" else ""
            snippet = mask_codes((t.get("snippet") or "").replace("\n", " "))[:100]  # masqué avant la coupe
            lines.append(f"[{t.get('_id')}] {people} · {when:%d/%m %H:%M} · {t.get('message_count')} msg{unread}"
                         + (f"\n     « {snippet} »" if snippet and snippet != "NULL" else ""))
        return lines

    def thread(self, thread_id: int, limit: int = 20) -> tuple[list[str], list[Message]]:
        sms = self._query("content://sms", ["_id", "date", "type", "read", "body"],
                          where=f"thread_id={int(thread_id)}", sort="date DESC")[:limit]
        messages = [Message(datetime.fromtimestamp(int(r["date"]) / 1000), r.get("type") == "2", r.get("body", ""),
                            "SMS", r.get("read") != "0") for r in sms if r.get("date", "").isdigit()]
        mms = self._query("content://mms", ["_id", "date", "msg_box", "read", "m_type"],
                          where=f"thread_id={int(thread_id)}", sort="date DESC")[:limit]
        if mms:
            ids = ",".join(r["_id"] for r in mms if r.get("_id", "").isdigit())
            parts = self._query("content://mms/part", ["_id", "mid", "ct", "text"], where=f"mid IN ({ids})")
            texts: dict[str, list[str]] = {}
            files: dict[str, list[tuple[str, str]]] = {}
            for p in parts:
                if p.get("ct") == "text/plain":
                    texts.setdefault(p.get("mid", ""), []).append(p.get("text", ""))
                elif p.get("ct", "").startswith(("image/", "video/", "audio/", "application/", "text/x-vcard")) and p.get(
                        "ct") != "application/smil":  # SMIL : mise en page MMS, pas une pièce jointe
                    files.setdefault(p.get("mid", ""), []).append((p.get("_id", ""), p["ct"]))
            senders = {r.get("msg_id", ""): r.get("address", "") for r in self._query(
                "content://mms/addr", ["msg_id", "type", "address"], where=f"msg_id IN ({ids}) AND type=137")}
            for r in mms:
                if r.get("date", "").isdigit():
                    attachments = files.get(r["_id"], [])
                    messages.append(Message(datetime.fromtimestamp(int(r["date"])), r.get("msg_box") == "2",
                                            " ".join(texts.get(r["_id"], [])) or ("" if attachments else "[sans texte]"),
                                            "RCS/MMS", r.get("read") != "0", attachments))
                    if r.get("msg_box") != "2" and senders.get(r["_id"]):
                        messages[-1].sender = senders[r["_id"]]
        messages.sort(key=lambda m: m.when)
        messages = messages[-limit:]
        thread = self._query("content://mms-sms/conversations?simple=true", ["_id", "recipient_ids"],
                             where=f"_id={int(thread_id)}")
        people = self._people(thread_id, thread[0].get("recipient_ids", "")) if thread else []
        group = bool(people) and people[0] == "groupe RCS"
        for m in messages:  # expéditeur nommé seulement dans un groupe : à deux, « lui/elle » suffit
            m.sender = self.name(m.sender) if group and m.sender else ""
        return people, messages

    def hidden(self, thread_id: int) -> bool:
        rows = self._query("content://mms-sms/conversations?simple=true", ["_id", "archived"],
                           where=f"_id={int(thread_id)}")
        return bool(rows) and rows[0].get("archived") == "1"

    def part(self, part_id: str) -> bytes:
        """Contenu brut d'une pièce jointe MMS/RCS (image, vidéo…)."""
        return self.adb.exec_out("content", "read", "--uri", f"content://mms/part/{int(part_id)}", timeout=60)

    def find(self, name: str) -> list[tuple[str, str]]:
        """Contacts dont le nom contient `name` : (nom, numéro), numéros en double retirés."""
        rows = self._query(f"content://com.android.contacts/data/phones/filter/{quote(name, safe='')}",
                           ["display_name", "data1"])
        seen, out = set(), []
        for r in rows:
            number = r.get("data1", "")
            if number and normalize(number) not in seen:
                seen.add(normalize(number))
                out.append((r.get("display_name", ""), number))
        return out

    def threads_with(self, numbers: list[str]) -> list[int]:
        """Conversations à deux (pas de groupe) avec l'un de ces numéros, plus récentes d'abord."""
        wanted = {normalize(n) for n in numbers}
        ids = {r["_id"] for r in self._query("content://mms-sms/canonical-addresses", ["_id", "address"])
               if normalize(r.get("address", "")) in wanted}
        threads = self._query("content://mms-sms/conversations?simple=true",
                              ["_id", "message_count", "recipient_ids"], sort="date DESC")
        return [int(t["_id"]) for t in threads
                if t.get("recipient_ids", "").strip() in ids and t.get("message_count", "0") != "0"]
