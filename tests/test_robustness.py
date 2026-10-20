"""Robustesse : téléphone absent, Wi-Fi coupé pendant une lecture, verrou et annulation, écran gardé allumé, grosses
sorties, lancement d'une appli, étape refusée, petits plantages. Aucun vrai téléphone, aucun vrai balayage réseau."""

from __future__ import annotations

import asyncio
import json
import socket
import struct
import subprocess
import threading
import time
from datetime import datetime

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from test_awake import Clock
from test_awake import Phone as AwakePhone
from test_batch import FakeServer
from test_guards import FakePhone, act, node, ui

from nexus_android import adb as adb_mod
from nexus_android import awake, extras, query, server
from nexus_android.adb import LinkError, PhoneError
from nexus_android.awake import KeepAwake
from nexus_android.messages import MAX_TEXT, Message, Messages
from nexus_android.screen import Screen
from nexus_android.server import Step


@pytest.fixture
def phone(monkeypatch, tmp_path):
    fake = FakePhone()
    scr = Screen(fake)  # type: ignore[arg-type]
    scr.size = (1080, 2000)
    monkeypatch.setattr(server, "ADB", fake)
    monkeypatch.setattr(server, "SCREEN", scr)
    monkeypatch.setattr(server, "MESSAGES", object())
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr("nexus_android.lock.LOCK_FILE", tmp_path / "android.lock")
    return fake


# ---------------------------------------------------------------- 1. téléphone absent : pas de balayage à chaque appel


def wifi(monkeypatch, tmp_path, *, same: bool = True, ports: tuple[int, ...] = (), answers: str = ""):
    """adb simulé sans téléphone joint ; `answers` : adresse où le téléphone répond à adb connect."""
    state = tmp_path / "wifi.json"
    state.write_text(json.dumps({"serialno": "RFTEST00001", "address": "192.0.2.67:33915"}), encoding="utf-8")
    monkeypatch.setattr(adb_mod, "WIFI_STATE", state)
    monkeypatch.setattr(adb_mod, "_find_adb", lambda: "adb")
    monkeypatch.delenv("NEXUS_ANDROID_SERIAL", raising=False)
    monkeypatch.setattr(adb_mod, "same_subnet", lambda host: same)
    scans: list[str] = []
    monkeypatch.setattr(adb_mod, "open_ports", lambda host: scans.append(host) or list(ports))
    calls: list[list[str]] = []
    connected: list[str] = []

    def raw(self, args, timeout=30.0, serial=True):
        calls.append(args)
        out = b""
        if args[:2] == ["devices", "-l"]:
            out = ("List of devices attached\n" + "".join(f"{a} device model:X\n" for a in connected)).encode()
        elif args[0] == "connect" and args[1] == answers:
            connected.append(args[1])
            out = f"connected to {args[1]}".encode()
        elif args[0] == "-s" and "ro.serialno" in args:
            out = b"RFTEST00001\n" if args[1] in connected else b""
        return subprocess.CompletedProcess(args, 0, out, b"")

    monkeypatch.setattr(adb_mod.Adb, "_raw", raw)
    return calls, scans


def searched(calls: list[list[str]]) -> list[str]:
    return [a[0] for a in calls if a[0] in ("connect", "mdns")]


def test_absent_phone_failure_kept_without_new_search(monkeypatch, tmp_path):
    calls, scans = wifi(monkeypatch, tmp_path)
    phone = adb_mod.Adb()
    with pytest.raises(PhoneError, match="Next Wi-Fi search in 1[12]\\d s"):
        phone.connect()
    assert searched(calls)[:2] == ["connect", "mdns"]  # dernière adresse connue d'abord
    assert calls[[a[0] for a in calls].index("connect")] == ["connect", "192.0.2.67:33915"]
    assert scans == ["192.0.2.67"]
    calls.clear()
    with pytest.raises(PhoneError, match="not found"):
        phone.connect()  # dans les 120 s : rien n'est recherché (le câble, lui, serait vu par adb devices)
    assert searched(calls) == [] and scans == ["192.0.2.67"]
    assert any(a[:2] == ["devices", "-l"] for a in calls)
    phone._absent_until = 0.0  # 120 s plus tard : nouvelle recherche
    with pytest.raises(PhoneError):
        phone.connect()
    assert len(scans) == 2


def test_no_scan_from_another_network_nor_from_the_timer(monkeypatch, tmp_path):
    calls, scans = wifi(monkeypatch, tmp_path, same=False)
    with pytest.raises(PhoneError):
        adb_mod.Adb().connect()
    assert scans == [] and searched(calls) == ["connect", "mdns"]
    calls, scans = wifi(monkeypatch, tmp_path, same=True)
    phone = adb_mod.Adb()
    with pytest.raises(PhoneError):
        phone.connect(scan=False)
    assert scans == [] and phone._absent_until == 0.0  # recherche partielle : pas gardée comme échec complet


