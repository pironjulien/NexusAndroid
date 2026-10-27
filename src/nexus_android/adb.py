"""Liaison ADB avec le téléphone : choix de l'appareil (USB d'abord, puis Wi-Fi jumelé), commandes shell, captures."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import shlex
import socket
import struct
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


class PhoneError(RuntimeError):
    """Erreur à rapporter telle quelle à l'agent (message en anglais, sans pile)."""


class LinkError(PhoneError):
    """Liaison perdue en cours de commande (délai dépassé, téléphone parti) : la commande a pu s'exécuter ou non.
    Un geste ainsi interrompu a un effet inconnu ; il se relit avant d'être refait, jamais répété à l'aveugle."""


# Erreurs d'adb sur l'appareil lui-même, pas sur la commande (« sh: foo: not found » n'en est pas une).
_LINK_LOST = re.compile(r"^(?:adb(?:\.exe)?: )?(?:error: )?(?:device .*not found|device offline|no devices|closed|"
                        r"protocol fault)", re.I | re.M)

def _find_adb() -> str:
    from . import platformtools  # importé ici : platformtools dépend de PhoneError

    return platformtools.find()


DEV_OPTIONS = "https://developer.android.com/studio/debug/dev-options"
OEM_DRIVERS = "https://developer.android.com/studio/run/oem-usb"
# Pas-à-pas du premier branchement, dit à l'agent qui le relaie à l'utilisateur (et par `nexus-android setup`).
FIRST_PLUG = (
    "1. On the phone: Settings > About phone (Samsung: About phone > Software information) > tap \"Build number\" "
    "7 times, until \"Developer mode has been turned on\". "
    "2. Settings > Developer options (Samsung: at the bottom of Settings) > turn on \"USB debugging\". "
    "3. Plug the phone into this PC with a USB data cable (some cables only charge); if the phone asks what the USB "
    "connection is for, choose \"File transfer\". "
    "4. On the phone, accept \"Allow USB debugging?\" (tick \"Always allow from this computer\"). "
    f"Official guide: {DEV_OPTIONS}. Phone plugged in and still not seen on Windows: install the phone maker's USB "
    f"driver ({OEM_DRIVERS}; Samsung: https://developer.samsung.com/android-usb-driver).")
UNAUTHORIZED = ("Phone plugged in but not authorized: accept \"Allow USB debugging?\" on its screen (tick \"Always "
                "allow from this computer\"). No prompt? Unlock the phone, unplug and plug the cable again; if it "
                "still does not show, Developer options > \"Revoke USB debugging authorizations\", then plug in again.")


@dataclass(frozen=True)
class Device:
    serial: str
    state: str
    model: str
    transport: str  # "usb" ou "wifi"


def transport_of(serial: str) -> str:
    """« wifi » pour une adresse ip:port ou un service mDNS du débogage sans fil, sinon « usb »."""
    return "wifi" if ":" in serial or "._adb-tls-connect." in serial else "usb"


def parse_devices(output: str) -> list[Device]:
    devices = []
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        if state == "no" and len(parts) > 2 and parts[2].startswith("permissions"):  # Linux : « no permissions (…) »
            state = "no permissions"
        model = next((p.split(":", 1)[1] for p in parts[2:] if p.startswith("model:")), "")
        devices.append(Device(serial, state, model.replace("_", "-"), transport_of(serial)))
    return devices


def parse_mdns(output: str) -> list[str]:
    """Adresses ip:port des téléphones jumelés annoncés sur le Wi-Fi (débogage sans fil)."""
    return re.findall(r"_adb-tls-connect\._tcp\.?\s+(\d+\.\d+\.\d+\.\d+:\d+)", output)


def parse_adbd_port(proc_net: str) -> int | None:
    """Port TCP en écoute d'adbd (utilisateur shell, uid 2000) dans /proc/net/tcp6 et /proc/net/tcp."""
    for line in proc_net.splitlines():
        fields = line.split()
        if len(fields) > 7 and fields[3] == "0A" and fields[7] == "2000":
            port = int(fields[1].rsplit(":", 1)[1], 16)
            if port != 5037:
                return port
    return None


def wifi_ip(ip_addr: str) -> str | None:
    """Adresse IPv4 du téléphone joignable en Wi-Fi depuis le PC, dans la sortie de `ip -4 addr` : wlan0 (téléphone
    client d'un réseau Wi-Fi), sinon le point d'accès du partage de connexion (One UI : ap_br_swlan0 ou swlan0),
    quand le PC est connecté au Wi-Fi du téléphone (bureau sans réseau Wi-Fi commun)."""
    pairs = re.findall(r"inet (\d+\.\d+\.\d+\.\d+)/\d+.*?\b(wlan0|ap_br_\w+|swlan\d+)\s*$", ip_addr, re.M)
    by_iface = {iface: ip for ip, iface in pairs}
    return by_iface.get("wlan0") or next(iter(by_iface.values()), None)


