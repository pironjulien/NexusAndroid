"""SMS et RCS du téléphone (fournisseur de messagerie Android), codes de sécurité masqués, contacts résolus."""

from __future__ import annotations

import base64
import binascii
import re
import secrets
import shlex
import time
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


# Noms des contacts, communs à toutes les sessions du démon : (instant de la lecture, nom). Relus après NAMES_TTL
# secondes, pour qu'un contact ajouté ou renommé apparaisse sans redémarrer le serveur.
_NAMES: dict[str, tuple[float, str]] = {}
NAMES_TTL = 600.0
_PARALLEL = 32  # requêtes lancées ensemble sur le téléphone (une machine Java chacune ; 30 en 1,5 s sur le Fold)
_IDS = "sed -n 's/^Row: [0-9]* _id=//p' | paste -sd, -"  # « Row: 0 _id=12 » … → « 12,11 »


def query_command(uri: str, projection: list[str], where: str | None = None, sort: str | None = None) -> str:
    argv = ["content", "query", "--uri", uri, "--projection", ":".join(projection)]
    if where:
        argv += ["--where", where]
    if sort:
        argv += ["--sort", sort]
    return " ".join(shlex.quote(a) for a in argv)


def chained(ids_command: str, uri: str, projection: list[str], column: str) -> str:
    """Deux requêtes enchaînées sur le téléphone (le fournisseur refuse les sous-requêtes SELECT) : les _id rendus
    par la première deviennent le filtre « column IN (…) » de la seconde, sans aller-retour avec le PC. Sortie vide
    si la première ne rend rien."""
    second = query_command(uri, projection)
    return f'ids=$({ids_command} | {_IDS}); [ -z "$ids" ] || {second} --where "{column} IN ($ids)"'


def split_batch(output: str, token: str, count: int) -> list[str]:
    """Sortie d'un lot : chaque commande précédée de « \\n@@<jeton> <i>\\n » (jeton aléatoire : un SMS ne peut pas
    imiter le séparateur)."""
    chunks = [""] * count
    parts = re.split(rf"\n@@{token} (\d+)\n", "\n" + output)
    for index, text in zip(parts[1::2], parts[2::2], strict=True):
        if int(index) < count:
            chunks[int(index)] = text
    return chunks


