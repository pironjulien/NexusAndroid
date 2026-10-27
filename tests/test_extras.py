"""Nouveautés d'octobre 2026 : liaison perdue, pliage, l'utilisateur au téléphone, accents, notifications, fichiers,
position, réglages. adb simulé : aucun vrai téléphone."""

from __future__ import annotations

import hashlib
import socket
import struct
import threading
import time

import pytest
from PIL import Image as PILImage
from test_guards import FakePhone, act, field, node, ui

from nexus_android import adb as adb_mod
from nexus_android import clip, extras, server
from nexus_android.adb import LinkError, PhoneError
from nexus_android.screen import is_black, parse_helpers, parse_posture
from nexus_android.server import Step

# ---------------------------------------------------------------- liaison perdue


def test_link_lost_pattern_is_device_level_only():
    assert adb_mod._LINK_LOST.search("error: device offline")
    assert adb_mod._LINK_LOST.search("adb.EXE: device offline")  # relevé sur le Fold en Wi-Fi
    assert adb_mod._LINK_LOST.search("adb.exe: error: device '192.0.2.67:37477' not found")
    assert adb_mod._LINK_LOST.search("error: no devices/emulators found")
    assert not adb_mod._LINK_LOST.search("/system/bin/sh: foo: not found")


def test_gesture_cut_by_link_loss_is_unknown_not_failed(phone, monkeypatch):
    phone.uis = [ui(node("Bouton", (0, 0, 500, 200)))]
    original = phone.run

    def run(*argv, timeout=30.0):
        if argv[:2] == ("input", "tap"):
            raise LinkError("adb shell : pas de réponse après 30 s.")
        return original(*argv, timeout=timeout)

    monkeypatch.setattr(phone, "run", run)
    out = act(Step(do="tap", target="Bouton"), Step(do="tap", target="Bouton"))
    assert out.startswith("? 1.") and "unknown effect" in out and "2." not in out


def test_offline_wifi_link_is_reconnected(monkeypatch, tmp_path):
    import subprocess

    monkeypatch.setattr(adb_mod, "WIFI_STATE", tmp_path / "wifi.json")
    (tmp_path / "wifi.json").write_text('{"serialno": "RFTEST00001", "address": "192.0.2.67:37477"}', encoding="utf-8")
    monkeypatch.setattr(adb_mod, "_find_adb", lambda: "adb")
    monkeypatch.setattr(time, "sleep", lambda s: None)
    state = {"link": "offline"}
    calls = []

    def raw(self, args, timeout=30.0, serial=True):
        calls.append(args)
        out = b""
        if args[:2] == ["devices", "-l"]:
            out = f"List of devices attached\n192.0.2.67:37477 {state['link']} model:SM_F971B\n".encode()
        elif args[:2] == ["reconnect", "offline"]:
            state["link"] = "device"
        elif "ro.serialno" in args:
            out = b"RFTEST00001\n"
        elif "boot_id" in " ".join(args):
            out = b"11bd9a22-5d18-4123-9858-51b02a379bdb\n"
        return subprocess.CompletedProcess(args, 0, out, b"")

    monkeypatch.setattr(adb_mod.Adb, "_raw", raw)
    device = adb_mod.Adb().connect()
    assert device.serial == "192.0.2.67:37477" and ["reconnect", "offline"] in calls
    assert not any(a[:2] == ["mdns", "services"] for a in calls)  # trouvé sans chercher ailleurs


# ---------------------------------------------------------------- pliage


def test_posture_parse():
    dump = "  mCommittedState=Optional[DeviceState{identifier=3, name='OPENED', app_accessible=true}]"
    assert parse_posture(dump) == "OPENED"
    assert parse_posture("rien") == ""


def test_coordinates_refused_after_fold_change(phone, monkeypatch):
    phone.uis = [ui(node("Bouton", (0, 0, 500, 200)))]
    server.SCREEN.posture = "CLOSED"  # type: ignore[union-attr]
    monkeypatch.setattr(server.SCREEN, "current_posture", lambda: "OPENED")
    out = act(Step(do="tap", target="100,100"))
    assert out.startswith("✗ 1.") and "folded or unfolded" in out
    assert not phone.inputs
    # Un texte visé est relu sur l'écran actuel : permis.
    assert act(Step(do="tap", target="Bouton")).startswith("~ 1.")


# ---------------------------------------------------------------- l'utilisateur au téléphone


