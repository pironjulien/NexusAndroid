"""Gestion des applications du téléphone : installer un APK, forcer l'arrêt, vider les données, désinstaller.

Forcer l'arrêt est sans perte (l'application redémarre à la prochaine ouverture) : libre. Installer, vider les données
et désinstaller exigent confirm=true (ordre explicite de l'utilisateur). Les rouages du système (téléphone, interface,
Paramètres, services Google…) ne sont jamais vidés ni désinstallés. Une application du système n'est désinstallée que
pour l'utilisateur (`--user 0`) : elle reste sur le téléphone et revient avec `restore`."""

from __future__ import annotations

import re
from pathlib import Path

from . import extras, policy
from .adb import Adb, PhoneError

# Jamais vidés ni désinstallés : le téléphone ne fonctionnerait plus, ou la liaison des agents tomberait.
CORE = {
    "android", "com.android.systemui", "com.android.phone", "com.android.settings", "com.android.shell",
    "com.android.providers.settings", "com.android.providers.telephony", "com.android.providers.contacts",
    "com.android.providers.media", "com.android.bluetooth", "com.android.networkstack", "com.android.permissioncontroller",
    "com.google.android.gms", "com.google.android.gsf", "com.android.vending", "com.sec.android.app.launcher",
    "com.samsung.android.providers.context", "com.android.server.telecom", "com.android.inputmethod.latin",
    "com.samsung.android.honeyboard", "com.android.adbd", "com.google.android.permissioncontroller",
}
MAX_APK = 2 * 1024 ** 3
ASK = "Explicit order from the user for THIS app → redo with confirm=true; agent's own initiative → ask the user first."


def _system(adb: Adb, package: str) -> bool:
    return package in {line.removeprefix("package:").strip()
                       for line in adb.shell("pm", "list", "packages", "-s", check=False).splitlines()}


def _core(package: str, what: str) -> None:
    if package in CORE or package.startswith("com.android.providers."):
        raise PhoneError(f"{package}: part of the system (phone, interface, settings, store, keyboard…), never "
                         f"{what} by the agent, even with confirm=true")


def info(adb: Adb, package: str) -> str:
    dump = adb.shell("dumpsys", "package", package, check=False)
    def field(name: str) -> str:
        found = re.search(rf"^\s*{name}=(\S+)", dump, re.M)
        return found.group(1) if found else "?"
    enabled = "disabled" if re.search(r"enabled=(2|3|4)\b", dump) else "enabled"
    return (f"{package}: version {field('versionName')} ({field('versionCode').split()[0]}), installed "
            f"{field('firstInstallTime')}, updated {field('lastUpdateTime')}, from "
            f"{field('installerPackageName')}, {enabled}{', system app' if _system(adb, package) else ''}")


def stop(adb: Adb, package: str) -> str:
    adb.shell("am", "force-stop", package)
    running = adb.shell("pidof", package, check=False).strip()
    return (f"{package}: stopped (no process left)" if not running else
            f"{package}: stop sent, but a process is still there (pid {running}): a service restarted it")


def clear(adb: Adb, package: str, confirm: bool) -> str:
    _core(package, "cleared")
    guard_protected(package)
    if not confirm:
        raise PhoneError(f"clear {package}: deletes all its data (accounts, settings, files of the app), for good. {ASK}")
    out = adb.shell("pm", "clear", package, check=False).strip()
    if "Success" not in out:
        raise PhoneError(f"clear {package}: {out[:200] or 'refused by Android'}")
    return f"{package}: data cleared (like a fresh install; sign in again)"


def uninstall(adb: Adb, package: str, confirm: bool) -> str:
    _core(package, "uninstalled")
    guard_protected(package)
    system = _system(adb, package)
    if not confirm:
        what = ("hidden for the user (system app: it stays on the phone, apps(action=\"restore\") brings it back)"
                if system else "removed with its data")
        raise PhoneError(f"uninstall {package}: {what}. {ASK}")
    args = ["pm", "uninstall", "--user", "0", package] if system else ["pm", "uninstall", package]
    out = adb.shell(*args, check=False).strip()
    if "Success" not in out:
        raise PhoneError(f"uninstall {package}: {out[:200] or 'refused by Android'}")
    return (f"{package}: hidden for the user (system app; apps(action=\"restore\") brings it back)" if system else
            f"{package}: uninstalled")


def restore(adb: Adb, package: str) -> str:
    out = adb.shell("cmd", "package", "install-existing", "--user", "0", package, check=False).strip()
    if "installed" not in out.lower():
        raise PhoneError(f"restore {package}: {out[:200] or 'refused by Android'}")
    return f"{package}: back for the user"


def install(adb: Adb, path: str, confirm: bool) -> str:
    """APK du PC installé (mise à jour gardant les données : -r). Dossiers usuels seulement, comme files push."""
    apk = extras.pc_path(path, True, "read")  # dossiers sensibles refusés ; confirm vérifié juste après
    if apk.suffix.lower() != ".apk" or not apk.is_file():
        raise PhoneError(f"{path}: not an .apk file on the PC")
    if apk.stat().st_size > MAX_APK:
        raise PhoneError(f"{apk.name}: larger than 2 GB")
    if not confirm:
        raise PhoneError(f"install {apk.name}: an app from outside the stores gets the permissions it asks for. {ASK}")
    out = adb.host("install", "-r", str(Path(apk)), timeout=600)
    if "Success" not in out:
        raise PhoneError(f"install {apk.name}: {out.strip().splitlines()[-1][:200] if out.strip() else 'failed'}")
    return f"{apk.name}: installed"


def guard_protected(package: str) -> None:
    """Applications bancaires, de paiement ou d'identité : jamais vidées ni désinstallées par l'agent (ses comptes et
    ses accès pourraient être perdus) ; l'utilisateur le fait lui-même."""
    if name := policy.protected_app(package):
        raise PhoneError(f"{name}: banking, payment or identity app, never cleared or uninstalled by the agent: the "
                         "user does it themselves")
