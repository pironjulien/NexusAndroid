"""Déclaration du serveur aux IA trouvées sur ce PC (`nexus-android setup`, pack Nexus), comme
NexusBrowser/scripts/register_clients.py : une entrée stdio `uvx --python 3.14 nexus-android==<version>` par client.

Claude Code : rien si un plugin nexus-android est activé, sinon `claude mcp add --scope user` (ou ~/.claude.json).
Claude Desktop : claude_desktop_config.json (installation classique et Store). Codex : [mcp_servers.nexus-android] de
config.toml, sauf plugin nexus-android activé. Antigravity : le plugin ~/.gemini/config/plugins/nexus-android (guide et
serveur), jamais d'entrée dans le mcp_config.json global (une ancienne entrée à nous y est retirée).
Une entrée à nous (uvx … nexus-android) est remplacée ; une entrée écrite autrement par l'utilisateur est gardée.
Chaque fichier est sauvegardé dans ~/.nexus/nexus-android/backups puis écrit de façon atomique."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
from datetime import datetime
from pathlib import Path

from . import __version__

NAME = "nexus-android"
PLUGIN_SOURCE = Path(__file__).resolve().parent / "plugin"
KEPT = "kept (an entry of yours already exists)"
PLUGIN = "provided by the nexus-android plugin"


def home() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home())


def backups() -> Path:
    return home() / ".nexus" / "nexus-android" / "backups"


def uvx() -> str:
    """Chemin absolu de uvx : un client déjà ouvert garde l'ancien PATH (uv installé après lui)."""
    return shutil.which("uvx") or "uvx"


def entry() -> dict:
    return {"command": uvx(), "args": ["--python", "3.14", f"{NAME}=={__version__}"]}


def ours(existing: object) -> bool:
    """Entrée écrite par cet installeur (uvx … nexus-android[==x]), ou aucune."""
    if existing is None:
        return True
    if not isinstance(existing, dict):
        return False
    command = Path(str(existing.get("command", ""))).name.lower()
    args = [str(a) for a in existing.get("args", [])]
    return command in {"uvx", "uvx.exe"} and any(re.fullmatch(rf"{NAME}(==[\w.]+)?", a) for a in args)


def _backup(path: Path) -> None:
    if path.exists():
        folder = backups()
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, folder / f"{path.parent.name}-{path.name}-{stamp}")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".nexus-tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() and path.stat().st_size else {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a JSON object")
    return data


def set_json_server(path: Path, value: dict) -> str:
    data = _read_json(path)
    servers = data.setdefault("mcpServers", {})
    if not ours(servers.get(NAME)):
        return KEPT
    if servers.get(NAME) == value:
        return "already declared"
    _backup(path)
    servers[NAME] = value
    _write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return "declared"


def claude_plugin_enabled() -> bool:
    try:
        enabled = _read_json(home() / ".claude" / "settings.json").get("enabledPlugins", {})
    except (OSError, ValueError):
        return False
    return any(key.split("@")[0] == NAME and on for key, on in enabled.items())


def claude_code() -> str | None:
    cli = shutil.which("claude")
    config = home() / ".claude.json"
    if claude_plugin_enabled():
        return PLUGIN
    if cli:
        current = _read_json(config).get("mcpServers", {}).get(NAME) if config.is_file() else None
        if not ours(current):
            return KEPT
        value = entry()
        if current is not None and {k: current.get(k) for k in value} == value:
            return "already declared"
        subprocess.run([cli, "mcp", "remove", NAME, "--scope", "user"], capture_output=True, check=False)
        added = subprocess.run([cli, "mcp", "add", NAME, "--scope", "user", "--", value["command"], *value["args"]],
                               capture_output=True, text=True, check=False)
        if added.returncode:
            return f"failed: {(added.stderr or added.stdout).strip()[:200]}"
        return "declared"
    if config.is_file():
        return set_json_server(config, {"type": "stdio", **entry()})
    return None


def claude_desktop() -> list[str]:
    roaming = Path(os.environ.get("APPDATA") or home() / "AppData" / "Roaming")
    local = Path(os.environ.get("LOCALAPPDATA") or home() / "AppData" / "Local")
    # Installation classique ; Store (MSIX, AnthropicPBC.Claude_…) : ancien AppData redirigé dans le paquet, puis
    # %LOCALAPPDATA%\Claude-Data\<paquet> des versions actuelles. Un dossier quitté garde seulement un marqueur
    # `.user-data-left.<paquet>` : écarté (Claude ne le lit plus).
    folders = [roaming / "Claude", *sorted((local / "Packages").glob("*Claude_*/LocalCache/Roaming/Claude")),
               *sorted(p for p in (local / "Claude-Data").glob("*") if p.is_dir())]
    live = [f for f in folders if f.is_dir() and not any(f.glob(".user-data-left.*"))]
    return [set_json_server(f / "claude_desktop_config.json", entry()) for f in live]


