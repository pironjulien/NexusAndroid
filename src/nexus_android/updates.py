"""Mises à jour faites par le serveur lui-même, hors de tout conteneur (contrat commun des produits Nexus) : au
démarrage, en arrière-plan et au plus une fois par jour, dernière version sur PyPI, Platform Tools à nous remises à
jour, et état écrit dans ~/.nexus/status/android.json pour l'appli Nexus.

Le serveur lancé par une IA (`uvx nexus-android`, sans version) prend la nouvelle version au prochain
démarrage de l'IA : ici, `uvx nexus-android@latest` la prépare dans le cache de uv. Hors ligne, uvx garde la version
en cache. NEXUS_ANDROID_UPDATE=0 : aucune requête, l'état dit seulement la version en cours."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, platformtools

PYPI = "https://pypi.org/pypi/nexus-android/json"
DAY = 24 * 3600.0
TIMEOUT = 15.0
DISABLED = os.environ.get("NEXUS_ANDROID_UPDATE", "1").strip().lower() in {"0", "off", "false", "no"}
# Version tenue par un autre (plugin d'un catalogue, épinglé) : l'état dit qui la met à jour, rien n'est préparé.
MANAGED = os.environ.get("NEXUS_ANDROID_MANAGED", "").strip()


def status_file() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home()) / ".nexus" / "status" / "android.json"


def memo_file() -> Path:
    return Path.home() / ".nexus" / "nexus-android" / "update.json"


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


def write_status(state: str, latest: str | None, message: str) -> None:
    path = status_file()
    data = {"product": "android", "version": __version__, "latest": latest or __version__, "state": state,
            "checked_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "message": message}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)  # l'appli ne lit jamais un fichier à moitié écrit
    except OSError:
        pass  # l'état est une information : jamais une raison d'empêcher le serveur de démarrer


def _memo() -> dict:
    try:
        data = json.loads(memo_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def latest_on_pypi() -> str:
    with urllib.request.urlopen(PYPI, timeout=TIMEOUT) as response:
        return str(json.loads(response.read(4 * 1024 * 1024))["info"]["version"])


def via_uvx() -> bool:
    """Serveur lancé par uvx (environnement dans le cache de uv) : la mise à jour passe par uvx. Installé autrement
    (install.ps1, extension Claude Desktop épinglée, pip) : c'est à son installeur de la faire."""
    prefix = Path(sys.prefix).as_posix().lower()
    return "/archive-v0/" in prefix or "/uv/cache/" in prefix or "/.cache/uv/" in prefix


def prepare(version: str) -> str | None:
    """Pose `version` dans le cache de uv pour le prochain lancement par l'IA ; rend un message d'échec, ou None."""
    uvx = shutil.which("uvx")
    if not uvx:
        return "uvx not found on the PATH"
    try:
        done = subprocess.run([uvx, f"nexus-android@{version}", "--version"], capture_output=True,
                              text=True, timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"uvx failed ({e})"
    if done.returncode or version not in done.stdout:
        return f"uvx failed ({(done.stderr or done.stdout).strip()[-200:]})"
    return None


def check(now: float | None = None) -> None:
    """Un passage complet ; chaque échec garde la version en cours et l'écrit dans l'état."""
    now = time.time() if now is None else now
    if DISABLED:
        write_status("idle", None, f"nexus-android {__version__}; update checks turned off (NEXUS_ANDROID_UPDATE=0)")
        return
    memo = _memo()
    if now - float(memo.get("checked", 0)) < DAY and memo.get("latest"):
        latest = str(memo["latest"])
    else:
        # Pas d'état « checking » écrit : un serveur arrêté pendant la vérification (client fermé aussitôt) le laissait
        # figé ; l'état précédent reste en place jusqu'au résultat, d'une seconde le plus souvent.
        try:
            latest = latest_on_pypi()
        except (OSError, ValueError, KeyError) as e:
            write_status("failed", memo.get("latest"), f"update check failed, nexus-android {__version__} kept ({e})")
            return
        notes = []
        try:
            if fresh := platformtools.refresh():
                notes.append(f"Platform Tools {fresh} installed")
        except Exception as e:  # noqa: BLE001 - adb en place gardé, quoi qu'il arrive au téléchargement
            notes.append(f"Platform Tools update failed ({e})")
        memo = {"checked": now, "latest": latest, "notes": notes}
        try:
            memo_file().parent.mkdir(parents=True, exist_ok=True)
            memo_file().write_text(json.dumps(memo), encoding="utf-8")
        except OSError:
            pass
    notes = "; ".join(memo.get("notes") or [])
    extra = f"; {notes}" if notes else ""
    if _version(latest) <= _version(__version__):  # version en cours plus récente que PyPI (pas encore servie) : elle
        write_status("idle", __version__, f"nexus-android {__version__} is up to date{extra}")
        return
    if MANAGED:
        write_status("idle", latest, f"nexus-android {latest} available; updated with the {MANAGED}{extra}")
        return
    if not via_uvx():
        write_status("idle", latest, f"nexus-android {latest} available; run its installer again to update{extra}")
        return
    write_status("updating", latest, f"preparing nexus-android {latest}")
    failure = prepare(latest)
    if failure:
        write_status("failed", latest, f"nexus-android {latest} not prepared, {__version__} kept: {failure}{extra}")
    else:
        write_status("done", latest, f"nexus-android {latest} ready; used from the next start of the AI app{extra}")


def start() -> None:
    """Au démarrage du serveur : en arrière-plan, jamais bloquant, jamais fatal."""
    def run() -> None:
        try:
            check()
        except Exception as e:  # noqa: BLE001
            write_status("failed", None, f"update check failed ({e})")

    threading.Thread(target=run, name="nexus-android-updates", daemon=True).start()
