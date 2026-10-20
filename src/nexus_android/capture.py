"""Captures gardées en fichier sur le PC, dans le dossier de files pull : capture d'écran en pleine résolution (codes de
sécurité noircis comme sur les images de look) et vidéo de l'écran (screenrecord d'Android, 180 s au plus).

Une vidéo ne se masque pas image par image : jamais lancée devant une application protégée (banque, identité), et le
compte rendu dit que les codes affichés pendant l'enregistrement y restent visibles."""

from __future__ import annotations

import io
import os
import re
import shlex
import struct
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import extras, policy
from .adb import Adb, PhoneError
from .screen import LIFTED, Element, Screen, State, blackout, is_black

MAX_SECONDS = 180  # limite de screenrecord (Android)
RECORD_SECONDS = 30  # record sans durée donnée
SHORT_SIDE = 720  # 720p : assez net pour montrer un problème, léger à envoyer
BIT_RATE = 4_000_000  # 4 Mbit/s : environ 30 Mo par minute
REMOTE_DIR = "/data/local/tmp"  # zone de l'utilisateur shell, invisible pour les applications
PREFIX = "nexus-android-rec-"
START_CHECK = 1.0  # s : un screenrecord qui ne démarre pas (encodeur, taille refusée) s'arrête dans ce délai
STOP_WAIT = 15.0  # s laissées à screenrecord pour finir le fichier MP4 après SIGINT
PULL_TIMEOUT = 600.0
IMAGE_TYPES = (".png", ".jpg", ".jpeg")
VIDEO_TYPES = (".mp4",)
_BAD_NAME = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")

VIDEO_NOTE = ("Security codes are not masked in videos (only on screenshots): everything shown during the recording "
              "is in the file, review it before sharing. Screens protected against capture (FLAG_SECURE: banking "
              "apps, some video players) come out black. No sound.")


LOCKED = ("Phone locked: nothing captured (it would only show the lock screen). Ask the user to unlock the phone, "
          "then try again; the agent never types a code.")
WAKE_WAIT = 2.0  # s au plus pour que l'écran s'allume après KEYCODE_WAKEUP
# Réveil par l'agent : le serveur le compte comme un geste de l'agent et garde l'écran allumé (comme look et act).
ON_WAKE: Callable[[Adb, State], None] | None = None


def ready(adb: Adb, screen: Screen) -> tuple[State, list[str]]:
    """Avant une capture : téléphone verrouillé → refus (jamais de code tapé) ; écran éteint et déverrouillé → allumé
    par KEYCODE_WAKEUP, pour ne jamais enregistrer une image noire ni une vidéo vide. Écran de verrouillage qu'Android
    tient déverrouillé (lieu de confiance) : levé sans code avant la capture."""
    state, lifted = screen.lift_keyguard(screen.state())
    if lifted and ON_WAKE:
        ON_WAKE(adb, state)
    if state.locked:
        raise PhoneError(LOCKED)
    if state.awake:
        return state, [LIFTED] if lifted else []
    adb.shell("input", "keyevent", "KEYCODE_WAKEUP")
    deadline = time.monotonic() + WAKE_WAIT
    while True:
        time.sleep(0.2)
        state = screen.state()
        if state.awake or time.monotonic() >= deadline:
            break
    if ON_WAKE:
        ON_WAKE(adb, state)
    state, lifted = screen.lift_keyguard(state)
    if state.locked:
        raise PhoneError("Screen was off; once woken, the phone shows its lock screen. " + LOCKED)
    if lifted:
        return state, ["Screen was off: turned on (KEYCODE_WAKEUP) before the capture.", LIFTED]
    if not state.awake:
        raise PhoneError("Screen off and still off after KEYCODE_WAKEUP: nothing captured. Ask the user to turn the "
                         "screen on.")
    return state, ["Screen was off: turned on (KEYCODE_WAKEUP) before the capture."]


