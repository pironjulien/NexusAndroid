"""capture : capture d'écran gardée en fichier (codes noircis) et vidéo de l'écran (screenrecord), avec un adb simulé.
Aucun vrai téléphone : le pid, l'arrêt par SIGINT, le rapatriement et l'effacement sont joués par le faux adb."""

from __future__ import annotations

import asyncio
import io
import struct
import subprocess
import threading
import time

import pytest
from PIL import Image as PILImage
from test_extras import home  # noqa: F401  (fixture home : profil Windows simulé)
from test_guards import FakePhone, node, ui
from test_robustness import _NoLock

from nexus_android import capture, screen, server
from nexus_android.adb import PhoneError

BANK = "fr.creditagricole.androidapp"


def mp4(seconds: float = 5.0) -> bytes:
    """MP4 minimal : ftyp, mdat, puis moov avec son en-tête mvhd (version 0, échelle 1000)."""
    def box(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", 8 + len(body)) + kind + body
    mvhd = bytes([0, 0, 0, 0]) + struct.pack(">IIII", 0, 0, 1000, round(seconds * 1000)) + bytes(80)
    return box(b"ftyp", b"isom" + bytes(4) + b"isommp41") + box(b"mdat", bytes(2048)) + box(b"moov", box(b"mvhd", mvhd))


class FakeProc:
    """`adb shell "echo $$; exec screenrecord …"` : pid écrit d'abord, en vie jusqu'au SIGINT (ou mort tout de suite)."""

    def __init__(self, alive: bool = True, output: bytes = b"") -> None:
        self.stdout = io.BytesIO(b"4242\n" + output)
        self.code: int | None = None if alive else 1

    def poll(self) -> int | None:
        return self.code

    def wait(self, timeout: float | None = None) -> int:
        if self.code is None:
            raise subprocess.TimeoutExpired("adb", timeout or 0)
        return self.code

    def kill(self) -> None:
        self.code = -9

    def communicate(self, timeout: float | None = None) -> tuple[bytes, None]:
        return self.stdout.read(), None


class RecPhone(FakePhone):
    def __init__(self) -> None:
        super().__init__()
        self.spawned: list[str] = []
        self.procs: list[FakeProc] = []
        self.pulls: list[tuple[str, ...]] = []
        self.other = ""  # pid d'un screenrecord d'un autre agent
        self.reject_size = False
        self.pull_fails = False
        self.video = mp4()
        self.awake = True
        self.lock_on_wake = False  # écran de verrouillage affiché dès que l'écran s'allume
        self.captures = 0

    def state_text(self) -> str:
        return super().state_text().replace("Awake", "Awake" if self.awake else "Asleep", 1)

    def exec_out(self, *argv: str, timeout: float = 30.0) -> bytes:
        self.captures += 1
        return super().exec_out(*argv, timeout=timeout)

    def run(self, *argv: str, timeout: float = 30.0) -> tuple[int, str, str]:
        if argv == ("input", "keyevent", "KEYCODE_WAKEUP"):
            self.calls.append(argv)
            self.awake = True
            if self.lock_on_wake:
                self.lock = "isKeyguardShowing=true"
            return 0, "", ""
        if argv[0] == "pidof":
            self.calls.append(argv)
            return (0, self.other, "") if self.other else (1, "", "")
        if argv[:2] == ("wm", "size"):
            self.calls.append(argv)
            return 0, "Physical size: 1248x1972\n", ""
        if argv[0] == "ps":  # screenrecord lancé (commande exec) et le shell d'un autre outil qui cite son fichier
            self.calls.append(argv)
            rows = [f"  {4242 + i} {c.split('exec ', 1)[1].removesuffix(' 2>&1')}" for i, c in enumerate(self.spawned)
                    if self.procs[i].code is None]
            return 0, "  PID ARGS\n  77 sh -c ls " + " ".join(rows) + "\n" + "\n".join(rows) + "\n", ""
        if argv[:2] == ("kill", "-KILL"):
            self.calls.append(argv)
            return 0, "", ""
        if argv[:2] == ("kill", "-INT"):
            self.calls.append(argv)
            for proc in self.procs:
                if proc.code is None:
                    proc.code = 0
            return 0, "", ""
        return super().run(*argv, timeout=timeout)

    def background(self, command: str) -> FakeProc:
        self.spawned.append(command)
        proc = FakeProc(alive=False, output=b"Encoder failed") if self.reject_size and "--size" in command else FakeProc()
        self.procs.append(proc)
        return proc

    def host(self, *args: str, timeout: float = 60.0) -> str:
        self.pulls.append(args)
        if self.pull_fails:
            raise PhoneError("adb pull: device offline")
        with open(args[2], "wb") as f:
            f.write(self.video)
        return ""

    @property
    def removed(self) -> list[tuple[str, ...]]:
        return [c for c in self.calls if c[:2] == ("rm", "-f")]


@pytest.fixture
def rec(monkeypatch, home):  # noqa: F811
    fake = RecPhone()
    monkeypatch.setattr(server, "ADB", fake)
    monkeypatch.setattr(server, "SCREEN", screen.Screen(fake))  # type: ignore[arg-type]
    monkeypatch.setattr(server, "MESSAGES", object())
    monkeypatch.setattr(server, "PhoneLock", _NoLock)
    monkeypatch.setattr(capture, "START_CHECK", 0.0)
    monkeypatch.setattr(capture, "_CURRENT", None)
    monkeypatch.setattr("time.sleep", lambda s: None)
    return fake


def inbox(home_dir) -> list:  # noqa: ANN001
    folder = home_dir / "Downloads" / "Telephone"
    return sorted(folder.iterdir()) if folder.exists() else []


# ---------------------------------------------------------------- vidéo


def test_start_then_stop_pulls_and_deletes(rec, home):  # noqa: F811
    started = capture.start(rec, None, "bug wifi", None, False)  # type: ignore[arg-type]
    assert "Screen recording started (720×1136, 4 Mbit/s, stops by itself after 180 s)" in started
    assert "not masked in videos" in started
    command = rec.spawned[0]
    assert command.startswith("echo $$; exec screenrecord --time-limit 180 --bit-rate 4000000 --size 720x1136 ")
    remote = command.split()[-2]
    assert remote.startswith("/data/local/tmp/nexus-android-rec-") and remote.endswith(".mp4")
    rec.calls.append(("input", "tap", "1", "1"))  # gestes possibles pendant l'enregistrement : rien de bloqué
    saved = capture.stop(rec)  # type: ignore[arg-type]
    assert ("kill", "-INT", "4242") in rec.calls
    target = home / "Downloads" / "Telephone" / "bug wifi.mp4"
    assert rec.pulls == [("pull", remote, str(target))]
    assert rec.removed == [("rm", "-f", remote)]
    assert target.read_bytes() == rec.video
    assert f"Screen recording saved on the PC: {target} (5.0 s, 2 KB, 720×1136)" in saved
    assert "not masked in videos" in saved and "FLAG_SECURE" in saved
    with pytest.raises(PhoneError, match="no recording started"):
        capture.stop(rec)  # type: ignore[arg-type]


def test_duration_capped_at_180_seconds_and_record_waits_then_saves(rec, home):  # noqa: F811
    capture.start(rec, 999, None, None, False)  # type: ignore[arg-type]
    assert "--time-limit 180 " in rec.spawned[0]
    capture.stop(rec)  # type: ignore[arg-type]
    rec.video = mp4(12.0)
    text = asyncio.run(server.capture("record", seconds=12))
    assert "--time-limit 12 " in rec.spawned[1]
    assert "Screen recording saved on the PC" in text and "(12.0 s," in text
    assert len(inbox(home)) == 2 and all(p.name.startswith("screen-recording-") for p in inbox(home))
    asyncio.run(server.capture("record", seconds=500))
    assert "--time-limit 180 " in rec.spawned[2]


def test_one_recording_at_a_time(rec):
    capture.start(rec, 30, None, None, False)  # type: ignore[arg-type]
    with pytest.raises(PhoneError, match="already running"):
        capture.start(rec, 30, None, None, False)  # type: ignore[arg-type]
    capture.stop(rec)  # type: ignore[arg-type]
    rec.other = "999"  # screenrecord d'un autre agent ou d'une appli
    with pytest.raises(PhoneError, match="already recording the screen"):
        capture.start(rec, 30, None, None, False)  # type: ignore[arg-type]
    assert len(rec.spawned) == 1


def test_finished_recording_collected_before_the_next_one(rec, home):  # noqa: F811
    capture.start(rec, 5, "first", None, False)  # type: ignore[arg-type]
    rec.procs[0].code = 0  # limite atteinte : screenrecord s'est arrêté seul
    text = capture.start(rec, 5, "second", None, False)  # type: ignore[arg-type]
    assert "Previous recording, which had ended, collected" in text and "first.mp4" in text
    assert ("kill", "-INT", "4242") not in rec.calls
    capture.stop(rec)  # type: ignore[arg-type]
    assert [p.name for p in inbox(home)] == ["first.mp4", "second.mp4"]


def test_bank_read_just_after_a_natural_end_keeps_the_video(rec, home, monkeypatch):  # noqa: F811
    """Relecture Codex (tour 4, A11) : screenrecord fini seul, puis un outil lit une banque au premier plan avant la
    prochaine lecture du premier plan (WATCH_EVERY) ; la fin, guettée toutes les END_POLL s, est déjà datée et la
    vidéo saine est gardée."""
    monkeypatch.setattr(capture, "END_POLL", 0.005)
    capture.start(rec, 5, "fin", None, False)  # type: ignore[arg-type]
    current = capture._CURRENT
    assert current is not None and current.ender is not None
    rec.procs[0].code = 0  # limite atteinte : screenrecord s'est arrêté seul
    current.ender.join(timeout=2)
    assert current.ended is not None and not current.ender.is_alive()
    capture._seen(BANK, screen.stamp())  # lecture d'un outil commencée après la fin
    assert current.tainted == ""
    capture.stop(rec)  # type: ignore[arg-type]
    assert [p.name for p in inbox(home)] == ["fin.mp4"]


def test_natural_end_dated_while_a_foreground_reading_is_stuck(rec, home, monkeypatch):  # noqa: F811
    """Relecture Codex (0.9.4) : la fin naturelle survient pendant une lecture ADB lente du premier plan (_observe,
    jusqu'à STATE_WAIT s) ; elle est datée quand même, et une banque lue par un outil juste après ne jette pas la vidéo
    saine."""
    monkeypatch.setattr(capture, "END_POLL", 0.005)
    monkeypatch.setattr(capture, "WATCH_EVERY", 0.01)
    entered, release = threading.Event(), threading.Event()
    shell = rec.shell

    def slow(*argv, **kwargs):
        if (capture.STATE_SH in argv and not release.is_set()
                and threading.current_thread().name == "nexus-android-video-watch"):
            entered.set()
            release.wait(5)
        return shell(*argv, **kwargs)

    monkeypatch.setattr(rec, "shell", slow)
    capture.start(rec, 5, "lente", None, False)  # type: ignore[arg-type]
    current = capture._CURRENT
    assert current is not None and entered.wait(2)  # la surveillance est bloquée dans sa lecture ADB
    rec.procs[0].code = 0  # limite atteinte pendant cette lecture
    deadline = time.monotonic() + 2
    while current.ended is None and time.monotonic() < deadline:
        threading.Event().wait(0.005)
    assert current.ended is not None
    capture._seen(BANK, screen.stamp())  # lecture d'un outil commencée après la fin
    release.set()
    assert current.tainted == ""
    capture.stop(rec)  # type: ignore[arg-type]
    assert [p.name for p in inbox(home)] == ["lente.mp4"]


def test_slow_reading_returning_after_the_end_is_not_unwatched_time(rec, home, monkeypatch):  # noqa: F811
    """Relecture Codex (0.9.4, second tour) : une lecture du premier plan commencée pendant la vidéo revient longtemps
    après sa fin ; l'attente d'après la fin ne compte pas comme un trou de surveillance (vidéo saine gardée)."""
    monkeypatch.setattr(capture, "END_POLL", 0.005)
    monkeypatch.setattr(capture, "WATCH_EVERY", 0.01)
    monkeypatch.setattr(capture, "WATCH_GAP", 0.3)
    entered, release = threading.Event(), threading.Event()
    shell = rec.shell

    def slow(*argv, **kwargs):
        if (capture.STATE_SH in argv and not release.is_set()
                and threading.current_thread().name == "nexus-android-video-watch"):
            entered.set()
            release.wait(5)
        return shell(*argv, **kwargs)

    monkeypatch.setattr(rec, "shell", slow)
    capture.start(rec, 5, "tardive", None, False)  # type: ignore[arg-type]
    current = capture._CURRENT
    assert current is not None and current.watcher is not None and entered.wait(2)
    rec.procs[0].code = 0  # fin pendant la lecture
    deadline = time.monotonic() + 2
    while current.ended is None and time.monotonic() < deadline:
        threading.Event().wait(0.005)
    assert current.ended is not None
    threading.Event().wait(0.6)  # la lecture revient bien après la fin (plus que WATCH_GAP)
    release.set()
    current.watcher.join(timeout=2)
    assert current.unseen() < capture.WATCH_GAP
    assert "Screen recording saved on the PC" in capture.stop(rec)  # type: ignore[arg-type]


def test_bank_shown_briefly_while_the_recording_starts_discards_it(rec, home, monkeypatch):  # noqa: F811
    """Relecture Codex (0.9.4, hors diff) : une banque passée devant pendant le démarrage de screenrecord (avant le pid
    et START_CHECK) était filmée sans être vue ; la surveillance part maintenant dès le lancement."""
    monkeypatch.setattr(capture, "END_POLL", 0.005)
    monkeypatch.setattr(capture, "WATCH_EVERY", 0.01)
    background, home_app = rec.background, rec.package

    class SlowStart(FakeProc):
        def __init__(self, proc: FakeProc) -> None:
            self.__dict__.update(proc.__dict__)
            self.pid_line = proc.stdout

        @property
        def stdout(self):  # pid écrit seulement une fois la banque partie
            deadline = time.monotonic() + 2
            while rec.package == BANK and not rec.removed and time.monotonic() < deadline:
                threading.Event().wait(0.005)  # jusqu'à ce que la surveillance voie la banque et efface la vidéo
            rec.package = home_app
            return self.pid_line

        @stdout.setter
        def stdout(self, value) -> None:
            pass

    def launch(command: str) -> FakeProc:
        proc = SlowStart(background(command))
        rec.procs[-1] = proc
        rec.package = BANK  # banque ouverte juste après le lancement
        return proc

    monkeypatch.setattr(rec, "background", launch)
    with pytest.raises(PhoneError, match="while the recording was starting"):
        capture.start(rec, 10, "debut", None, False)  # type: ignore[arg-type]
    assert capture._CURRENT is None and inbox(home) == []
    assert rec.removed  # fichier effacé du téléphone
    killed = [c for c in rec.calls if c[:2] == ("kill", "-KILL")]
    assert set(killed) == {("kill", "-KILL", "4242")}  # pid pas encore lu : screenrecord retrouvé par son fichier, seul
    with pytest.raises(PhoneError, match="no recording started"):
        capture.stop(rec)  # type: ignore[arg-type]


def test_late_bank_reading_of_a_failed_try_is_settled_before_the_fallback(rec, home, monkeypatch):  # noqa: F811
    """Relecture Codex (0.9.5) : la lecture du premier essai (taille refusée) revient avec une banque après l'échec ;
    elle est soldée avant le repli, et le démarrage est refusé au lieu d'effacer plus tard le fichier du second essai."""
    monkeypatch.setattr(capture, "END_POLL", 0.005)
    monkeypatch.setattr(capture, "WATCH_EVERY", 0.01)
    entered = threading.Event()
    shell, background = rec.shell, rec.background

    def slow(*argv, **kwargs):
        if capture.STATE_SH in argv and threading.current_thread().name == "nexus-android-video-watch":
            if not entered.is_set():
                entered.set()
                threading.Event().wait(0.3)  # lecture lente : la banque arrive pendant ce temps
                rec.package = BANK
        return shell(*argv, **kwargs)

    class FailsOnceRead(FakeProc):
        @property
        def stdout(self):
            entered.wait(2)  # le premier essai meurt pendant la lecture de sa surveillance
            self.code = 1
            return io.BytesIO(b"4242\nEncoder failed")

        @stdout.setter
        def stdout(self, value) -> None:
            pass

    def launch(command: str) -> FakeProc:
        if rec.procs:
            return background(command)
        rec.spawned.append(command)
        proc = FailsOnceRead()
        rec.procs.append(proc)
        return proc

    monkeypatch.setattr(rec, "shell", slow)
    monkeypatch.setattr(rec, "background", launch)
    with pytest.raises(PhoneError, match="while the recording was starting"):
        capture.start(rec, 10, "repli", None, False)  # type: ignore[arg-type]
    assert len(rec.spawned) == 1 and capture._CURRENT is None and inbox(home) == []


def test_fallback_try_has_its_own_file_and_unwatched_time(rec):
    """Relecture Codex (0.9.5) : chaque essai a son fichier, et son temps non surveillé part de son propre lancement."""
    rec.reject_size = True
    capture.start(rec, 10, None, None, False)  # type: ignore[arg-type]
    first, second = (c.split()[-2] for c in rec.spawned)  # « … <fichier> 2>&1 »
    assert first != second
    current = capture._CURRENT
    assert current is not None and current.remote == second
    assert current.last_seen == current.started and current.blind == 0.0
    assert ("rm", "-f", first) in rec.removed and ("rm", "-f", second) not in rec.removed


def test_no_recording_in_a_protected_app(rec):
    rec.package = BANK
    with pytest.raises(PhoneError, match="no screen recording"):
        capture.start(rec, 10, None, None, True)  # type: ignore[arg-type]
    assert rec.spawned == []


def test_size_refused_by_the_encoder_falls_back_to_screen_resolution(rec):
    rec.reject_size = True
    text = capture.start(rec, 10, None, None, False)  # type: ignore[arg-type]
    assert len(rec.spawned) == 2 and "--size" not in rec.spawned[1]
    assert "screen resolution" in text


def test_pull_failure_keeps_the_file_on_the_phone_for_a_retry(rec, home):  # noqa: F811
    capture.start(rec, 10, None, None, False)  # type: ignore[arg-type]
    rec.pull_fails = True
    with pytest.raises(PhoneError, match="stays on the phone"):
        capture.stop(rec)  # type: ignore[arg-type]
    assert rec.removed == [] and inbox(home) == []
    rec.pull_fails = False
    assert "saved on the PC" in capture.stop(rec)  # type: ignore[arg-type]
    assert len(rec.removed) == 1


def test_recording_stopped_and_saved_when_the_server_stops(rec, home):  # noqa: F811
    capture.start(rec, 60, None, None, False)  # type: ignore[arg-type]
    capture.shutdown()
    assert ("kill", "-INT", "4242") in rec.calls and len(inbox(home)) == 1 and capture._CURRENT is None


def test_video_folder_follows_the_files_rules(rec, home):  # noqa: F811
    with pytest.raises(PhoneError, match="confirm=true"):
        capture.start(rec, 10, None, str(home / "Projets"), False)  # type: ignore[arg-type]
    with pytest.raises(PhoneError, match="sensitive path"):
        capture.start(rec, 10, None, str(home / ".ssh"), True)  # type: ignore[arg-type]
    assert rec.spawned == []


def test_mp4_duration_unreadable_without_index(tmp_path):
    cut = tmp_path / "cut.mp4"
    cut.write_bytes(mp4()[:-120])  # moov tronqué : pas d'index
    assert capture.mp4_duration(cut) is None
    whole = tmp_path / "whole.mp4"
    whole.write_bytes(mp4(7.5))
    assert capture.mp4_duration(whole) == 7.5


def test_video_size_keeps_proportions():
    assert capture.video_size(1248, 1972) == "720x1136"
    assert capture.video_size(1972, 1248) == "1136x720"
    assert capture.video_size(1080, 2340) == "720x1568"
    assert capture.video_size(600, 1000) == "592x992"  # jamais agrandi
    assert capture.video_size(0, 0) == ""


# ---------------------------------------------------------------- capture d'écran


def test_screenshot_full_resolution_with_codes_blacked_out(rec, home):  # noqa: F811
    title = node("Code de vérification", (0, 100, 900, 160), clickable=False)
    body = node("482913", (0, 170, 900, 230), clickable=False)
    rec.uis = [ui(title, body)]
    text = capture.screenshot(rec, "facture", None, False)  # type: ignore[arg-type]
    target = home / "Downloads" / "Telephone" / "facture.png"
    assert text.startswith(f"Screenshot saved on the PC: {target} (1080×2000,") and "blacked out" in text
    image = PILImage.open(target).convert("RGB")
    assert image.size == (1080, 2000)
    assert max(image.getpixel((450, 200))) < 60  # type: ignore[arg-type]  # code noirci
    assert min(image.getpixel((450, 1000))) > 200  # type: ignore[arg-type]  # reste intact
    capture.screenshot(rec, "facture.jpg", None, False)  # type: ignore[arg-type]
    capture.screenshot(rec, "facture", None, False)  # type: ignore[arg-type]
    assert [p.name for p in inbox(home)] == ["facture (1).png", "facture.jpg", "facture.png"]
    assert PILImage.open(home / "Downloads" / "Telephone" / "facture.jpg").format == "JPEG"


def test_screenshot_refused_without_tree_or_in_a_protected_app(rec, home):  # noqa: F811
    rec.uis = [""]  # arbre illisible : un code ne serait pas noirci
    with pytest.raises(PhoneError, match="would not be blacked out"):
        capture.screenshot(rec, None, None, False)  # type: ignore[arg-type]
    with pytest.raises(PhoneError, match="unmasked=true"):  # confirm (dossier, appli) ne vaut pas accord du masquage
        capture.screenshot(rec, None, None, True)  # type: ignore[arg-type]
    assert "without code masking" in capture.screenshot(rec, None, None, False, unmasked=True)  # type: ignore[arg-type]
    rec.uis = [ui(node("Solde", (0, 0, 500, 100)))]
    rec.package = BANK
    with pytest.raises(PhoneError, match="banking, payment or identity"):
        capture.screenshot(rec, None, None, False)  # type: ignore[arg-type]
    assert "explicit order" in capture.screenshot(rec, None, None, True)  # type: ignore[arg-type]
    assert len(inbox(home)) == 2


def test_black_screenshot_says_flag_secure(rec):
    black = io.BytesIO()
    PILImage.new("RGB", (1080, 2000), "black").save(black, "PNG")
    rec.png = black
    rec.uis = [ui(node("Film", (0, 0, 500, 100)))]
    assert "FLAG_SECURE" in capture.screenshot(rec, None, None, False)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["../x.png", "a/b.png", "c:x.png", "report.txt", "..", "trailing."])
