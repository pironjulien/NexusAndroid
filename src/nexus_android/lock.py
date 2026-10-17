"""Verrou commun aux trois agents : chaque client (Claude, Codex, Antigravity) lance son propre serveur, mais il n'y a
qu'un téléphone. Un seul processus à la fois le pilote ; le système libère le verrou si ce processus meurt."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from .adb import PhoneError

LOCK_FILE = Path.home() / ".nexus" / "android.lock"  # hors des sources
LOCK_WAIT = 30.0  # secondes d'attente au plus avant de rendre la main
_OWNER = 64  # position, hors de l'octet verrouillé, où le détenteur inscrit son pid (lisible par les autres)

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, 0)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        fcntl.lockf(fd, fcntl.LOCK_UN, 1, 0)


class PhoneLock:
    """Verrou de fichier inter-processus sur un octet de LOCK_FILE, attente bornée."""

    def __init__(self, path: Path | None = None, wait: float | None = None) -> None:
        self.path = path or LOCK_FILE
        self.wait = LOCK_WAIT if wait is None else wait
        self._fd: int | None = None

    def _owner(self, fd: int) -> str:
        try:
            os.lseek(fd, _OWNER, os.SEEK_SET)
            return os.read(fd, 32).decode("ascii", "replace").strip("\x00 \n")
        except OSError:
            return ""

    def __enter__(self) -> PhoneLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self.wait
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                owner = self._owner(fd)
                os.close(fd)
                raise PhoneError(
                    f"Téléphone occupé : un autre agent le pilote{f' (processus {owner})' if owner else ''} encore "
                    f"après {self.wait:g} s d'attente. Réessayer dans un moment ; rien n'a été fait.")
            time.sleep(0.1)
        os.lseek(fd, _OWNER, os.SEEK_SET)
        os.write(fd, f"{os.getpid():<31}".encode("ascii"))
        self._fd = fd
        return self

    def __exit__(self, *_: object) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                _unlock(fd)
            finally:
                os.close(fd)
