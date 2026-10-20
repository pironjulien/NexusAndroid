"""Fonctions des applications (AppFunctions) : liste lue dans la sortie JSON de cmd app_function, nom court résolu,
lecture libre, suppression avec confirm=true, appel avec l'accord lié à l'action, erreurs de l'appli rendues ; adb
simulé."""

from __future__ import annotations

import json

import pytest

from nexus_android import functions, server
from nexus_android.adb import PhoneError

LISTING = {
    "com.sec.android.app.clockpackage": [
        {"AppFunctionStaticMetadata-x": {"functionId": ["com.sec.android.app.clockpackage.crossappactions#"
                                                        "AlarmCrossAppActions_findAlarms"],
                                         "schemaName": ["findAlarms"], "enabledByDefault": [True]}},
        {"AppFunctionRuntimeMetadata-x": {}},
        {"AppFunctionStaticMetadata-y": {"functionId": ["com.sec.android.app.clockpackage.crossappactions#"
                                                        "AlarmCrossAppActions_deleteAlarms"],
                                         "schemaName": ["deleteAlarms"], "enabledByDefault": [True]}}],
    "com.samsung.android.dialer": [
        {"AppFunctionStaticMetadata-z": {"functionId": ["com.samsung.android.dialer#PhoneFunctions_makeCall"],
                                         "schemaName": ["makeCall"], "enabledByDefault": [True]}}],
}


class FnPhone:
    serial = "x"

    def __init__(self) -> None:
        self.runs: list[tuple[str, ...]] = []

    def shell(self, *argv: str, timeout: float = 30.0, check: bool = True) -> str:
        if argv[:3] == ("cmd", "app_function", "list-app-functions"):
            pkg = argv[argv.index("--package") + 1] if "--package" in argv else ""
            return json.dumps({k: v for k, v in LISTING.items() if not pkg or k == pkg})
        return ""

    def run(self, *argv: str, timeout: float = 30.0) -> tuple[int, str, str]:
        self.runs.append(argv)
        params = argv[argv.index("--parameters") + 1]
        if params == "{}":
            return 0, "Error executing app function: Parameter [findAlarmsParams] cannot be null (code 1001).", ""
        return 0, '{"androidAppfunctionsReturnValue": [{"label": "Réveil", "note": "code 482913"}]}', ""


@pytest.fixture
def phone(monkeypatch):
    fake = FnPhone()
    monkeypatch.setattr(server, "_init", lambda: (fake, object(), object()))
    monkeypatch.setattr(server, "_find_package", lambda adb, app: {"clock": "com.sec.android.app.clockpackage",
                                                                    "dialer": "com.samsung.android.dialer"}.get(app, app))
    return fake


def test_catalog_and_risk():
    assert functions.risk("findAlarms") == "read" and functions.risk("createNote") == "write"
    assert functions.risk("deleteNotes") == "risky" and functions.risk("makeCall") == "outward"


def test_list_marks_the_functions_that_change_something(phone):
    text = server._functions("list", None, None, None, False, None)
    assert "com.sec.android.app.clockpackage: deleteAlarms *, findAlarms" in text
    assert "com.samsung.android.dialer: makeCall *" in text


def test_read_runs_and_masks_codes(phone):
    out = server._functions("run", "clock", "findAlarms", {"findAlarmsParams": {}}, False, None)
    assert "Réveil" in out and "482913" not in out
    run = phone.runs[-1]
    assert run[run.index("--function") + 1].endswith("#AlarmCrossAppActions_findAlarms")
    with pytest.raises(PhoneError, match=r"Parameter \[findAlarmsParams\] cannot be null"):
        server._functions("run", "clock", "findAlarms", None, False, None)  # erreur de l'appli rendue telle quelle


def test_delete_needs_confirm_and_call_needs_the_accord(phone):
    with pytest.raises(PhoneError, match="confirm=true"):
        server._functions("run", "clock", "deleteAlarms", {"deleteAlarmsParams": {}}, False, None)
    assert phone.runs == []
    server._functions("run", "clock", "deleteAlarms", {"deleteAlarmsParams": {}}, True, None)
    assert len(phone.runs) == 1
    with pytest.raises(PhoneError, match="accord"):
        server._functions("run", "dialer", "makeCall", {"makeCallParams": {}}, True, None)
    assert len(phone.runs) == 1


def test_unknown_function_lists_the_known_ones(phone):
    with pytest.raises(PhoneError, match="unknown in com.sec.android.app.clockpackage: deleteAlarms, findAlarms"):
        server._functions("run", "clock", "snooze", None, False, None)


def test_unusual_names_are_never_presumed_harmless():
    """Relecture de Codex : sans nom de schéma, « SmsFunctions_send_message » était réduit à « message », exécuté sans
    confirmation."""
    assert functions.risk("message", "SmsFunctions_send_message") == "outward"
    assert functions.risk("findCallRecords", "PhoneFunctions_findCallRecords") == "read"  # « call » y est un nom
    assert functions.risk("frobnicate") == "risky" and functions.risk("invokeRememberThis") == "risky"
    assert functions.risk("acceptCall", "CallAppFunctions_acceptCall") == "outward"


def test_errors_are_masked_like_answers(phone):
    def failing(*argv, timeout=30.0):
        phone.runs.append(argv)
        return 1, "", "Error: access_token=abcdefghijklmnopqrstuvwxyz0123456789; code de connexion 482913"
    phone.run = failing  # type: ignore[method-assign]
    with pytest.raises(PhoneError) as caught:
        server._functions("run", "clock", "findAlarms", {"findAlarmsParams": {}}, False, None)
    assert "abcdefghijklmnop" not in str(caught.value) and "482913" not in str(caught.value)
    assert functions.clean("Votre code :\n482913\nsoit 1889 en tout") == "Votre code :\n[masked code]\nsoit 1889 en tout"


def test_purchase_and_wipe_verbs_and_banking_apps(phone):
    assert functions.risk("buyItem") == "outward" and functions.risk("purchasePlan") == "outward"
    assert functions.risk("wipeAllData") == "risky" and functions.risk("formatStorage") == "risky"
    with pytest.raises(PhoneError, match="banking"):
        functions.run(phone, "com.revolut.revolut", "getBalance", None, False, None)  # type: ignore[arg-type]


def test_code_after_a_blank_line_is_masked_in_answers_and_errors(phone):
    """Relecture de Codex, tour 2 : « code :⏎⏎482913 » passait dans clean()."""
    assert "482913" not in functions.clean("Votre code de connexion :\n\n482913")

    def failing(*argv, timeout=30.0):
        return 1, "", "Error: Votre code de connexion :\n\n482913"
    phone.run = failing  # type: ignore[method-assign]
    with pytest.raises(PhoneError) as caught:
        server._functions("run", "clock", "findAlarms", {"findAlarmsParams": {}}, False, None)
    assert "482913" not in str(caught.value)
