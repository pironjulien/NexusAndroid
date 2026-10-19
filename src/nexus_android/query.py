"""Serveur de lectures gardé en vie sur le téléphone (phone/NexusQuery.java) : supprime le démarrage d'une machine Java
(environ 1 s) que paie chaque `content query` lancé par adb shell.

Même méthode que le serveur de scrcpy : un petit dex poussé dans /data/local/tmp, lancé par app_process sous
l'identité shell (aucune application à installer), joint par adb forward sur un socket local abstrait. Il interroge les
fournisseurs comme la commande `content`, en lecture seule, et sérialise lui-même les lignes (longueurs préfixées : un
SMS ne peut pas imiter une ligne). Il ne sert que l'utilisateur shell (adbd) et s'arrête seul après IDLE secondes sans
requête. Chaque lancement tire un jeton aléatoire : il entre dans le nom du socket et le serveur le renvoie, tout autre
serveur est refusé. Absent ou en panne : None, et l'appelant repasse par adb shell (méthode d'avant) ; nouvel essai
après RETRY secondes. Sans réponse en READ_WAIT secondes, ou liaison perdue : LinkError tout de suite, jamais le repli
(qui attendrait autant).

Le même serveur lit aussi l'écran (requête `screen`, voir screen.py) : une connexion UiAutomation gardée, unique sur le
téléphone, rendue par `release` avant tout `uiautomator dump`."""

from __future__ import annotations

import hashlib
import re
import secrets
import socket
import struct
import threading
import time
import weakref
from dataclasses import dataclass, field
from pathlib import Path

from .adb import Adb, LinkError, PhoneError

DEX = Path(__file__).with_name("nexus-query.dex")
IDLE = 600  # secondes sans requête avant l'arrêt du serveur sur le téléphone
RETRY = 60.0  # après un échec, secondes avant de retenter le serveur (adb shell entre-temps)
START_WAIT = 5.0  # attente du serveur après son lancement
READ_WAIT = 10.0  # s sans réponse à une lecture : liaison tenue pour coupée (le serveur répond en quelques dizaines de ms)
MAGIC = b"NXQ2"
MAX_COLUMNS = 512
MAX_ROWS = 1_000_000


@dataclass
class Table:
    """Résultat d'une lecture : lignes (colonne → valeur, NULL comme l'imprime `content`), ou message d'erreur."""

    rows: list[dict[str, str]] = field(default_factory=list)
    error: str = ""
    raw: bytes = b""  # réponse binaire (lecture d'écran), décodée par l'appelant


class WrongServer(ValueError):
    """Un serveur répond, mais pas avec notre jeton : jamais servi, relancé avec un nouveau jeton."""


def encode(queries: list[list[str]]) -> bytes:
    out = [MAGIC, struct.pack(">i", len(queries))]
    for argv in queries:
        out.append(struct.pack(">i", len(argv)))
        for arg in argv:
            data = arg.encode("utf-8")
            out += [struct.pack(">i", len(data)), data]
    return b"".join(out)


class _Reader:
    def __init__(self, data: bytes, pos: int) -> None:
        self.data, self.pos = data, pos

    def number(self) -> int:
        if self.pos + 4 > len(self.data):
            raise ValueError("truncated response")
        value = struct.unpack(">i", self.data[self.pos:self.pos + 4])[0]
        self.pos += 4
        return value

    def text(self) -> str | None:
        size = self.number()
        if size == -1:
            return None
        if size < 0 or self.pos + size > len(self.data):
            raise ValueError("truncated response")
        value = self.data[self.pos:self.pos + size].decode("utf-8", "replace")
        self.pos += size
        return value

    def blob(self, size: int) -> bytes:
        if size < 0 or self.pos + size > len(self.data):
            raise ValueError("truncated response")
        value = self.data[self.pos:self.pos + size]
        self.pos += size
        return value