def test_no_gesture_while_owner_uses_phone(phone, monkeypatch):
    monkeypatch.setattr(server, "BUSY_WAIT", 0.0)
    monkeypatch.setattr(server, "_LAST_INPUT", 0.0)
    monkeypatch.setattr(server.awake, "idle_on_phone", lambda adb: 0.5)
    phone.uis = [ui(node("Bouton", (0, 0, 500, 200)))]
    with pytest.raises(PhoneError, match="The user is using the phone right now"):
        act(Step(do="tap", target="Bouton"))
    assert not phone.inputs


def test_own_recent_gesture_is_not_owner(phone, monkeypatch):
    monkeypatch.setattr(server, "BUSY_WAIT", 0.0)
    monkeypatch.setattr(server, "_LAST_INPUT", time.time())
    monkeypatch.setattr(server.awake, "idle_on_phone", lambda adb: 0.5)
    phone.uis = [ui(node("Bouton", (0, 0, 500, 200)))]
    assert act(Step(do="tap", target="Bouton")).startswith("~ 1.")


# ---------------------------------------------------------------- accents


def test_accented_text_is_pasted(phone, monkeypatch):
    pasted = []
    monkeypatch.setattr(clip, "paste", lambda adb, text, kept=None: pasted.append(text) or True)
    phone.uis = [ui(field()), ui(field("Réponse été 🧪"))]
    out = act(Step(do="type", text="Réponse été 🧪"))
    assert pasted == ["Réponse été 🧪"] and out.startswith("✓ 1.") and "non remis" not in out
    assert not [c for c in phone.inputs if c[1] == "text"]


def test_pasted_but_clipboard_not_restored_is_not_a_failure(phone, monkeypatch):
    monkeypatch.setattr(clip, "paste", lambda adb, text, kept=None: False)
    phone.uis = [ui(field()), ui(field("été"))]
    out = act(Step(do="type", text="été"))
    assert out.startswith("✓ 1.") and "the user's clipboard was not restored" in out


def test_link_lost_while_typing_ascii_stays_unknown(phone, monkeypatch):
    original = phone.run

    def run(*argv, timeout=30.0):
        if argv[:2] == ("input", "text"):
            raise LinkError("liaison perdue avec le téléphone (adb.EXE: device offline)")
        return original(*argv, timeout=timeout)

    monkeypatch.setattr(phone, "run", run)
    phone.uis = [ui(field())]
    assert act(Step(do="type", text="bonjour")).startswith("? 1.")


REAL_FETCH = clip._fetch  # pris avant la fixture no_download de conftest


def _pin(monkeypatch, data: bytes) -> None:
    monkeypatch.setattr(clip, "SHA256", hashlib.sha256(data).hexdigest())


def test_scrcpy_server_downloaded_once_then_reused(monkeypatch):
    fetched = []
    _pin(monkeypatch, b"serveur")
    monkeypatch.setattr(clip, "_fetch", lambda url: fetched.append(url) or b"serveur")
    assert clip.ensure_local().read_bytes() == b"serveur"
    assert clip.ensure_local() == clip.LOCAL
    assert fetched == [clip.URL]
    assert clip.URL == f"https://github.com/Genymobile/scrcpy/releases/download/v{clip.VERSION}/scrcpy-server-v{clip.VERSION}"
    assert not list(clip.LOCAL.parent.glob("*.part"))


def test_scrcpy_download_with_wrong_digest_is_rejected(monkeypatch):
    _pin(monkeypatch, b"serveur")
    monkeypatch.setattr(clip, "_fetch", lambda url: b"autre chose")
    with pytest.raises(PhoneError, match="expected hash"):
        clip.ensure_local()
    assert not clip.LOCAL.exists() or not list(clip.LOCAL.parent.iterdir())


def test_altered_local_copy_is_downloaded_again(monkeypatch):
    _pin(monkeypatch, b"serveur")
    clip.LOCAL.parent.mkdir(parents=True)
    clip.LOCAL.write_bytes(b"altere")
    monkeypatch.setattr(clip, "_fetch", lambda url: b"serveur")
    assert clip.ensure_local().read_bytes() == b"serveur"


def test_download_is_size_bounded(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n: int) -> bytes:
            return b"x" * n

    monkeypatch.setattr(clip.urllib.request, "urlopen", lambda url, timeout: Response())
    with pytest.raises(OSError, match="KB"):
        REAL_FETCH(clip.URL)


