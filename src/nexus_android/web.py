"""Pages web du téléphone lues par le protocole DevTools de Chrome (CDP), comme NexusBrowser sur le PC : texte complet
de la page (même hors de l'écran), titre, adresse et liens, sans défiler ni toucher l'écran.

Chrome ouvre la prise `chrome_devtools_remote` dès que le débogage USB est actif ; une application ouvre
`webview_devtools_remote_<pid>` quand ses WebView sont déboguables. Le PC y accède par `adb forward` (réservé à
l'utilisateur shell, donc à adb). Android gèle les applications en arrière-plan : la prise d'un navigateur qui n'est
pas au premier plan peut ne pas répondre. Lecture seule : aucun script de la page n'est modifié, aucun champ lu
(mots de passe compris), seul innerText et les liens sont rendus ; codes, jetons, e-mails et numéros masqués."""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import struct
import time
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from . import logs as logs_mod
from . import policy
from .adb import Adb, PhoneError
from .messages import has_keyword, mask_codes

TIMEOUT = 8.0
DEADLINE = 20.0  # s au plus pour la réponse de la page, quels que soient les messages intermédiaires
MAX_HEADER = 16 * 1024
MAX_TEXT = 30_000
MAX_LINKS = 60
_SOCKET = re.compile(r"@((?:chrome|webview|[\w.]+)_devtools_remote(?:_(\d+))?)\s*$")
# Texte et liens de la page, sans rien changer : innerText (ce qu'un humain lit) et liens visibles avec leur texte.
READ_JS = """(() => {
  const links = [...document.querySelectorAll('a[href]')].filter(a => (a.innerText || '').trim())
    .slice(0, LIMIT).map(a => [a.innerText.trim().replace(/\\s+/g, ' ').slice(0, 80), a.href]);
  return {title: document.title, url: location.href, text: document.body ? document.body.innerText : '', links};
})()""".replace("LIMIT", str(MAX_LINKS))


@dataclass
class Tab:
    socket: str
    package: str
    id: str
    title: str
    url: str
    ws: str


class WebSocket:
    """Client WebSocket minimal (RFC 6455) : poignée de main, trames texte masquées, réponses fragmentées."""

    def __init__(self, port: int, path: str, timeout: float = TIMEOUT) -> None:
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
                          .encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise PhoneError("web: the page refused the DevTools connection")
            head += chunk
            end = head.find(b"\r\n\r\n")
            if (end if end >= 0 else len(head)) > MAX_HEADER:  # fin d'en-tête réelle, même dans le dernier morceau
                raise PhoneError("web: DevTools handshake too long")
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise PhoneError(f"web: DevTools answered {head.split(b'\r\n', 1)[0][:80].decode('latin-1')}")
        self.buffer = head.split(b"\r\n\r\n", 1)[1]
        self.deadline = time.monotonic() + DEADLINE  # échéance absolue de tout l'échange, jusque dans chaque lecture

    def _read(self, n: int) -> bytes:
        while len(self.buffer) < n:
            left = self.deadline - time.monotonic()
            if left <= 0:
                raise PhoneError(f"web: no answer from the page within {DEADLINE:g} s")
            self.sock.settimeout(min(left, TIMEOUT))
            try:
                chunk = self.sock.recv(65536)
            except TimeoutError:
                raise PhoneError(f"web: no answer from the page within {DEADLINE:g} s") from None
            if not chunk:
                raise PhoneError("web: DevTools connection closed")
            self.buffer += chunk
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

    def send(self, text: str) -> None:
        data = text.encode()
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else
                                struct.pack(">BH", 0x80 | 126, n) if n < 65536 else struct.pack(">BQ", 0x80 | 127, n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def receive(self, limit: int = 32 * 1024 * 1024) -> str:
        message = b""
        while True:
            if time.monotonic() > self.deadline:  # pings et fragments sans fin : l'échéance tient quand même
                raise PhoneError(f"web: no answer from the page within {DEADLINE:g} s")
            first, second = self._read(2)
            n = second & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            if second & 0x80:
                self._read(4)  # un serveur ne masque pas ses trames ; ignoré s'il le fait
            if len(message) + n > limit:
                raise PhoneError("web: page answer too large")
            payload = self._read(n)
            opcode = first & 0x0F
            if opcode == 0x8:
                raise PhoneError("web: DevTools connection closed")
            if opcode in (0x9, 0xA):  # ping, pong : sans contenu utile
                continue
            message += payload
            if first & 0x80:
                return message.decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def sockets(adb: Adb) -> list[tuple[str, str]]:
    """(prise DevTools, paquet) ouvertes sur le téléphone : Chrome, WebView déboguables (Stetho exclu : autre
    protocole)."""
    out = adb.shell("cat", "/proc/net/unix", check=False)
    found: dict[str, str] = {}
    for line in out.splitlines():
        if (m := _SOCKET.search(line)) and not m.group(1).startswith("stetho"):
            name, pid = m.group(1), m.group(2)
            if name == "chrome_devtools_remote":
                found[name] = "com.android.chrome"
            elif pid:
                process = adb.shell("cat", f"/proc/{pid}/cmdline", check=False).split("\0")[0].strip()
                found[name] = process.split(":", 1)[0] or "?"  # processus secondaire « paquet:web » : son paquet
            else:
                found[name] = name.removesuffix("_devtools_remote")
    return sorted(found.items())


@contextmanager
def forward(adb: Adb, name: str) -> Iterator[int]:
    port = adb.host("forward", "tcp:0", f"localabstract:{name}").strip()
    if not port.isdigit():
        raise PhoneError(f"web: port forwarding failed ({port[:100]})")
    try:
        yield int(port)
    finally:
        try:
            adb.host("forward", "--remove", f"tcp:{port}", timeout=10)
        except PhoneError:
            pass


def _json(port: int, path: str) -> object:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=TIMEOUT) as response:
        return json.loads(response.read(4 * 1024 * 1024))


