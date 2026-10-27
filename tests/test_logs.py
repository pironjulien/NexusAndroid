"""logs : plantages groupés, ANR, dernières lignes d'une application, masquage des données personnelles et refus devant
une application protégée, avec un adb simulé (sorties de logcat, ps et dumpsys inventées)."""

from __future__ import annotations

import asyncio

import pytest
from test_english import french
from test_robustness import _NoLock

from nexus_android import logs, server
from nexus_android.adb import PhoneError

BANK = "fr.creditagricole.androidapp"


def line(stamp: str, pid: int, level: str, tag: str, msg: str, tid: int | None = None) -> str:
    return f"2026-10-19 {stamp} {pid:>5} {tid or pid:>5} {level} {tag:<8}: {msg}"


def java_crash(stamp: str, pid: int, package: str, message: str, app_frame: str = "com.foo.Main.onCreate") -> list[str]:
    body = ["FATAL EXCEPTION: main", f"Process: {package}, PID: {pid}",
            f"java.lang.IllegalStateException: {message}",
            "\tat android.app.Activity.performCreate(Activity.java:8000)",
            f"\tat {app_frame}(Main.kt:42)",
            "\tat android.os.Looper.loop(Looper.java:300)",
            "Caused by: java.lang.NullPointerException: user secret field",
            "\tat java.util.Objects.requireNonNull(Objects.java:200)",
            "\t... 12 more"]
    return [line(stamp, pid, "E", "AndroidRuntime", m) for m in body]


NATIVE = [line("14:00:01.000", 900, "F", "DEBUG", m) for m in (
    "*** *** *** *** *** *** *** *** *** *** *** *** *** *** *** ***",
    "Cmdline: com.game",
    "pid: 880, tid: 881, name: RenderThread  >>> com.game <<<",
    "signal 11 (SIGSEGV), code 1 (SEGV_MAPERR), fault addr 0x0",
    "Cause: null pointer dereference",
    "backtrace:",
    "      #00 pc 000000000004f0a8  /data/app/~~x/com.game/lib/arm64/libgame.so (draw+12) (BuildId: abcdef0123)",
    "      #01 pc 0000000000012345  /apex/com.android.runtime/lib64/bionic/libc.so (abort+164)",
)]
SIGNAL = line("14:00:00.900", 880, "F", "libc", "Fatal signal 11 (SIGSEGV), code 1, fault addr 0x0 in tid 881 "
              "(RenderThread), pid 880 (com.game)")


class LogPhone:
    """adb simulé : tampons de logcat, liste des processus, boîte de dépôt et heure du téléphone."""

    def __init__(self) -> None:
        self.crash: list[str] = []
        self.main: list[str] = []
        self.events: list[str] = []
        self.dropbox = ""
        self.ps = "  PID NAME\n    1 init\n 4321 com.foo\n 4322 com.foo:remote\n 5555 " + BANK + "\n"
        self.calls: list[tuple[str, ...]] = []

    def shell(self, *argv: str, timeout: float = 30.0, check: bool = True) -> str:
        self.calls.append(argv)
        if argv[0] == "date":
            return "2026-10-19 15:00:00\n"
        if argv[0] == "ps":
            return self.ps
        if argv[0] == "sh" and "dropbox" in argv[2]:
            return self.dropbox
        if argv[0] == "logcat":
            buffer = argv[argv.index("-b") + 1]
            return "\n".join({"crash": self.crash, "events": self.events}.get(buffer, self.main)) + "\n"
        if argv[:3] == ("pm", "list", "packages"):
            return "package:com.foo\npackage:com.game\npackage:" + BANK + "\n"
        return ""


@pytest.fixture
def fake(monkeypatch):
    phone = LogPhone()
    monkeypatch.setattr(server, "ADB", phone)
    monkeypatch.setattr(server, "SCREEN", object())
    monkeypatch.setattr(server, "MESSAGES", object())
    monkeypatch.setattr(server, "PhoneLock", _NoLock)
    monkeypatch.setattr(server, "_PACKAGES", {})
    return phone


def run(**kw) -> str:
    return asyncio.run(server.logs(**kw))


# ---------------------------------------------------------------- plantages