def test_accents_without_scrcpy_server_fail_clearly_and_type_nothing(phone):
    phone.uis = [ui(field()), ui(field())]
    out = act(Step(do="type", text="Réponse été"))
    assert out.startswith("✗ 1.") and "nothing was typed" in out and clip.URL in out and "install.ps1" in out
    assert not phone.inputs  # pas de translittération : aucun autre texte tapé à la place
    assert not any(c[0] == "push" for c in phone.calls)


def test_password_field_still_refused_for_accents(phone, monkeypatch):
    monkeypatch.setattr(clip, "paste", lambda adb, text, kept=None: pytest.fail("collé dans un mot de passe"))
    phone.uis = [ui(field(password=True))]
    assert "password field" in act(Step(do="type", text="été"))


def _serve(handler):
    a, b = socket.socketpair()
    thread = threading.Thread(target=handler, args=(b,), daemon=True)
    thread.start()
    a.settimeout(5)
    return clip.Channel(a), thread


def _read(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        data += sock.recv(n - len(data))
    return data


def test_clipboard_channel_protocol():
    seen = []

    def phone_side(sock):
        assert _read(sock, 2) == bytes([clip.GET_CLIPBOARD, 0])
        text = b"ancien"
        sock.sendall(bytes([clip.CLIPBOARD]) + struct.pack(">I", len(text)) + text)
        for _ in range(2):
            kind, sequence, paste, size = struct.unpack(">BQBI", _read(sock, 14))
            seen.append((kind, paste, _read(sock, size).decode()))
            sock.sendall(bytes([clip.ACK_CLIPBOARD]) + struct.pack(">Q", sequence))

    channel, thread = _serve(phone_side)
    before = channel.get()
    channel.set(7, "été 🧪", paste=True)
    channel.set(8, before or "", paste=False)
    thread.join(2)
    assert before == "ancien"
    assert seen == [(clip.SET_CLIPBOARD, 1, "été 🧪"), (clip.SET_CLIPBOARD, 0, "ancien")]


def test_empty_clipboard_reads_as_none():
    def phone_side(sock):
        _read(sock, 2)  # le serveur ne répond rien quand le presse-papiers est vide
        time.sleep(0.5)

    channel, _ = _serve(phone_side)
    assert channel.get(wait=0.2) is None


# ---------------------------------------------------------------- écran


def test_black_capture_and_helpers():
    assert is_black(PILImage.new("RGB", (200, 300), "black"))
    assert not is_black(PILImage.new("RGB", (200, 300), "white"))
    ps = ("PID NAME ARGS\n123 app_process app_process / com.mobilenext.mobilecli.DeviceServer\n"
          "456 app_process CLASSPATH=/data/local/tmp/nexus-scrcpy.jar app_process / com.genymobile.scrcpy.Server\n"
          "789 com.whatsapp com.whatsapp")
    assert parse_helpers(ps) == ["app_process / com.mobilenext.mobilecli.DeviceServer"]


# ---------------------------------------------------------------- notifications

DUMP = """Current Notification Manager state:
  Notification List:
    NotificationRecord(0x1: pkg=com.google.android.apps.messaging user=UserHandle{0} id=3 flags=AUTO_CANCEL)
      notification=
            when=1792260271059/1792260271059
            extras={
                android.title=String (Banque Exemple)
                android.text=String (Votre code de validation : 482913)
                android.bigText=String (Votre code de validation : 482913
valable 10 minutes (ne le communiquez pas))
            }
    NotificationRecord(0x2: pkg=com.microsoft.appmanager user=UserHandle{0} id=101 flags=ONGOING_EVENT|NO_CLEAR)
      notification=
            when=1792260271000/1792260271000
            extras={
                android.title=String (Vos appareils sont connectés.)
                android.text=String (Connecté à PC-BUREAU.)
            }
  TimeoutPendingIntent:
  mArchive=Archive (100 notifications)
    StatusBarNotification(pkg=com.whatsapp user=UserHandle{0} id=1)
    StatusBarNotification(pkg=com.whatsapp user=UserHandle{0} id=2)
  Snoozed notifications:
"""


def test_notifications_parse_mask_and_filter():
    notices, dismissed = extras.parse_notifications(DUMP)
    assert [n.package for n in notices] == ["com.google.android.apps.messaging", "com.microsoft.appmanager"]
    assert notices[0].text.endswith("(ne le communiquez pas)")
    assert "482913" not in notices[0].line() and "[masked code]" in notices[0].line()
    assert notices[1].ongoing and not notices[0].ongoing
    assert dismissed == ["com.whatsapp", "com.whatsapp"]

    class Fake:
        def shell(self, *argv, **kw):
            return DUMP

    out = extras.notifications(Fake(), all_=False)  # type: ignore[arg-type]
    assert "PC-BUREAU" not in out and "1 ongoing hidden" in out and "com.whatsapp ×2" in out
    assert "PC-BUREAU" in extras.notifications(Fake(), all_=True)  # type: ignore[arg-type]


# ---------------------------------------------------------------- fichiers, position, réglages


def test_phone_path_stays_in_shared_storage():
    assert extras.phone_path("DCIM/Camera") == "/sdcard/DCIM/Camera"
    assert extras.phone_path("/storage/emulated/0/Download/a.pdf") == "/sdcard/Download/a.pdf"
    assert extras.phone_path(None) == "/sdcard/Download"
    for bad in ("/data/data/com.whatsapp", "/sdcard/../data", "/system"):
        with pytest.raises(PhoneError):
            extras.phone_path(bad)


def test_push_never_overwrites(tmp_path):
    local = tmp_path / "photo.jpg"
    local.write_bytes(b"x")

    class Fake:
        pushed = False

        def run(self, *argv, **kw):
            return (0, "", "") if argv[:2] == ("test", "-e") else (1, "", "")

        def shell(self, *argv, **kw):
            return ""

        def host(self, *argv, **kw):
            Fake.pushed = True
            return ""

    with pytest.raises(PhoneError, match="already exists"):
        extras.push(Fake(), str(local), confirm=True)  # type: ignore[arg-type]
    assert not Fake.pushed


@pytest.fixture
def home(monkeypatch, tmp_path):
    """Profil Windows simulé : dossiers usuels, dossier Démarrage et OneDrive sous tmp_path."""
    user = tmp_path / "user"
    for name in ("Downloads", "Documents", ".ssh", "OneDrive/Documents/Administratif", "OneDrive/Documents/NexusSecure",
                 "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup", "Projets"):
        (user / name).mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("OneDrive", str(user / "OneDrive"))
    monkeypatch.setenv("APPDATA", str(user / "AppData" / "Roaming"))
    monkeypatch.delenv("OneDriveConsumer", raising=False)
    monkeypatch.delenv("OneDriveCommercial", raising=False)
    monkeypatch.setattr(extras, "PC_INBOX", user / "Downloads" / "Telephone")
    return user


class FilesPhone:
    def __init__(self) -> None:
        self.hosts: list[tuple[str, ...]] = []

    def run(self, *argv, **kw):
        return 1, "", ""

    def shell(self, *argv, **kw):
        return ""

    def host(self, *argv, **kw):
        self.hosts.append(argv)
        return ""


def test_sensitive_pc_paths_refused_even_with_confirm(home):
    startup = home / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup"
    secrets = [home / ".ssh" / "id_ed25519", home / "Projets" / ".env", home / "Projets" / ".env.local",
               home / "OneDrive/Documents/NexusSecure/cle.txt", home / "OneDrive/Documents/Administratif/impots.pdf",
               startup / "x.lnk", home / "Downloads" / ".." / ".ssh" / "config", home / ".aws" / "credentials",
               home / ".nexus" / "jeton"]
    phone = FilesPhone()
    for path in secrets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("secret")
        with pytest.raises(PhoneError, match="sensitive path"):
            extras.push(phone, str(path), confirm=True)  # type: ignore[arg-type]
    for folder in (home / ".ssh", startup, home / "Downloads" / ".." / ".gnupg"):
        with pytest.raises(PhoneError, match="sensitive path"):
            extras.pull(phone, "Download/a.txt", str(folder), confirm=True)  # type: ignore[arg-type]
    with pytest.raises(PhoneError, match="sensitive path"):  # fichier .env rapatrié
        extras.pull(phone, "Download/.env", str(home / "Downloads"), confirm=True)  # type: ignore[arg-type]
    assert phone.hosts == [] and not (home / ".gnupg").exists()


def test_symlink_to_a_sensitive_folder_is_resolved(home):
    link = home / "Downloads" / "raccourci"
    try:
        link.symlink_to(home / ".ssh", target_is_directory=True)
    except OSError:
        pytest.skip("liens symboliques non permis sur ce poste")
    with pytest.raises(PhoneError, match="sensitive path"):
        extras.pull(FilesPhone(), "Download/a.txt", str(link), confirm=True)  # type: ignore[arg-type]


def test_unusual_pc_paths_need_confirm(home):
    phone = FilesPhone()
    other = home / "Projets" / "photo.jpg"
    other.write_bytes(b"x")
    with pytest.raises(PhoneError, match="confirm=true"):
        extras.push(phone, str(other))  # type: ignore[arg-type]
    with pytest.raises(PhoneError, match="confirm=true"):
        extras.pull(phone, "Download/a.txt", str(home / "Projets"))  # type: ignore[arg-type]
    assert phone.hosts == []
    assert extras.push(phone, str(other), confirm=True).startswith("Copied to the phone")  # type: ignore[arg-type]
    usual = home / "Documents" / "cv.pdf"
    usual.write_bytes(b"x")
    assert extras.push(phone, str(usual)).startswith("Copied to the phone")  # type: ignore[arg-type]
    assert extras.pull(phone, "Download/a.txt").startswith("Copied to the PC")  # type: ignore[arg-type]  # ~/Downloads/Telephone
    assert extras.pull(phone, "Download/b.txt", str(home / "OneDrive" / "Documents")).startswith("Copied to the PC")  # type: ignore[arg-type]
    assert [h[0] for h in phone.hosts] == ["push", "push", "pull", "pull"]


def test_location_picks_fresh_precise_fix():
    dump = ("last location=Location[network 48.857500,2.295100 hAcc=300.0 et=+1d11h48m57s291ms]\n"
            "last location=Location[fused 48.858370,2.294481 hAcc=4.753 et=+1d11h48m43s769ms alt=109.8]")
    uptime = extras._elapsed("+1d11h50m00s")
    lat, lon, acc, age = extras.parse_location(dump, uptime)  # type: ignore[misc]
    assert (lat, lon) == (48.858370, 2.294481) and acc < 5 and 70 < age < 80


def test_wifi_off_refused_on_wifi_link():
    class Fake:
        calls: list = []

        def shell(self, *argv, **kw):
            Fake.calls.append(argv)
            return "1"

    with pytest.raises(PhoneError, match="would cut the link"):
        extras.setting(Fake(), "wifi", "off", "wifi")  # type: ignore[arg-type]
    assert not [c for c in Fake.calls if c[0] == "svc"]


def test_volume_parse():
    assert extras._volume("volume is 5 in range [0..15]") == (0, 15, "33 % (media)")


@pytest.fixture
def phone(monkeypatch):
    fake = FakePhone()
    scr = server.Screen(fake)  # type: ignore[arg-type]
    scr.size = (1080, 2000)
    monkeypatch.setattr(server, "ADB", fake)
    monkeypatch.setattr(server, "SCREEN", scr)
    monkeypatch.setattr(server, "MESSAGES", object())
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return fake


def test_list_files_follows_the_sdcard_link():
    # /sdcard est un lien : `ls -l /sdcard` décrit le lien, `ls -l /sdcard/` liste le dossier (émulateur Android 14).
    class LinkPhone:
        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        def shell(self, *argv, **kw):
            self.calls.append(argv)
            if argv[-1].endswith("/"):
                return ("total 8\ndrwxrws--- 3 u0_a177 media_rw 4096 2026-10-27 09:21 Download\n"
                        "drwxrws--- 3 u0_a177 media_rw 4096 2026-10-27 09:20 Pictures\n")
            return "lrw-r--r-- 1 root root 21 2024-07-11 18:27 /sdcard -> /storage/self/primary\n"

    phone = LinkPhone()
    listing = extras.list_files(phone, "/sdcard")  # type: ignore[arg-type]
    assert "Download  (folder)" in listing and "Pictures" in listing and "->" not in listing
    assert phone.calls[-1] == ("ls", "-lAt", "/sdcard/")
    extras.list_files(phone, "Download/")  # type: ignore[arg-type]
    assert phone.calls[-1] == ("ls", "-lAt", "/sdcard/Download/")


def test_list_files_on_a_file_is_a_clear_error():
    class FilePhone:
        def shell(self, *argv, **kw):
            return "ls: /sdcard/Download/a.pdf/: Not a directory\n"

    with pytest.raises(PhoneError, match="Not a directory"):
        extras.list_files(FilePhone(), "Download/a.pdf")  # type: ignore[arg-type]