def tabs(adb: Adb) -> list[Tab]:
    found: list[Tab] = []
    for name, package in sockets(adb):
        with forward(adb, name) as port:
            try:
                pages = _json(port, "/json/list")
            except (OSError, ValueError):
                continue  # application gelée en arrière-plan, ou prise d'un autre protocole
        for page in pages if isinstance(pages, list) else []:
            if isinstance(page, dict) and page.get("type") == "page":
                path = re.sub(r"^ws://[^/]+", "", str(page.get("webSocketDebuggerUrl", "")))
                found.append(Tab(name, package, str(page.get("id", "")), str(page.get("title", "")),
                                 str(page.get("url", "")), path))
    return found


# Sites de banque, de paiement, d'impôts et d'identité : jamais lus par DevTools (comme les applications protégées).
_PROTECTED_HOST = re.compile(
    r"(bank|banque|bnp|credit|caisse|boursorama|lcl\.|societegenerale|labanquepostale|fortuneo|hellobank|revolut|"
    r"n26|qonto|paypal|stripe|lydia|sumeria|wise\.com|binance|coinbase|kraken|impots\.gouv|ameli\.fr|"
    r"franceconnect|caf\.fr|ants\.gouv|service-public|urssaf|pole-emploi|francetravail)",
    re.I)


def protected_site(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    checkout = re.match(r"(www\.)?amazon\.[a-z.]+$", host) and re.match(r"/(ap|gp/buy|checkout)", parts.path)
    return bool(_PROTECTED_HOST.search(host)) or bool(policy._PROTECTED_PATTERN.search(host)) or bool(checkout)


def _clean(text: str) -> str:
    """Masquage ligne par ligne : un code n'est masqué que si sa ligne parle de code (« Votre code : 482913 ») ;
    sur une page entière, le mot « code » quelque part masquerait toutes les années et tous les nombres."""
    out, keyed = [], False
    for line in text.split("\n"):
        out.append(logs_mod.mask(mask_codes(line, context=keyed)))  # mot-clé sur la ligne d'avant : « code :⏎482913 »
        keyed = has_keyword(line) or (keyed and not line.strip())
    return "\n".join(out)


def read(adb: Adb, tab: Tab) -> dict[str, object]:
    if not re.fullmatch(r"[A-Za-z][\w]*(?:\.[\w]+)+", tab.package):
        raise PhoneError(f"web: page of an unidentified app ({tab.package[:60]}): not read")
    if name := policy.protected_app(tab.package):
        raise PhoneError(f"{name}: banking, payment or identity app, its pages are never read through DevTools")
    if protected_site(tab.url):
        raise PhoneError(f"{urllib.parse.urlsplit(tab.url).hostname}: banking, payment, tax or identity site, never "
                         "read through DevTools (look at the screen instead, read-only)")
    if not tab.ws:
        raise PhoneError("web: this page is already inspected by another tool (DevTools open elsewhere)")
    with forward(adb, tab.socket) as port:
        ws = WebSocket(port, tab.ws)
        try:
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                "params": {"expression": READ_JS, "returnByValue": True}}))
            deadline = time.monotonic() + DEADLINE
            while True:
                if time.monotonic() > deadline:
                    raise PhoneError(f"web: no answer from the page within {DEADLINE:g} s")
                answer = json.loads(ws.receive())
                if answer.get("id") == 1:
                    break
        finally:
            ws.close()
    if "error" in answer or answer.get("result", {}).get("exceptionDetails"):
        raise PhoneError("web: the page could not be read (script refused by the page)")
    value = answer.get("result", {}).get("result", {}).get("value") or {}
    # Règle revérifiée sur la page réellement lue (même évaluation que le texte) : l'onglet a pu naviguer vers une
    # banque entre la liste des onglets et la lecture.
    actual = str(value.get("url") or "")
    if not actual:
        raise PhoneError("web: the page did not say its address, nothing returned (it could be a protected site)")
    if protected_site(actual):
        raise PhoneError(f"{urllib.parse.urlsplit(actual).hostname}: the tab moved to a banking, payment, tax or "
                         "identity site before the reading; nothing returned (look at the screen instead, read-only)")
    text = _clean(str(value.get("text", "")))
    links = [(_clean(str(t)), logs_mod.mask(str(h))) for t, h in value.get("links", [])
             if not str(h).startswith("javascript:")]
    return {"title": _clean(str(value.get("title", ""))), "url": logs_mod.mask(actual), "text": text,
            "links": links}