def test_crashes_grouped_one_per_block_newest_last(fake):
    fake.crash = (["--------- beginning of crash"] + java_crash("10:00:00.000", 4321, "com.foo", "first")
                  + java_crash("11:00:00.000", 7777, "com.other", "second", "org.other.Thing.run")
                  + [SIGNAL] + NATIVE)
    text = run(kind="crash")
    assert text.startswith("Crashes (crash log since 10-19 10:00:00; newest last;") and ": 3 crash(es)." in text
    blocks = text.split("\n\n")[1:]
    assert [b.splitlines()[0] for b in blocks] == [
        "10-19 10:00:00 com.foo (pid 4321): Java exception",
        "10-19 11:00:00 com.other (pid 7777): Java exception",
        "10-19 14:00:01 com.game (pid 900): native crash",  # la ligne libc « Fatal signal » du même plantage en moins
    ]
    first = blocks[0]
    assert "java.lang.IllegalStateException: first" in first and "Caused by: java.lang.NullPointerException" in first
    assert "at android.app.Activity.performCreate(Activity.java:8000)" in first  # sommet de la pile
    assert "at com.foo.Main.onCreate(Main.kt:42)" in first  # première ligne de l'application
    assert "Looper" not in first and "1 more frame(s) (framework)" in first
    native = blocks[2]
    assert "signal 11 (SIGSEGV)" in native and "Cause: null pointer dereference" in native
    assert "libgame.so (draw+12)" in native and "BuildId" not in native


def test_crash_filtered_by_app_and_time(fake):
    fake.crash = java_crash("10:00:00.000", 4321, "com.foo", "old") + java_crash(
        "14:30:00.000", 4322, "com.foo:remote", "new") + java_crash("14:40:00.000", 1, "com.foobar", "other")
    text = run(kind="crash", app="com.foo")
    assert ": 2 crash(es)." in text and "com.foobar" not in text
    text = run(kind="crash", app="com.foo", since="1h")  # heure du téléphone : 15:00
    assert ": 1 crash(es)." in text and "new" in text and "old" not in text
    assert "No crash of com.game" in run(kind="crash", app="game")


def test_crash_of_an_uninstalled_app_by_full_package_name(fake):
    fake.crash = java_crash("10:00:00.000", 10, "org.gone.app", "bye", "org.gone.app.Main.x")
    assert "org.gone.app (pid 10)" in run(kind="crash", app="org.gone.app")
    with pytest.raises(Exception, match="no app matches"):
        run(kind="crash", app="gone")


def test_protected_app_crash_shows_type_and_app_frames_only(fake):
    fake.crash = java_crash("10:00:00.000", 5555, BANK, "IBAN FR76 3000 6000 0112 3456 7890 189",
                            "fr.creditagricole.androidapp.Login.check")
    text = run(kind="crash")
    assert "Ma Banque (Crédit Agricole): exception type and app frames only" in text
    assert "  java.lang.IllegalStateException\n" in text and "IBAN" not in text
    assert "Caused by: java.lang.NullPointerException" in text and "user secret field" not in text
    assert "at fr.creditagricole.androidapp.Login.check(Main.kt:42)" in text
    assert "Activity.performCreate" not in text  # pile du système : pas de l'application


def test_crash_output_bounded_to_the_newest(fake):
    fake.crash = [x for i in range(400) for x in java_crash(f"10:{i // 60:02d}:{i % 60:02d}.000", 1000 + i,
                                                             "com.foo", f"boom {i} " + "x" * 300)]
    text = run(kind="crash")
    assert len(text) <= logs.MAX_LOG + 300
    assert "400 crash(es), the" in text and "most recent shown" in text
    assert "(pid 1399)" in text and "(pid 1000)" not in text