WIFI_STATE = Path.home() / ".nexus" / "nexus-android" / "wifi.json"
TLS_PORTS = range(30000, 50000)  # plage des ports aléatoires du débogage sans fil
ABSENT_TTL = 120.0  # s : téléphone introuvable en Wi-Fi, échec gardé en mémoire (aucune nouvelle recherche)
MAX_TRIED = 4  # ports du balayage essayés par adb connect, au plus
HANDSHAKE_WAIT = 1.0  # s : réponse d'adbd à la poignée de main
STATE_WAIT = 5.0  # s : lectures d'état (écran, verrouillage, inactivité, démarrage)

# Messages du protocole ADB (en-tête de 24 octets, petit-boutiste) : à un CNXN, adbd répond CNXN, AUTH (clé à
# présenter) ou STLS (débogage sans fil, TLS). Tout autre service du téléphone se tait ou répond autre chose.
_CNXN, _AUTH, _STLS = 0x4E584E43, 0x48545541, 0x534C5453


def adb_hello() -> bytes:
    """CNXN d'un hôte ADB : la première chose qu'adbd attend sur son port TCP."""
    payload = b"host::\x00"
    return struct.pack("<6I", _CNXN, 0x01000001, 256 * 1024, len(payload), sum(payload), _CNXN ^ 0xFFFFFFFF) + payload


def speaks_adb(reply: bytes) -> bool:
    """La réponse est-elle un en-tête ADB valide (CNXN, AUTH ou STLS, mot magique cohérent) ?"""
    if len(reply) < 24:
        return False
    command, *_, magic = struct.unpack("<6I", reply[:24])
    return command in (_CNXN, _AUTH, _STLS) and magic == command ^ 0xFFFFFFFF


