"""Captures gardées en fichier sur le PC, dans le dossier de files pull : capture d'écran en pleine résolution (codes de
sécurité noircis comme sur les images de look) et vidéo de l'écran (screenrecord d'Android, 180 s au plus).

Une vidéo ne se masque pas image par image : jamais lancée devant une application protégée (banque, identité), et le
compte rendu dit que les codes affichés pendant l'enregistrement y restent visibles."""

from __future__ import annotations

import io
import itertools
import os
import re
import shlex
import struct
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import extras, policy
from . import screen as screen_mod
from .adb import STATE_WAIT, Adb, PhoneError
from .screen import LIFTED, STATE_SH, Element, Screen, State, blackout, is_black, needs_ocr, ocr_elements, parse_state

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
    # Chaque lecture complète de l'arbre (avant et après la capture) : ses éléments et surfaces, jugés chacun ; et
    # chaque appli vue (premier plan relu, appli des éléments) : une banque apparue pendant la capture compte.
    readings: list[tuple[list[Element], list[tuple[int, int, int, int]]]] = []
    seen_apps = {state.package}

    def read_tree() -> list[Element]:
        try:
            tree = screen.dump()
        finally:  # même si l'arbre échoue : le premier plan lu avec lui compte (une banque n'est jamais perdue)
            seen_apps.add(screen.last_state.package if screen.last_state else "")
        readings.append((tree, list(screen.surfaces)))
        seen_apps.update(_shown_apps(tree))
        return tree

    try:
        try:
            elements = read_tree()
        except PhoneError as e:
            error = e
        image = screen.capture()
        try:  # toujours relue après l'image, même si la première lecture a échoué (banque apparue pendant screencap)
            elements += [e for e in read_tree() if e.masked or e.password]
        except PhoneError as e:
            error = error or e
    finally:
        protected = next((n for p in sorted(seen_apps) if (n := policy.protected_app(p))), None) or protected
        if protected:
            screen.release()  # comme look : connexion d'accessibilité rendue devant une appli bancaire
    if protected and not confirm:
        raise PhoneError(f"{protected}: banking, payment or identity app shown during the capture, screenshot not "
                         "saved. Only on the user's explicit order for this screen: confirm=true")
    if error is not None:
        if not unmasked:
            raise PhoneError(f"UI tree unreadable ({str(error)[:160]}): a visible security code would not be blacked "
                             "out, screenshot not saved. Try again in a moment, or unmasked=true if the user accepts "
                             "the file without masking.")
        notes.append("Saved without code masking (UI tree unreadable, unmasked=true).")
    elif any(needs_ocr(tree, image.size, surfaces) for tree, surfaces in readings):  # besoin d'une lecture = OCR
        # Texte absent de l'arbre (surface dessinée, jeu) : codes repérés sur l'image par OCR, noircis avant l'écriture.
        seen = ocr_elements(image, image.width, state.package)
        if seen is not None:
            elements += [e for e in seen if e.masked]
        elif not unmasked:
            raise PhoneError("text drawn on the screen (not in the UI tree) could not be read by OCR, so a visible "
                             "security code would not be blacked out: screenshot not saved. Try again, or "
                             "unmasked=true if the user accepts the file without masking.")
        else:
            notes.append("Saved without masking of the drawn text (OCR unavailable, unmasked=true).")
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


WATCH_EVERY = 1.0  # s entre deux lectures du premier plan pendant un enregistrement
END_POLL = 0.05  # s entre deux regards sur la fin de screenrecord (processus adb local)
WATCH_GAP = 5.0  # s sans lecture réussie du premier plan : surveillance tenue pour interrompue, vidéo jetée


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
    started: float  # screen_mod.stamp()
    # Surveillance continue du premier plan (fil _watch) : une appli protégée vue une fois rend la vidéo inutilisable,
    # pour de bon (tainted) ; un trou de surveillance trop long aussi (blind).
    tainted: str = ""
    last_seen: float = 0.0  # dernière lecture réussie du premier plan (screen_mod.stamp())
    blind: float = 0.0  # plus long intervalle sans lecture réussie pendant l'enregistrement
    ended: float | None = None  # fin de screenrecord constatée par la surveillance
    stop_watch: threading.Event = field(default_factory=threading.Event)
    watcher: threading.Thread | None = None
    ender: threading.Thread | None = None  # fil de la fin (_watch_end)

    def running(self) -> bool:
        return self.proc.poll() is None

    def elapsed(self) -> float:
        return screen_mod.stamp() - self.started

    def unseen(self) -> float:
        """Plus long moment de l'enregistrement sans lecture du premier plan (jusqu'à sa fin, ou maintenant)."""
        end = self.ended if self.ended is not None else screen_mod.stamp()
        return max(self.blind, end - self.last_seen)


