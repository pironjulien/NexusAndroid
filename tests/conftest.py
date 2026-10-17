"""Aucun test n'écrit l'état réel de l'écran gardé allumé ni ne lance de minuteur."""

from __future__ import annotations

import pytest

from nexus_android import awake, server


@pytest.fixture(autouse=True)
def awake_state(monkeypatch, tmp_path):
    path = tmp_path / "awake.json"
    monkeypatch.setattr(awake, "AWAKE_STATE", path)
    monkeypatch.setattr(server, "_schedule", lambda delay: None)
    return path
