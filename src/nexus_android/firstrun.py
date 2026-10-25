"""`nexus-android setup` : premier branchement guidé dans un terminal. adb trouvé ou téléchargé, serveur déclaré aux IA
trouvées (register.py), puis le pas-à-pas du débogage USB affiché et suivi jusqu'à ce que le téléphone réponde ; le
téléphone est alors enregistré pour ce PC.

Pack Nexus (NEXUS_PACK=1, ou --no-wait) : aucune question ni attente du téléphone ; code 0 si adb est prêt et aucune
déclaration n'a échoué, et une dernière ligne `NEXUS_PACK_RESULT Client=état; …`. --no-register : aucune IA touchée."""

from __future__ import annotations

import os
import re
import time

from . import __version__, platformtools, register
from .adb import FIRST_PLUG, UNAUTHORIZED, Adb, PhoneError

POLL = 2.0  # s entre deux relectures d'adb devices
WAIT = 900.0  # s au plus (15 min) pour activer le débogage et brancher le téléphone


def _steps(text: str) -> str:
    """Pas-à-pas d'une ligne → une étape par ligne, pour le terminal."""
    return "\n".join("  " + part for part in re.split(r" (?=\d\. )", text))


def _short(result: str) -> str:
    """État d'un client pour la ligne NEXUS_PACK_RESULT."""
    for prefix, short in (("failed", "failed"), ("kept", "kept"), ("provided by", "plugin"), ("plugin", "plugin")):
        if result.startswith(prefix):
            return short
    return "declared"


def run(argv: list[str] | None = None, wait: float = WAIT) -> int:
    argv = argv or []
    pack = os.environ.get("NEXUS_PACK", "").strip().lower() in {"1", "true", "yes"}
    no_wait = pack or "--no-wait" in argv
    print(f"NexusAndroid {__version__}: setup on this PC" + ("" if no_wait else " (Ctrl+C to stop)") + ".\n", flush=True)
    try:
        path = platformtools.find(say=lambda text: print(text, flush=True))
    except PhoneError as e:
        print(e)
        if pack:
            print("NEXUS_PACK_RESULT adb=failed")
        return 1
    print(f"adb: {path}\n")
    failed = False
    if "--no-register" not in argv:
        results = register.declare_all()
        for client, result in results.items():
            print(f"{client}: {result}")
        print("Restart each AI app to load nexus-android.\n" if results else
              "No AI client found (Claude, Codex, Antigravity): install one, then run setup again.\n")
        failed = any(r.startswith("failed") for r in results.values())
        if pack:
            print("NEXUS_PACK_RESULT " + "; ".join(f"{c}={_short(r)}" for c, r in results.items()), flush=True)
    if no_wait:
        if not pack:
            print("Phone not checked (--no-wait): run `uvx --python 3.14 nexus-android setup` to connect it.")
        return 1 if failed else 0
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
                print("No phone after 15 minutes: run `nexus-android setup` again when it is plugged in.")
                return 1
            time.sleep(POLL)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 1
    print(f"Phone ready: {device.model or device.serial} ({device.transport.upper()}), registered on this PC.")
    print("Now ask your AI: \"Look at my phone and tell me what is on the screen.\"")
    if device.transport == "usb":
        print("Without the cable: Developer options > Wireless debugging > Pair device with pairing code, then "
              f"\"{path}\" pair <ip>:<port> with the code shown (once per PC, same Wi-Fi).")
    return 0
