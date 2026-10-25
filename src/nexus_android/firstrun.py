"""`nexus-android setup` : premier branchement guidé dans un terminal. adb trouvé ou téléchargé, puis le pas-à-pas du
débogage USB affiché et suivi jusqu'à ce que le téléphone réponde ; le téléphone est alors enregistré pour ce PC."""

from __future__ import annotations

import re
import time

from . import __version__, platformtools
from .adb import FIRST_PLUG, UNAUTHORIZED, Adb, PhoneError

POLL = 2.0  # s entre deux relectures d'adb devices
WAIT = 900.0  # s au plus (15 min) pour activer le débogage et brancher le téléphone


def _steps(text: str) -> str:
    """Pas-à-pas d'une ligne → une étape par ligne, pour le terminal."""
    return "\n".join("  " + part for part in re.split(r" (?=\d\. )", text))


def run(wait: float = WAIT) -> int:
    print(f"NexusAndroid {__version__}: phone setup on this PC (Ctrl+C to stop).\n")
    try:
        path = platformtools.find(say=print)
    except PhoneError as e:
        print(e)
        return 1
    print(f"adb: {path}\n")
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