class Discarded(PhoneError):
    """Vidéo jetée (appli protégée vue, ou premier plan non surveillé) : effacée du téléphone, rien sur le PC."""


_CURRENT: Recording | None = None  # un enregistrement à la fois par serveur, et un seul screenrecord sur le téléphone
_GUARD = threading.Lock()
_TRIES = itertools.count(1)  # numéro de chaque lancement de screenrecord : un fichier distinct par essai
_DROPPED = ""  # raison du rejet de l'enregistrement en cours, jeté par la surveillance (dite au prochain stop)


def _discarded(why: str) -> str:
    return f"recording discarded: {why}; the file was erased from the phone and nothing was saved on the PC"


def _erase(rec: Recording) -> None:
    """screenrecord tué (aucune image de plus) et fichier effacé du téléphone ; rien n'est rapatrié."""
    rec.stop_watch.set()
    if rec.pid.isdigit():
        rec.adb.shell("kill", "-KILL", rec.pid, check=False)
    else:  # pid pas encore lu (démarrage) : screenrecord retrouvé par son fichier, unique (pas pkill -f, qui tuerait
        # aussi le shell dont la commande contient ce chemin)
        for line in rec.adb.shell("ps", "-A", "-o", "PID,ARGS", check=False).splitlines():
            pid, _, args = line.strip().partition(" ")
            if pid.isdigit() and args.lstrip().startswith("screenrecord ") and rec.remote in args.split():
                rec.adb.shell("kill", "-KILL", pid, check=False)
    if rec.running():
        rec.proc.kill()
    rec.adb.shell("rm", "-f", rec.remote, check=False)


def _taint(rec: Recording, protected: str) -> None:
    """Appli protégée au premier plan pendant l'enregistrement : marque irréversible, vidéo effacée aussitôt. Si
    l'enregistrement était en cours (personne ne le rapatrie), la raison est gardée pour le prochain stop."""
    global _CURRENT, _DROPPED
    with _GUARD:
        first = not rec.tainted
        if first:
            rec.tainted = protected
        if _CURRENT is rec:
            _CURRENT = None
            _DROPPED = f"{protected} (banking, payment or identity app) came to the foreground while recording"
    if first:
        try:
            _erase(rec)
        except PhoneError:
            pass  # liaison perdue : la marque suffit, le fichier ne sera jamais rapatrié (effacé à la prochaine vidéo)


def _observe(rec: Recording) -> None:
    """Une lecture du premier plan : appli protégée → vidéo marquée et effacée ; lecture réussie notée (trou de
    surveillance mesuré entre deux lectures réussies)."""
    if not rec.running():
        return  # prélevée après la fin de screenrecord : ce premier plan n'est pas dans la vidéo
    try:  # datée au début du prélèvement : une preuve prélevée pendant l'enregistrement vaut, même livrée après la fin
        out = rec.adb.shell("sh", "-c", STATE_SH, check=False, timeout=STATE_WAIT)
    except (PhoneError, OSError):
        return
    state = parse_state(out, out)
    if protected := policy.protected_app(state.package):
        _taint(rec, protected)  # preuve positive : marque la vidéo même si la lecture est partielle
    if "mWakefulness=" not in out:  # sortie tronquée : la lecture ne compte pas comme surveillance réussie
        return
    if not state.package and "mWakefulness=Asleep" not in out and not _dozing_off(rec, out):
        # Premier plan illisible : seule la preuve d'un écran sans appli vaut lecture surveillée (Asleep, ou Always On
        # Display : Dozing ET tous les affichages en DOZE ou OFF) ; Dreaming (écran de veille) affiche encore une appli.
        return
    now = screen_mod.stamp()
    if rec.ended is not None:  # lecture revenue après la fin : l'attente d'après la fin n'est pas du temps filmé
        now = min(now, rec.ended)
    rec.blind = max(rec.blind, now - rec.last_seen)
    rec.last_seen = now


DISPLAY_SH = "dumpsys display | grep -E 'Display State='; true"
_DISPLAY_STATE = re.compile(r"Display State=(\w+)")


