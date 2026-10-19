"""Liaison ADB avec le téléphone : choix de l'appareil (USB d'abord, puis Wi-Fi jumelé), commandes shell, captures."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


class PhoneError(RuntimeError):
    """Erreur à rapporter telle quelle à l'agent (message en français, sans pile)."""


class LinkError(PhoneError):
    """Liaison perdue en cours de commande (délai dépassé, téléphone parti) : la commande a pu s'exécuter ou non.
    Un geste ainsi interrompu a un effet inconnu ; il se relit avant d'être refait, jamais répété à l'aveugle."""


# Erreurs d'adb sur l'appareil lui-même, pas sur la commande (« sh: foo: not found » n'en est pas une).
_LINK_LOST = re.compile(r"^(?:adb(?:\.exe)?: )?(?:error: )?(?:device .*not found|device offline|no devices|closed|"
                        r"protocol fault)", re.I | re.M)

def _find_adb() -> str:
    explicit = os.environ.get("NEXUS_ANDROID_ADB")
    if explicit and Path(explicit).is_file():
        return explicit
    found = shutil.which("adb")
    if found:
        return found
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    for candidate in winget.glob("Google.PlatformTools_*/platform-tools/adb.exe"):
        return str(candidate)
    raise PhoneError("adb introuvable : installer les Platform Tools (winget install Google.PlatformTools).")


@dataclass(frozen=True)
class Device:
    serial: str
    state: str
    model: str
    transport: str  # "usb" ou "wifi"


def parse_devices(output: str) -> list[Device]:
    devices = []
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        model = next((p.split(":", 1)[1] for p in parts[2:] if p.startswith("model:")), "")
        wifi = ":" in serial or "._adb-tls-connect." in serial
        devices.append(Device(serial, state, model.replace("_", "-"), "wifi" if wifi else "usb"))
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


