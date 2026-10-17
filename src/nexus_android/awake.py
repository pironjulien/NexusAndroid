"""Écran gardé allumé pendant le pilotage : au premier geste sur un téléphone déverrouillé, la mise en veille
(screen_off_timeout) est montée à 30 min ; après IDLE secondes sans geste d'aucun agent, la valeur d'origine est
remise et l'écran éteint (ce qui reverrouille). Jamais de déverrouillage : c'est toujours l'utilisateur qui le fait.

L'état est commun aux serveurs des trois agents (AWAKE_STATE, hors des sources) et ne se lit ni ne s'écrit que sous
PhoneLock : une seule sauvegarde de la valeur d'origine, une seule restauration. Après un plantage, le prochain serveur
qui démarre restaure la valeur d'origine dès que la session a expiré."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .adb import Adb, PhoneError

AWAKE_STATE = Path.home() / ".nexus" / "nexus-android" / "awake.json"
RAISED = 30 * 60 * 1000  # ms : écran gardé allumé pendant le travail de l'agent
IDLE = float(os.environ.get("NEXUS_ANDROID_AWAKE_IDLE") or 120)  # secondes sans geste avant de rendre la main
ENABLED = os.environ.get("NEXUS_ANDROID_KEEP_AWAKE", "1") != "0"
OWNER_MARGIN = 5.0  # s : activité plus récente que le dernier geste de l'agent = l'utilisateur utilise le téléphone


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, path)


def _timeout(adb: Adb) -> int | None:
    value = adb.shell("settings", "get", "system", "screen_off_timeout", check=False).strip()
    return int(value) if value.isdigit() else None


def idle_on_phone(adb: Adb) -> float | None:
    """Secondes depuis la dernière activité sur le téléphone (toucher de l'utilisateur ou geste injecté), d'après dumpsys."""
    match = re.search(r"^\s*lastUserActivityTime=\d+ \((\d+) ms ago\)", adb.shell("dumpsys", "power", check=False), re.M)
    return int(match.group(1)) / 1000 if match else None


class KeepAwake:
    """À appeler sous PhoneLock (état partagé entre processus)."""

    def __init__(self, adb: Adb, path: Path | None = None, idle: float | None = None,
                 clock=time.time) -> None:
        self.adb = adb
        self.path = path or AWAKE_STATE
        self.idle = IDLE if idle is None else idle
        self.clock = clock

    def state(self) -> dict:
        return _load(self.path)

    def active_until(self) -> float | None:
        """Heure (epoch) à laquelle l'écran sera rendu, si une session est en cours."""
        state = self.state()
        return state["last"] + self.idle if state.get("active") else None

    def _phone(self) -> str:
        serial = getattr(self.adb, "serial", None)
        return self.adb.identity(serial) if serial else ""

    def touch(self) -> None:
        """Geste sur un téléphone déverrouillé : ouvre la session (sauvegarde puis montée) ou la prolonge."""
        state = self.state()
        now = self.clock()
        if state.get("active"):
            if _timeout(self.adb) != RAISED:  # remise à zéro entre-temps (utilisateur, redémarrage) : la remonter
                self.adb.shell("settings", "put", "system", "screen_off_timeout", str(RAISED))
            _save(self.path, {**state, "last": now, "pid": os.getpid()})
            return
        current = _timeout(self.adb)
        if current is None:
            raise PhoneError("mise en veille illisible (settings) : écran non gardé allumé")
        # Jamais la valeur montée prise pour l'origine (session perdue) : dernière origine connue, sinon 30 s.
        original = current if current != RAISED else int(state.get("original") or 30000)
        _save(self.path, {"active": True, "original": original, "phone": self._phone(), "start": now, "last": now,
                          "pid": os.getpid()})
        self.adb.shell("settings", "put", "system", "screen_off_timeout", str(RAISED))

    def extend(self) -> None:
        """Lecture de l'écran (look) pendant une session : la prolonge sans en ouvrir une."""
        state = self.state()
        if state.get("active"):
            _save(self.path, {**state, "last": self.clock()})

    def due(self) -> float | None:
        """Secondes avant la restauration (0 : à faire maintenant) ; None : aucune session."""
        until = self.active_until()
        return None if until is None else max(0.0, until - self.clock())

    def restore(self, force: bool = False) -> str | None:
        """Session expirée (ou force) : remet la valeur d'origine puis éteint l'écran, sauf si l'utilisateur utilise le
        téléphone. Renvoie ce qui a été fait, None si rien à faire."""
        state = self.state()
        if not state.get("active"):
            return None
        idle_for = self.clock() - state["last"]
        if not force and idle_for < self.idle:
            return None
        if state.get("phone") and self._phone() and state["phone"] != self._phone():
            return None  # autre appareil joint : rien à toucher sur celui-ci
        current = _timeout(self.adb)
        done = []
        if current == RAISED:
            self.adb.shell("settings", "put", "system", "screen_off_timeout", str(state["original"]))
            done.append(f"mise en veille remise à {state['original'] // 1000} s")
        else:
            done.append("mise en veille déjà changée sur le téléphone, laissée telle quelle")
        _save(self.path, {"active": False, "original": state["original"], "phone": state.get("phone", ""),
                          "restored": self.clock()})
        phone_idle = idle_on_phone(self.adb)
        if current == RAISED and (phone_idle is None or phone_idle >= idle_for - OWNER_MARGIN):
            self.adb.shell("input", "keyevent", "KEYCODE_SLEEP")
            done.append("écran éteint")
        else:
            done.append("écran laissé à l'utilisateur (il s'en sert)")
        return ", ".join(done)
