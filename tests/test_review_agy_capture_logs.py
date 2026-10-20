"""Relecture d'Antigravity du 19/10 (capture, logs, lecteurs d'accessibilité) : mots de passe noircis, code apparu
entre l'arbre et la capture, cartes et IBAN masqués, vidéo jetée devant une appli protégée, commandes d'Android qui ne
gardent pas l'accessibilité, adresses natives regroupées, processus d'un plantage retrouvé par son pid."""

from __future__ import annotations

import pytest
from PIL import Image as PILImage
from test_capture import BANK, inbox, rec  # noqa: F401  (fixture rec : faux téléphone qui enregistre)
from test_extras import home  # noqa: F401
from test_guards import node, ui

from nexus_android import capture, logs, screen, server
from nexus_android.adb import PhoneError


def test_password_field_blacked_out_on_screenshots(rec, home):  # noqa: F811
    rec.uis = [ui(node("••••••", (0, 300, 900, 400), cls="android.widget.EditText", password=True))]
    capture.screenshot(rec, "connexion", None, False)  # type: ignore[arg-type]
    image = PILImage.open(home / "Downloads" / "Telephone" / "connexion.png").convert("RGB")
    assert max(image.getpixel((450, 350))) < 60  # type: ignore[arg-type]
    assert min(image.getpixel((450, 1000))) > 200  # type: ignore[arg-type]


def test_code_shown_only_after_the_first_tree_still_blacked_out(rec, home):  # noqa: F811
    code = ui(node("Code de vérification", (0, 100, 900, 160), clickable=False),
              node("482913", (0, 170, 900, 230), clickable=False))
    rec.uis = [ui(), code]  # arbre lu avant la capture : rien ; après : le code (notification arrivée entre-temps)
    text = capture.screenshot(rec, "x", None, False)  # type: ignore[arg-type]
    assert "blacked out" in text
    image = PILImage.open(home / "Downloads" / "Telephone" / "x.png").convert("RGB")
    assert max(image.getpixel((450, 200))) < 60  # type: ignore[arg-type]


@pytest.mark.parametrize("card", ["4970 1012 3456 7893", "4970-1012-3456-7893", "4970101234567893",
                                  "378282246310005"])
def test_card_numbers_masked_in_logs(card):
    assert logs.mask(f"payment card={card} ok") .count(card) == 0
    assert logs.CARD in logs.mask(f"order with {card} accepted")


def test_iban_masked_but_not_random_numbers():
    assert logs.mask("refund to FR76 3000 6000 0112 3456 7890 189 done") == f"refund to {logs.IBAN} done"
    assert logs.mask("iban GB82WEST12345698765432") == f"iban {logs.IBAN}"
    assert logs.mask("bytes=1234567890123 size 4970101234567890") == "bytes=1234567890123 size 4970101234567890"
    assert logs.mask("FR00 3000 6000 0112 3456 7890 189") == "FR00 3000 6000 0112 3456 7890 189"  # clé fausse


def test_video_discarded_when_a_protected_app_comes_up(rec, home):  # noqa: F811
    capture.start(rec, None, "bug", None, False)  # type: ignore[arg-type]
    remote = rec.spawned[0].split()[-2]
    capture.guard(rec, "com.android.settings")  # type: ignore[arg-type]
    assert capture._CURRENT is not None
    capture.guard(rec, BANK)  # type: ignore[arg-type]
    assert capture._CURRENT is None and ("rm", "-f", remote) in rec.calls
    with pytest.raises(PhoneError, match="recording discarded"):
        capture.stop(rec)  # type: ignore[arg-type]
    assert inbox(home) == [] and rec.pulls == []


def test_video_discarded_at_stop_if_a_protected_app_is_shown(rec, home):  # noqa: F811
    capture.start(rec, None, "bug", None, False)  # type: ignore[arg-type]
    rec.package = BANK  # l'utilisateur a ouvert sa banque lui-même
    with pytest.raises(PhoneError, match="recording discarded"):
        capture.stop(rec)  # type: ignore[arg-type]
    assert inbox(home) == []


def test_video_guard_runs_after_every_tool(rec):  # noqa: F811
    capture.start(rec, None, "bug", None, False)  # type: ignore[arg-type]
    rec.package = BANK
    server.SCREEN.last_state = server.SCREEN.state()  # type: ignore[union-attr]
    server._video_guard()
    assert capture._CURRENT is None


def test_android_shell_commands_are_not_accessibility_holders():
    ps = ("21 app_process /system/bin/app_process /system/bin com.android.commands.input.Input tap 1 1\n"
          "22 app_process /system/bin/app_process /system/bin com.android.commands.am.Am start -n x/y\n"
          "23 app_process app_process /system/bin com.android.commands.uiautomator.Launcher runtest x.jar\n")
    assert screen.parse_holders(ps) == [("com.android.commands.uiautomator.Launcher", "23")]


def test_native_addresses_do_not_split_identical_crashes():
    crash = logs.Crash("native", "2026-10-19 10:00:00.000", 1, process="com.app")
    one = logs.signature(crash, ["signal 11 (SIGSEGV), fault addr 0x7a3c00"])
    two = logs.signature(crash, ["signal 11 (SIGSEGV), fault addr 0x91ff10"])
    assert one == two


def test_crash_without_process_line_named_by_its_pid(monkeypatch):
    block = ("2026-10-19 10:00:00.000  4321  4321 E AndroidRuntime: FATAL EXCEPTION: main\n"
             "2026-10-19 10:00:00.001  4321  4321 E AndroidRuntime: java.lang.IllegalStateException: solde 1234\n")

    class Phone:
        def shell(self, *argv, check=True, **kw):  # noqa: ANN001, ANN002, ANN003
            if argv[0] == "logcat":
                return block
            return "PID NAME\n4321 " + BANK + "\n"

    text = logs.crashes(Phone(), "", None, None)  # type: ignore[arg-type]
    assert BANK in text and "exception type and app frames only" in text and "1234" not in text
