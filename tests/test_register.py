"""Déclaration aux IA (register.py) et mode pack de `setup` (NEXUS_PACK=1)."""

from __future__ import annotations

import json
import tomllib

import pytest

from nexus_android import firstrun, register

SPEC = "nexus-android"  # sans version : uvx garde la version en cache, updates.py prépare la suivante


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setattr(register.shutil, "which", lambda name: r"C:\uv\uvx.exe" if name == "uvx" else None)
    return tmp_path


def test_nothing_installed(home):
    assert register.declare_all() == {}


def test_every_client_declared(home):
    (home / ".claude.json").write_text('{"mcpServers": {"other": {"command": "x"}}}', encoding="utf-8")
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text('model = "gpt"\n\n[mcp_servers.other]\ncommand = "x"\n', encoding="utf-8")
    (home / ".gemini" / "antigravity").mkdir(parents=True)
    (home / "AppData" / "Roaming" / "Claude").mkdir(parents=True)
    store = home / "AppData" / "Local" / "Packages" / "AnthropicPBC.Claude_abc" / "LocalCache" / "Roaming" / "Claude"
    store.mkdir(parents=True)
    results = register.declare_all()
    assert results == {"Claude Code": "declared", "Codex": "declared", "Antigravity": "plugin",
                       "Claude Desktop": "declared", "Claude Desktop (2)": "declared"}
    claude = json.loads((home / ".claude.json").read_text(encoding="utf-8"))["mcpServers"]
    assert claude["other"] == {"command": "x"} and claude["nexus-android"]["args"] == ["--python", "3.14", SPEC]
    codex = tomllib.loads((home / ".codex" / "config.toml").read_text(encoding="utf-8"))
    assert codex["model"] == "gpt" and codex["mcp_servers"]["other"] == {"command": "x"}
    assert codex["mcp_servers"]["nexus-android"] == {"command": r"C:\uv\uvx.exe", "args": ["--python", "3.14", SPEC],
                                                     "startup_timeout_sec": 60}
    plugin = home / ".gemini" / "config" / "plugins" / "nexus-android"
    assert (plugin / "plugin.json").is_file() and (plugin / "skills" / "nexus-android" / "SKILL.md").is_file()
    assert json.loads((plugin / "mcp_config.json").read_text(encoding="utf-8"))["mcpServers"]["nexus-android"]["args"][-1] == SPEC
    assert not (home / ".gemini" / "config" / "mcp_config.json").exists()  # jamais d'entrée globale
    assert json.loads((store / "claude_desktop_config.json").read_text(encoding="utf-8"))["mcpServers"]["nexus-android"]
    again = register.declare_all()
    assert again["Codex"] == "already declared" and again["Antigravity"] == "plugin (already installed)"
    assert again["Claude Code"] == "already declared"
    assert list((home / ".nexus" / "nexus-android" / "backups").iterdir())


def test_claude_desktop_store_data_folder(home):
    left = home / "AppData" / "Local" / "Packages" / "AnthropicPBC.Claude_x" / "LocalCache" / "Roaming" / "Claude"
    left.mkdir(parents=True)
    (left / ".user-data-left.anthropicpbc.claude_x").write_text("", encoding="utf-8")
    data = home / "AppData" / "Local" / "Claude-Data" / "anthropicpbc.claude_x"
    data.mkdir(parents=True)
    (data / "claude_desktop_config.json").write_text('{"preferences": {"theme": "dark"}}', encoding="utf-8")
    assert register.declare_all() == {"Claude Desktop": "declared"}
    config = json.loads((data / "claude_desktop_config.json").read_text(encoding="utf-8"))
    assert config["preferences"] == {"theme": "dark"} and config["mcpServers"]["nexus-android"]["args"][-1] == SPEC
    assert not (left / "claude_desktop_config.json").exists()


