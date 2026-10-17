"""Saisie de tout texte (accents, emoji) par le presse-papiers : `input text` d'Android n'accepte que l'ASCII.

Le serveur de scrcpy (Genymobile, Apache 2.0) tourne le temps de la saisie sous l'identité shell, sans vidéo ni son,
avec son seul canal de contrôle : il lit le presse-papiers de l'utilisateur, y met le texte et le colle dans le champ actif,
puis remet l'ancien contenu (vide s'il était vide). Méthode vérifiée sur un Galaxy Fold.
Le serveur s'arrête à la fin de chaque saisie : aucun programme d'aide ne reste sur le téléphone."""

from __future__ import annotations

import hashlib
import os
import random
import socket
import struct
import subprocess
import time
from pathlib import Path

from .adb import Adb, LinkError, PhoneError

VERSION = "5.0.1"
SHA256 = "764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536"  # digest publié par GitHub pour la v5.0.1
LOCAL = Path.home() / ".nexus" / "nexus-android" / "tools" / f"scrcpy-server-v{VERSION}"  # posé par install.ps1
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
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise PhoneError("presse-papiers : le téléphone a fermé le canal")
            data += chunk
        return data

    def get(self, wait: float = 1.5) -> str | None:
        """Texte du presse-papiers ; None s'il est vide (le serveur ne répond alors rien)."""
        self.sock.sendall(bytes([GET_CLIPBOARD, 0]))
        self.sock.settimeout(wait)
        try:
            kind = self._read(1)[0]
        except TimeoutError:
            return None
        finally:
            self.sock.settimeout(5)
        if kind != CLIPBOARD:
            raise PhoneError(f"presse-papiers : réponse inattendue ({kind})")
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
            raise PhoneError(f"presse-papiers : réponse inattendue ({kind})")


def _ensure_server(adb: Adb) -> None:
    if not LOCAL.is_file() or hashlib.sha256(LOCAL.read_bytes()).hexdigest() != SHA256:
        raise PhoneError("Saisie avec accents indisponible : serveur scrcpy absent du PC ou altéré (lancer "
                         "install.ps1). Pour un message : draft(to, text).")
    size = adb.shell("stat", "-c", "%s", REMOTE, check=False).strip()
    if size != str(LOCAL.stat().st_size):
        adb.host("push", str(LOCAL), REMOTE)


def paste(adb: Adb, text: str) -> bool:
    """Colle `text` dans le champ qui a le focus, puis rend à l'utilisateur son presse-papiers d'avant. Renvoie False si le
    collage est fait mais l'ancien contenu n'a pas pu être remis (liaison coupée juste après)."""
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise PhoneError("texte trop long pour une saisie (256 Ko au plus)")
    _ensure_server(adb)
    scid = f"{random.randrange(1, 0x7FFFFFFF):08x}"
    port = adb.host("forward", "tcp:0", f"localabstract:scrcpy_{scid}").strip()
    server: subprocess.Popen[bytes] | None = None
    sock = None
    try:
        if not port.isdigit():
            raise PhoneError(f"presse-papiers : redirection impossible ({port[:100]})")
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
                raise PhoneError(f"presse-papiers : serveur scrcpy arrêté ({err[:200] or 'sans message'})")
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
                raise LinkError("presse-papiers : serveur scrcpy injoignable après 8 s ; rien n'a été collé")
            time.sleep(0.1)
        sock.settimeout(5)
        channel = Channel(sock)
        before = channel.get()
        sequence = int.from_bytes(os.urandom(4)) or 1
        try:
            channel.set(sequence, text, paste=True)
        except (OSError, PhoneError) as e:
            raise LinkError(f"collage interrompu ({e}) : effet inconnu") from None
        time.sleep(0.3)  # le collage part avant la remise de l'ancien contenu
        try:
            channel.set(sequence + 1, before or "", paste=False)
        except (OSError, PhoneError):
            return False  # texte collé : ne pas faire croire à un échec qui pousserait à recommencer
        return True
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