def shell_crash(stamp: str, pid: int, proxy: str) -> list[str]:
    """Plantage d'un processus sans paquet (app_process lancé par le shell), tel qu'Android l'écrit : l'exception,
    puis sa réimpression après « Couldn't report crash », puis l'erreur du signalement."""
    exception = [f"java.lang.IllegalStateException: UiAutomationService android.accessibilityservice."
                 f"IAccessibilityServiceClient$Stub$Proxy@{proxy}already registered!",
                 "\tat android.os.Parcel.createExceptionOrNull(Parcel.java:3415)",
                 "\tat android.app.UiAutomation.connect(UiAutomation.java:310)",
                 "\tat NexusQuery.main(NexusQuery.java:71)",
                 "Caused by: android.os.RemoteException: Remote stack trace:",
                 "\tat com.android.server.accessibility.UiAutomationManager.register(UiAutomationManager.java:179)"]
    body = (["FATAL EXCEPTION: main", f"PID: {pid}", *exception, "Couldn't report crash. Here's the crash:",
             *exception, "Error reporting crash. Here's the error:", "java.lang.RuntimeException: Bad file descriptor",
             "\tat android.os.BinderProxy.transactNative(Native Method)",
             "\tat java.lang.Thread.dispatchUncaughtException(Thread.java:3380)"])
    return [line(stamp, pid, "E", "AndroidRuntime", m) for m in body]


def test_identical_crashes_in_one_block_and_reprint_dropped(fake):
    fake.crash = (shell_crash("15:44:46.168", 698, "c118856") + java_crash("15:50:00.000", 4321, "com.foo", "other")
                  + shell_crash("15:52:10.000", 1234, "a1b2c3d") + shell_crash("16:02:10.500", 5678, "ffee001"))
    text = run(kind="crash")
    assert ": 4 crash(es) in 2 distinct block(s)." in text
    blocks = text.split("\n\n")[1:]
    assert [b.splitlines()[0] for b in blocks] == [  # groupe placé à son dernier plantage : le plus récent en dernier
        "10-19 15:50:00 com.foo (pid 4321): Java exception",
        "process without package (shell, app_process): Java exception ×3 (first 10-19 15:44:46, last 16:02:10, "
        "pids 698, 1234, 5678)",
    ]
    shell = blocks[1]
    assert shell.count("UiAutomationService") == 1 and "Couldn't report crash" not in shell
    assert shell.count("Caused by: android.os.RemoteException") == 1
    assert "@ffee001already" in shell  # exemple : le plus récent
    assert "  (error reporting crash: java.lang.RuntimeException: Bad file descriptor)" in shell
    assert "BinderProxy" not in shell and "dispatchUncaughtException" not in shell
    assert "at NexusQuery.main(NexusQuery.java:71)" in shell


def test_crashes_that_differ_stay_apart(fake):
    fake.crash = java_crash("10:00:00.000", 10, "com.foo", "first") + java_crash("10:01:00.000", 11, "com.foo", "second")
    text = run(kind="crash")
    assert ": 2 crash(es)." in text and "×" not in text
    fake.crash = java_crash("10:00:00.000", 10, "com.foo", "same") + java_crash("10:01:00.000", 11, "com.bar", "same")
    assert "×" not in run(kind="crash")  # même exception, autre application


def test_reprint_of_a_different_exception_is_kept(fake):
    fake.crash = [line("10:00:00.000", 50, "E", "AndroidRuntime", m) for m in (
        "FATAL EXCEPTION: main", "PID: 50", "java.lang.IllegalStateException: first",
        "\tat NexusQuery.main(NexusQuery.java:1)", "Couldn't report crash. Here's the crash:",
        "java.lang.IllegalArgumentException: second", "\tat NexusQuery.main(NexusQuery.java:2)")]
    text = run(kind="crash")
    assert "IllegalStateException: first" in text and "IllegalArgumentException: second" in text


# ---------------------------------------------------------------- masquage


@pytest.mark.parametrize("raw, hidden", [
    ("Your verification code is 482913", "482913"),
    ("otp 7391 from server", "7391"),
    ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456", "abcdefghijklmnop"),
    ("token=Zx9KqLm2Pq81sV0aTt3W", "Zx9KqLm2Pq81sV0aTt3W"),  # gitleaks:allow (valeur factice de test)
    ("password: hunter2hunter2", "hunter2hunter2"),
    ("key sk-ant-api03-AbCdEfGhIjKlMnOpQrStUv", "AbCdEfGhIjKl"),
    ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N", "eyJhbGciOiJIUzI1NiJ9"),  # gitleaks:allow (valeur factice de test)
    ("hash 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08", "9f86d081884c7d65"),
    ("random Qm9iIGlzIGEgc3VwZXIgc2VjcmV0IGtleQx7Hd9", "Qm9iIGlzIGEgc3VwZXIg"),
    ("sync for julien.test@example.org done", "julien.test@example.org"),
    ("incoming call +33 6 12 34 56 78", "12 34 56 78"),
    ("sms from 0612345678", "0612345678"),
    ("dial 06.12.34.56.78", "06.12.34.56.78"),
])
def test_mask_hides_codes_secrets_mails_and_phones(raw, hidden):
    assert hidden not in logs.mask(raw), logs.mask(raw)