def rows_of(output: str, projection: list[str]) -> list[dict[str, str]]:
    if "Error" in output[:200] or "Exception" in output[:200]:
        raise RuntimeError(output.strip()[:300])
    return parse_rows(output, projection[-1])


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
    """Chaque `content query` démarre une machine Java sur le téléphone (environ 1 s) ; lancées ensemble dans un seul
    adb shell, dix coûtent autant qu'une. Les lectures se font donc par vagues de requêtes parallèles."""

    def __init__(self, adb: Adb) -> None:
        self.adb = adb
        self._archived: dict[int, bool] = {}  # conversation lue par thread() → masquée dans l'application

    def _batch(self, commands: list[str]) -> list[str]:
        """Commandes shell exécutées en parallèle sur le téléphone, en un seul adb shell ; sorties dans l'ordre."""
        if not commands:
            return []
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
        return rows_of(self._batch([query_command(uri, projection, where, sort)])[0], projection)

    def resolve(self, addresses: list[str], extra: list[str] | None = None) -> list[str]:
        """Noms du répertoire de ces adresses : celles absentes du cache sont lues ensemble, en une vague, avec les
        commandes `extra` dont les sorties sont rendues."""
        now = time.monotonic()
        todo = list(dict.fromkeys(a for a in addresses if a and (a not in _NAMES or now - _NAMES[a][0] >= NAMES_TTL)))
        commands = [query_command(f"content://com.android.contacts/phone_lookup/{quote(a, safe='')}", ["display_name"])
                    for a in todo]
        outputs = self._batch((extra or []) + commands)
        for address, out in zip(todo, outputs[len(extra or []):], strict=True):
            rows = rows_of(out, ["display_name"])
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
    def _canonical(output: str) -> dict[str, str]:
        return {r.get("_id", ""): r.get("address", "") for r in rows_of(output, ["_id", "address"])}

    @staticmethod
    def _addresses(recipient_ids: str, canonical: dict[str, str]) -> list[str]:
        return [canonical.get(i, "") for i in recipient_ids.split() if i.isdigit()]

    @staticmethod
    def _members(addresses: list[str], addr_rows: list[dict[str, str]], top: set[str] | None = None) -> list[str] | None:
        """Membres d'un groupe RCS, qui n'a qu'une adresse codée : adresses des derniers messages du fil (ceux de
        `top` si donné), moi exclu. None si ce n'est pas un groupe."""
        owners = {normalize(o) for o in map(rcs_group_owner, addresses) if o}
        if not owners:
            return None
        members: dict[str, str] = {}
        for r in addr_rows:
            address = r.get("address", "")
            if top is not None and r.get("msg_id") not in top:
                continue
            if address and normalize(address) not in owners and "insert-address-token" not in address:
                members.setdefault(normalize(address), address)
        return list(members.values())

    @staticmethod
    def _recent_addr(thread_id: int, count: int = 30) -> str:
        """Adresses (expéditeur et destinataires) des `count` derniers MMS/RCS du fil."""
        return chained(query_command("content://mms", ["_id"], f"thread_id={int(thread_id)}", f"date DESC LIMIT {count}"),
                       "content://mms/addr", ["msg_id", "type", "address"], "msg_id")

    def conversations(self, limit: int = 15) -> list[str]:
        projection = ["_id", "date", "message_count", "read", "archived", "recipient_ids", "snippet"]
        threads_out, canonical_out = self._batch([
            query_command("content://mms-sms/conversations?simple=true", projection, "message_count>0",
                          f"date DESC LIMIT {int(limit)}"),
            query_command("content://mms-sms/canonical-addresses", ["_id", "address"])])
        threads = [t for t in rows_of(threads_out, projection)
                   if t.get("message_count", "0") not in ("0", "")][:limit]  # brouillons vides
        canonical = self._canonical(canonical_out)
        addresses = {t["_id"]: self._addresses(t.get("recipient_ids", ""), canonical) for t in threads}
        groups = [i for i, a in addresses.items() if any(map(rcs_group_owner, a))]
        # Vague 2 : noms des participants et adresses récentes des groupes ; vague 3 : noms des membres des groupes,
        # seulement s'ils manquent au cache.
        group_out = self.resolve([a for i, a_list in addresses.items() if i not in groups for a in a_list],
                                 [self._recent_addr(int(g)) for g in groups])
        members = {g: self._members(addresses[g], rows_of(out, ["msg_id", "type", "address"])) or []
                   for g, out in zip(groups, group_out, strict=True)}
        self.resolve([a for m in members.values() for a in m])
        lines = []
        for t in threads:
            i = t["_id"]
            names = ["groupe RCS"] + [self.name(a) for a in members[i]] if i in members else [
                self.name(a) for a in addresses[i]]
            people = ", ".join(names) or "?"
            when = datetime.fromtimestamp(int(t.get("date", "0") or 0) / 1000)
            unread = "" if t.get("read") == "1" else " · NON LU"
            unread += HIDDEN if t.get("archived") == "1" else ""
            snippet = mask_codes((t.get("snippet") or "").replace("\n", " "))[:100]  # masqué avant la coupe
            lines.append(f"[{t.get('_id')}] {people} · {when:%d/%m %H:%M} · {t.get('message_count')} msg{unread}"
                         + (f"\n     « {snippet} »" if snippet and snippet != "NULL" else ""))
        return lines

    def thread(self, thread_id: int, limit: int = 20) -> tuple[list[str], list[Message]]:
        tid = int(thread_id)
        last = f"date DESC LIMIT {int(limit)}"
        sms_p, mms_p = ["_id", "date", "type", "read", "body"], ["_id", "date", "msg_box", "read", "m_type"]
        part_p, addr_p, thread_p = ["_id", "mid", "ct", "text"], ["msg_id", "type", "address"], [
            "_id", "archived", "recipient_ids"]
        sms_out, mms_out, part_out, addr_out, thread_out, canonical_out = self._batch([
            query_command("content://sms", sms_p, f"thread_id={tid}", last),
            query_command("content://mms", mms_p, f"thread_id={tid}", last),
            chained(query_command("content://mms", ["_id"], f"thread_id={tid}", last), "content://mms/part", part_p,
                    "mid"),
            self._recent_addr(tid, max(int(limit), 30)),
            query_command("content://mms-sms/conversations?simple=true", thread_p, f"_id={tid}"),
            query_command("content://mms-sms/canonical-addresses", ["_id", "address"])])
        sms = rows_of(sms_out, sms_p)[:limit]
        messages = [Message(datetime.fromtimestamp(int(r["date"]) / 1000), r.get("type") == "2", r.get("body", ""),
                            "SMS", r.get("read") != "0") for r in sms if r.get("date", "").isdigit()]
        mms_all = rows_of(mms_out, mms_p)
        mms = mms_all[:limit]
        addr_rows = rows_of(addr_out, addr_p)
        if mms:
            texts: dict[str, list[str]] = {}
            files: dict[str, list[tuple[str, str]]] = {}
            for p in rows_of(part_out, part_p):
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
                                            " ".join(texts.get(r["_id"], [])) or ("" if attachments else "[sans texte]"),
                                            "RCS/MMS", r.get("read") != "0", attachments))
                    if r.get("msg_box") != "2" and senders.get(r["_id"]):
                        messages[-1].sender = senders[r["_id"]]
        messages.sort(key=lambda m: m.when)
        messages = messages[-limit:]
        thread = rows_of(thread_out, thread_p)
        self._archived[tid] = bool(thread) and thread[0].get("archived") == "1"
        addresses = self._addresses(thread[0].get("recipient_ids", ""), self._canonical(canonical_out)) if thread else []
        # Membres d'un groupe : adresses des 30 derniers MMS/RCS (toutes lues quand limit < 30).
        top = {r.get("_id", "") for r in mms_all[:30]} if limit >= 30 else None
        members = self._members(addresses, addr_rows, top)
        senders_shown = [m.sender for m in messages if m.sender] if members is not None else []
        self.resolve((addresses if members is None else members) + senders_shown)
        people = [self.name(a) for a in addresses] if members is None else ["groupe RCS"] + [
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
    def _find_command(name: str) -> str:
        return query_command(f"content://com.android.contacts/data/phones/filter/{quote(name, safe='')}",
                             ["display_name", "data1"])

    @staticmethod
    def _found(output: str) -> list[tuple[str, str]]:
        seen, out = set(), []
        for r in rows_of(output, ["display_name", "data1"]):
            number = r.get("data1", "")
            if number and normalize(number) not in seen:
                seen.add(normalize(number))
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
            query_command("content://mms-sms/canonical-addresses", ["_id", "address"]),
            query_command("content://mms-sms/conversations?simple=true", threads_p, sort="date DESC")])
        found = self._found(found_out)
        wanted = {normalize(n) for _, n in found}
        ids = {i for i, a in self._canonical(canonical_out).items() if normalize(a) in wanted}
        return found, [int(t["_id"]) for t in rows_of(threads_out, threads_p)
                       if t.get("recipient_ids", "").strip() in ids and t.get("message_count", "0") != "0"]
