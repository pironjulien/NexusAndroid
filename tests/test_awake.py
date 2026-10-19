"""Écran gardé allumé pendant le pilotage : sauvegarde, restauration après inactivité, au démarrage, une seule fois."""

from __future__ import annotations

import json
import re
import time

import pytest

from nexus_android import server
from nexus_android.awake import IDLE_SH, RAISED, KeepAwake
from nexus_android.screen import STATE_SH, Screen
from nexus_android.server import Step


class Phone:
    """adb simulé : réglage screen_off_timeout, dernière activité sur le téléphone, commandes enregistrées."""

    serial = "SERIAL"

    def __init__(self, timeout: int = 30000, locked: bool = False) -> None:
        self.timeout = timeout
        self.locked = locked
        self.idle_ms = 10**9  # personne n'a touché le téléphone depuis longtemps
        self.calls: list[tuple[str, ...]] = []

    def identity(self, serial: str) -> str:
        return "R5FOLD8"

    def run(self, *argv: str, timeout: float = 30.0) -> tuple[int, str, str]:
        self.calls.append(argv)
        if argv[:3] == ("settings", "get", "system"):
            return 0, f"{self.timeout}\n", ""
        if argv[:3] == ("settings", "put", "system"):
            self.timeout = int(argv[4])
            return 0, "", ""
        if argv[:2] == ("sh", "-c") and argv[2] == IDLE_SH:
            return 0, f"  lastUserActivityTime=123 ({self.idle_ms} ms ago)\n", ""
        if argv[:2] == ("dumpsys", "window"):
            lock = "isKeyguardShowing=true" if self.locked else "isKeyguardShowing=false"
            return 0, lock + "\n  mCurrentFocus=Window{abc u0 com.app/x.Main}", ""
        if argv[0] == "sh" and argv[2] == STATE_SH:  # état en un aller-retour
            lock = "isKeyguardShowing=true" if self.locked else "isKeyguardShowing=false"
            return 0, f"mWakefulness=Awake\n{lock}\n  mCurrentFocus=Window{{abc u0 com.app/x.Main}}", ""
        if argv[0] in ("sh", "cat"):
            return 0, "<?xml version='1.0' ?><hierarchy rotation=\"0\"></hierarchy>", ""
        return 0, "", ""

    def shell(self, *argv: str, timeout: float = 30.0, check: bool = True) -> str:
        return self.run(*argv, timeout=timeout)[1]

    @property
    def slept(self) -> bool:
        return ("input", "keyevent", "KEYCODE_SLEEP") in self.calls

    @property
    def puts(self) -> int:
        return sum(c[:3] == ("settings", "put", "system") for c in self.calls)


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def keeper(phone: Phone, clock: Clock, path) -> KeepAwake:
    return KeepAwake(phone, path=path, idle=120, clock=clock)  # type: ignore[arg-type]


@pytest.fixture
def served(monkeypatch, tmp_path):
    """Serveur branché sur un Phone simulé, verrou commun dans tmp_path."""

    def plug(phone: Phone) -> Phone:
        scr = Screen(phone)  # type: ignore[arg-type]
        scr.size = (1080, 2000)
        monkeypatch.setattr(server, "ADB", phone)
        monkeypatch.setattr(server, "SCREEN", scr)
        monkeypatch.setattr(server, "MESSAGES", object())
        monkeypatch.setattr(time, "sleep", lambda s: None)
        monkeypatch.setattr("nexus_android.lock.LOCK_FILE", tmp_path / "android.lock")
        return phone

    return plug


def test_first_gesture_saves_original_then_raises(awake_state):
    phone, clock = Phone(30000), Clock()
    keeper(phone, clock, awake_state).touch()
    assert phone.timeout == RAISED
    state = keeper(phone, clock, awake_state).state()
    assert state["active"] and state["original"] == 30000 and state["phone"] == "R5FOLD8"


def test_no_double_save_across_servers(awake_state):
    """Deuxième agent (autre serveur, même fichier d'état) : la valeur montée n'est jamais prise pour l'origine."""
    phone, clock = Phone(60000), Clock()
    keeper(phone, clock, awake_state).touch()
    clock.now += 30
    keeper(phone, clock, awake_state).touch()
    state = keeper(phone, clock, awake_state).state()
    assert state["original"] == 60000 and state["last"] == clock.now
    assert phone.puts == 1