def _dozing_off(rec: Recording, out: str) -> bool:
    """Always On Display (vu sur le Fold : mWakefulness=Dozing, aucun premier plan, « Display State=DOZE » et « OFF ») :
    seule l'horloge est affichée, aucune appli ; compte comme un écran éteint."""
    if "mWakefulness=Dozing" not in out:
        return False
    try:
        states = _DISPLAY_STATE.findall(rec.adb.shell("sh", "-c", DISPLAY_SH, check=False, timeout=STATE_WAIT))
    except (PhoneError, OSError):
        return False
    return bool(states) and all(s in ("DOZE", "DOZE_SUSPEND", "OFF") for s in states)


def _watch_end(rec: Recording) -> None:
    """Fil de la fin : screenrecord guetté toutes les END_POLL s (lecture locale, gratuite), dans son propre fil pour
    qu'une lecture ADB lente du premier plan (_observe, jusqu'à STATE_WAIT s) ne retarde pas la date de fin ; une
    lecture faite après une fin naturelle ne jette donc pas une vidéo saine (l'incertitude restante, sous END_POLL,
    marque la vidéo par prudence)."""
    while not rec.stop_watch.wait(END_POLL):
        if not rec.running():  # fin constatée : plus rien n'est filmé, aucune lecture ne peut marquer la vidéo
            rec.ended = screen_mod.stamp()
            return


def _watch(rec: Recording) -> None:
    """Fil de surveillance : le premier plan relu toutes les WATCH_EVERY s tant que screenrecord tourne (fin guettée
    à part, _watch_end)."""
    while not rec.stop_watch.wait(WATCH_EVERY):
        if rec.ended is not None or not rec.running():
            return
        _observe(rec)
        if rec.tainted:
            return


def guard(adb: Adb, package: str, seen_at: float | None = None) -> None:
    """Appli bancaire, de paiement ou d'identité au premier plan pendant un enregistrement (lu par un outil) : même
    marque que la surveillance, screenrecord arrêté et fichier effacé du téléphone (une vidéo ne se masque pas).
    Seulement une lecture commencée après le début de l'enregistrement (seen_at), et pendant qu'il tourne."""
    rec = _CURRENT
    if rec is None:
        return
    if seen_at is None:  # lecture non datée : seulement si la vidéo tourne encore
        if not rec.running():
            return
    elif seen_at < rec.started or (rec.ended is not None and seen_at > rec.ended):
        return  # preuve datée hors de l'enregistrement (avant son début ou après sa fin constatée)
    if protected := policy.protected_app(package):
        _taint(rec, protected)


def _seen(package: str, since: float) -> None:
    """Branché sur screen.ON_STATE : toute lecture de l'état, par n'importe quel outil, sert au garde de la vidéo."""
    if _CURRENT is not None:
        try:
            guard(_CURRENT.adb, package, since)
        except PhoneError:
            pass


screen_mod.ON_STATE = _seen


APP_SHARE = 0.25  # part de l'écran qu'une appli doit couvrir pour compter comme affichée (pas une poignée)


def _shown_apps(tree: list[Element]) -> set[str]:
    """Applis réellement affichées d'après l'arbre : celles dont les éléments couvrent au moins APP_SHARE de l'écran.
    La poignée de Samsung Wallet sur l'accueil (un petit élément d'une appli de paiement) n'est pas une banque à
    l'écran ; une fenêtre bancaire ouverte par-dessus, si."""
    if not tree:
        return set()
    width = max(e.bounds[2] for e in tree) or 1
    height = max(e.bounds[3] for e in tree) or 1
    boxes: dict[str, list[int]] = {}
    for e in tree:
        box = boxes.setdefault(e.package, list(e.bounds))
        box[:] = [min(box[0], e.bounds[0]), min(box[1], e.bounds[1]), max(box[2], e.bounds[2]),
                  max(box[3], e.bounds[3])]
    return {p for p, b in boxes.items() if (b[2] - b[0]) * (b[3] - b[1]) >= APP_SHARE * width * height}


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


def _spawn(adb: Adb, argv: list[str], make: Callable[[subprocess.Popen[bytes], float], Recording]) -> tuple[
        Recording, str]:
    """screenrecord lancé sur le téléphone, la commande adb gardée ouverte ; (enregistrement, erreur : "" s'il tourne
    encore après START_CHECK s). Tout ce qui suit le lancement peut être filmé : la surveillance (_CURRENT, fils de
    surveillance et de fin) part dès la commande envoyée, avant le pid et START_CHECK, et le temps non surveillé se
    compte depuis le lancement de cet essai (`started`, passé à make : rien de ce qui le précède n'est filmé)."""
    global _CURRENT
    started = screen_mod.stamp()
    proc = adb.background("echo $$; exec " + " ".join(shlex.quote(a) for a in argv) + " 2>&1")
    rec = make(proc, started)
    rec.watcher = threading.Thread(target=_watch, args=(rec,), name="nexus-android-video-watch", daemon=True)
    rec.ender = threading.Thread(target=_watch_end, args=(rec,), name="nexus-android-video-end", daemon=True)
    with _GUARD:
        _CURRENT = rec
    rec.ender.start()
    rec.watcher.start()
    rec.pid = _first_line(proc)
    deadline = time.monotonic() + START_CHECK
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.1)
    if proc.poll() is not None or not rec.pid.isdigit():
        _abandon(rec)
        return rec, _output(proc)[:300] or "screenrecord stopped at once"
    return rec, ""