def file_name(name: str | None, kind: str, allowed: tuple[str, ...]) -> str:
    """Nom de fichier donné (sans dossier : dest le choisit), extension ajoutée s'il n'en a pas ; sinon nom daté."""
    if not name or not name.strip():
        return f"{kind}-{datetime.now():%Y%m%d-%H%M%S}{allowed[0]}"
    raw = name.strip()
    if _BAD_NAME.search(raw) or raw.strip(".") == "" or raw.endswith("."):
        raise PhoneError(f"name \"{raw[:80]}\": a file name only (the folder is given by dest), without "
                         "\\ / : * ? \" < > |")
    suffix = Path(raw).suffix.lower()
    if not suffix:
        return raw + allowed[0]
    if suffix not in allowed:
        raise PhoneError(f"name \"{raw[:80]}\": extension {', '.join(allowed)} expected")
    return raw


def _destination(dest: str | None, name: str, confirm: bool) -> Path:
    """Fichier du PC, mêmes garde-fous que files pull (dossiers usuels libres, ailleurs confirm=true, secrets
    jamais) ; nom libre trouvé sans écraser."""
    folder = extras.pc_path(dest or extras.PC_INBOX, confirm, "written")
    target = extras.pc_path(folder / name, confirm, "written")
    folder.mkdir(parents=True, exist_ok=True)
    return extras.free_path(target)


def _size(path: Path) -> str:
    size = path.stat().st_size
    return f"{size / 1024**2:.1f} MB" if size >= 1024**2 else f"{max(1, round(size / 1024))} KB"


# ---------------------------------------------------------------- capture d'écran


def screenshot(adb: Adb, name: str | None, dest: str | None, confirm: bool, unmasked: bool = False) -> str:
    """Écran actuel en pleine résolution (PNG, ou JPEG selon le nom), codes et mots de passe noircis d'après l'arbre UI
    lu juste avant ET juste après la capture (un code apparu ou disparu entre les deux reste couvert). Application
    protégée : confirm=true ; arbre illisible : unmasked=true, accord à part (un confirm donné pour le dossier ne
    suffit pas)."""
    screen = Screen(adb)  # lecture à part : la liste [N] et l'échelle du dernier look restent celles de la session
    state, notes = ready(adb, screen)
    protected = policy.protected_app(state.package)
    if protected and not confirm:
        raise PhoneError(f"{protected}: banking, payment or identity app in the foreground, screenshot not saved. "
                         "Only on the user's explicit order for this screen: confirm=true")
    target = _destination(dest, file_name(name, "screenshot", IMAGE_TYPES), confirm)
    elements: list[Element] = []
    error: PhoneError | None = None
    try:
        try:
            elements = screen.dump()
        except PhoneError as e:
            error = e
        image = screen.capture()
        if error is None:
            try:
                elements += [e for e in screen.dump() if e.masked or e.password]
            except PhoneError as e:
                error = e
    finally:
        if protected:
            screen.release()  # comme look : connexion d'accessibilité rendue devant une appli bancaire
    if error is not None:
        if not unmasked:
            raise PhoneError(f"UI tree unreadable ({str(error)[:160]}): a visible security code would not be blacked "
                             "out, screenshot not saved. Try again in a moment, or unmasked=true if the user accepts "
                             "the file without masking.")
        notes.append("Saved without code masking (UI tree unreadable, unmasked=true).")
    masked = len({e.bounds for e in elements if e.masked})
    if masked:
        notes.append(f"{masked} area(s) with a security code blacked out.")
    if protected:
        notes.append(f"{protected} on screen, saved on the user's explicit order (confirm=true).")
    if not state.awake:
        notes.append("Screen off: the image is black.")
    elif is_black(image):
        notes.append("All-black image: screen protected against capture (FLAG_SECURE) or in transition.")
    picture = blackout(image, elements)  # capture pleine résolution : pixels image = pixels écran
    buffer = io.BytesIO()
    if target.suffix.lower() == ".png":
        picture.save(buffer, "PNG", optimize=True)
    else:
        picture.convert("RGB").save(buffer, "JPEG", quality=92)
    target.write_bytes(buffer.getvalue())
    width, height = picture.size
    return " ".join([f"Screenshot saved on the PC: {target} ({width}×{height}, {_size(target)}).", *notes])


# ---------------------------------------------------------------- enregistrement vidéo