def test_lost_session_never_saves_raised_value(awake_state):
    phone, clock = Phone(60000), Clock()
    k = keeper(phone, clock, awake_state)
    k.touch()
    clock.now += 200
    k.restore()
    phone.timeout = RAISED  # réglage resté monté sans session en cours
    k.touch()
    assert k.state()["original"] == 60000


def test_restore_after_idle_then_sleep(awake_state):
    phone, clock = Phone(30000), Clock()
    k = keeper(phone, clock, awake_state)
    k.touch()
    clock.now += 119
    assert k.restore() is None and phone.timeout == RAISED and 0 < (k.due() or 0) <= 1
    clock.now += 2
    assert k.due() == 0
    assert "screen turned off" in (k.restore() or "")
    assert phone.timeout == 30000 and phone.slept
    assert k.state()["active"] is False and k.due() is None
    assert k.restore() is None  # une seule restauration
    assert phone.puts == 2


def test_restore_leaves_screen_to_owner_while_in_use(awake_state):
    phone, clock = Phone(30000), Clock()
    k = keeper(phone, clock, awake_state)
    k.touch()
    clock.now += 300
    phone.idle_ms = 4000  # l'utilisateur a touché l'écran il y a 4 s
    assert "screen left to the user" in (k.restore() or "")
    assert phone.timeout == 30000 and not phone.slept


def test_restore_respects_setting_changed_by_owner(awake_state):
    phone, clock = Phone(30000), Clock()
    k = keeper(phone, clock, awake_state)
    k.touch()
    phone.timeout = 120000  # l'utilisateur a choisi 2 min dans les Paramètres pendant la session
    clock.now += 300
    k.restore()
    assert phone.timeout == 120000 and not phone.slept


def test_gesture_keeps_awake_only_when_unlocked(served):
    for locked, expected in ((True, 30000), (False, RAISED)):
        phone = served(Phone(30000, locked=locked))
        server._act([Step(do="key", keys="back")], "none")
        assert phone.timeout == expected
        assert not any(c[:2] == ("input", "text") for c in phone.calls)  # jamais de code saisi


def test_startup_restores_expired_session(awake_state, served):
    """Serveur planté : au démarrage d'un autre serveur, la session expirée est rendue (valeur d'origine, veille)."""
    phone = served(Phone(30000))
    keeper(phone, Clock(time.time() - 600), awake_state).touch()  # écrite par un serveur disparu il y a 10 min
    data = json.loads(awake_state.read_text(encoding="utf-8"))
    awake_state.write_text(json.dumps({**data, "pid": 999999}), encoding="utf-8")
    assert "screen turned off" in (server._check_idle() or "")
    assert phone.timeout == 30000 and phone.slept


def test_startup_keeps_live_session_of_other_agent(awake_state, served, monkeypatch):
    phone = served(Phone(30000))
    keeper(phone, Clock(time.time()), awake_state).touch()
    data = json.loads(awake_state.read_text(encoding="utf-8"))
    awake_state.write_text(json.dumps({**data, "pid": 999999}), encoding="utf-8")  # session d'un autre agent
    scheduled: list[float] = []
    monkeypatch.setattr(server, "_schedule", scheduled.append)
    assert server._check_idle() is None
    assert phone.timeout == RAISED and not phone.slept and scheduled and scheduled[0] > 100
    assert server._check_idle(force=True) is None  # en partant, ce serveur ne rend pas la session d'un autre
    assert phone.timeout == RAISED


def test_exit_restores_own_session(awake_state, served):
    phone = served(Phone(30000))
    server._act([Step(do="key", keys="back")], "none")
    assert phone.timeout == RAISED
    assert server._check_idle(force=True)
    assert phone.timeout == 30000


def test_phone_status_says_until_when(awake_state):
    phone = Phone(30000)
    keeper(phone, Clock(time.time()), awake_state).touch()
    assert re.fullmatch(r" · screen kept on until \d\d:\d\d", server._awake_note(phone))  # type: ignore[arg-type]