TOML_TABLE = re.compile(r"^\[(?P<name>[^\]]+)\]\s*$", re.M)


def _toml(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)  # une chaîne JSON est une chaîne TOML valide


def codex() -> str | None:
    folder = home() / ".codex"
    if not folder.is_dir() and not shutil.which("codex"):
        return None
    config = folder / "config.toml"
    text = config.read_text(encoding="utf-8-sig") if config.is_file() else ""
    data = tomllib.loads(text)
    plugins = data.get("plugins", {})
    if any(key.split("@")[0] == NAME and isinstance(v, dict) and v.get("enabled", True) for key, v in plugins.items()):
        return PLUGIN
    current = data.get("mcp_servers", {}).get(NAME)
    if not ours(current):
        return KEPT
    value = {**entry(), "startup_timeout_sec": 60}  # premier lancement de uvx : paquets à poser, plus de 10 s
    if current == value:
        return "already declared"
    if current is not None:  # nos tables ([mcp_servers.nexus-android] et sous-tables) retirées, chacune jusqu'à la suivante
        parts, dropping, position = [], False, 0
        for match in TOML_TABLE.finditer(text):
            if not dropping:
                parts.append(text[position:match.start()])
            name = match.group("name").strip()
            dropping = name == f"mcp_servers.{NAME}" or name.startswith(f"mcp_servers.{NAME}.")
            position = match.start()
        if not dropping:
            parts.append(text[position:])
        text = "".join(parts)
        if tomllib.loads(text).get("mcp_servers", {}).get(NAME) is not None:
            return "kept (entry written in a form this installer does not edit)"
    block = (f"[mcp_servers.{NAME}]\ncommand = {_toml(value['command'])}\n"
             f"args = [{', '.join(_toml(a) for a in value['args'])}]\nstartup_timeout_sec = 60\n")
    new = text.rstrip("\n") + ("\n\n" if text.strip() else "") + block
    if tomllib.loads(new).get("mcp_servers", {}).get(NAME) != value:
        return "failed: the edited config.toml would not read back"  # jamais un fichier que Codex ne lirait pas
    _backup(config)
    _write(config, new)
    return "declared"


def antigravity() -> str | None:
    gemini = home() / ".gemini"
    if not (gemini / "antigravity").is_dir() and not (gemini / "config").is_dir():
        return None
    root = gemini / "config"
    target = root / "plugins" / NAME
    mcp_file = target / "mcp_config.json"
    current = _read_json(mcp_file).get("mcpServers", {}).get(NAME) if mcp_file.is_file() else None
    if mcp_file.is_file() and not ours(current):
        return "kept (a nexus-android plugin of yours already exists)"
    changed = False
    for file in sorted(PLUGIN_SOURCE.rglob("*")):
        if not file.is_file() or "__pycache__" in file.parts:
            continue
        destination = target / file.relative_to(PLUGIN_SOURCE)
        if not destination.is_file() or destination.read_bytes() != file.read_bytes():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, destination)
            changed = True
    if current != entry():
        _write(mcp_file, json.dumps({"mcpServers": {NAME: entry()}}, indent=2) + "\n")
        changed = True
    global_mcp = root / "mcp_config.json"  # ancienne entrée globale à nous : outils en double avec le plugin
    if global_mcp.is_file():
        data = _read_json(global_mcp)
        if NAME in data.get("mcpServers", {}) and ours(data["mcpServers"][NAME]):
            _backup(global_mcp)
            del data["mcpServers"][NAME]
            _write(global_mcp, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    try:  # un plugin découvert est actif par défaut ; un choix explicite de l'utilisateur est seulement signalé
        disabled = _read_json(root / "config.json").get("plugins", {}).get(NAME, {}).get("enabled") is False
    except (OSError, ValueError, AttributeError):
        disabled = False
    state = "plugin" if changed else "plugin (already installed)"
    return state + (", turned off in Antigravity's settings" if disabled else "")


def declare_all() -> dict[str, str]:
    """Résultat par client trouvé (les absents ne figurent pas)."""
    results: dict[str, str | None] = {}
    for client, declare in (("Claude Code", claude_code), ("Codex", codex), ("Antigravity", antigravity)):
        try:
            results[client] = declare()
        except (OSError, ValueError, tomllib.TOMLDecodeError) as error:  # fichier illisible : laissé tel quel
            results[client] = f"failed: {error}"
    try:
        for index, result in enumerate(claude_desktop()):
            results["Claude Desktop" + (f" ({index + 1})" if index else "")] = result
    except (OSError, ValueError) as error:
        results["Claude Desktop"] = f"failed: {error}"
    return {client: result for client, result in results.items() if result is not None}
