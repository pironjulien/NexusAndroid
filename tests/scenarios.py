"""Scénarios réels façon AndroidWorld sur le téléphone de l'utilisateur, notés par l'état du téléphone (pas par ce que
répond l'outil) : chaque tâche passe par les outils du serveur, puis l'état est relu par adb ; tout réglage changé est
remis à sa valeur d'avant. Lancé par test.ps1 -Full (téléphone joignable et déverrouillé) ; code 0 si tout réussit.

    uv run python tests/scenarios.py [nom…]"""

from __future__ import annotations

import re
import sys
import time
import traceback
from collections.abc import Callable

from nexus_android import server as s
from nexus_android.server import Step

SCENARIOS: dict[str, Callable[[], str]] = {}


def scenario(fn: Callable[[], str]) -> Callable[[], str]:
    SCENARIOS[fn.__name__] = fn
    return fn


def adb():
    return s._init()[0]


def setting(ns: str, key: str) -> str:
    return adb().shell("settings", "get", ns, key, check=False).strip()


def top_activity() -> str:
    out = adb().shell("sh", "-c", "dumpsys activity activities | grep -m1 topResumedActivity", check=False)
    found = re.search(r"([\w.]+/[\w.$]+)", out)
    return found.group(1) if found else ""


def text(result) -> str:
    return result if isinstance(result, str) else str(result[0])


def home() -> None:
    s._act([Step(do="key", keys="back"), Step(do="key", keys="back"), Step(do="key", keys="home")], "none")


@scenario
def map_opens_the_display_screen() -> str:
    """« Ouvre le réglage du mode sombre » : map trouve l'écran et l'ouvre directement."""
    found = text(s._map("find", "mode sombre", None, None, None, None, False, 3))
    target = re.search(r'target="([^"]+DisplaySettingsActivity)"', found) or re.search(r'target="([^"]+)"', found)
    assert target, f"no direct opening in: {found[:300]}"
    s._map("open", None, target.group(1), None, None, None, False, 3)
    time.sleep(1)
    assert "DisplaySettings" in top_activity(), f"foreground: {top_activity()}"
    return f"opened {top_activity()}"


@scenario
def search_field_holds_the_typed_word() -> str:
    """« Cherche luminosité dans les Paramètres » : le champ de recherche contient exactement le mot."""
    home()
    s._launch(action="android.settings.SETTINGS")
    report = text(s._act([Step(do="tap", target="Rechercher"), Step(do="wait", text="Que recherchez-vous", timeout=5),
                          Step(do="type", text="luminosité")], "none"))
    field = next((e for e in s._fresh(s._init()[1]) or [] if e.editable and e.focused), None)
    assert field is not None and field.text == "luminosité", f"field: {field.text if field else None}; {report}"
    return "field reads « luminosité »"


@scenario
def do_not_disturb_on_then_back() -> str:
    """« Active Ne pas déranger » : zen_mode change, puis la valeur d'avant est remise."""
    before = setting("global", "zen_mode")
    modes = {"0": "off", "1": "priority", "2": "on", "3": "alarms"}
    assert before in modes, f"unexpected zen_mode {before!r}: nothing changed"
    try:
        s._setting("dnd", "on" if before == "0" else "off")
        assert setting("global", "zen_mode") != before, "zen_mode unchanged"
    finally:
        s._setting("dnd", modes[before])  # le mode exact d'avant (priorité, alarmes seulement…)
    assert setting("global", "zen_mode") == before, "zen_mode not restored"
    return f"zen_mode {before} → on → {before}"


@scenario
def brightness_set_and_restored() -> str:
    """« Mets la luminosité à 30 % » : valeur relue dans les réglages, puis mode et valeur d'avant remis."""
    mode, value = setting("system", "screen_brightness_mode"), setting("system", "screen_brightness")
    try:
        s._setting("brightness", "30")
        assert setting("system", "screen_brightness_mode") == "0", "still automatic"
        assert setting("system", "screen_brightness") != value or value == "", "value unchanged"
    finally:
        adb().shell("settings", "put", "system", "screen_brightness", value, check=False)
        adb().shell("settings", "put", "system", "screen_brightness_mode", mode, check=False)
    assert setting("system", "screen_brightness_mode") == mode, "mode not restored"
    if mode != "1":  # automatique : Android recalcule lui-même la valeur, seul le mode compte
        assert setting("system", "screen_brightness") == value, "value not restored"
    return f"brightness mode {mode}, value {value} restored"


@scenario
def diff_sees_a_changed_setting() -> str:
    """« Qu'est-ce qui a changé ? » : un réglage modifié entre deux instantanés apparaît dans diff."""
    s._map("snapshot", None, None, None, None, None, False, 3)
    before = setting("system", "font_scale")
    try:
        adb().shell("settings", "put", "system", "font_scale", "1.15", check=False)
        out = text(s._map("diff", None, None, None, None, None, False, 3))
        assert "system/font_scale" in out, out[:400]
    finally:
        adb().shell("settings", "put", "system", "font_scale", before or "1.0", check=False)
    return "font_scale change seen by diff"


@scenario
def web_page_read_without_scrolling() -> str:
    """« Que dit la page example.com ? » : web lit la page ouverte dans Chrome."""
    s._act([Step(do="open_url", text="https://example.com")], "none")
    time.sleep(3)
    page = text(s._web("read", None, None))
    assert page.startswith("Example Domain"), page[:200]
    return "Example Domain read"


@scenario
def app_stopped() -> str:
    """« Force l'arrêt de Maps » : plus aucun processus de Maps."""
    s._launch(app="com.google.android.apps.maps")
    time.sleep(2)
    s._apps("stop", "com.google.android.apps.maps", None, False)
    time.sleep(1)
    assert not adb().shell("pidof", "com.google.android.apps.maps", check=False).strip(), "Maps still running"
    return "Maps has no process left"


def main(names: list[str]) -> int:
    status = s._status()
    if status.locked:  # écran de verrouillage qu'Android tient déverrouillé (lieu de confiance) : levé sans code
        adb_, screen, _ = s._init()
        state, _lifted = s._lift(adb_, screen, screen.state())
        if state.locked:
            print("Phone locked: unlock it, then run again.")
            return 2
    failed = 0
    for name, fn in SCENARIOS.items():
        if names and name not in names:
            continue
        start = time.monotonic()
        try:
            detail = fn()
            print(f"PASS {name} ({time.monotonic() - start:.1f} s): {detail}")
        except Exception as e:  # noqa: BLE001 - chaque scénario est noté, les suivants continuent
            failed += 1
            print(f"FAIL {name}: {e}")
            traceback.print_exc(limit=2)
    home()
    print(f"{len(SCENARIOS) - failed if not names else 'done'}: {failed} failure(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