def decode(data: bytes, count: int, token: str) -> list[Table]:
    """Réponse complète du serveur ; WrongServer si le jeton n'est pas le nôtre, ValueError si elle est tronquée ou
    d'un autre protocole."""
    if data[:4] != MAGIC:
        raise ValueError("response without header")
    reader = _Reader(data, 4)
    if reader.text() != token:
        raise WrongServer("server without our token")
    tables = []
    for _ in range(count):
        status = reader.number()
        if status == 1:
            tables.append(Table(error=reader.text() or "Error"))
            continue
        if status == 2:
            tables.append(Table(raw=reader.blob(reader.number())))
            continue
        if status != 0:
            raise ValueError("response from another protocol")
        width = reader.number()
        if not 0 <= width <= MAX_COLUMNS:
            raise ValueError("response from another protocol")
        names = [reader.text() or "" for _ in range(width)]
        height = reader.number()
        if not 0 <= height <= MAX_ROWS:
            raise ValueError("response from another protocol")
        rows = []
        for _ in range(height):
            values = [reader.text() for _ in range(width)]
            rows.append({n: "NULL" if v is None else v for n, v in zip(names, values, strict=True)})
        tables.append(Table(rows))
    return tables


PREFIX = "nexus-query"


def forwarded(listing: str, serial: str, prefix: str) -> list[tuple[str, str]]:
    """(port PC, jeton) des redirections déjà posées vers un serveur de cette version, pour ce téléphone, d'après
    `adb forward --list`."""
    return re.findall(rf"(?m)^{re.escape(serial)} tcp:(\d+) localabstract:{re.escape(prefix)}-([0-9a-f]{{32}})\s*$",
                      listing)