def test_scanned_ports_tried_with_a_limit(monkeypatch, tmp_path):
    calls, scans = wifi(monkeypatch, tmp_path, ports=tuple(range(40000, 40010)))
    with pytest.raises(PhoneError):
        adb_mod.Adb().connect()
    tried = [a[1] for a in calls if a[0] == "connect"]
    assert tried[0] == "192.0.2.67:33915" and len(tried) == 1 + adb_mod.MAX_TRIED
    calls, scans = wifi(monkeypatch, tmp_path, ports=(40000, 40001), answers="192.0.2.67:40001")
    assert adb_mod.Adb().connect().serial == "192.0.2.67:40001"


def test_open_ports_keeps_only_ports_that_speak_adb(monkeypatch):
    """Vrai balayage sur 127.0.0.1 : un faux adbd (répond AUTH), un service muet, un port fermé."""
    monkeypatch.setattr(adb_mod, "HANDSHAKE_WAIT", 0.3)
    auth = 0x48545541
    adbd, mute = socket.create_server(("127.0.0.1", 0)), socket.create_server(("127.0.0.1", 0))
    closed = socket.create_server(("127.0.0.1", 0))
    closed_port = closed.getsockname()[1]
    closed.close()
    hello: list[bytes] = []

    def serve() -> None:
        conn, _ = adbd.accept()
        with conn:
            hello.append(conn.recv(64))
            conn.sendall(struct.pack("<6I", auth, 1, 0, 20, 0, auth ^ 0xFFFFFFFF))

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        ports = [adbd.getsockname()[1], mute.getsockname()[1], closed_port]
        assert adb_mod.open_ports("127.0.0.1", ports) == [ports[0]]
        assert adb_mod.speaks_adb(hello[0][:24]) and hello[0][24:] == b"host::\x00"
    finally:
        worker.join(2)
        adbd.close()
        mute.close()
    assert not adb_mod.speaks_adb(b"HTTP/1.1 400 Bad Request\r\n\r\n")


def test_timer_never_scans(monkeypatch, awake_state):
    awake_state.write_text(json.dumps({"active": True, "original": 30000, "last": 0, "pid": 1}), encoding="utf-8")

    class Absent:
        serial = None
        asked: list[bool] = []

        def connect(self, scan: bool = True):
            self.asked.append(scan)
            raise PhoneError("introuvable")

    monkeypatch.setattr(server, "ADB", Absent())
    monkeypatch.setattr(server, "SCREEN", object())
    monkeypatch.setattr(server, "MESSAGES", object())
    monkeypatch.setattr(server, "PhoneLock", _NoLock)
    assert server._check_idle() is None and Absent.asked == [False]


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


# ---------------------------------------------------------------- 2. Wi-Fi coupé pendant une lecture


class Link:
    """Liaison simulée : le téléphone semble joint, puis tout se tait."""

    def __init__(self) -> None:
        self.serial: str | None = "192.0.2.67:5555"
        self.lost_calls = 0

    def lost(self) -> None:
        self.lost_calls += 1
        self.serial = None

    def connect(self) -> None:
        raise AssertionError("pas de nouvelle recherche dans le même appel")

    def shell(self, *argv, **kw):
        raise AssertionError("pas de repli adb shell après une coupure")


def test_read_cut_raises_link_error_fast_without_fallback(monkeypatch):
    monkeypatch.setattr(query, "READ_WAIT", 0.5)
    silent = socket.create_server(("127.0.0.1", 0))  # accepte (file d'attente) et ne répond jamais
    link = Link()
    reads = query.QueryServer(link)  # type: ignore[arg-type]
    reads.port, reads.token, reads.serial = silent.getsockname()[1], "a" * 32, link.serial
    monkeypatch.setattr(reads, "_start", lambda: pytest.fail("pas de relance du serveur sur une coupure"))
    messages = Messages(link, fast=False)  # type: ignore[arg-type]
    messages.fast = reads
    started = time.monotonic()
    try:
        with pytest.raises(LinkError, match="no response from the phone"):
            messages._batch([["query", "--uri", "content://sms", "--projection", "_id"]])
    finally:
        silent.close()
    assert time.monotonic() - started < 2.5  # au lieu d'environ 3 min (60 s, trois fois 30 s, puis le repli)
    assert link.lost_calls == 1 and link.serial is None and reads.port is None
    assert time.monotonic() - reads.failed_at >= query.RETRY  # pas une panne du serveur : il resservira