def test_mask_keeps_stack_frames_and_ordinary_text():
    frame = "\tat com.foo.Main.onCreate(Main.kt:4242)"
    assert logs.mask(frame) == frame
    native = "      #00 pc 000000000004f0a8  /system/lib64/libc.so (abort+164)"
    assert logs.mask(native) == native
    text = "java.lang.IllegalStateException: Fragment not attached to a context"
    assert logs.mask(text) == text


def test_query_cannot_probe_a_masked_code(fake):
    fake.main = [line("14:00:00.000", 4321, "I", "Foo", "your code 482913")]
    assert "No log line" in run(kind="recent", app="com.foo", query="4829")
    assert "[masked code]" in run(kind="recent", app="com.foo", query="code")


# ---------------------------------------------------------------- dernières lignes


def test_recent_lines_of_an_app_running_dead_and_named(fake):
    fake.main = [
        line("13:00:00.000", 2000, "I", "ActivityManager", "Start proc 3000:com.foo/u0a123 for activity"),
        line("13:00:01.000", 3000, "W", "Foo", "old instance warning"),  # instance d'avant, morte depuis
        line("13:00:02.000", 2000, "I", "ActivityManager", "Process com.foo (pid 3000) has died"),
        line("13:00:03.000", 9999, "D", "Other", "unrelated"),
        line("13:00:04.000", 4322, "E", "FooRemote", "remote failed for julien.test@example.org"),
        line("13:00:05.000", 4321, "I", "Foo", "main ok"),
    ]
    text = run(kind="recent", app="foo")
    head, *rows = text.splitlines()
    assert head.startswith("Last 5 log line(s) of com.foo (time, pid, level, tag: message; newest last;")
    assert "unrelated" not in text and rows[-1].endswith("Foo: main ok")
    assert "[masked e-mail]" in text and "example.org" not in text
    assert "logcat" in fake.calls[-2][0] and str(logs.SCAN) in fake.calls[-2]  # lu large, filtré sur le PC
    assert len(run(kind="recent", app="foo", lines=2).splitlines()) == 3


def test_recent_since_and_lines_bounds(fake):
    fake.main = [line(f"14:{i // 60:02d}:{i % 60:02d}.000", 4321, "I", "Foo", "y" * 200) for i in range(3000)]
    text = run(kind="recent", lines=5000)
    assert len(text) <= logs.MAX_LOG + 400 and text.splitlines()[-1].startswith("10-19 14:49:59.000")
    run(kind="recent", since="14:30")
    assert ("-t", "2026-10-19 14:30:00.000") == fake.calls[-2][-2:]
    with pytest.raises(Exception, match="since"):
        run(kind="recent", since="yesterday-ish")


def test_recent_refused_for_a_protected_app_and_its_lines_left_out(fake):
    with pytest.raises(Exception, match="never read"):
        run(kind="recent", app=BANK)
    assert not any(c[0] == "logcat" for c in fake.calls)
    fake.main = [line("14:00:00.000", 5555, "I", "Bank", "balance 1 234,56 EUR"),
                 line("14:00:01.000", 2000, "I", "ActivityManager", f"Displayed {BANK}/.Main"),
                 line("14:00:02.000", 4321, "I", "Foo", "fine")]
    text = run(kind="recent")
    assert "balance" not in text and BANK not in text and "fine" in text
    assert "2 line(s) of banking, payment or identity apps left out" in text


# ---------------------------------------------------------------- ANR