@dataclass
class Recording:
    adb: Adb
    proc: subprocess.Popen[bytes]
    pid: str  # screenrecord sur le téléphone (arrêt par SIGINT : fichier MP4 terminé proprement)
    remote: str
    folder: Path
    name: str
    confirm: bool
    limit: int
    size: str  # « 720x1136 », ou "" (résolution de l'écran)
    started: float  # time.monotonic()

    def running(self) -> bool:
        return self.proc.poll() is None

    def elapsed(self) -> float:
        return time.monotonic() - self.started


_CURRENT: Recording | None = None  # un enregistrement à la fois par serveur, et un seul screenrecord sur le téléphone
_GUARD = threading.Lock()
_DROPPED = ""  # appli protégée qui a fait jeter l'enregistrement en cours (dit au prochain stop)


def guard(adb: Adb, package: str) -> None:
    """Appli bancaire, de paiement ou d'identité au premier plan pendant un enregistrement : screenrecord arrêté et
    fichier effacé du téléphone, rien ne vient sur le PC (une vidéo ne se masque pas)."""
    global _CURRENT, _DROPPED
    if _CURRENT is None or not (protected := policy.protected_app(package)):
        return
    with _GUARD:
        rec, _CURRENT = _CURRENT, None
    if rec is None:
        return
    adb.shell("kill", "-KILL", rec.pid, check=False)
    if rec.running():
        rec.proc.kill()
    adb.shell("rm", "-f", rec.remote, check=False)
    _DROPPED = protected


def video_size(width: int, height: int) -> str:
    """Taille 720p (petit côté 720, jamais agrandi) aux proportions de l'écran, côtés multiples de 16 (encodeur AVC)."""
    short, long = sorted((width, height))
    if short <= 0:
        return ""
    target = min(SHORT_SIDE, short)
    scaled = (target // 16 * 16, round(long * target / short / 16) * 16)
    return f"{scaled[0]}x{scaled[1]}" if width <= height else f"{scaled[1]}x{scaled[0]}"


def _display(adb: Adb, screen: Screen) -> tuple[int, int]:
    """Taille de l'affichage actif dans son orientation actuelle (serveur de lecture), sinon celle de `wm size`."""
    snap = screen.snapshot()
    if snap and snap.size[0] > 0 and snap.size[1] > 0:
        return snap.size
    sizes = re.findall(r"(Physical|Override) size: (\d+)x(\d+)", adb.shell("wm", "size", check=False))
    if not sizes:
        return 0, 0
    _, w, h = sizes[-1]  # taille imposée (Override) si elle existe
    return int(w), int(h)


def _first_line(proc: subprocess.Popen[bytes], timeout: float = 10.0) -> str:
    """Première ligne de la sortie (le pid écrit par `echo $$`), attendue au plus `timeout` s."""
    line: list[bytes] = []
    reader = threading.Thread(target=lambda: line.append(proc.stdout.readline() if proc.stdout else b""), daemon=True)
    reader.start()
    reader.join(timeout)
    return line[0].decode("utf-8", "replace").strip() if line else ""


def _output(proc: subprocess.Popen[bytes]) -> str:
    try:
        out, _ = proc.communicate(timeout=5)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return ""
    return (out or b"").decode("utf-8", "replace").strip()


def _spawn(adb: Adb, argv: list[str]) -> tuple[subprocess.Popen[bytes], str, str]:
    """screenrecord lancé sur le téléphone, la commande adb gardée ouverte ; (processus, pid sur le téléphone, erreur :
    "" s'il tourne encore après START_CHECK s)."""
    proc = adb.background("echo $$; exec " + " ".join(shlex.quote(a) for a in argv) + " 2>&1")
    pid = _first_line(proc)
    deadline = time.monotonic() + START_CHECK
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.1)
    if proc.poll() is not None or not pid.isdigit():
        if proc.poll() is None:
            proc.kill()
        return proc, pid, _output(proc)[:300] or "screenrecord stopped at once"
    return proc, pid, ""


def start(adb: Adb, seconds: int | None, name: str | None, dest: str | None, confirm: bool) -> str:
    """Démarre l'enregistrement et rend la main aussitôt : les gestes continuent pendant ce temps. Android l'arrête
    seul au bout de `seconds` (180 au plus) ; stop le termine plus tôt et rapatrie le fichier."""
    rec, notes = begin(adb, seconds, name, dest, confirm)
    what = rec.size.replace("x", "×") if rec.size else "screen resolution"
    return " ".join([f"Screen recording started ({what}, {BIT_RATE // 1_000_000} Mbit/s, stops by itself after "
                     f"{rec.limit} s): gestures can go on; capture(action=\"stop\") ends it and saves the file on the "
                     "PC.", VIDEO_NOTE, *notes])