def same_subnet(host: str, prefix: int = 24) -> bool:
    """Le PC a-t-il une adresse dans le même /24 que le téléphone ? Route choisie par le système (connect UDP : aucun
    paquet envoyé) ; sinon, balayer serait inutile (téléphone sur un autre réseau, PC ailleurs)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((host, 9))
            local = probe.getsockname()[0]
        return ipaddress.ip_network(f"{local}/{prefix}", strict=False) == ipaddress.ip_network(
            f"{host}/{prefix}", strict=False)
    except (OSError, ValueError):
        return False


def open_ports(host: str, ports: Iterable[int] = TLS_PORTS, timeout: float = 0.4) -> list[int]:
    """Ports où adbd répond sur le téléphone : le port du débogage sans fil change quand adbd redémarre (câble
    retiré, veille), et One UI ne l'annonce pas en mDNS. Chaque port ouvert est vérifié par une poignée de main ADB
    (CNXN → CNXN, AUTH ou STLS) : jamais un adb connect de 8 s sur un autre service."""

    async def scan() -> list[int]:
        gate = asyncio.Semaphore(800)
        found: list[int] = []

        async def probe(port: int) -> None:
            async with gate:
                try:
                    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
                except (OSError, TimeoutError):
                    return
                try:
                    writer.write(adb_hello())
                    await asyncio.wait_for(writer.drain(), HANDSHAKE_WAIT)
                    if speaks_adb(await asyncio.wait_for(reader.readexactly(24), HANDSHAKE_WAIT)):
                        found.append(port)
                except (OSError, TimeoutError, asyncio.IncompleteReadError):
                    pass  # port muet ou fermé : ce n'est pas adbd, il n'est pas retenu
                finally:
                    writer.close()

        await asyncio.gather(*(probe(p) for p in ports))
        return sorted(found)

    return asyncio.run(scan())


class Adb:
    def __init__(self) -> None:
        self.path = _find_adb()
        self.forced: str | None = os.environ.get("NEXUS_ANDROID_SERIAL") or None  # appareil imposé à l'installation
        self.serial: str | None = self.forced
        self._ids: dict[str, str] = {}  # numéro adb → ro.serialno (identité réelle de l'appareil)
        self.rebooted = False  # le téléphone a redémarré depuis le dernier contact (à dire une fois, puis False)
        self._absent_until = 0.0  # heure (monotonic) avant laquelle la recherche Wi-Fi n'est pas refaite
        self.links = 0  # connexions réussies : un canal en échec est retenté sans attendre après une reconnexion

    def _raw(self, args: list[str], timeout: float = 30.0, serial: bool = True) -> subprocess.CompletedProcess[bytes]:
        cmd = [self.path]
        targeted = serial and self.serial
        if targeted:
            cmd += ["-s", str(self.serial)]
        try:
            return subprocess.run(cmd + args, capture_output=True, timeout=timeout,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as e:
            if targeted:
                self.lost()
            raise LinkError(f"adb {' '.join(args[:2])}: no response after {timeout:.0f} s.") from e

    def lost(self) -> None:
        """Délai dépassé sur le téléphone : la liaison est tenue pour coupée. Une liaison Wi-Fi est retirée d'adb (sinon
        il la garde « device » et chaque appel reprendrait le même délai) ; le téléphone est recherché au prochain
        appel."""
        stale, self.serial = self.serial, self.forced
        if stale and (":" in stale or "._adb-tls-connect." in stale):
            try:
                subprocess.run([self.path, "disconnect", stale], capture_output=True, timeout=5,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.TimeoutExpired):
                pass  # déconnexion de politesse : l'adresse périmée n'est de toute façon plus utilisée

    def devices(self) -> list[Device]:
        return parse_devices(self._raw(["devices", "-l"], serial=False).stdout.decode("utf-8", "replace"))

    def _online(self) -> list[Device]:
        """Appareils prêts, émulateurs exclus (jamais pris pour le téléphone de l'utilisateur)."""
        return [d for d in self.devices() if d.state == "device" and not d.serial.startswith("emulator-")]

    def _remembered(self) -> dict:
        try:
            return json.loads(WIFI_STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, state: dict) -> None:
        WIFI_STATE.parent.mkdir(parents=True, exist_ok=True)
        WIFI_STATE.write_text(json.dumps(state), encoding="utf-8")

    def identity(self, serial: str) -> str:
        """Numéro de série matériel (ro.serialno) de l'appareil joint sous ce numéro adb ; "" s'il ne répond pas."""
        if serial not in self._ids:
            try:
                out = self._raw(["-s", serial, "shell", "getprop", "ro.serialno"], timeout=8, serial=False)
            except PhoneError:
                return ""
            ident = out.stdout.decode("utf-8", "replace").strip() if not out.returncode else ""
            if not ident:
                return ""
            self._ids[serial] = ident
        return self._ids[serial]

    def _remember_wifi(self, serial: str, ident: str) -> None:
        """Branché en USB : note l'identité du téléphone, son adresse Wi-Fi et le port TLS d'adbd, que le téléphone
        (One UI) n'annonce pas en mDNS. Le port ne change qu'au redémarrage du débogage sans fil (reboot, autre
        réseau). Appelé seulement pour le téléphone de l'utilisateur, identité vérifiée."""
        run = lambda *a: self._raw(["-s", serial, "shell", *a], serial=False).stdout.decode("utf-8", "replace")  # noqa: E731
        before = self._remembered()
        state = {**before, "serialno": ident, "usb": serial}
        ip = wifi_ip(run("ip", "-4", "addr"))
        port = parse_adbd_port(run("cat", "/proc/net/tcp6", "/proc/net/tcp"))
        if ip and port:
            state["address"] = f"{ip}:{port}"
        if state != before:
            self._save(state)

    def _try_wifi(self, address: str, known: str) -> bool:
        """Se connecte à une adresse Wi-Fi ; garde la liaison seulement si c'est le téléphone de l'utilisateur."""
        try:
            out = self._raw(["connect", address], timeout=8, serial=False).stdout
        except PhoneError:  # adresse périmée (port changé) : ne pas empêcher d'essayer les suivantes
            self._raw(["disconnect", address], timeout=5, serial=False)
            return False
        if b"connected to" not in out:
            return False
        if self.identity(address) == known:
            return True
        self._raw(["disconnect", address], timeout=5, serial=False)  # autre appareil du réseau (box TV…)
        return False

    def connect(self, scan: bool = True) -> Device:
        """Choisit le téléphone de l'utilisateur : celui imposé, sinon USB, sinon Wi-Fi ; tente les téléphones jumelés du
        Wi-Fi (dernière adresse notée, puis annonce mDNS, puis balayage des ports si le PC est sur le même réseau).
        Seul l'appareil dont le numéro de série matériel est celui noté au premier branchement USB est accepté ;
        émulateurs et autres appareils du réseau sont écartés, et wifi.json n'est jamais réécrit avec un autre appareil.
        Recherche Wi-Fi vaine : rien n'est recherché à nouveau pendant ABSENT_TTL s (le câble reste vu tout de suite).
        scan=False (minuteur de l'écran gardé allumé) : jamais de balayage."""
        if self.forced:
            return self._connect_forced(self.forced)
        remembered = self._remembered()
        known = remembered.get("serialno") or remembered.get("usb") or ""  # "usb" : fichier d'avant l'identité
        mine = self._mine(self._online(), known)
        if not mine and any(d.state == "offline" for d in self.devices()):
            # Liaison Wi-Fi endormie (« device offline », vu sur le Fold) : la relancer avant de chercher ailleurs.
            self._raw(["reconnect", "offline"], timeout=10, serial=False)
            for _ in range(10):
                time.sleep(0.5)
                if mine := self._mine(self._online(), known):
                    break
        if not mine and known:
            mine = self._search_wifi(remembered, known, scan)
        if not mine:
            self.serial = None
            wait = self._absent_until - time.monotonic()
            again = f" Next Wi-Fi search in {wait:.0f} s (a USB cable is detected immediately)." if wait > 0 else ""
            raise PhoneError(self._missing(known) + again)
        self._absent_until = 0.0
        chosen = sorted(mine, key=lambda d: d.transport != "usb")[0]
        ident = self.identity(chosen.serial)
        if chosen.transport == "usb":
            try:
                self._remember_wifi(chosen.serial, ident)
            except (OSError, PhoneError):
                pass  # adresse Wi-Fi non mémorisée : l'USB marche, elle sera relue au prochain branchement
        elif re.fullmatch(r"\d+\.\d+\.\d+\.\d+:\d+", chosen.serial) and chosen.serial != remembered.get("address"):
            self._save({**remembered, "serialno": ident, "address": chosen.serial})  # port du débogage sans fil changé
        self.serial = chosen.serial
        self.links += 1
        self.note_boot()
        return chosen

    def _search_wifi(self, remembered: dict, known: str, scan: bool) -> list[Device]:
        """Téléphone de l'utilisateur sur le Wi-Fi : dernière adresse connue d'abord, puis mDNS, puis (scan, même
        réseau) les ports où adbd répond, MAX_TRIED au plus. Échec complet : gardé ABSENT_TTL s."""
        if time.monotonic() < self._absent_until:
            return []
        address = remembered.get("address") or ""
        if address and self._try_wifi(address, known):
            return self._mine(self._online(), known)
        mdns = self._raw(["mdns", "services"], timeout=10, serial=False).stdout.decode("utf-8", "replace")
        for candidate in dict.fromkeys(a for a in parse_mdns(mdns) if a != address):
            if self._try_wifi(candidate, known):
                return self._mine(self._online(), known)
        if not scan:
            return []  # échec partiel (sans balayage) : pas gardé, la prochaine vraie recherche balaiera
        if address:
            host = address.rsplit(":", 1)[0]
            if same_subnet(host):
                for port in [p for p in open_ports(host) if f"{host}:{p}" != address][:MAX_TRIED]:
                    if self._try_wifi(f"{host}:{port}", known):
                        return self._mine(self._online(), known)
        self._absent_until = time.monotonic() + ABSENT_TTL
        return []

    def note_boot(self, boot: str | None = None) -> None:
        """Redémarrage du téléphone depuis le dernier contact de ce PC : signalé une fois (rebooted). boot : identifiant
        déjà lu par l'appelant (phone, en un seul aller-retour), sinon lu ici."""
        if boot is None:
            try:
                boot = self.boot_id()
            except PhoneError:
                return
        boot = boot.strip()
        state = self._remembered()
        if not re.fullmatch(r"[0-9a-f-]{36}", boot) or boot == state.get("boot_id"):
            return
        self.rebooted = bool(state.get("boot_id"))
        self._save({**state, "boot_id": boot})

    def _mine(self, devices: list[Device], known: str) -> list[Device]:
        if known:
            return [d for d in devices if self.identity(d.serial) == known]
        # Jamais branché : le téléphone en USB devient celui de l'utilisateur (le câble est un geste volontaire).
        usb = [d for d in devices if d.transport == "usb" and self.identity(d.serial)]
        if len(usb) > 1:
            raise PhoneError("Several USB devices (" + ", ".join(d.model or d.serial for d in usb) + ") and no "
                             "phone registered yet: leave only the user's phone plugged in.")
        return usb

    def _missing(self, known: str) -> str:
        visible = self.devices()
        if any(d.state == "unauthorized" for d in visible):
            return UNAUTHORIZED
        if any(d.state == "no permissions" for d in visible):  # Linux sans règle udev pour ce téléphone
            return ("Phone plugged in but this computer's USB permissions block adb (\"no permissions\"): add the "
                    "Android udev rules (https://developer.android.com/studio/run/device#setting-up), then plug in "
                    "again.")
        others = [d.model or d.serial for d in visible if d.state == "device"]
        ignored = f" Visible devices ignored (not the user's phone): {', '.join(others)}." if others else ""
        if not known:
            return ("No phone set up on this PC yet. Guide the user through these steps (once per PC): " + FIRST_PLUG
                    + " It will then also be found over Wi-Fi once paired." + ignored)
        return (f"User's phone (serial number {known}) not found: plug in the USB cable, or enable wireless "
                "debugging (phone paired with this PC and on the same Wi-Fi). After a phone restart or a "
                "network change, Android turns wireless debugging off: turn it back on (Developer options) "
                "or plug in the cable once." + ignored)

    def _connect_forced(self, serial: str) -> Device:
        """NEXUS_ANDROID_SERIAL : appareil choisi explicitement à l'installation, pris tel quel."""
        devices = [d for d in self.devices() if d.state == "device"]
        if ":" in serial and all(d.serial != serial for d in devices):
            self._raw(["connect", serial], timeout=8, serial=False)  # adresse Wi-Fi imposée
            devices = [d for d in self.devices() if d.state == "device"]
        chosen = next((d for d in devices if d.serial == serial), None)
        if not chosen:
            raise PhoneError(f"Device set by NEXUS_ANDROID_SERIAL ({serial}) unreachable.")
        self.serial = serial
        self.links += 1
        return chosen

    def run(self, *argv: str, timeout: float = 30.0) -> tuple[int, str, str]:
        """Commande shell Android : (code, sortie, erreurs). Chaque argument est protégé (texte libre, accents)."""
        if not self.serial:
            self.connect()
        command = " ".join(shlex.quote(a) for a in argv)
        result = self._raw(["shell", command], timeout=timeout)
        out = result.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
        err = result.stderr.decode("utf-8", "replace").strip()
        if result.returncode and _LINK_LOST.search(err):
            self.serial = self.forced  # téléphone perdu : le rechercher au prochain appel
            raise LinkError(f"link to the phone lost ({err.splitlines()[0][:120]})")
        return result.returncode, out, err

    def host(self, *args: str, timeout: float = 60.0) -> str:
        """Commande adb côté PC sur le téléphone choisi (push, pull, forward) ; erreur → PhoneError."""
        if not self.serial:
            self.connect()
        result = self._raw(list(args), timeout=timeout)
        out = (result.stdout + result.stderr).decode("utf-8", "replace").strip()
        if result.returncode:
            if _LINK_LOST.search(out):
                self.serial = self.forced
                raise LinkError(f"link to the phone lost ({out.splitlines()[0][:120]})")
            raise PhoneError(f"adb {args[0]}: {out[:300]}")
        return out

    def boot_id(self) -> str:
        """Identifiant du démarrage en cours du téléphone (change à chaque redémarrage)."""
        return self.shell("cat", "/proc/sys/kernel/random/boot_id", check=False, timeout=STATE_WAIT).strip()

    def shell(self, *argv: str, timeout: float = 30.0, check: bool = True) -> str:
        """Commande shell Android ; chaque argument est protégé (texte libre, accents, retours à la ligne)."""
        code, out, err = self.run(*argv, timeout=timeout)
        if code and check:
            raise PhoneError(f"{argv[0]}: {(err or out).strip()[:300]}")
        return out

    def background(self, command: str) -> subprocess.Popen[bytes]:
        """Commande shell Android laissée tourner (enregistrement de l'écran) : sortie et erreurs mêlées, lues par
        l'appelant ; sans fenêtre."""
        if not self.serial:
            self.connect()
        return subprocess.Popen([self.path, "-s", str(self.serial), "shell", command], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def exec_out(self, *argv: str, timeout: float = 30.0) -> bytes:
        if not self.serial:
            self.connect()
        result = self._raw(["exec-out", " ".join(shlex.quote(a) for a in argv)], timeout=timeout)
        if result.returncode:
            err = result.stderr.decode("utf-8", "replace").strip()
            if _LINK_LOST.search(err):
                self.serial = self.forced
                raise LinkError(f"link to the phone lost ({err.splitlines()[0][:120]})")
            raise PhoneError(err[:300] or "exec-out failed")
        return result.stdout
