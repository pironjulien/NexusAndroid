"""Gestes par le canal de contrôle de scrcpy : format exact des messages (scrcpy v5), suites d'événements (appui,
glissé, glisser-déposer, deux doigts), choix du canal ou du repli `input` dans act, sans téléphone."""

from __future__ import annotations

import struct

import pytest
from test_guards import FakePhone, act, node, ui

from nexus_android import control, server
from nexus_android.control import DOWN, FINGER, MOVE, UP, Fingers, pinch_tracks, touch, zoom_tracks
from nexus_android.server import Step


def decode(data: bytes) -> tuple[int, int, int, int, int]:
    kind, action, pointer, x, y, w, h, pressure, button, buttons = struct.unpack(">BBqiiHHHII", data)
    assert kind == 2 and (w, h) == (1080, 2000) and button == buttons == 0
    assert pressure == (0 if action == UP else 0xFFFF)
    return action, pointer, x, y, pressure


def recorder() -> tuple[Fingers, list[tuple[int, int, int, int, int]]]:
    events: list[tuple[int, int, int, int, int]] = []
    return Fingers(lambda data: events.append(decode(data)), (1080, 2000), sleep=lambda s: None), events


def test_touch_message_is_32_bytes_in_scrcpy_v5_order():
    data = touch(DOWN, FINGER, 540, 1000, (1080, 2000))
    assert len(data) == 32 and data[:2] == b"\x02\x00"
    assert struct.unpack(">q", data[2:10])[0] == -2 and struct.unpack(">ii", data[10:18]) == (540, 1000)


def test_tap_swipe_and_drag_sequences():
    fingers, events = recorder()
    fingers.tap((10, 20))
    assert [e[:4] for e in events] == [(DOWN, FINGER, 10, 20), (UP, FINGER, 10, 20)]
    events.clear()
    fingers.swipe((0, 0), (0, 800), ms=80)
    assert events[0][0] == DOWN and events[-1][:4] == (UP, FINGER, 0, 800)
    moves = [e for e in events if e[0] == MOVE]
    assert len(moves) == 10 and moves[-1][2:4] == (0, 800) and all(a[3] < b[3] for a, b in zip(moves, moves[1:], strict=False))


def test_pinch_uses_two_fingers_down_together():
    fingers, events = recorder()
    fingers.multi(pinch_tracks((500, 1000), 100, 600), ms=40)
    downs = [e for e in events if e[0] == DOWN]
    ups = [e for e in events if e[0] == UP]
    assert [d[1] for d in downs] == [1, 2] and sorted(u[1] for u in ups) == [1, 2]
    assert events.index(downs[1]) < events.index(next(e for e in events if e[0] == MOVE))  # posés ensemble
    first, second = [e for e in events if e[0] == MOVE and e[1] == 1], [e for e in events if e[0] == MOVE and e[1] == 2]
    assert first[-1][3] == 700 and second[-1][3] == 1300  # écartés de 100 à 600 px autour du centre


class FakeFingers:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def tap(self, at, hold_ms=40):
        self.calls.append(("tap", at, hold_ms))

    def swipe(self, start, end, ms=400, hold_ms=0, release_ms=0):
        self.calls.append(("swipe", start, end, ms, hold_ms))

    def multi(self, tracks, ms=500):
        self.calls.append(("multi", tracks, ms))


@pytest.fixture
def channel(guarded, monkeypatch):
    fake = FakeFingers()
    monkeypatch.setattr(server, "_fingers", lambda adb, screen: fake)
    return fake


def test_gestures_go_through_the_channel_not_input(channel, guarded):
    guarded.uis = [ui(node("OK", (0, 0, 200, 200)))]
    assert act(Step(do="tap", target="OK")).startswith("~")
    act(Step(do="long_press", target="OK"))
    act(Step(do="swipe", target="100,1500", to="100,500"))
    act(Step(do="zoom", direction="in", target="540,1000"))
    kinds = [c[0] for c in channel.calls]
    assert kinds == ["tap", "tap", "swipe", "multi"] and channel.calls[1][2] == 700
    assert guarded.inputs == []  # aucun `input` lancé


