"""adb sur ce PC : celui déjà installé (variable, PATH, winget, SDK Android), sinon les Platform Tools officielles de
Google, téléchargées une fois dans ~/.nexus/nexus-android/tools (empreinte du manifeste du dépôt SDK contrôlée)."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import stat
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from .adb import PhoneError

REPOSITORY = "https://dl.google.com/android/repository/"
MANIFEST = REPOSITORY + "repository2-3.xml"  # manifeste lu par sdkmanager : version, taille et SHA-1 de chaque archive
OFFICIAL = "https://developer.android.com/tools/releases/platform-tools"
TOOLS = Path.home() / ".nexus" / "nexus-android" / "tools"
LOCAL = TOOLS / "platform-tools"
EXE = "adb.exe" if os.name == "nt" else "adb"
MANIFEST_MAX = 8 * 1024 * 1024  # le manifeste fait ~420 Ko
ARCHIVE_MAX = 64 * 1024 * 1024  # l'archive Windows fait ~8 Mo, macOS ~16 Mo
TIMEOUT = 60.0
DISABLED = os.environ.get("NEXUS_ANDROID_ADB_DOWNLOAD", "1").strip().lower() in {"0", "off", "false", "no"}


def host_os() -> str:
    return "windows" if os.name == "nt" else "macosx" if sys.platform == "darwin" else "linux"


def installed() -> str | None:
    """adb déjà présent : NEXUS_ANDROID_ADB, PATH, winget, SDK Android (variables ou dossier par défaut), copie locale."""
    explicit = os.environ.get("NEXUS_ANDROID_ADB")
    if explicit and Path(explicit).is_file():
        return explicit
    if found := shutil.which("adb"):
        return found
    home = Path.home()
    local_app = Path(os.environ.get("LOCALAPPDATA", ""))
    winget = local_app / "Microsoft" / "WinGet" / "Packages"
    if os.environ.get("LOCALAPPDATA"):
        for candidate in winget.glob("Google.PlatformTools_*/platform-tools/adb.exe"):
            return str(candidate)
    sdks = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
            str(local_app / "Android" / "Sdk") if os.environ.get("LOCALAPPDATA") else None,
            str(home / "Library" / "Android" / "sdk"), str(home / "Android" / "Sdk")]
    for sdk in filter(None, sdks):
        if (candidate := Path(sdk) / "platform-tools" / EXE).is_file():
            return str(candidate)
    if (LOCAL / EXE).is_file():
        return str(LOCAL / EXE)
    return None


def latest(manifest: bytes, host: str) -> tuple[str, str, int, str]:
    """(url, sha1, taille, version) de l'archive stable des Platform Tools pour `host` dans le manifeste du dépôt SDK."""
    root = ET.fromstring(manifest)
    for package in root.iter("remotePackage"):
        if package.get("path") != "platform-tools":
            continue
        channel = package.find("channelRef")
        if channel is not None and channel.get("ref") != "channel-0":  # canal stable seulement
            continue
        rev = package.find("revision")
        version = ".".join(rev.findtext(p) or "0" for p in ("major", "minor", "micro")) if rev is not None else "?"
        for archive in package.iter("archive"):
            if archive.findtext("host-os") != host:
                continue
            url, sha1 = archive.findtext("complete/url"), archive.findtext("complete/checksum")
            size = archive.findtext("complete/size")
            if url and sha1 and size and "/" not in url and url.endswith(".zip"):
                return REPOSITORY + url, sha1.strip().lower(), int(size), version
    raise ValueError(f"no stable Platform Tools archive for {host} in the SDK repository manifest")


def _get(url: str, limit: int) -> bytes:
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise OSError(f"{url}: larger than expected")
    return data


def _manual(reason: str) -> PhoneError:
    return PhoneError(f"adb not found and {reason}. Install Google's Platform Tools by hand ({OFFICIAL}; on Windows: "
                      "winget install Google.PlatformTools), or set NEXUS_ANDROID_ADB to the full path of adb, then "
                      "try again.")


def fetch(say=lambda _text: None) -> str:
    """Télécharge les Platform Tools officielles (manifeste du dépôt SDK de Google, SHA-1 et taille vérifiés), les pose
    dans LOCAL (remplacement atomique du dossier) et rend le chemin d'adb."""
    host = host_os()
    try:
        url, sha1, size, version = latest(_get(MANIFEST, MANIFEST_MAX), host)
        say(f"Downloading Google's Android Platform Tools {version} ({size // 1_000_000} MB) from {url} ...")
        data = _get(url, ARCHIVE_MAX)
    except (OSError, urllib.error.URLError, ValueError, ET.ParseError) as e:
        raise _manual(f"the automatic download failed ({e})") from None
    if len(data) != size or hashlib.sha1(data).hexdigest() != sha1:  # noqa: S324 - empreinte publiée par Google
        raise _manual("the downloaded archive does not match the size and SHA-1 published by Google; it was rejected")
    part = TOOLS / f"platform-tools.{os.getpid()}.part"
    try:
        shutil.rmtree(part, ignore_errors=True)
        part.mkdir(parents=True)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.infolist():
                target = (part / member.filename).resolve()
                if not target.is_relative_to(part.resolve()):
                    raise ValueError(f"unsafe path in the archive: {member.filename}")
            archive.extractall(part)
        inner = part / "platform-tools"
        if not (inner / EXE).is_file():
            raise ValueError(f"{EXE} missing from the archive")
        if os.name != "nt":
            for name in ("adb", "fastboot"):
                if (tool := inner / name).is_file():
                    tool.chmod(tool.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if not (LOCAL / EXE).is_file():  # un autre serveur a pu le poser entre-temps : le sien est gardé
            shutil.rmtree(LOCAL, ignore_errors=True)
            os.replace(inner, LOCAL)
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        raise _manual(f"the downloaded archive could not be unpacked into {TOOLS} ({e})") from None
    finally:
        shutil.rmtree(part, ignore_errors=True)
    say(f"Platform Tools {version} installed in {LOCAL}.")
    return str(LOCAL / EXE)


def find(say=lambda _text: None) -> str:
    """Chemin d'adb : l'installé d'abord (même version que le serveur adb déjà lancé, le plus souvent), sinon
    téléchargé une fois, sauf NEXUS_ANDROID_ADB_DOWNLOAD=0."""
    if found := installed():
        return found
    if DISABLED:
        raise _manual("the automatic download is turned off (NEXUS_ANDROID_ADB_DOWNLOAD=0)")
    return fetch(say)
