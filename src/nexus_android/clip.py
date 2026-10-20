"""Presse-papiers du téléphone : saisie de tout texte (accents, emoji ; `input text` d'Android n'accepte que l'ASCII),
lecture et écriture (outil clipboard).

Le serveur de scrcpy (Genymobile, Apache 2.0) tourne le temps de la saisie sous l'identité shell, sans vidéo ni son,
avec son seul canal de contrôle : il lit le presse-papiers de l'utilisateur, y met le texte et le colle dans le champ actif,
puis remet l'ancien contenu (vide s'il était vide). Méthode vérifiée sur un Galaxy Fold.
Le serveur s'arrête à la fin de chaque saisie, lecture ou écriture : aucun programme d'aide ne reste sur le téléphone.

Le fichier du serveur (version et empreinte figées) est posé par install.ps1, ou téléchargé à la première saisie depuis
la release officielle de Genymobile/scrcpy sur GitHub ; il n'est gardé que si son SHA-256 est celui écrit ici."""

from __future__ import annotations

import hashlib
import os
import random
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .adb import Adb, LinkError, PhoneError

VERSION = "5.0.1"
SHA256 = "764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536"  # digest publié par GitHub pour la v5.0.1
LOCAL = Path.home() / ".nexus" / "nexus-android" / "tools" / f"scrcpy-server-v{VERSION}"  # install.ps1 ou 1re saisie
URL = f"https://github.com/Genymobile/scrcpy/releases/download/v{VERSION}/scrcpy-server-v{VERSION}"
DOWNLOAD_MAX = 4 * 1024 * 1024  # le serveur v5.0.1 fait 717 Ko : bien au-delà, ce n'est pas lui
DOWNLOAD_TIMEOUT = 20.0
REMOTE = "/data/local/tmp/nexus-scrcpy.jar"
GET_CLIPBOARD, SET_CLIPBOARD = 8, 9  # messages de contrôle (ControlMessage.java)
CLIPBOARD, ACK_CLIPBOARD = 0, 1  # messages du téléphone (DeviceMessage.java)
MAX_BYTES = 256 * 1024 - 14  # CLIPBOARD_TEXT_MAX_LENGTH du serveur


def set_message(sequence: int, text: str, paste: bool) -> bytes:
    data = text.encode("utf-8")
    return struct.pack(">BQBI", SET_CLIPBOARD, sequence, int(paste), len(data)) + data


class Channel:
    """Canal de contrôle d'un serveur scrcpy (socket déjà connecté)."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock

    def _read(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            try:
                chunk = self.sock.recv(n - len(data))
            except TimeoutError:
                # Délai dépassé au milieu d'une réponse : message clair plutôt que l'exception brute du socket.
                raise PhoneError(f"clipboard: the phone stopped answering (no reply within "
                                 f"{self.sock.gettimeout() or 0:g} s)") from None
            if not chunk:
                raise PhoneError("clipboard: the phone closed the channel")
            data += chunk
        return data

    def get(self, wait: float = 1.5) -> str | None:
        """Texte du presse-papiers ; None s'il est vide (le serveur ne répond alors rien)."""
        self.sock.sendall(bytes([GET_CLIPBOARD, 0]))
        self.sock.settimeout(wait)
        try:
            first = self.sock.recv(1)
        except TimeoutError:
            return None  # aucune réponse : presse-papiers vide
        finally:
            self.sock.settimeout(5)
        if not first:
            raise PhoneError("clipboard: the phone closed the channel")
        kind = first[0]
        if kind != CLIPBOARD:
            raise PhoneError(f"clipboard: unexpected reply ({kind})")
        return self._read(struct.unpack(">I", self._read(4))[0]).decode("utf-8", "replace")

    def set(self, sequence: int, text: str, paste: bool) -> None:
        """Met le texte dans le presse-papiers (et le colle si paste) ; attend l'accusé de réception."""
        self.sock.sendall(set_message(sequence, text, paste))
        while True:
            kind = self._read(1)[0]
            if kind == ACK_CLIPBOARD and struct.unpack(">Q", self._read(8))[0] == sequence:
                return
            if kind == CLIPBOARD:  # copie faite entre-temps sur le téléphone : lue et ignorée
                self._read(struct.unpack(">I", self._read(4))[0])
                continue
            raise PhoneError(f"clipboard: unexpected reply ({kind})")


def _fetch(url: str) -> bytes:
    """Contenu de `url` (HTTPS, redirections suivies), borné à DOWNLOAD_MAX octets."""
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response:
        data = response.read(DOWNLOAD_MAX + 1)
    if len(data) > DOWNLOAD_MAX:
        raise OSError(f"file larger than {DOWNLOAD_MAX // 1024} KB")
    return data


TYPING = "typing"  # usage du serveur : saisie (act type) ou outil clipboard, pour le message d'indisponibilité


def _unavailable(reason: str, use: str = TYPING) -> PhoneError:
    where = (f"The scrcpy server v{VERSION} (Genymobile, clipboard) must be at {LOCAL} with SHA-256 {SHA256}. "
             f"Download it from {URL} (or run install.ps1 again)")
    if use == TYPING:
        return PhoneError(f"Typing accented characters, emoji or line breaks is unavailable, nothing was typed: {reason}. {where}, "
                          "then type again. ASCII text on one line is typed without it; for a message: draft(to, text).")
    return PhoneError(f"Clipboard unavailable, nothing was done: {reason}. {where}, then try again.")


