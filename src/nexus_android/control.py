"""Gestes par le canal de contrôle de scrcpy : un toucher part en quelques millisecondes au lieu des 100 à 300 ms d'un
`adb shell input` (une JVM démarrée par geste), et plusieurs doigts deviennent possibles (pincer, zoomer).

Le serveur de scrcpy (le même fichier vérifié que pour le collage, voir clip.py) est lancé au premier geste, sans
vidéo ni son, et gardé ouvert entre les gestes ; il s'arrête après IDLE secondes sans geste, et à l'arrêt de ce
serveur MCP. Les événements portent des coordonnées brutes de l'écran : sans vidéo, scrcpy les injecte telles quelles
(Controller.getEventPointAndDisplayId). Indisponible (fichier absent, téléphone qui refuse) : repli sur `input`, qui
sait tout faire sauf plusieurs doigts."""

from __future__ import annotations

import struct
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack

from . import clip
from .adb import Adb, LinkError, PhoneError

INJECT_TOUCH = 2  # ControlMessage.TYPE_INJECT_TOUCH_EVENT
DOWN, UP, MOVE = 0, 1, 2  # MotionEvent.ACTION_*
FINGER = -2  # POINTER_ID_GENERIC_FINGER (scrcpy) : un doigt ; les autres doigts ont leur propre numéro
PRESSURE = 0xFFFF  # 1.0 en virgule fixe 16 bits
ENABLED = True  # False : toujours input (tests sans téléphone)
IDLE = 60.0  # s sans geste avant d'arrêter le serveur scrcpy
RETRY = 60.0  # s après un échec du canal avant de le retenter (input entre-temps), comme query.RETRY
STEP_MS = 8  # intervalle entre deux positions d'un glissé (≈ 120 Hz, comme un vrai doigt)

Point = tuple[int, int]


def touch(action: int, pointer: int, x: int, y: int, size: tuple[int, int], pressure: int = PRESSURE) -> bytes:
    """Message INJECT_TOUCH_EVENT de scrcpy v5 (ControlMessageReader.parseInjectTouchEvent), 32 octets."""
    w, h = (max(0, min(v, 0xFFFF)) for v in size)
    return struct.pack(">BBqiiHHHII", INJECT_TOUCH, action, pointer, x, y, w, h,
                       pressure if action != UP else 0, 0, 0)


