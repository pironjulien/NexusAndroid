"""Routines : une suite de gestes réussie, enregistrée sous un nom et rejouée plus tard, par n'importe lequel des trois
agents (fichier commun sur le PC, comme remember/routine de NexusWindows).

Les cibles sont enregistrées par des repères stables, jamais par [N] (numéro d'un seul look) ni par coordonnées
(qui changent avec le pliage, la police, la langue) : l'identifiant de ressource de l'élément (« id:send_button »)
quand il est unique à l'écran, sinon son texte, sinon sa description d'accessibilité (« desc:Envoyer »). Un texte
tapé peut être un paramètre {{x}}, fourni au rejeu (params={"x": "..."}). Les garde-fous s'appliquent au rejeu
comme à un geste ordinaire : confirm et accord ne sont jamais enregistrés."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .adb import PhoneError

ROUTINES = Path.home() / ".nexus" / "nexus-android" / "routines.json"
NAME = re.compile(r"[\w][\w .-]{0,59}")
PARAM = re.compile(r"\{\{(\w{1,32})\}\}")
_LOCK = threading.Lock()


def anchor(element: Any, fresh: list[Any]) -> str | None:
    """Repère stable d'un élément dans l'écran `fresh` : id: si unique, sinon texte unique, sinon desc: unique."""
    if element is None:
        return None
    if element.rid and sum(1 for e in fresh if e.rid == element.rid) == 1:
        return f"id:{element.rid}"
    if element.text and sum(1 for e in fresh if e.text == element.text) == 1:
        return element.text
    if element.desc and sum(1 for e in fresh if e.desc == element.desc) == 1:
        return f"desc:{element.desc}"
    return None


def find(target: str, fresh: list[Any]) -> Any | None:
    """Élément désigné par un repère id:… ou desc:… (identifiant complet « paquet:id/nom » ou seulement « nom »)."""
    kind, _, value = target.partition(":")
    value = value.strip()
    if kind == "id":
        short = value.rsplit("/", 1)[-1]
        found = [e for e in fresh if e.rid in (value, short)]
    else:
        found = [e for e in fresh if e.desc == value] or [e for e in fresh if e.desc.casefold() == value.casefold()]
    return sorted(found, key=lambda e: not e.clickable)[0] if found else None


def _load() -> dict[str, Any]:
    try:
        return json.loads(ROUTINES.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise PhoneError(f"routines file unreadable ({ROUTINES}): {e}") from None


def _save(data: dict[str, Any]) -> None:
    ROUTINES.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ROUTINES.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, ROUTINES)  # atomique : un autre agent ne lit jamais un fichier à moitié écrit
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def check_name(name: str) -> str:
    name = name.strip()
    if not NAME.fullmatch(name):
        raise PhoneError("routine name: letters, digits, space, . _ - (60 at most)")
    return name


def remember(name: str, steps: list[dict[str, Any]], package: str, agent: str = "") -> None:
    name = check_name(name)
    for step in steps:
        text = step.get("text") or ""
        if PARAM.search(text) and PARAM.sub("", text).strip() and step.get("do") == "type":
            raise PhoneError("a typed text with {{x}} must be made only of parameters (\"{{message}}\"), so that "
                             "nothing personal stays in the routine")
    with _LOCK:
        data = _load()
        data[name] = {"package": package, "steps": steps, "saved": datetime.now().isoformat(timespec="seconds"),
                      **({"by": agent} if agent else {})}
        _save(data)


def forget(name: str) -> bool:
    with _LOCK:
        data = _load()
        found = data.pop(check_name(name), None) is not None
        if found:
            _save(data)
        return found


def get(name: str) -> dict[str, Any]:
    routine = _load().get(check_name(name))
    if routine is None:
        known = ", ".join(sorted(_load())) or "none"
        raise PhoneError(f"no routine named \"{name}\" (known: {known})")
    return routine


def fill(steps: list[dict[str, Any]], params: dict[str, str]) -> list[dict[str, Any]]:
    """Étapes d'une routine avec ses paramètres {{x}} remplacés ; un paramètre manquant est refusé avant tout geste."""
    missing = sorted({m for s in steps for m in PARAM.findall(s.get("text") or "")} - set(params))
    if missing:
        raise PhoneError(f"routine parameters missing: {', '.join(missing)} (params={{\"{missing[0]}\": \"...\"}})")
    safe = [{k: v for k, v in s.items() if k not in ("confirm", "accord")} for s in steps]  # fichier modifié à la main
    return [{**s, **({"text": PARAM.sub(lambda m: params[m.group(1)], s["text"])} if s.get("text") else {})}
            for s in safe]


def for_app(package: str) -> list[str]:
    """Noms des routines enregistrées dans cette application (fichier lu une seule fois)."""
    return sorted(name for name, routine in _load().items() if routine.get("package") == package)


def listing(package: str | None = None) -> list[str]:
    """Noms des routines (celles de l'application au premier plan d'abord quand package est donné)."""
    data = _load()
    names = sorted(data)
    if package:
        names.sort(key=lambda n: data[n].get("package") != package)
    return names