def ensure_local(use: str = TYPING) -> Path:
    """Fichier du serveur scrcpy sur le PC, vérifié ; téléchargé (empreinte contrôlée) s'il manque ou est altéré."""
    if LOCAL.is_file() and hashlib.sha256(LOCAL.read_bytes()).hexdigest() == SHA256:
        return LOCAL
    try:
        data = _fetch(URL)
    except (OSError, urllib.error.URLError, ValueError) as e:
        raise _unavailable(f"download failed ({e})", use) from None
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise _unavailable("the downloaded file does not have the expected hash, it is rejected", use)
    part = LOCAL.with_name(f"{LOCAL.name}.{os.getpid()}.part")
    try:
        LOCAL.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(data)
        os.replace(part, LOCAL)  # atomique : un autre serveur ne lit jamais un fichier à moitié écrit
    except OSError as e:
        part.unlink(missing_ok=True)
        raise _unavailable(f"cannot write to {LOCAL.parent} ({e})", use) from None
    return LOCAL


def _ensure_server(adb: Adb, use: str) -> None:
    ensure_local(use)
    size = adb.shell("stat", "-c", "%s", REMOTE, check=False).strip()
    if size != str(LOCAL.stat().st_size):
        adb.host("push", str(LOCAL), REMOTE)


@contextmanager
def _session(adb: Adb, use: str = TYPING) -> Iterator[Channel]:
    """Serveur scrcpy lancé le temps du bloc, canal de contrôle ouvert ; arrêté et redirection retirée à la sortie."""
    _ensure_server(adb, use)
    scid = f"{random.randrange(1, 0x7FFFFFFF):08x}"
    port = adb.host("forward", "tcp:0", f"localabstract:scrcpy_{scid}").strip()
    server: subprocess.Popen[bytes] | None = None
    sock = None
    try:
        if not port.isdigit():
            raise PhoneError(f"clipboard: port forwarding failed ({port[:100]})")
        cmd = [adb.path, "-s", adb.serial or "", "shell",
               f"CLASSPATH={REMOTE} app_process / com.genymobile.scrcpy.Server {VERSION} scid={scid} log_level=warn "
               "tunnel_forward=true video=false audio=false control=true send_device_meta=false "
               "send_dummy_byte=true clipboard_autosync=false cleanup=false"]
        server = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + 8
        while sock is None:
            if server.poll() is not None:
                err = (server.stderr.read() if server.stderr else b"").decode("utf-8", "replace").strip()
                raise PhoneError(f"clipboard: scrcpy server stopped ({err[:200] or 'no message'})")
            try:
                candidate = socket.create_connection(("127.0.0.1", int(port)), timeout=2)
                candidate.settimeout(2)
                if candidate.recv(1) == b"\x00":  # octet d'accueil : le serveur écoute vraiment
                    sock = candidate
                    break
                candidate.close()
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise LinkError("clipboard: scrcpy server unreachable after 8 s; nothing was done")
            time.sleep(0.1)
        sock.settimeout(5)
        yield Channel(sock)
    finally:
        if sock:
            sock.close()
        if server:
            try:
                server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server.kill()
                try:  # arrêter aussi le serveur côté téléphone : aucun programme d'aide ne doit y rester
                    adb.shell("pkill", "-f", f"scid=[{scid[0]}]{scid[1:]}", check=False, timeout=10)  # [x] : pas soi
                except PhoneError:
                    pass
        if port.isdigit():
            try:
                adb.host("forward", "--remove", f"tcp:{port}", timeout=10)
            except PhoneError:
                pass


def _sequence() -> int:
    return int.from_bytes(os.urandom(4)) or 1


def _fits(text: str, what: str) -> None:
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise PhoneError(f"text too long to {what} (256 KB at most)")


def paste(adb: Adb, text: str) -> bool:
    """Colle `text` dans le champ qui a le focus, puis rend à l'utilisateur son presse-papiers d'avant. Renvoie False si le
    collage est fait mais l'ancien contenu n'a pas pu être remis (liaison coupée juste après)."""
    _fits(text, "type")
    with _session(adb) as channel:
        before = channel.get()
        sequence = _sequence()
        try:
            channel.set(sequence, text, paste=True)
        except (OSError, PhoneError) as e:
            raise LinkError(f"paste interrupted ({e}): unknown effect") from None
        time.sleep(0.3)  # le collage part avant la remise de l'ancien contenu
        try:
            channel.set(sequence + 1, before or "", paste=False)
        except (OSError, PhoneError):
            return False  # texte collé : ne pas faire croire à un échec qui pousserait à recommencer
        return True


def read(adb: Adb) -> str | None:
    """Texte du presse-papiers de l'utilisateur, None s'il est vide ou ne contient pas de texte (image, fichier)."""
    with _session(adb, "clipboard") as channel:
        return channel.get()


def write(adb: Adb, text: str) -> None:
    """Met `text` dans le presse-papiers (sans coller) ; l'accusé de réception du téléphone confirme l'écriture."""
    _fits(text, "copy")
    with _session(adb, "clipboard") as channel:
        try:
            channel.set(_sequence(), text, paste=False)
        except (OSError, PhoneError) as e:
            raise LinkError(f"clipboard write interrupted ({e}): unknown effect") from None
