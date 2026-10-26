"""`nexus-android setup` : premier branchement guidé dans un terminal. adb trouvé ou téléchargé, serveur déclaré aux IA
trouvées (register.py), puis le pas-à-pas du débogage USB affiché et suivi jusqu'à ce que le téléphone réponde ; le
téléphone est alors enregistré pour ce PC.

Pack Nexus (NEXUS_PACK=1, ou --no-wait) : aucune question ni attente du téléphone, et une dernière ligne
`NEXUS_PACK_RESULT Client=état; …`. --no-register : aucune IA touchée ; --apps claude-code,codex (ou NEXUS_PACK_APPS) :
seules ces IA. Code de sortie : 0 tout est prêt, 1 adb ou téléphone pas prêt, 2 une IA pas configurée, 3 les deux."""

from __future__ import annotations

import os
import re
import time

from . import __version__, platformtools, register
from .adb import FIRST_PLUG, UNAUTHORIZED, Adb, PhoneError

POLL = 2.0  # s entre deux relectures d'adb devices
WAIT = 900.0  # s au plus (15 min) pour activer le débogage et brancher le téléphone
PHONE_FAILED = 1  # adb introuvable, ou téléphone pas connecté
APP_FAILED = 2  # au moins une IA pas configurée


def _steps(text: str) -> str:
    """Pas-à-pas d'une ligne → une étape par ligne, pour le terminal."""
    return "\n".join("  " + part for part in re.split(r" (?=\d\. )", text))


def _short(result: str) -> str:
    """État d'un client pour la ligne NEXUS_PACK_RESULT."""
    for prefix, short in (("failed", "failed"), ("kept", "kept"), ("provided by", "plugin"), ("plugin", "plugin")):
        if result.startswith(prefix):
            return short
    return "declared"


def _apps(argv: list[str]) -> set[str] | None:
    """Périmètre d'IA : --apps, sinon NEXUS_PACK_APPS (pack, -Only/-Skip), sinon toutes (None)."""
    scope = os.environ.get("NEXUS_PACK_APPS", "")
    if "--apps" in argv:
        index = argv.index("--apps")
        scope = argv[index + 1] if index + 1 < len(argv) else ""
    return register.parse_apps(scope) if scope.strip() else None


def run(argv: list[str] | None = None, wait: float = WAIT) -> int:
    argv = argv or []
    pack = os.environ.get("NEXUS_PACK", "").strip().lower() in {"1", "true", "yes"}
    no_wait = pack or "--no-wait" in argv
    try:
        apps = _apps(argv)
    except ValueError as e:
        print(f"setup: {e}")
        return APP_FAILED
    print(f"NexusAndroid {__version__}: setup on this PC" + ("" if no_wait else " (Ctrl+C to stop)") + ".\n", flush=True)
    try:
        path = platformtools.find(say=lambda text: print(text, flush=True))
    except PhoneError as e:
        print(e)
        if pack:
            print("NEXUS_PACK_RESULT adb=failed")
        return PHONE_FAILED
    print(f"adb: {path}\n")
    failed: list[str] = []
    if "--no-register" not in argv:
        results = register.declare_all(apps)
        for client, result in results.items():
            print(f"{client}: {result}")
        print("Restart each AI app to load nexus-android.\n" if results else
              "No AI client found (Claude, Codex, Antigravity): install one, then run setup again.\n")
        failed = [client for client, result in results.items() if result.startswith("failed")]
        if pack:
            print("NEXUS_PACK_RESULT " + "; ".join(f"{c}={_short(r)}" for c, r in results.items()), flush=True)
    apps_code = APP_FAILED if failed else 0
    apps_line = f"AI apps NOT configured: {', '.join(failed)} (see above); fix it, then run setup again."
    if no_wait:
        if not pack:
            print("Phone not checked (--no-wait): run `uvx nexus-android setup` to connect it.")
            if failed:
                print(apps_line)
        return apps_code
    adb = Adb()
    shown = ""
    deadline = time.monotonic() + wait
    try:
        while True:
            try:
                device = adb.connect(scan=False)
                break
            except PhoneError as e:
                states = {d.state for d in adb.devices()}
                if "unauthorized" in states:
                    message = UNAUTHORIZED
                elif not states:
                    message = "Waiting for the phone. Steps, once per PC:\n" + _steps(FIRST_PLUG)
                else:
                    message = str(e)
                if message != shown:
                    print(message + "\n")
                    shown = message
            if time.monotonic() > deadline:
                print("Phone NOT connected after 15 minutes: run `nexus-android setup` again when it is plugged in.")
                if failed:
                    print(apps_line)
                return PHONE_FAILED | apps_code
            time.sleep(POLL)
    except KeyboardInterrupt:
        print("\nStopped: phone NOT connected." + (f" {apps_line}" if failed else ""))
        return PHONE_FAILED | apps_code
    print(f"Phone connected: {device.model or device.serial} ({device.transport.upper()}), registered on this PC.")
    if device.transport == "usb":
        print("Without the cable: Developer options > Wireless debugging > Pair device with pairing code, then "
              f"\"{path}\" pair <ip>:<port> with the code shown (once per PC, same Wi-Fi).")
    if failed:
        print(apps_line)
        return apps_code
    print("Ready. Now ask your AI: \"Look at my phone and tell me what is on the screen.\"")
    return 0