def _abandon(rec: Recording) -> None:
    """Lancement raté : surveillance arrêtée, screenrecord tué (le pid peut manquer) et fichier effacé du téléphone ; la
    lecture en cours du fil de surveillance est soldée (sa marque éventuelle comptée par l'appelant)."""
    global _CURRENT
    with _GUARD:
        if _CURRENT is rec:
            _CURRENT = None
    try:
        _erase(rec)
    except PhoneError:
        pass
    for thread in (rec.watcher, rec.ender):
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=STATE_WAIT + WATCH_EVERY)


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
    """Enregistrement lancé (un seul à la fois) ; renvoie l'enregistrement et les remarques à dire. Une vidéo
    précédente finie seule est rapatriée par le même contrôle que stop, après les vérifications de celle-ci ; si elle
    ne peut pas l'être, rien n'est lancé et sa poignée est gardée (stop la retente)."""
    global _CURRENT, _DROPPED
    notes: list[str] = []
    with _GUARD:
        previous = _CURRENT
        if previous and previous.running():
            left = max(0, round(previous.limit - previous.elapsed()))
            raise PhoneError(f"a recording is already running (for {round(previous.elapsed())} s, ends by itself "
                             f"within {left} s): capture(action=\"stop\") first")
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
    if previous:  # fini seul (limite atteinte) sans avoir été rapatrié : rangé avant d'en lancer un autre
        with _GUARD:
            if _CURRENT is previous:
                _CURRENT = None
            else:
                previous = None  # jeté entre-temps par la surveillance
    if previous:
        try:
            notes.append("Previous recording, which had ended, collected: " + collect(previous))
        except Discarded as e:
            notes.append(f"Previous {e}.")
        except PhoneError as e:
            with _GUARD:
                if _CURRENT is None:
                    _CURRENT = previous  # fichier gardé sur le téléphone : stop le rapatrie au prochain essai
            raise PhoneError(f"previous recording not collected ({e}); nothing new was started") from None
    with _GUARD:
        if _DROPPED:  # vidéo précédente jetée par la surveillance, jamais dite : dite ici
            notes.append("Previous " + _discarded(_DROPPED) + ".")
            _DROPPED = ""
    running = adb.shell("pidof", "screenrecord", check=False).strip()
    if running:
        raise PhoneError(f"Android is already recording the screen (screenrecord, pid {running}: another agent or "
                         "app): nothing was started")
    # Restes d'un serveur arrêté net (plus d'une heure : jamais le fichier fini d'un autre agent, pas encore rapatrié).
    adb.shell("find", REMOTE_DIR, "-maxdepth", "1", "-name", f"{PREFIX}*.mp4", "-mmin", "+60", "-delete", check=False)
    size = video_size(*_display(adb, screen))
    if screen.last_state and (protected := policy.protected_app(screen.last_state.package)):
        # Premier plan relu par la mesure de l'affichage : une banque passée devant entre-temps, rien n'est lancé.
        raise PhoneError(f"{protected}: banking, payment or identity app in the foreground, no screen recording "
                         "(security codes cannot be masked in a video). The user records it themselves if needed.")
    display = screen._display_id()  # Fold : écran intérieur ou extérieur, comme les captures
    base = ["screenrecord", "--time-limit", str(limit), "--bit-rate", str(BIT_RATE)]
    tail = ["--display-id", display] if display else []

    def launch(sized: bool) -> tuple[Recording, str]:
        """Un essai : son propre fichier (un essai abandonné ne peut pas effacer celui du suivant) et son propre temps
        non surveillé, compté depuis son lancement (tout ce qui le suit peut être filmé, rien de ce qui le précède)."""
        # Compteur, pas l'horloge : time_ns() ne bouge que toutes les ~16 ms sous Windows (deux essais, même nom).
        remote = f"{REMOTE_DIR}/{PREFIX}{os.getpid()}-{int(time.time())}-{next(_TRIES)}.mp4"

        def make(proc: subprocess.Popen[bytes], started: float) -> Recording:
            return Recording(adb, proc, "", remote, folder, file, confirm, limit, size if sized else "", started,
                             last_seen=started)

        return _spawn(adb, base + (["--size", size] if sized else []) + tail + [remote], make)

    tries = [launch(bool(size))]
    if tries[0][1] and size and not tries[0][0].tainted:  # taille refusée par l'encodeur : résolution de l'écran
        tries.append(launch(False))
    rec, error = tries[-1]
    if tainted := next((r.tainted for r, _ in tries if r.tainted), ""):
        # Banque passée devant pendant un démarrage (même celui d'un essai abandonné, vue par sa dernière lecture) :
        # rien n'est gardé, dit ici et pas au prochain stop.
        if not error:
            _abandon(rec)
        with _GUARD:
            _DROPPED = ""
        raise PhoneError(_discarded(f"{tainted} (banking, payment or identity app) came to the foreground while the "
                                    "recording was starting"))
    if error:
        raise PhoneError(f"screen recording not started: {error}")
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
    with _GUARD:
        rec, _CURRENT = _CURRENT, None
        dropped = _DROPPED if rec is None else ""
        if dropped:
            _DROPPED = ""
    if dropped:
        raise Discarded(_discarded(dropped))
    if rec is None:
        running = adb.shell("pidof", "screenrecord", check=False).strip()
        raise PhoneError("no recording started by this server" + (
            f"; Android is recording the screen for another agent or app (screenrecord, pid {running})" if running
            else ": capture(action=\"start\") or capture(action=\"record\", seconds=N)"))
    try:
        return collect(rec)
    except Discarded:
        raise
    except PhoneError:
        with _GUARD:
            if _CURRENT is None:
                _CURRENT = rec  # fichier gardé sur le téléphone : stop le rapatrie au prochain essai
        raise