class QueryServer:
    """Client du serveur de lectures pour un téléphone (une instance par liaison Adb, partagée par les sessions)."""

    def __init__(self, adb: Adb) -> None:
        self.adb = adb
        digest = hashlib.sha256(DEX.read_bytes()).hexdigest()[:12] if DEX.is_file() else ""
        self.prefix = f"{PREFIX}-{digest}"  # une version du dex = un préfixe : jamais un ancien serveur pris pour le bon
        self.remote = f"/data/local/tmp/{self.prefix}.dex"
        self.port: int | None = None
        self.token = ""  # jeton du serveur joint par self.port
        self.serial: str | None = None  # appareil de la redirection en cours
        self.failed_at = -RETRY
        self.lock = threading.Lock()

    @property
    def name(self) -> str:
        return f"{self.prefix}-{self.token}"

    def run(self, queries: list[list[str]]) -> list[Table] | None:
        """Résultats des requêtes (arguments de `content`, « query » en tête), dans l'ordre ; None si le serveur est
        indisponible (l'appelant passe par adb shell)."""
        if not queries:
            return []
        if not DEX.is_file() or time.monotonic() - self.failed_at < RETRY:
            return None
        with self.lock:
            if not self.adb.serial:
                self.adb.connect()  # téléphone absent : PhoneError à l'appelant, ce n'est pas une panne du serveur
            try:
                if self.serial != self.adb.serial:
                    self.port = None
                if self.port is None:
                    self._adopt()
                try:
                    return self._request(queries)
                except TimeoutError:
                    raise
                except (OSError, ValueError):
                    # Serveur relancé entre-temps par un autre agent (nouveau jeton) : sa redirection est reprise.
                    if self._adopt():
                        try:
                            return self._request(queries)
                        except TimeoutError:
                            raise
                        except (OSError, ValueError):
                            pass
                    self._start()  # absent (premier appel, téléphone redémarré, arrêté pour inactivité) ou ancien
                    return self._request(queries)
            except TimeoutError as e:
                # Aucune réponse : liaison coupée (Wi-Fi perdu) bien plus souvent que serveur figé. Jamais le repli adb
                # shell (même attente) : erreur de liaison tout de suite, et téléphone recherché au prochain appel.
                self.port = None
                self.adb.lost()
                raise LinkError(f"no response from the phone to a read after {READ_WAIT:g} s (link lost?)") from e
            except LinkError:
                self.port = None
                raise
            except (OSError, ValueError, PhoneError):
                self.failed_at = time.monotonic()
                self.port = None
                return None

    def release(self) -> None:
        """Demande au serveur déjà lancé, s'il y en a un, de rendre sa connexion UiAutomation (avant un `uiautomator
        dump`, qui en a besoin) ; n'en lance jamais un."""
        if not DEX.is_file():
            return
        with self.lock:
            try:
                if not self.adb.serial or (self.port is None and not self._adopt()):
                    return
                self._request([["release"]], timeout=3)
            except (OSError, ValueError, PhoneError):
                pass

    def _adopt(self) -> bool:
        """Reprend la redirection déjà posée pour ce téléphone (autre processus, démon relancé), avec son jeton, pour ne
        pas en accumuler une par processus. True si elle diffère de celle en cours."""
        listed = forwarded(self.adb.host("forward", "--list", timeout=10), self.adb.serial or "", self.prefix)
        if not listed:
            return False
        port, token = int(listed[-1][0]), listed[-1][1]
        changed = (port, token) != (self.port, self.token)
        self.port, self.token, self.serial = port, token, self.adb.serial
        return changed

    def _request(self, queries: list[list[str]], timeout: float | None = None) -> list[Table]:
        if self.port is None or not self.token:
            raise OSError("no server reached")
        with socket.create_connection(("127.0.0.1", self.port), timeout=READ_WAIT if timeout is None else timeout) as sock:
            sock.sendall(encode(queries))
            chunks = []
            while chunk := sock.recv(1 << 16):
                chunks.append(chunk)
        return decode(b"".join(chunks), len(queries), self.token)

    def _start(self) -> None:
        """Pousse le dex s'il manque, arrête les autres serveurs et leurs redirections, lance le serveur avec un jeton
        neuf et attend qu'il réponde avec ce jeton. Un autre serveur sur le nom (jamais normal) : nouveau jeton."""
        present = self.adb.shell("sh", "-c", f"[ -f {self.remote} ] && echo ok", check=False).strip()
        if present != "ok":
            self.adb.shell("sh", "-c", "rm -f /data/local/tmp/nexus-query-*.dex", check=False)
            self.adb.host("push", str(DEX), self.remote, timeout=30)
        for attempt in range(2):
            listed = self.adb.host("forward", "--list", timeout=10)
            for port in re.findall(rf"(?m)^{re.escape(self.adb.serial or '')} tcp:(\d+) localabstract:{PREFIX}-",
                                   listed):
                self.adb.host("forward", "--remove", f"tcp:{port}", timeout=10)
            # À part du lancement, qui nomme NexusQuery dans sa propre ligne de commande ; [N] : pkill ne se vise pas.
            self.adb.shell("pkill", "-f", "app_process / [N]exusQuery", check=False)
            self.token = secrets.token_hex(16)
            self.adb.shell("sh", "-c", f"CLASSPATH={self.remote} setsid nohup app_process / NexusQuery {self.name} "
                           f"{IDLE} {self.token} </dev/null >/dev/null 2>&1 &", check=False)
            port = self.adb.host("forward", "tcp:0", f"localabstract:{self.name}", timeout=10).strip()
            if not port.isdigit():
                raise PhoneError(f"port forwarding failed ({port[:100]})")
            self.port, self.serial = int(port), self.adb.serial
            deadline = time.monotonic() + START_WAIT
            while True:
                try:
                    self._request([], timeout=3)
                    return
                except WrongServer:
                    if attempt:
                        raise
                    break  # nom occupé par un autre : relance avec un nouveau jeton
                except (OSError, ValueError):
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.1)


_SERVERS: weakref.WeakKeyDictionary[Adb, QueryServer] = weakref.WeakKeyDictionary()
_SERVERS_LOCK = threading.Lock()


def server_for(adb: Adb) -> QueryServer:
    with _SERVERS_LOCK:
        if (server := _SERVERS.get(adb)) is None:
            server = _SERVERS[adb] = QueryServer(adb)
        return server