def test_user_entries_and_plugins_kept(home):
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text('{"enabledPlugins": {"nexus-android@nexus-plugins": true}}',
                                                    encoding="utf-8")
    (home / ".codex").mkdir()
    toml = '[mcp_servers.nexus-android]\ncommand = "python"\nargs = ["-m", "nexus_android"]\n'
    (home / ".codex" / "config.toml").write_text(toml, encoding="utf-8")
    plugin = home / ".gemini" / "config" / "plugins" / "nexus-android"
    plugin.mkdir(parents=True)
    daemon = '{"mcpServers": {"nexus-android": {"serverUrl": "http://127.0.0.1:16180/mcp"}}}'
    (plugin / "mcp_config.json").write_text(daemon, encoding="utf-8")
    results = register.declare_all()
    assert results["Claude Code"] == register.PLUGIN
    assert results["Codex"] == register.KEPT
    assert results["Antigravity"].startswith("kept")
    assert (home / ".codex" / "config.toml").read_text(encoding="utf-8") == toml
    assert (plugin / "mcp_config.json").read_text(encoding="utf-8") == daemon


def test_codex_plugin_and_old_entry_replaced(home):
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text('[plugins."nexus-android@personal"]\nenabled = true\n', encoding="utf-8")
    assert register.codex() == register.PLUGIN
    old = '[mcp_servers.nexus-android]\ncommand = "uvx"\nargs = ["nexus-android"]\n\n[mcp_servers.nexus-android.env]\nA = "1"\n'
    (home / ".codex" / "config.toml").write_text(old + '\n[other]\nx = 1\n', encoding="utf-8")
    assert register.codex() == "declared"
    data = tomllib.loads((home / ".codex" / "config.toml").read_text(encoding="utf-8"))
    assert data["other"] == {"x": 1} and "env" not in data["mcp_servers"]["nexus-android"]


def test_old_global_antigravity_entry_removed(home):
    root = home / ".gemini" / "config"
    root.mkdir(parents=True)
    (root / "mcp_config.json").write_text(json.dumps({"mcpServers": {
        "nexus-android": {"command": "uvx", "args": ["nexus-android"]}, "x": {"command": "y"}}}), encoding="utf-8")
    (root / "config.json").write_text('{"plugins": {"nexus-android": {"enabled": false}}}', encoding="utf-8")
    assert register.antigravity() == "plugin, turned off in Antigravity's settings"
    assert json.loads((root / "mcp_config.json").read_text(encoding="utf-8"))["mcpServers"] == {"x": {"command": "y"}}


def test_unreadable_file_reported_not_touched(home):
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text("[broken", encoding="utf-8")
    assert register.declare_all()["Codex"].startswith("failed")
    assert (home / ".codex" / "config.toml").read_text(encoding="utf-8") == "[broken"


def test_pack_mode_never_waits(monkeypatch, capsys):
    monkeypatch.setenv("NEXUS_PACK", "1")
    monkeypatch.setattr(firstrun.platformtools, "find", lambda say: "adb")
    monkeypatch.setattr(firstrun.register, "declare_all",
                        lambda: {"Claude Desktop": "declared", "Codex": "already declared", "Antigravity": "plugin"})
    monkeypatch.setattr(firstrun, "Adb", lambda: pytest.fail("pack mode must not wait for the phone"))
    assert firstrun.run([]) == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last == "NEXUS_PACK_RESULT Claude Desktop=declared; Codex=declared; Antigravity=plugin"
    monkeypatch.setattr(firstrun.register, "declare_all", lambda: {"Codex": "failed: bad toml"})
    assert firstrun.run([]) == 1


def test_pack_mode_adb_failure(monkeypatch, capsys):
    monkeypatch.setenv("NEXUS_PACK", "1")

    def no_adb(say):
        raise firstrun.PhoneError("adb not found and the automatic download failed")

    monkeypatch.setattr(firstrun.platformtools, "find", no_adb)
    assert firstrun.run([]) == 1
    assert capsys.readouterr().out.strip().endswith("NEXUS_PACK_RESULT adb=failed")