def test_adb_timeout_drops_the_wifi_link_and_searches_again(monkeypatch):
    monkeypatch.setattr(adb_mod, "_find_adb", lambda: "adb")
    monkeypatch.delenv("NEXUS_ANDROID_SERIAL", raising=False)
    runs: list[list[str]] = []

    def run(cmd, **kw):
        runs.append(cmd)
        if "shell" in cmd:
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(adb_mod.subprocess, "run", run)
    phone = adb_mod.Adb()
    phone.serial = "192.0.2.67:5555"
    with pytest.raises(LinkError, match="no response after 5 s"):
        phone.shell("cat", "/proc/sys/kernel/random/boot_id", timeout=adb_mod.STATE_WAIT)
    assert phone.serial is None and runs[-1] == ["adb", "disconnect", "192.0.2.67:5555"]


# ---------------------------------------------------------------- 3. verrou de ce serveur et annulation


def test_process_lock_waits_at_most_lock_wait(monkeypatch):
    monkeypatch.setattr(server, "LOCK_WAIT", 0.3)
    monkeypatch.setattr(server, "PhoneLock", _NoLock)
    server.LOCK.acquire()
    try:
        started = time.monotonic()
        with pytest.raises(ToolError, match="busy with another agent"):
            asyncio.run(server._run(lambda: "fait"))
        assert time.monotonic() - started < 2
    finally:
        server.LOCK.release()
    assert asyncio.run(server._run(lambda: "fait")) == "fait"


def test_cancelled_call_stops_the_worker_thread(monkeypatch):
    monkeypatch.setattr(server, "PhoneLock", _NoLock)
    seen: dict[str, bool] = {}

    def slow() -> None:
        deadline = time.monotonic() + 5
        while not server._cancelled() and time.monotonic() < deadline:
            threading.Event().wait(0.01)
        seen["cancelled"] = server._cancelled()

    async def main() -> None:
        task = asyncio.create_task(server._run(slow))
        await asyncio.sleep(0.2)
        task.cancel()  # notifications/cancelled ou client parti : le SDK annule la tâche
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert seen == {"cancelled": True}


def test_batch_stops_between_steps_when_cancelled(phone):
    cancel = threading.Event()
    original = phone.run

    def run(*argv, timeout=30.0):
        if argv[0] == "input":
            cancel.set()  # le client abandonne pendant le premier geste
        return original(*argv, timeout=timeout)

    phone.run = run
    phone.uis = [ui(node("Un", (0, 0, 500, 200)), node("Deux", (0, 300, 500, 500)))]
    token = server._CANCEL.set(cancel)
    try:
        report = act(Step(do="tap", target="Un"), Step(do="tap", target="Deux"))
    finally:
        server._CANCEL.reset(token)
    assert report.startswith("~ 1.") and "✗ 2. Call cancelled by the client" in report and len(phone.inputs) == 1


# ---------------------------------------------------------------- 4. écran gardé allumé : 5 min, renouvelé


def test_raised_timeout_is_five_minutes_and_original_restored(tmp_path):
    assert awake.RAISED == 5 * 60 * 1000
    phone, clock = AwakePhone(timeout=60000), Clock()
    keep = KeepAwake(phone, path=tmp_path / "awake.json", idle=120, clock=clock)  # type: ignore[arg-type]
    keep.touch()
    assert phone.timeout == 300000
    phone.timeout = 30000  # remis par l'utilisateur ou un redémarrage : remonté au geste suivant
    keep.touch()
    assert phone.timeout == 300000
    clock.now += 121
    assert keep.restore() and phone.timeout == 60000 and phone.slept


def test_value_raised_by_the_previous_version_is_restored(tmp_path):
    """Serveur d'avant tué avec 30 min en place : la nouvelle version la reconnaît comme la sienne."""
    path = tmp_path / "awake.json"
    path.write_text(json.dumps({"active": True, "original": 60000, "last": 0.0}), encoding="utf-8")
    phone, clock = AwakePhone(timeout=30 * 60 * 1000), Clock()
    assert KeepAwake(phone, path=path, idle=120, clock=clock).restore()  # type: ignore[arg-type]
    assert phone.timeout == 60000
    path.write_text(json.dumps({"active": False, "original": 45000}), encoding="utf-8")
    phone.timeout = 30 * 60 * 1000
    KeepAwake(phone, path=path, idle=120, clock=clock).touch()  # type: ignore[arg-type]
    assert json.loads(path.read_text(encoding="utf-8"))["original"] == 45000  # jamais la valeur montée


