"""Écran de verrouillage qu'Android tient déverrouillé (Extend Unlock, lieu de confiance) : levé sans code par look,
act et capture ; vraiment verrouillé, refus inchangé. Faux téléphone, aucun vrai adb."""

from __future__ import annotations

import asyncio

import pytest
from test_capture import RecPhone
from test_extras import home  # noqa: F401  (fixture home : profil Windows simulé)
from test_guards import node, ui
from test_robustness import _NoLock

from nexus_android import capture, screen, server
from nexus_android.server import Step

TRUSTED = ('User "Owner" (id=0, flags=0x4c13) (current): trustState=TRUSTED, trustManaged=1, deviceLocked=0, '
           "isActiveUnlockRunning=0, strongAuthRequired=0x0")
UNTRUSTED = ('User "Owner" (id=0, flags=0x4c13) (current): trustState=UNTRUSTED, trustManaged=1, '
             "deviceLocked=1, isActiveUnlockRunning=0, strongAuthRequired=0x0")
DISMISS = ("wm", "dismiss-keyguard")


class TrustPhone(RecPhone):
    """Téléphone en veille, écran de verrouillage affiché ; `dumpsys trust` dit si Android le tient déverrouillé.
    wm dismiss-keyguard ne lève l'écran que si deviceLocked=0 (comme Android : sinon il demande le code)."""

    def __init__(self) -> None:
        super().__init__()
        self.awake = False
        self.lock = "isKeyguardShowing=true"
        self.trust = TRUSTED

    def run(self, *argv: str, timeout: float = 30.0) -> tuple[int, str, str]:
        if argv[0] == "sh" and argv[2] == screen.TRUST_SH:
            self.calls.append(argv)
            return 0, self.trust, ""
        if argv == DISMISS:
            self.calls.append(argv)
            if "deviceLocked=0" in self.trust:
                self.lock = "isKeyguardShowing=false"
            return 0, "", ""
        return super().run(*argv, timeout=timeout)

    @property
    def typed(self) -> list[tuple[str, ...]]:
        return [c for c in self.calls if c[:2] == ("input", "text")]


@pytest.fixture
def fold(monkeypatch, home):  # noqa: F811
    fake = TrustPhone()
    scr = screen.Screen(fake)  # type: ignore[arg-type]
    scr.size = (1080, 2000)
    monkeypatch.setattr(server, "ADB", fake)
    monkeypatch.setattr(server, "SCREEN", scr)
    monkeypatch.setattr(server, "MESSAGES", object())
    monkeypatch.setattr(server, "PhoneLock", _NoLock)
    monkeypatch.setattr(capture, "_CURRENT", None)
    monkeypatch.setattr(server.awake, "idle_on_phone", lambda adb: None)
    monkeypatch.setattr("time.sleep", lambda s: None)
    fake.uis = [ui(node("Accueil", (0, 0, 500, 200)))]
    return fake


def text(out: list) -> str:
    return "\n".join(x for x in out if isinstance(x, str))


def test_trusted_lock_screen_lifted_by_look(fold):
    report = text(server._view("elements"))
    assert "LOCKED" not in report and screen.LIFTED in report and "Accueil" in report
    assert ("input", "keyevent", "KEYCODE_WAKEUP") in fold.calls and DISMISS in fold.calls
    assert fold.typed == []  # jamais de code tapé


def test_untrusted_lock_screen_refused_as_before(fold):
    fold.trust = UNTRUSTED
    assert "LOCKED (the user must unlock it)" in text(server._view("elements"))
    report = server._act([Step(do="tap", target="Accueil")], "none")[0]
    assert "✗ 1. Phone locked" in report and screen.LIFTED not in report
    assert DISMISS not in fold.calls and not [c for c in fold.inputs if c[1] != "keyevent"]
    with pytest.raises(Exception, match="Phone locked: nothing captured"):
        asyncio.run(server.capture("screenshot"))
    assert fold.captures == 0 and DISMISS not in fold.calls


def test_trusted_lock_screen_lifted_by_act(fold):
    report = server._act([Step(do="tap", target="Accueil")], "none")[0]
    assert report.startswith(screen.LIFTED) and "✗" not in report
    assert ("input", "tap", "250", "100") in fold.calls and fold.typed == []


def test_trusted_lock_screen_lifted_by_capture(fold, home):  # noqa: F811
    saved = asyncio.run(server.capture("screenshot"))
    assert screen.LIFTED in saved and DISMISS in fold.calls and fold.captures == 1
    assert server._LAST_INPUT > 0  # levée comptée comme un geste de l'agent


def test_lock_screen_that_does_not_lift_stays_refused(fold, monkeypatch):
    """Android dit deviceLocked=0 mais l'écran de verrouillage reste (confiance retirée entre-temps) : refus."""
    monkeypatch.setattr(fold, "run", lambda *argv, timeout=30.0: (
        fold.calls.append(argv) or (0, "", "")) if argv == DISMISS else TrustPhone.run(fold, *argv, timeout=timeout))
    assert "LOCKED (the user must unlock it)" in text(server._view("elements"))
    assert "Phone locked" in server._act([Step(do="tap", target="Accueil")], "none")[0]
    assert not [c for c in fold.inputs if c[1] != "keyevent"]


def test_trust_read_for_the_current_user_only():
    other = 'User "Invité" (id=10, flags=0x0): trustState=TRUSTED, trustManaged=1, deviceLocked=0'
    assert screen.parse_trusted(TRUSTED)
    assert not screen.parse_trusted(UNTRUSTED + "\n" + other)
    assert not screen.parse_trusted("")


def test_look_lifts_without_opening_a_keep_awake_session(fold, monkeypatch):
    """Un simple regard ne garde pas l'écran allumé (téléphone en poche) ; un geste, si."""
    kept: list[object] = []
    monkeypatch.setattr(server, "_keep_awake", lambda adb, state: kept.append(state))
    assert screen.LIFTED in text(server._view("elements")) and kept == []
    fold.lock = "isKeyguardShowing=true"
    fold.awake = False
    assert server._act([Step(do="tap", target="Accueil")], "none")[0].startswith(screen.LIFTED)
    assert len(kept) >= 1


def test_unreadable_state_after_dismiss_is_not_a_lift(fold, monkeypatch):
    """dumpsys illisible pendant la relecture (adb qui saute) : jamais pris pour un écran levé."""
    def run(*argv, timeout=30.0):
        if argv == DISMISS:
            fold.calls.append(argv)
            fold.lock = ""  # plus aucun indicateur de verrouillage lisible
            return 0, "", ""
        return TrustPhone.run(fold, *argv, timeout=timeout)
    monkeypatch.setattr(fold, "run", run)
    scr = screen.Screen(fold)  # type: ignore[arg-type]
    state, lifted = scr.lift_keyguard(scr.state())
    assert not lifted and not state.lock_known
