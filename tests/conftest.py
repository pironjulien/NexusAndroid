"""Aucun test n'écrit l'état réel de l'écran gardé allumé, ne lance de minuteur ni ne télécharge le serveur scrcpy."""

from __future__ import annotations

import pytest

from nexus_android import awake, clip, messages, screen, server


@pytest.fixture(autouse=True)
def awake_state(monkeypatch, tmp_path):
    path = tmp_path / "awake.json"
    monkeypatch.setattr(awake, "AWAKE_STATE", path)
    monkeypatch.setattr(server, "_schedule", lambda delay: None)
    return path


@pytest.fixture(autouse=True)
def no_download(monkeypatch, tmp_path):
    """Serveur scrcpy cherché dans un dossier vide du test, et aucun accès réseau : un test qui veut un téléchargement
    remplace clip._fetch."""
    monkeypatch.setattr(clip, "LOCAL", tmp_path / "tools" / f"scrcpy-server-v{clip.VERSION}")

    def offline(url: str) -> bytes:
        raise OSError("réseau coupé pendant les tests")

    monkeypatch.setattr(clip, "_fetch", offline)


@pytest.fixture(autouse=True)
def fresh_country(monkeypatch):
    """Pays de la SIM gardé par téléphone pour toutes les sessions : vide au début de chaque test."""
    monkeypatch.setattr(messages, "_COUNTRIES", {})


@pytest.fixture(autouse=True)
def no_screen_server(monkeypatch):
    """Téléphones simulés : écran lu par uiautomator et screencap ; un test du lecteur d'écran le rallume."""
    monkeypatch.setattr(screen, "FAST", False)