def open_ports(host: str, ports: range = TLS_PORTS, timeout: float = 0.4) -> list[int]:
    """Ports TCP ouverts sur le téléphone : le port du débogage sans fil change quand adbd redémarre (câble
    retiré, veille), et One UI ne l'annonce pas en mDNS."""

    async def scan() -> list[int]:
        gate = asyncio.Semaphore(800)
        found: list[int] = []

        async def probe(port: int) -> None:
            async with gate:
                try:
                    _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
                    found.append(port)
                    writer.close()
                except (OSError, TimeoutError):
                    pass

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

    def _raw(self, args: list[str], timeout: float = 30.0, serial: bool = True) -> subprocess.CompletedProcess[bytes]:
        cmd = [self.path]
        if serial and self.serial:
            cmd += ["-s", self.serial]
        try:
            return subprocess.run(cmd + args, capture_output=True, timeout=timeout,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as e:
            raise LinkError(f"adb {' '.join(args[:2])} : pas de réponse après {timeout:.0f} s.") from e

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

    def connect(self) -> Device:
        """Choisit le téléphone de l'utilisateur : celui imposé, sinon USB, sinon Wi-Fi ; tente les téléphones jumelés du
        Wi-Fi (annonce mDNS, puis dernière adresse notée lors d'un branchement USB). Seul l'appareil dont le numéro
        de série matériel est celui noté au premier branchement USB est accepté ; émulateurs et autres appareils
        du réseau sont écartés, et wifi.json n'est jamais réécrit avec un autre appareil."""
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
            mdns =self._raw(["mdns", "services"], timeout=10, serial=False).stdout.decode("utf-8", "replace")
            for address in dict.fromkeys(parse_mdns(mdns) + ([remembered["address"]] if remembered.get("address") else [])):
                if self._try_wifi(address, known):
                    break
            mine = self._mine(self._online(), known)
            if not mine and remembered.get("address"):
                host = remembered["address"].rsplit(":", 1)[0]
                for port in open_ports(host):
                    if self._try_wifi(f"{host}:{port}", known):
                        break
                mine = self._mine(self._online(), known)
        if not mine:
            self.serial = None
            raise PhoneError(self._missing(known))
        chosen = sorted(mine, key=lambda d: d.transport != "usb")[0]
        ident = self.identity(chosen.serial)
        if chosen.transport == "usb":
            try:
                self._remember_wifi(chosen.serial, ident)
            except (OSError, PhoneError):
                pass
        elif re.fullmatch(r"\d+\.\d+\.\d+\.\d+:\d+", chosen.serial) and chosen.serial != remembered.get("address"):
            self._save({**remembered, "serialno": ident, "address": chosen.serial})  # port du débogage sans fil changé
        self.serial = chosen.serial
        self._note_boot()
        return chosen

    def _note_boot(self) -> None:
        """Redémarrage du téléphone depuis le dernier contact de ce PC : signalé une fois (rebooted)."""
        try:
            boot = self.boot_id()
        except PhoneError:
            return
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
            raise PhoneError("Plusieurs appareils en USB (" + ", ".join(d.model or d.serial for d in usb) + ") et aucun "
                             "téléphone encore enregistré : ne laisser branché que le téléphone de l'utilisateur.")
        return usb

    def _missing(self, known: str) -> str:
        visible = self.devices()
        if any(d.state == "unauthorized" for d in visible):
            return "Téléphone branché mais non autorisé : accepter « Autoriser le débogage » sur l'écran."
        others = [d.model or d.serial for d in visible if d.state == "device"]
        ignored = f" Appareils visibles écartés (pas le téléphone de l'utilisateur) : {', '.join(others)}." if others else ""
        if not known:
            return ("Téléphone de l'utilisateur pas encore enregistré sur ce PC : le brancher une fois en USB (débogage USB "
                    "autorisé) ; il sera ensuite retrouvé aussi en Wi-Fi." + ignored)
        return (f"Téléphone de l'utilisateur (n° de série {known}) introuvable : brancher le câble USB, ou activer le débogage "
                "sans fil (téléphone jumelé à ce PC et sur le même Wi-Fi). Après un redémarrage du téléphone ou un "
                "changement de réseau, Android coupe le débogage sans fil : le rallumer (Options de développement) "
                "ou brancher le câble une fois." + ignored)

    def _connect_forced(self, serial: str) -> Device:
        """NEXUS_ANDROID_SERIAL : appareil choisi explicitement à l'installation, pris tel quel."""
        devices = [d for d in self.devices() if d.state == "device"]
        if ":" in serial and all(d.serial != serial for d in devices):
            self._raw(["connect", serial], timeout=8, serial=False)  # adresse Wi-Fi imposée
            devices = [d for d in self.devices() if d.state == "device"]
        chosen = next((d for d in devices if d.serial == serial), None)
        if not chosen:
            raise PhoneError(f"Appareil imposé par NEXUS_ANDROID_SERIAL ({serial}) injoignable.")
        self.serial = serial
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
            raise LinkError(f"liaison perdue avec le téléphone ({err.splitlines()[0][:120]})")
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
                raise LinkError(f"liaison perdue avec le téléphone ({out.splitlines()[0][:120]})")
            raise PhoneError(f"adb {args[0]} : {out[:300]}")
        return out

    def boot_id(self) -> str:
        """Identifiant du démarrage en cours du téléphone (change à chaque redémarrage)."""
        return self.shell("cat", "/proc/sys/kernel/random/boot_id", check=False).strip()

    def shell(self, *argv: str, timeout: float = 30.0, check: bool = True) -> str:
        """Commande shell Android ; chaque argument est protégé (texte libre, accents, retours à la ligne)."""
        code, out, err = self.run(*argv, timeout=timeout)
        if code and check:
            raise PhoneError(f"{argv[0]} : {(err or out).strip()[:300]}")
        return out

    def exec_out(self, *argv: str, timeout: float = 30.0) -> bytes:
        if not self.serial:
            self.connect()
        result = self._raw(["exec-out", " ".join(shlex.quote(a) for a in argv)], timeout=timeout)
        if result.returncode:
            err = result.stderr.decode("utf-8", "replace").strip()
            if _LINK_LOST.search(err):
                self.serial = self.forced
                raise LinkError(f"liaison perdue avec le téléphone ({err.splitlines()[0][:120]})")
            raise PhoneError(err[:300] or "exec-out a échoué")
        return result.stdout