def test_bad_file_names_refused(name):
    with pytest.raises(PhoneError):
        capture.file_name(name, "screenshot", capture.IMAGE_TYPES)


def test_file_names():
    assert capture.file_name("report", "screenshot", capture.IMAGE_TYPES) == "report.png"
    assert capture.file_name("Report.JPEG", "screenshot", capture.IMAGE_TYPES) == "Report.JPEG"
    assert capture.file_name(None, "screen-recording", capture.VIDEO_TYPES).startswith("screen-recording-")


# ---------------------------------------------------------------- écran éteint ou téléphone verrouillé


WAKE = ("input", "keyevent", "KEYCODE_WAKEUP")


def test_screen_off_is_woken_as_an_agent_gesture_before_capturing(rec, monkeypatch):
    kept: list[object] = []
    monkeypatch.setattr(server, "_keep_awake", lambda adb, state: kept.append(state))
    monkeypatch.setattr(server, "_LAST_INPUT", 0.0)
    rec.awake = False
    rec.uis = [ui(node("Film", (0, 0, 500, 100)))]
    text = asyncio.run(server.capture("screenshot"))
    assert rec.calls.count(WAKE) == 1 and "turned on (KEYCODE_WAKEUP)" in text and "black" not in text
    assert server._LAST_INPUT > 0 and len(kept) == 1  # geste de l'agent, écran gardé allumé
    rec.awake = False
    capture.start(rec, 10, None, None, False)  # type: ignore[arg-type]
    assert rec.calls.count(WAKE) == 2 and len(rec.spawned) == 1
    capture.stop(rec)  # type: ignore[arg-type]


@pytest.mark.parametrize("awake", [True, False])
def test_locked_phone_refused_without_capture_or_code(rec, home, awake):  # noqa: F811
    rec.awake = awake
    rec.lock = "isKeyguardShowing=true"
    for action in ("screenshot", "record", "start"):
        with pytest.raises(Exception, match="Phone locked: nothing captured.*unlock.*never types a code"):
            asyncio.run(server.capture(action, seconds=5))  # type: ignore[arg-type]
    assert rec.captures == 0 and rec.spawned == [] and WAKE not in rec.calls and inbox(home) == []
    assert not any(c[0] == "input" and c[1] != "keyevent" for c in rec.calls)


def test_lock_screen_shown_on_wake_refused(rec, home):  # noqa: F811
    rec.awake = False
    rec.lock_on_wake = True
    with pytest.raises(PhoneError, match="lock screen.*Phone locked"):
        capture.screenshot(rec, None, None, False)  # type: ignore[arg-type]
    with pytest.raises(PhoneError, match="Phone locked"):
        capture.start(rec, 5, None, None, False)  # type: ignore[arg-type]
    assert rec.captures == 0 and rec.spawned == [] and inbox(home) == []