def begin(adb: Adb, seconds: int | None, name: str | None, dest: str | None,
          confirm: bool) -> tuple[Recording, list[str]]:
    """Enregistrement lancé (un seul à la fois) ; renvoie l'enregistrement et les remarques à dire."""
    global _CURRENT
    notes: list[str] = []
    with _GUARD:
        previous = _CURRENT
        if previous and previous.running():
            left = max(0, round(previous.limit - previous.elapsed()))
            raise PhoneError(f"a recording is already running (for {round(previous.elapsed())} s, ends by itself "
                             f"within {left} s): capture(action=\"stop\") first")
        _CURRENT = None
    if previous:  # fini seul (limite atteinte) sans avoir été rapatrié : rangé avant d'en lancer un autre
        try:
            notes.append("Previous recording, which had ended, collected: " + collect(previous))
        except PhoneError as e:
            notes.append(f"Previous recording not collected ({e}).")
    limit = max(1, min(int(seconds or MAX_SECONDS), MAX_SECONDS))
    screen = Screen(adb)
    state, woken = ready(adb, screen)
    notes += woken
    if protected := policy.protected_app(state.package):
        raise PhoneError(f"{protected}: banking, payment or identity app in the foreground, no screen recording "
                         "(security codes cannot be masked in a video). The user records it themselves if needed.")
    folder = extras.pc_path(dest or extras.PC_INBOX, confirm, "written")
    file = file_name(name, "screen-recording", VIDEO_TYPES)
    extras.pc_path(folder / file, confirm, "written")  # nom refusé (.env…) avant de lancer quoi que ce soit
    running = adb.shell("pidof", "screenrecord", check=False).strip()
    if running:
        raise PhoneError(f"Android is already recording the screen (screenrecord, pid {running}: another agent or "
                         "app): nothing was started")
    # Restes d'un serveur arrêté net (plus d'une heure : jamais le fichier fini d'un autre agent, pas encore rapatrié).
    adb.shell("find", REMOTE_DIR, "-maxdepth", "1", "-name", f"{PREFIX}*.mp4", "-mmin", "+60", "-delete", check=False)
    remote = f"{REMOTE_DIR}/{PREFIX}{os.getpid()}-{int(time.time())}.mp4"
    size = video_size(*_display(adb, screen))
    display = screen._display_id()  # Fold : écran intérieur ou extérieur, comme les captures
    base = ["screenrecord", "--time-limit", str(limit), "--bit-rate", str(BIT_RATE)]
    tail = (["--display-id", display] if display else []) + [remote]
    proc, pid, error = _spawn(adb, base + (["--size", size] if size else []) + tail)
    if error and size:  # taille refusée par l'encodeur de ce téléphone : résolution de l'écran
        proc, pid, error = _spawn(adb, base + tail)
        size = ""
    if error:
        adb.shell("rm", "-f", remote, check=False)
        raise PhoneError(f"screen recording not started: {error}")
    rec = Recording(adb, proc, pid, remote, folder, file, confirm, limit, size, time.monotonic())
    with _GUARD:
        _CURRENT = rec
    if not state.awake:
        notes.append("Screen off: the video stays black until it is turned on.")
    return rec, notes


def wait_end(timeout: float) -> None:
    """Attend la fin de l'enregistrement en cours (limite atteinte), `timeout` s au plus ; sans verrou du téléphone."""
    rec = _CURRENT
    if rec is not None:
        try:
            rec.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass


