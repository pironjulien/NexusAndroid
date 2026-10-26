"""`nexus-android mirror` : écran du téléphone sur le PC par scrcpy (installé par l'utilisateur), avec le même adb
que le serveur. Deux adb de versions différentes se relancent l'un l'autre et coupent la liaison des agents ; scrcpy
prend celui de la variable ADB. Aucune appli installée sur le téléphone (scrcpy y dépose seulement son serveur)."""

from __future__ import annotations

import os
import shutil
import subprocess

from .adb import PhoneError

INSTALL = ("scrcpy is not installed. Install it once (Windows: winget install Genymobile.scrcpy; macOS: brew install "
           "scrcpy; Linux: your package manager), or see https://github.com/Genymobile/scrcpy#get-the-app, then run "
           "nexus-android mirror again.")


def scrcpy() -> str | None:
    """scrcpy : NEXUS_ANDROID_SCRCPY, sinon PATH (winget y place son lien)."""
    explicit = os.environ.get("NEXUS_ANDROID_SCRCPY")
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    return shutil.which("scrcpy")


def run(argv: list[str]) -> int:
    """Lance scrcpy au premier plan (ses options passent telles quelles) ; rend son code de sortie."""
    exe = scrcpy()
    if exe is None:
        print(INSTALL)
        return 1
    from . import platformtools  # importé ici : platformtools dépend de PhoneError

    try:
        adb = platformtools.find(print)
    except PhoneError as error:
        print(error)
        return 1
    try:
        return subprocess.call([exe, *argv], env={**os.environ, "ADB": adb})
    except KeyboardInterrupt:
        return 0