def test_long_observation_pokes_the_screen_as_an_agent_gesture(phone, monkeypatch, awake_state):
    """look seul pendant plus de 3 min : KEYCODE_WAKEUP relance le délai de veille, marqué comme geste de l'agent."""
    now = time.time()
    awake_state.write_text(json.dumps({"active": True, "original": 60000, "start": now - 200, "last": now - 10,
                                       "poked": now - 200}), encoding="utf-8")
    monkeypatch.setattr(server, "_LAST_INPUT", 0.0)
    server._view("elements")
    wakeups = [c for c in phone.calls if c == ("input", "keyevent", "KEYCODE_WAKEUP")]
    assert len(wakeups) == 1 and server._LAST_INPUT >= now
    assert json.loads(awake_state.read_text(encoding="utf-8"))["poked"] >= now
    server._view("elements")  # relancé à l'instant : rien de plus
    assert len([c for c in phone.calls if c == ("input", "keyevent", "KEYCODE_WAKEUP")]) == 1
    awake_state.write_text(json.dumps({"active": True, "original": 60000, "last": now, "poked": now - 200}),
                           encoding="utf-8")
    phone.calls.clear()
    keep = KeepAwake(phone)  # type: ignore[arg-type]
    assert keep.extend(screen_on=False) is False and phone.inputs == []  # écran éteint : jamais réveillé par un look


# ---------------------------------------------------------------- 5. grosses sorties, pagination


class Book:
    def __init__(self, items: list[Message]) -> None:
        self.items = items
        self.asked: list[tuple[int, int | None]] = []

    def thread(self, conversation: int, limit: int, before_ms: int | None = None):
        self.asked.append((limit, before_ms))
        older = [m for m in self.items if before_ms is None or m.when.timestamp() * 1000 < before_ms]
        return ["Alice (0611223344)"], older[-limit:]

    def parts(self, ids):
        return {}

    def hidden(self, conversation: int) -> bool:
        return False