def stop(adb: Adb) -> str:
    global _CURRENT, _DROPPED
    if _CURRENT is not None:
        guard(adb, Screen(adb).state().package)
    with _GUARD:
        rec, _CURRENT = _CURRENT, None
    if rec is None and _DROPPED:
        dropped, _DROPPED = _DROPPED, ""
        raise PhoneError(f"recording discarded: {dropped} (banking, payment or identity app) came to the foreground "
                         "while recording; the file was erased from the phone and nothing was saved on the PC")
    if rec is None:
        running = adb.shell("pidof", "screenrecord", check=False).strip()
        raise PhoneError("no recording started by this server" + (
            f"; Android is recording the screen for another agent or app (screenrecord, pid {running})" if running
            else ": capture(action=\"start\") or capture(action=\"record\", seconds=N)"))
    try:
        return collect(rec)
    except PhoneError:
        with _GUARD:
            if _CURRENT is None:
                _CURRENT = rec  # fichier gardé sur le téléphone : stop le rapatrie au prochain essai
        raise


def collect(rec: Recording) -> str:
    """Arrête screenrecord s'il tourne (SIGINT : MP4 terminé proprement), rapatrie le fichier puis l'efface du
    téléphone."""
    adb, notes = rec.adb, []
    limited = not rec.running()
    if rec.running():
        adb.shell("kill", "-INT", rec.pid, check=False)
        try:
            rec.proc.wait(timeout=STOP_WAIT)
        except subprocess.TimeoutExpired:
            adb.shell("kill", "-KILL", rec.pid, check=False)
            rec.proc.kill()
            notes.append("screenrecord did not stop cleanly: the video may be incomplete.")
    elapsed = min(rec.elapsed(), float(rec.limit))
    output = _output(rec.proc)
    target = extras.free_path(rec.folder / rec.name)
    rec.folder.mkdir(parents=True, exist_ok=True)
    try:
        adb.host("pull", rec.remote, str(target), timeout=PULL_TIMEOUT)
    except PhoneError as e:
        target.unlink(missing_ok=True)
        detail = f" (screenrecord: {output[:200]})" if output else ""
        raise PhoneError(f"recording not copied to the PC: {e}{detail}. The file stays on the phone: "
                         "capture(action=\"stop\") tries again") from None
    adb.shell("rm", "-f", rec.remote, check=False)
    duration = mp4_duration(target)
    if duration is None:
        notes.append("MP4 file unreadable (no index): the video may be incomplete.")
    if limited and elapsed >= rec.limit - 1:
        notes.append(f"Stopped by itself at the {rec.limit} s limit.")
    if duration is not None and duration < elapsed - 2:
        notes.append(f"Video shorter than the recording time ({duration:.1f} s of {elapsed:.0f} s): Android adds "
                     "frames only when the screen changes, and none while it is off.")
    length = duration if duration is not None else elapsed
    what = rec.size.replace("x", "×") if rec.size else "screen resolution"
    return " ".join([f"Screen recording saved on the PC: {target} ({length:.1f} s, {_size(target)}, {what}).",
                     *notes, VIDEO_NOTE])


def shutdown() -> None:
    """Arrêt du serveur : un enregistrement encore en cours est arrêté et rapatrié (au mieux, sans erreur)."""
    global _CURRENT
    with _GUARD:
        rec, _CURRENT = _CURRENT, None
    if rec is None:
        return
    try:
        collect(rec)
    except Exception:  # liaison perdue à la fermeture : screenrecord s'arrête seul à sa limite
        if rec.running():
            rec.proc.kill()


def mp4_duration(path: Path) -> float | None:
    """Durée (s) lue dans l'en-tête mvhd du fichier MP4 ; None si le fichier n'a pas d'index (enregistrement coupé)."""
    try:
        total = path.stat().st_size
        with open(path, "rb") as f:
            pos = 0
            while pos + 8 <= total:
                f.seek(pos)
                head = f.read(16)
                size, kind = struct.unpack(">I4s", head[:8])
                header = 8
                if size == 1 and len(head) >= 16:
                    size, header = struct.unpack(">Q", head[8:16])[0], 16
                elif size == 0:
                    size = total - pos
                if size < header:
                    return None
                if kind == b"moov":
                    f.seek(pos + header)
                    body = f.read(min(size - header, 1 << 20))
                    i = body.find(b"mvhd")
                    if i < 4 or len(body) < i + 36:
                        return None
                    if body[i + 4] == 1:
                        scale, duration = struct.unpack(">IQ", body[i + 24:i + 36])
                    else:
                        scale, duration = struct.unpack(">II", body[i + 16:i + 24])
                    return duration / scale if scale else None
                pos += size
    except (OSError, struct.error):
        return None
    return None
