"""Serveur de lectures gardé en vie sur le téléphone (phone/NexusQuery.java) : supprime le démarrage d'une machine Java
(environ 1 s) que paie chaque `content query` lancé par adb shell.

Même méthode que le serveur de scrcpy : un petit dex poussé dans /data/local/tmp, lancé par app_process sous
l'identité shell (aucune application à installer), joint par adb forward sur un socket local abstrait. Il exécute les
requêtes avec le code même de la commande `content` (sorties identiques), en lecture seule, ne sert que l'utilisateur
shell (adbd) et s'arrête seul après IDLE secondes sans requête. Absent ou en panne : None, et l'appelant repasse par
adb shell (méthode d'avant) ; nouvel essai après RETRY secondes."""

from __future__ import annotations

import hashlib
import re
import socket
import struct
import threading
import time
import weakref
from pathlib import Path

from .adb import Adb, PhoneError

DEX = Path(__file__).with_name("nexus-query.dex")
IDLE = 600  # secondes sans requête avant l'arrêt du serveur sur le téléphone
RETRY = 60.0  # après un échec, secondes avant de retenter le serveur (adb shell entre-temps)
START_WAIT = 5.0  # attente du serveur après son lancement
MAGIC = b"NXQ1"


def encode(queries: list[list[str]]) -> bytes:
    out = [MAGIC, struct.pack(">i", len(queries))]
    for argv in queries:
        out.append(struct.pack(">i", len(argv)))
        for arg in argv:
            data = arg.encode("utf-8")
            out += [struct.pack(">i", len(data)), data]
    return b"".join(out)


def decode(data: bytes, count: int) -> list[str]:
    """Réponse complète du serveur ; ValueError si elle est tronquée ou d'un autre protocole."""
    if data[:4] != MAGIC:
        raise ValueError("réponse sans en-tête")
    pos, outputs = 4, []
    for _ in range(count):
        if pos + 4 > len(data):
            raise ValueError("réponse tronquée")
        size = struct.unpack(">i", data[pos:pos + 4])[0]
        if pos + 4 + size > len(data):
            raise ValueError("réponse tronquée")
        outputs.append(data[pos + 4:pos + 4 + size].decode("utf-8", "replace"))
        pos += 4 + size
    return outputs


def forwarded(listing: str, serial: str, name: str) -> list[str]:
    """Ports PC déjà redirigés vers ce socket du téléphone, d'après `adb forward --list`."""
    return re.findall(rf"(?m)^{re.escape(serial)} tcp:(\d+) localabstract:{re.escape(name)}\s*$", listing)


class QueryServer:
    """Client du serveur de lectures pour un téléphone (une instance par liaison Adb, partagée par les sessions)."""

    def __init__(self, adb: Adb) -> None:
        self.adb = adb
        digest = hashlib.sha256(DEX.read_bytes()).hexdigest()[:12] if DEX.is_file() else ""
        self.name = f"nexus-query-{digest}"  # une version du dex = un nom : jamais un ancien serveur pris pour le bon
        self.remote = f"/data/local/tmp/{self.name}.dex"
        self.port: int | None = None
        self.serial: str | None = None  # appareil de la redirection en cours
        self.failed_at = -RETRY
        self.lock = threading.Lock()

    def run(self, queries: list[list[str]]) -> list[str] | None:
        """Sorties des requêtes (arguments de `content`, « query » en tête), dans l'ordre ; None si le serveur est
        indisponible (l'appelant passe par adb shell)."""
        if not queries:
            return []
        if not DEX.is_file() or time.monotonic() - self.failed_at < RETRY:
            return None
        with self.lock:
            try:
                if not self.adb.serial:
                    self.adb.connect()
                if self.port is None or self.serial != self.adb.serial:
                    self._forward()
                try:
                    return self._request(queries)
                except (OSError, ValueError):
                    self._start()  # absent (premier appel, téléphone redémarré, arrêté pour inactivité) ou ancien
                    return self._request(queries)
            except (OSError, ValueError, PhoneError):
                self.failed_at = time.monotonic()
                self.port = None
                return None

    def _forward(self) -> None:
        """Redirection vers le serveur : celle déjà posée pour ce téléphone (autre processus, démon relancé) est reprise,
        pour ne pas en accumuler une par processus."""
        listed = self.adb.host("forward", "--list", timeout=10)
        port = next((p for p in forwarded(listed, self.adb.serial or "", self.name)), None) or self.adb.host(
            "forward", "tcp:0", f"localabstract:{self.name}", timeout=10).strip()
        if not port.isdigit():
            raise PhoneError(f"redirection impossible ({port[:100]})")
        self.port, self.serial = int(port), self.adb.serial

    def _request(self, queries: list[list[str]], timeout: float = 60.0) -> list[str]:
        assert self.port is not None
        with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as sock:
            sock.sendall(encode(queries))
            chunks = []
            while chunk := sock.recv(1 << 16):
                chunks.append(chunk)
        return decode(b"".join(chunks), len(queries))

    def _start(self) -> None:
        """Pousse le dex s'il manque, arrête les autres versions, lance le serveur et attend qu'il réponde."""
        present = self.adb.shell("sh", "-c", f"[ -f {self.remote} ] && echo ok", check=False).strip()
        if present != "ok":
            self.adb.shell("sh", "-c", "rm -f /data/local/tmp/nexus-query-*.dex", check=False)
            self.adb.host("push", str(DEX), self.remote, timeout=30)
        # À part du lancement, qui nomme NexusQuery dans sa propre ligne de commande ; [N] : pkill ne se vise pas.
        self.adb.shell("pkill", "-f", "app_process / [N]exusQuery", check=False)
        self.adb.shell("sh", "-c", f"CLASSPATH={self.remote}:/system/framework/content.jar setsid nohup app_process / "
                       f"NexusQuery {self.name} {IDLE} </dev/null >/dev/null 2>&1 &", check=False)
        deadline = time.monotonic() + START_WAIT
        while True:
            try:
                self._request([], timeout=3)
                return
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
