"""Journal commun des outils Nexus (NexusHarness/NexusShared/journal/FORMAT.md, produit « android ») : une ligne par geste ou
par appel d'outil, en plus du journal détaillé, dans %LOCALAPPDATA%\\NexusJournal\\android\\<agent>\\<AAAA-MM>.jsonl.
Jamais de texte d'écran, de SMS, de contact, de texte tapé ni de code : app = nom du paquet, erreur = une cause prise
dans une liste fermée (jamais un morceau du message, dont même le début peut porter un nom ou un numéro). Jamais
bloquant : les lignes partent dans une file bornée qu'un fil à part écrit sur le disque ; file pleine, la ligne est
abandonnée."""

from __future__ import annotations

import json
import os
import queue
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__

AGENTS = ("claude", "codex", "antigravity")
_PACKAGE = re.compile(r"^[A-Za-z][\w]*(\.[A-Za-z_][\w]*)+$")
# Causes reconnues dans les messages de l'outil (anglais, écrits par lui) → libellé fixe. Seul le libellé est écrit.
CAUSES: tuple[tuple[str, str], ...] = (
    ("cancelled", "cancelled"),
    ("using the phone", "user busy"),
    ("took over", "user took over"),
    ("link lost", "link lost"),
    ("password field", "password field"),
    ("no active input field", "no input field"),
    ("confirm=true", "confirmation required"),
    ("accord", "accord required"),
    ("banking, payment or identity", "protected app"),
    ("locked", "phone locked"),
    ("busy with another agent", "phone busy"),
    ("free of", "target hidden"),
    ("not found", "target not found"),
    ("no longer there", "screen changed"),
    ("unreadable", "screen unreadable"),
    ("unavailable", "unavailable"),
    ("not launched", "launch failed"),
    ("not opened", "open failed"),
    ("several numbers", "ambiguous contact"),
    ("no contact", "contact not found"),
    ("timed out", "timeout"),
    ("timeout", "timeout"),
    ("refused", "refused"),
)
_QUEUE: queue.Queue[tuple[str, str, str]] = queue.Queue(maxsize=1000)
_WRITER: threading.Thread | None = None
_START = threading.Lock()


def agent_of(name: str | None) -> str | None:
    """IA d'après UN indice (nom du client MCP, ou User-Agent, ou NEXUS_AGENT) ; None si inconnue."""
    seen = (name or "").casefold()
    if "codex" in seen:
        return "codex"
    if "antigravity" in seen or "jetski" in seen or "gemini" in seen:
        return "antigravity"
    if "claude" in seen:
        return "claude"
    return None


def cause(message: Any) -> str | None:
    """Cause courte d'un échec, prise dans CAUSES ; « error » sinon. Jamais un morceau du message."""
    if not message:
        return None
    text = str(message).casefold()
    return next((label for key, label in CAUSES if key in text), "error")


def issue_d_erreur(message: str) -> str:
    """Issue d'un refus ou d'un échec d'après le message de l'outil (lu ici, jamais écrit)."""
    found = cause(message)
    if found in ("confirmation required", "accord required", "password field", "protected app") \
            or "never" in message.casefold():
        return "refus"
    if found in ("user busy", "user took over"):
        return "différé"
    return "✗"


def app_propre(package: str | None) -> str | None:
    """Nom de paquet seulement (com.whatsapp) ; tout autre texte est écarté."""
    return package if package and _PACKAGE.match(package) else None


def actif() -> bool:
    """Faux si NEXUS_JOURNAL=0 (ou off, false, no) : aucune ligne écrite, aucun dossier créé."""
    return os.environ.get("NEXUS_JOURNAL", "").strip().lower() not in ("0", "off", "false", "no")


def _racine() -> Path:
    base = os.environ.get("NEXUS_JOURNAL_HOME") or os.path.join(
        os.environ.get("LOCALAPPDATA", str(Path.home())), "NexusJournal")
    return Path(base) / "android"


def _write(agent: str, mois: str, text: str) -> None:
    chemin = _racine() / agent / f"{mois}.jsonl"  # mois du geste, pas celui de l'écriture (file vidée après minuit)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with chemin.open("a", encoding="utf-8") as f:
        f.write(text + "\n")


def _drain() -> None:
    while True:
        agent, mois, text = _QUEUE.get()
        try:
            _write(agent, mois, text)
        except Exception:  # noqa: BLE001, S110 - journal : jamais bloquant, ligne perdue plutôt qu'un geste retenu
            pass
        finally:
            _QUEUE.task_done()


def flush(timeout: float = 2.0) -> None:
    """Attend que la file soit écrite (tests, arrêt du serveur), au plus `timeout` s."""
    deadline = threading.Event()
    waiter = threading.Thread(target=lambda: (_QUEUE.join(), deadline.set()), daemon=True)
    waiter.start()
    deadline.wait(timeout)


def ecrire(agent: str | None, appel: str | None, app: str | None, action: str, issue: str, ms: float | None,
           erreur: Any = None, technique: str | None = None) -> None:
    """Une ligne déposée dans la file (jamais d'attente) ; agent inconnu : rien (le journal est rangé par IA)."""
    global _WRITER
    try:
        if agent not in AGENTS or not actif():
            return
        maintenant = datetime.now(UTC)
        ligne = {
            "v": 1,
            "ts": maintenant.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "agent": agent,
            "outil": __version__,
            "appel": appel,
            "app": app_propre(app),
            "cadre": None,
            "action": action,
            "technique": technique,
            "issue": issue,
            "ms": round(ms) if ms is not None else None,
            "premier_plan": False,  # téléphone : jamais la souris ni le clavier du PC
            "erreur": cause(erreur),
        }
        if _WRITER is None or not _WRITER.is_alive():
            with _START:
                if _WRITER is None or not _WRITER.is_alive():
                    _WRITER = threading.Thread(target=_drain, name="nexus-android-journal", daemon=True)
                    _WRITER.start()
        _QUEUE.put_nowait((agent, f"{maintenant:%Y-%m}", json.dumps(ligne)))  # échappé : jamais d'erreur d'encodage
    except Exception:  # noqa: BLE001 - journal : jamais bloquant (file pleine comprise)
        pass