def test_anrs_from_drop_box_and_events_without_duplicates(fake):
    fake.dropbox = ("2026-10-18 09:00:00 data_app_anr (compressed text, 9000 bytes)\nProcess: com.foo\nPID: 4000\n"
                    "Subject: Input dispatching timed out (Waiting to send key event)\n"
                    "2026-10-19 12:00:00 data_app_anr (compressed text, 9000 bytes)\nProcess: com.game\nPID: 880\n"
                    "Subject: Broadcast of Intent { act=x }\n"
                    f"2026-10-19 12:30:00 data_app_anr (compressed text, 9000 bytes)\nProcess: {BANK}\nPID: 5555\n"
                    "Subject: executing service for account 482913\n")
    fake.events = [line("12:00:10.000", 2000, "I", "am_anr", "[0,880,com.game,952745540,Broadcast of Intent]"),
                   line("13:00:00.000", 2000, "I", "am_anr", "[0,4321,com.foo,1,Input dispatching timed out]")]
    text = run(kind="anr")
    head, *rows = text.splitlines()
    assert head.startswith("ANRs, app not responding (newest last;") and head.endswith(": 4 ANR(s).")
    assert rows[0] == "10-18 09:00:00 com.foo (pid 4000): Input dispatching timed out (Waiting to send key event)"
    assert rows[1].startswith("10-19 12:00:00 com.game") and rows[-1].startswith("10-19 13:00:00 com.foo (pid 4321)")
    assert "reason not shown" in rows[2] and "482913" not in text
    assert ": 2 ANR(s)." in run(kind="anr", app="com.foo")
    assert "No ANR of com.foo" in run(kind="anr", app="com.foo", since="10m")


# ---------------------------------------------------------------- outil


def test_logs_tool_is_read_only_and_english(fake):
    tool = next(t for t in asyncio.run(server.mcp.list_tools()) if t.name == "logs")
    hints = tool.annotations
    assert hints and hints.read_only_hint and hints.idempotent_hint and not hints.destructive_hint
    for text in (run(kind="crash"), run(kind="anr"), run(kind="recent", app="foo")):
        assert not french(text), text


def test_since_formats():
    from datetime import datetime
    now = datetime(2026, 10, 19, 15, 0, 0)
    assert logs.since_stamp("15m", now) == "2026-10-19 14:45:00.000"
    assert logs.since_stamp("1d", now) == "2026-10-18 15:00:00.000"
    assert logs.since_stamp("9:05", now) == "2026-10-19 09:05:00.000"
    assert logs.since_stamp("10-18 22:10", now) == "2026-10-18 22:10:00.000"
    assert logs.since_stamp("2026-10-17T08:00:30", now) == "2026-10-17 08:00:30.000"
    with pytest.raises(PhoneError):
        logs.since_stamp("soon", now)


def test_mask_keeps_ordinary_numbers_readable():
    """Journaux lisibles : seuls les nombres annoncés comme des codes sont masqués (pid, tailles, identifiants restent)."""
    from nexus_android.logs import mask
    text = "Start proc 7391:com.app/u0a245 for activity; size 1048576; uid 10245"
    assert mask(text) == text


def test_mask_stack_frame_hides_secrets_keeps_numbers():
    frame = "\tat com.foo.Main.onCreate(Main.kt:4242) ghp_AAAAAAAAAAAAAAAAAAAAAAAAbC1 a@example.com"
    out = logs.mask(frame)
    assert "ghp_" not in out and "a@example.com" not in out and "Main.kt:4242" in out


def test_mask_stack_frame_keeps_only_its_technical_part():
    out = logs.mask("\tat com.foo.Main.onCreate(Main.kt:4242) OTP : 482913")
    assert out.startswith("\tat com.foo.Main.onCreate(Main.kt:4242)") and "482913" not in out
    native = "  #00 pc 000000000004a2b0  /apex/com.android.runtime/lib64/bionic/libc.so (abort+164)"
    assert logs.mask(native) == native


@pytest.mark.parametrize("secret", ["a@example.com", "AKIAAAAAAAAAAAAAAAAAAAAAAAAA", "code : 482913"])
def test_mask_native_frame_prefix_needs_a_whole_address(secret):
    """Relecture de Codex : « #00 pc » suivi d'une donnée n'en prenait pas le premier caractère pour une adresse."""
    out = logs.mask(f"#00 pc {secret}")
    assert secret.split()[-1] not in out