def test_without_channel_input_is_used_and_zoom_refused(guarded):
    guarded.uis = [ui(node("Accueil", (0, 0, 1080, 2000), clickable=False), node("OK", (0, 0, 200, 200)))]
    act(Step(do="tap", target="OK"))
    act(Step(do="drag", target="100,1500", to="100,500"))
    assert [c[:2] for c in guarded.inputs] == [("input", "tap"), ("input", "draganddrop")]
    assert "several fingers" in act(Step(do="zoom", direction="out"))


def test_drag_guards_both_ends(guarded):
    guarded.uis = [ui(node("Supprimer", (0, 400, 1080, 600)))]
    report = act(Step(do="drag", target="540,1500", to="Supprimer"))
    assert report.startswith("✗") and guarded.inputs == []


@pytest.fixture
def guarded(monkeypatch):
    import time

    from nexus_android.screen import Screen
    fake = FakePhone()
    scr = Screen(fake)  # type: ignore[arg-type]
    scr.size = (1080, 2000)
    monkeypatch.setattr(server, "ADB", fake)
    monkeypatch.setattr(server, "SCREEN", scr)
    monkeypatch.setattr(server, "MESSAGES", object())
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return fake


def test_channel_closed_after_idle(monkeypatch):
    opened, closed = [], []

    class Session:
        def __enter__(self):
            opened.append(1)
            return object()

        def __exit__(self, *a):
            closed.append(1)

    monkeypatch.setattr(control, "ENABLED", True)
    monkeypatch.setattr(control.clip, "_session", lambda adb, use: Session())
    ctl = control.Control(type("A", (), {"serial": "x"})())  # type: ignore[arg-type]
    assert ctl.fingers((1080, 2000)) is not None and ctl.fingers((1080, 2000)) is not None
    assert opened == [1]  # gardé ouvert entre deux gestes
    ctl._idle()
    assert closed == [1]
    ctl.close()


@pytest.mark.parametrize("center", [(624, 585), (624, 986), (100, 1000), (1100, 300), (624, 1700)])
@pytest.mark.parametrize("zoom_in", [True, False])
def test_zoom_fingers_stay_out_of_the_status_bar_and_edges(center, zoom_in):
    """Vu en vrai le 20/10 : zoom arrière fort près du haut, un doigt dans la barre d'état ouvrait le volet."""
    w, h = 1248, 1972
    for strength in (1, 2, 3):
        for start, end in zoom_tracks(center, (w, h), zoom_in, strength):
            for x, y in (start, end):
                assert h * 0.1 <= y <= h * 0.9 and w * 0.06 <= x <= w * 0.94


def test_zoom_refused_without_room():
    with pytest.raises(ValueError):
        zoom_tracks((624, 150), (1248, 1972), True)


def test_zoom_never_lands_a_finger_on_a_pay_button(channel, guarded):
    """Relecture de Codex : le zoom ne jugeait que son centre ; un doigt pouvait se poser sur « Payer »."""
    guarded.uis = [ui(node("Carte", (0, 300, 1080, 1700), clickable=False), node("Payer", (440, 950, 520, 1050)))]
    report = act(Step(do="zoom", direction="in", target="540,1000", amount=3))
    assert report.startswith("✗") and "would land on \"Payer\"" in report and channel.calls == []
    report = act(Step(do="zoom", direction="in", target="540,1000", amount=3, confirm=True))
    assert report.startswith("✗") and channel.calls == []  # même avec confirm=true


def test_zoom_checks_the_app_of_each_touched_element(channel, guarded):
    """Relecture de Codex, tour 2 : état lu « Maps » mais bouton bancaire sans nom sous un doigt."""
    guarded.uis = [ui(node("Compte", (0, 300, 1080, 1700), clickable=False, package="fr.creditagricole.androidapp"),
                      node("", (440, 950, 520, 1050), package="fr.creditagricole.androidapp"))]
    report = act(Step(do="zoom", direction="in", target="540,1000", amount=3, confirm=True))
    assert report.startswith("✗") and channel.calls == []