def path(start: Point, end: Point, ms: int) -> Iterator[Point]:
    """Positions intermédiaires d'un glissé de `ms` millisecondes, la dernière exactement sur `end`."""
    steps = max(1, ms // STEP_MS)
    for i in range(1, steps + 1):
        yield (round(start[0] + (end[0] - start[0]) * i / steps), round(start[1] + (end[1] - start[1]) * i / steps))


class Fingers:
    """Suite d'événements de toucher envoyés par un canal (send) avec un rythme réel (sleep)."""

    def __init__(self, send: Callable[[bytes], None], size: tuple[int, int],
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.send, self.size, self.sleep = send, size, sleep

    def _event(self, action: int, pointer: int, point: Point) -> None:
        self.send(touch(action, pointer, point[0], point[1], self.size))

    def tap(self, at: Point, hold_ms: int = 40) -> None:
        self._event(DOWN, FINGER, at)
        self.sleep(hold_ms / 1000)
        self._event(UP, FINGER, at)

    def swipe(self, start: Point, end: Point, ms: int = 400, hold_ms: int = 0, release_ms: int = 0) -> None:
        """Glissé ; hold_ms : doigt gardé immobile avant de bouger (glisser-déposer) ; release_ms : immobile à la fin
        (pas d'élan : la liste s'arrête où le doigt s'arrête)."""
        self._event(DOWN, FINGER, start)
        if hold_ms:
            self.sleep(hold_ms / 1000)
        for point in path(start, end, ms):
            self.sleep(STEP_MS / 1000)
            self._event(MOVE, FINGER, point)
        if release_ms:
            self.sleep(release_ms / 1000)
        self._event(UP, FINGER, end)

    def multi(self, tracks: Sequence[tuple[Point, Point]], ms: int = 500) -> None:
        """Plusieurs doigts posés ensemble, chacun de son départ à son arrivée (pincer, écarter, tourner)."""
        ids = list(range(1, len(tracks) + 1))
        for pointer, (start, _) in zip(ids, tracks, strict=True):
            self._event(DOWN, pointer, start)
        paths = [list(path(start, end, ms)) for start, end in tracks]
        for i in range(len(paths[0])):
            self.sleep(STEP_MS / 1000)
            for pointer, steps in zip(ids, paths, strict=True):
                self._event(MOVE, pointer, steps[i])
        self.sleep(0.05)
        for pointer, (_, end) in reversed(list(zip(ids, tracks, strict=True))):
            self._event(UP, pointer, end)


EDGE = 0.1  # part de la hauteur gardée libre en haut et en bas (barre d'état, barre de navigation, gestes des bords)


def zoom_tracks(center: Point, size: tuple[int, int], zoom_in: bool, strength: int = 1) -> list[tuple[Point, Point]]:
    """Deux doigts pour zoomer autour de `center`, toujours dans la zone de contenu : jamais dans la barre d'état
    (un doigt qui y descend ouvre le volet des notifications) ni sur les bords (gestes retour, barre de navigation),
    sur l'axe qui a le plus de place."""
    w, h = size
    cx, cy = center
    if not (h * EDGE <= cy <= h * (1 - EDGE) and w * 0.06 <= cx <= w * 0.94):
        raise ValueError("center on a bar or an edge of the screen: aim inside the content")
    room_v = min(cy - h * EDGE, h * (1 - EDGE) - cy)
    room_h = min(cx - w * 0.06, w * 0.94 - cx)
    # Horizontal d'abord : barres de recherche, onglets et volets du bas sont des bandes horizontales, deux doigts à
    # la même hauteur restent dans le contenu (vu sur Maps : un doigt posé sur la barre de recherche, zoom ignoré).
    horizontal = room_h >= 0.5 * room_v
    room = int(max(room_h if horizontal else room_v, 0))
    far = min(2 * room, min(w, h) * (2 + max(1, min(strength, 3))) // 8)
    near = max(far // 5, 40)
    if far <= near:
        raise ValueError("no room to zoom here: aim nearer the middle of the screen")
    gaps = (near, far) if zoom_in else (far, near)
    return pinch_tracks(center, *gaps, horizontal=horizontal)


def pinch_tracks(center: Point, start_gap: int, end_gap: int, horizontal: bool = False) -> list[tuple[Point, Point]]:
    """Deux doigts de part et d'autre du centre : écart start_gap → end_gap pixels (plus grand : zoom avant)."""
    cx, cy = center
    if horizontal:
        return [((cx - start_gap // 2, cy), (cx - end_gap // 2, cy)), ((cx + start_gap // 2, cy), (cx + end_gap // 2, cy))]
    return [((cx, cy - start_gap // 2), (cx, cy - end_gap // 2)), ((cx, cy + start_gap // 2), (cx, cy + end_gap // 2))]


class Control:
    """Serveur scrcpy gardé ouvert pour les gestes d'un téléphone ; arrêté après IDLE s sans geste."""

    def __init__(self, adb: Adb) -> None:
        self.adb = adb
        self._stack: ExitStack | None = None
        self._channel: clip.Channel | None = None
        self._serial: str | None = None
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self.failed = ""  # dernière raison d'indisponibilité (repli sur input)
        # Dernier échec du canal : instant et liaison (numéro adb, nombre de connexions) ; retenté après RETRY s, ou tout
        # de suite après une reconnexion (sinon chaque geste reprendrait le délai de démarrage avant le repli).
        self._failed_at = -RETRY
        self._failed_link: tuple[str | None, int] | None = None

    def _open(self) -> clip.Channel:
        if self._channel is not None and self._serial == self.adb.serial:
            return self._channel
        self._close()
        stack = ExitStack()
        try:
            channel = stack.enter_context(clip._session(self.adb, "gestures"))
        except BaseException:
            stack.close()
            raise
        self._stack, self._channel, self._serial = stack, channel, self.adb.serial
        return channel

    def _close(self) -> None:
        stack, self._stack, self._channel = self._stack, None, None
        if stack is not None:
            try:
                stack.close()
            except (OSError, PhoneError):
                pass  # canal déjà fermé ou téléphone parti : rien à libérer

    def close(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._close()

    def _idle(self) -> None:
        with self._lock:
            self._close()

    def channel(self) -> clip.Channel | None:
        """Canal du serveur scrcpy gardé ouvert (démarré si besoin, minuteur d'inactivité relancé), pour les gestes et
        le collage ; None : indisponible (gestes par input, collage par un serveur à lui)."""
        with self._lock:
            if self._timer:
                self._timer.cancel()
            link = (self.adb.serial, getattr(self.adb, "links", 0))
            if link == self._failed_link and time.monotonic() - self._failed_at < RETRY:
                return None  # échec récent sur la même liaison : input tout de suite, self.failed garde la raison
            try:
                channel = self._open()
            except (PhoneError, OSError) as e:
                self.failed = str(e)[:200]
                self._failed_at, self._failed_link = time.monotonic(), link
                return None
            self._failed_link = None
            self._timer = threading.Timer(IDLE, self._idle)
            self._timer.daemon = True
            self._timer.start()
            return channel

    def broken(self) -> None:
        """Canal en panne (collage interrompu) : fermé, rouvert au prochain usage."""
        with self._lock:
            self._close()

    def fingers(self, size: tuple[int, int]) -> Fingers | None:
        """Doigts reliés au serveur scrcpy ; None : indisponible, l'appelant passe par input."""
        if (channel := self.channel()) is None:
            return None

        def send(data: bytes) -> None:
            try:
                channel.sock.sendall(data)
            except OSError as e:
                with self._lock:
                    self._close()  # canal mort : rouvert au prochain geste
                raise LinkError(f"gesture interrupted ({e}): unknown effect") from None

        return Fingers(send, size)