def test_long_thread_cut_at_message_limits_with_a_cursor(monkeypatch):
    items = [Message(datetime.fromtimestamp(1_792_000_000 + i * 60.5), i % 2 == 0, f"message {i} " + "x" * 1000, "SMS")
             for i in range(300)]
    book = Book(items)
    monkeypatch.setattr(server, "ADB", object())
    monkeypatch.setattr(server, "SCREEN", object())
    monkeypatch.setattr(server, "MESSAGES", book)
    text = server._messages(7, None, 300, 0)[0]
    assert len(text) <= MAX_TEXT + 500 and "message 299 " in text and "message 0 " not in text
    assert "output limited" in text and text.rstrip().endswith('")')
    cursor = text.rsplit('before="', 1)[1].split('"')[0]
    first_shown = int(text.split("\n")[1].split("message ")[1].split()[0])
    assert datetime.fromisoformat(cursor) == items[first_shown].when.replace(
        microsecond=items[first_shown].when.microsecond // 1000 * 1000)
    following = server._messages(7, None, 300, 0, cursor)[0]
    assert f"message {first_shown - 1} " in following and f"message {first_shown} " not in following
    assert book.asked[-1][1] == round(items[first_shown].when.timestamp() * 1000)
    with pytest.raises(PhoneError, match="ISO date expected"):
        server._messages(7, None, 300, 0, "hier")


def test_before_filters_sms_in_ms_and_mms_in_seconds():
    reads = FakeServer({})
    messages = Messages(Link(), fast=False)  # type: ignore[arg-type]
    messages.fast = reads  # type: ignore[assignment]
    messages.thread(3, 15, before_ms=1_792_000_000_500)
    sent = [" ".join(q) for call in reads.calls for q in call]
    assert any("content://sms" in q and "thread_id=3 AND date<1792000000500" in q for q in sent)
    assert any("content://mms " in q and "thread_id=3 AND date<1792000001" in q for q in sent)  # ⌈ms / 1000⌉
    reads.calls.clear()
    messages.conversation_page(15, before_ms=1_792_000_000_000)
    assert "message_count>0 AND date<1792000000000" in " ".join(reads.calls[0][0])


def test_notifications_bounded():
    dump = "  Notification List:\n" + "".join(
        f"\n    NotificationRecord(0x{i:x}: pkg=com.app{i} user=0)\n      when={1792000000000 + i}\n"
        f"      extras={{\n        android.title=String (Titre {i})\n        android.text=String ({'y' * 390})\n      }}\n"
        for i in range(200))

    class Dump:
        def shell(self, *argv, **kw):
            return dump

    text = extras.notifications(Dump(), False, 200)  # type: ignore[arg-type]
    assert len(text) <= MAX_TEXT and "older ones not shown" in text


# ---------------------------------------------------------------- 6. cible « @x,y »


def test_point_accepts_the_at_sign_of_look():
    screen = Screen(FakePhone())  # type: ignore[arg-type]
    screen.size, screen.scale = (1080, 2000), 0.5
    assert server._point(screen, "@540,1100") == server._point(screen, "540,1100") == (1080, 2200)
    assert server._point(screen, " @ 540 , 1100 ") == (1080, 2200)
    assert server._point(screen, "@@540,1100") is None


# ---------------------------------------------------------------- 7. lancement d'une appli


def test_launch_resolves_the_launcher_activity_and_polls_the_foreground(phone, monkeypatch):
    monkeypatch.setattr(server, "_PACKAGES", {})
    original = phone.run
    lists: list[int] = []

    def run(*argv, timeout=30.0):
        if argv[:2] == ("pm", "list"):
            lists.append(1)
            return 0, "package:com.whatsapp\npackage:com.android.settings\n", ""
        if argv[:3] == ("cmd", "package", "resolve-activity"):
            return 0, "priority=0 preferredOrder=0 match=0x108000\ncom.whatsapp/.Main\n", ""
        if argv[0] == "am":
            phone.package = "com.whatsapp"
        return original(*argv, timeout=timeout)

    phone.run = run
    assert "Launched: com.whatsapp (in the foreground, read back)" in server._launch("whatsapp")[0]
    server._launch("com.whatsapp")
    starts = [c for c in phone.calls if c[0] == "am"]
    assert starts[0][-2:] == ("-n", "com.whatsapp/.Main") and not any(c[0] == "monkey" for c in phone.calls)
    assert len(lists) == 1  # liste des paquets gardée entre deux lancements


def test_launch_falls_back_to_monkey_and_reports_unverified(phone, monkeypatch):
    monkeypatch.setattr(server, "_PACKAGES", {})
    monkeypatch.setattr(server, "LAUNCH_WAIT", 0.2)  # échéance réelle (monotone) : pas 5 s d'attente dans le test
    original = phone.run
    phone.run = lambda *a, timeout=30.0: (0, "package:com.example.tool\n", "") if a[:2] == ("pm", "list") else (
        (0, "No activity found\n", "") if a[:3] == ("cmd", "package", "resolve-activity") else original(*a, timeout=timeout))
    note = server._launch("tool")[0]
    assert any(c[0] == "monkey" for c in phone.calls) and "not verified" in note


# ---------------------------------------------------------------- 8. étape refusée : pas un geste de l'agent


def test_refused_step_does_not_mark_the_last_gesture(phone, monkeypatch):
    monkeypatch.setattr(server, "_LAST_INPUT", 0.0)
    phone.uis = [ui(node("Payer 12 €", (0, 0, 500, 200)), node("Ouvrir", (0, 300, 500, 500)))]
    assert act(Step(do="tap", target="Payer 12 €")).startswith("✗")
    assert server._LAST_INPUT == 0.0 and phone.inputs == []
    assert act(Step(do="tap", target="Ouvrir")).startswith("~")
    assert server._LAST_INPUT > 0


# ---------------------------------------------------------------- 9. petits plantages


def test_df_parse_survives_wrapped_or_empty_output():
    assert server._df("Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/block/dm-50 "
                      "231231232 115615616 115615616 50% /data\n") == pytest.approx((220.52, 110.26), abs=0.01)
    wrapped = ("Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/block/a-very-long-device-name\n"
               "        104857600 52428800 52428800 50% /data\n")
    assert server._df(wrapped) == (100.0, 50.0)
    assert server._df("") is None and server._df("df: /data: Permission denied\n") is None


def test_hand_installed_apks_are_outside_the_stores():
    listing = ("package:com.whatsapp  installer=com.android.vending\npackage:org.fdroid.fdroid  installer=null\n"
               "package:com.sec.app  installer=com.sec.android.app.samsungapps\n")
    assert server._outside_stores(listing) == ["org.fdroid.fdroid"]


def test_brightness_null_and_bad_values():
    class Settings:
        def shell(self, *argv, **kw):
            return {"screen_brightness_mode": "0", "screen_brightness": "null"}.get(argv[-1], "")

    assert extras.setting(Settings(), "brightness", None, "usb") == ("brightness: ?", "?")  # type: ignore[arg-type]
    with pytest.raises(PhoneError, match="1-100"):
        extras.setting(Settings(), "brightness", "fort", "usb")  # type: ignore[arg-type]
    assert extras._percent("40 %", "") == 40
