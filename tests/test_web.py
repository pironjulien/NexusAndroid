"""Pages web par DevTools : client WebSocket (poignée de main, trames masquées, réponse fragmentée et longue) contre un
petit serveur local, prises trouvées sur le téléphone, lecture masquée, applis protégées refusées ; adb simulé."""

from __future__ import annotations

import json
import socket
import struct
import threading

import pytest

from nexus_android import server, web
from nexus_android.adb import PhoneError


def _frames(payload: bytes, pieces: int = 2) -> bytes:
    """Réponse d'un serveur en trames texte non masquées, coupée en `pieces` fragments (le dernier avec FIN)."""
    size = max(1, len(payload) // pieces)
    chunks = [payload[i:i + size] for i in range(0, len(payload), size)]
    out = b""
    for i, chunk in enumerate(chunks):
        first = (0x80 if i == len(chunks) - 1 else 0) | (0x1 if i == 0 else 0x0)
        n = len(chunk)
        head = bytes([first, n]) if n < 126 else (struct.pack(">BBH", first, 126, n) if n < 65536 else
                                                   struct.pack(">BBQ", first, 127, n))
        out += head + chunk
    return out


def serve(answer: dict) -> tuple[int, list[dict]]:
    """Serveur DevTools d'une seule connexion : lit la requête masquée du client, répond `answer` fragmenté."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    seen: list[dict] = []

    def run() -> None:
        conn, _ = listener.accept()
        head = b""
        while b"\r\n\r\n" not in head:
            head += conn.recv(4096)
        conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
        first, second = conn.recv(2)
        n = second & 0x7F
        if n == 126:
            n = struct.unpack(">H", conn.recv(2))[0]
        mask = conn.recv(4)
        data = b""
        while len(data) < n:
            data += conn.recv(n - len(data))
        assert second & 0x80  # un client masque toujours
        seen.append(json.loads(bytes(b ^ mask[i % 4] for i, b in enumerate(data))))
        conn.sendall(bytes([0x89, 0]))  # ping : ignoré
        conn.sendall(_frames(json.dumps({"id": 99}).encode(), 1))  # événement d'un autre id : ignoré
        conn.sendall(_frames(json.dumps(answer).encode(), 3))
        conn.close()
        listener.close()

    threading.Thread(target=run, daemon=True).start()
    return listener.getsockname()[1], seen


def test_websocket_round_trip_with_fragments():
    long_text = "x" * 70_000  # longueur sur 8 octets
    port, seen = serve({"id": 1, "result": {"result": {"value": {"text": long_text}}}})
    ws = web.WebSocket(port, "/devtools/page/1")
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate"}))
    assert json.loads(ws.receive()) == {"id": 99}
    assert json.loads(ws.receive())["result"]["result"]["value"]["text"] == long_text
    ws.close()
    assert seen == [{"id": 1, "method": "Runtime.evaluate"}]


class WebPhone:
    serial = "x"

    def __init__(self, port: int = 0) -> None:
        self.port = port
        self.calls: list[tuple[str, ...]] = []

    def shell(self, *argv: str, timeout: float = 30.0, check: bool = True) -> str:
        if argv == ("cat", "/proc/net/unix"):
            return ("0: 00000002 0 10000 0001 01 1 @chrome_devtools_remote\n"
                    "0: 00000002 0 10000 0001 01 2 @webview_devtools_remote_4242\n"
                    "0: 00000002 0 10000 0001 01 3 @stetho_com.app_devtools_remote\n")
        if argv == ("cat", "/proc/4242/cmdline"):
            return "fr.creditagricole.androidapp\0"
        return ""

    def host(self, *args: str, timeout: float = 60.0) -> str:
        self.calls.append(args)
        return str(self.port) if args[:2] == ("forward", "tcp:0") else ""


def test_sockets_found_without_stetho():
    assert web.sockets(WebPhone()) == [("chrome_devtools_remote", "com.android.chrome"),  # type: ignore[arg-type]
                                       ("webview_devtools_remote_4242", "fr.creditagricole.androidapp")]


def test_read_masks_and_refuses_protected_apps(monkeypatch):
    answer = {"id": 1, "result": {"result": {"value": {
        "title": "Compte", "url": "https://x.fr/?token=abcdefghijklmnopqrstuvwxyz123456",
        "text": "Construite en 1889.\nVotre code de connexion : 482913\nÉcrire à jean@example.com",
        "links": [["Aide", "https://x.fr/aide"], ["Clic", "javascript:void(0)"]]}}}}
    port, _ = serve(answer)
    phone = WebPhone(port)
    tab = web.Tab("chrome_devtools_remote", "com.android.chrome", "1", "Compte", "https://x.fr", "/devtools/page/1")
    page = web.read(phone, tab)  # type: ignore[arg-type]
    assert "1889" in str(page["text"]) and "482913" not in str(page["text"]) and "jean@" not in str(page["text"])
    assert "abcdefghijklmnop" not in str(page["url"])
    assert page["links"] == [("Aide", "https://x.fr/aide")]
    assert ("forward", "--remove", f"tcp:{port}") in phone.calls  # redirection retirée
    bank = web.Tab("webview_devtools_remote_4242", "fr.creditagricole.androidapp", "1", "", "", "/x")
    with pytest.raises(PhoneError, match="banking"):
        web.read(phone, bank)  # type: ignore[arg-type]


def test_web_tool_lists_and_reads_the_foreground_page(monkeypatch):
    tabs = [web.Tab("s", "com.other", "1", "Autre", "https://a", "/a"),
            web.Tab("chrome_devtools_remote", "com.android.chrome", "2", "Ici", "https://b", "/b")]
    monkeypatch.setattr(web, "tabs", lambda adb: tabs)
    monkeypatch.setattr(web, "read", lambda adb, tab: {"title": tab.title, "url": tab.url,
                                                       "text": "ligne un\nligne deux", "links": []})

    class Scr:
        def state(self):
            return type("S", (), {"package": "com.android.chrome"})()

    monkeypatch.setattr(server, "_init", lambda: (object(), Scr(), object()))
    assert server._web("tabs", None, None).splitlines()[2].startswith("[2] com.android.chrome · Ici")
    assert server._web("read", None, None).startswith("Ici\nhttps://b")  # la page de l'appli au premier plan
    assert server._web("read", 1, "deux").endswith("ligne deux")
    with pytest.raises(PhoneError, match="tab 5 does not exist"):
        server._web("read", 5, None)


def test_code_on_the_next_line_and_secondary_process(monkeypatch):
    """Relecture de Codex : « code :⏎482913 » passait ; « paquet:web » d'une appli bancaire échappait au refus."""
    assert web._clean("Votre code de connexion :\n482913\n\nConstruite en 1889") == (
        "Votre code de connexion :\n[masked code]\n\nConstruite en 1889")

    class Proc(WebPhone):
        def shell(self, *argv, timeout=30.0, check=True):
            if argv == ("cat", "/proc/4242/cmdline"):
                return "fr.creditagricole.androidapp:web\0"
            return super().shell(*argv, timeout=timeout, check=check)

    found = dict(web.sockets(Proc()))  # type: ignore[arg-type]
    assert found["webview_devtools_remote_4242"] == "fr.creditagricole.androidapp"
    with pytest.raises(PhoneError, match="unidentified app"):
        web.read(Proc(), web.Tab("x", "?", "1", "", "", "/x"))  # type: ignore[arg-type]


def test_banking_sites_in_chrome_are_not_read():
    assert web.protected_site("https://www.boursorama.com/espace-client") and web.protected_site(
        "https://cfspart.impots.gouv.fr/x") and not web.protected_site("https://fr.wikipedia.org/wiki/Banque")
    tab = web.Tab("chrome_devtools_remote", "com.android.chrome", "1", "", "https://mabanque.bnpparibas/compte", "/x")
    with pytest.raises(PhoneError, match="banking, payment, tax or identity site"):
        web.read(WebPhone(), tab)  # type: ignore[arg-type]


def test_deadline_holds_against_endless_pings(monkeypatch):
    """Relecture de Codex, tour 2 : l'échéance n'était vérifiée qu'entre deux réponses ; des pings sans fin la
    tenaient ouverte."""
    import time
    monkeypatch.setattr(web, "DEADLINE", 0.3)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def run() -> None:
        conn, _ = listener.accept()
        head = b""
        while b"\r\n\r\n" not in head:
            head += conn.recv(4096)
        conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\n\r\n")
        try:
            for _ in range(200):
                conn.sendall(bytes([0x89, 0]))
                time.sleep(0.01)
        except OSError:
            pass
        conn.close()
        listener.close()

    threading.Thread(target=run, daemon=True).start()
    ws = web.WebSocket(listener.getsockname()[1], "/x")
    start = time.monotonic()
    with pytest.raises(PhoneError, match="no answer"):
        ws.receive()
    assert time.monotonic() - start < 1.5
    ws.close()