def _vet(rec: Recording) -> None:
    """Contrôle commun à toute collecte (stop, begin, arrêt du serveur), screenrecord arrêté : appli protégée vue au
    premier plan pendant l'enregistrement, ou premier plan resté sans surveillance plus de WATCH_GAP s → vidéo effacée
    du téléphone, Discarded (rien ne vient sur le PC)."""
    rec.stop_watch.set()
    if rec.watcher is not None and rec.watcher is not threading.current_thread():
        rec.watcher.join(timeout=STATE_WAIT + WATCH_EVERY)
    if rec.ender is not None and rec.ender is not threading.current_thread():
        rec.ender.join(timeout=WATCH_EVERY)
    if rec.tainted:
        raise Discarded(_discarded(f"{rec.tainted} (banking, payment or identity app) came to the foreground while "
                                   "recording"))
    if (unseen := rec.unseen()) > WATCH_GAP:
        try:
            _erase(rec)
        except PhoneError:
            pass
        raise Discarded(_discarded(f"the foreground app could not be checked for {unseen:.0f} s during the recording "
                                   "(link lost?), so a banking or identity screen may be in it"))


def collect(rec: Recording) -> str:
    """Arrête screenrecord s'il tourne (SIGINT : MP4 terminé proprement), passe le contrôle commun (_vet), rapatrie le
    fichier puis l'efface du téléphone."""
    adb, notes = rec.adb, []
    limited = not rec.running()
    if rec.running():
        _observe(rec)  # dernière lecture du premier plan avant l'arrêt
        if not rec.tainted:
            adb.shell("kill", "-INT", rec.pid, check=False)
            try:
                rec.proc.wait(timeout=STOP_WAIT)
            except subprocess.TimeoutExpired:
                adb.shell("kill", "-KILL", rec.pid, check=False)
                rec.proc.kill()
                notes.append("screenrecord did not stop cleanly: the video may be incomplete.")
            rec.ended = screen_mod.stamp()
    if rec.ended is None:
        # Fini seul avant que la surveillance ne le voie : fin figée dès la première collecte, pour qu'une collecte
        # retentée plus tard (rapatriement échoué) ne compte pas l'attente comme du temps non surveillé.
        rec.ended = screen_mod.stamp()
    _vet(rec)
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
    if rec.tainted:  # marquée pendant le rapatriement (outil lancé en parallèle) : la copie du PC est effacée aussi
        target.unlink(missing_ok=True)
        _vet(rec)
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
    """Arrêt du serveur : un enregistrement encore en cours est arrêté et rapatrié par le même contrôle que stop (au
    mieux, sans erreur ; vidéo jetée si une appli protégée y est passée)."""
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
