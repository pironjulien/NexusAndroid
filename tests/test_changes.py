"""act(then="changes") : seulement ce qui a changé depuis la dernière liste, numéros [N] cohérents pour l'act suivant,
liste complète quand la comparaison n'a pas de sens, codes masqués jamais révélés. adb simulé (test_guards)."""

from __future__ import annotations

import re

from test_guards import node, phone, ui  # noqa: F401  (fixture phone)

from nexus_android import screen as screen_mod
from nexus_android import server
from nexus_android.messages import MASK
from nexus_android.server import Step

A = node("Inbox", (0, 0, 500, 100), rid="com.app:id/inbox")
B = node("3 unread", (0, 200, 500, 300), rid="com.app:id/counter")
C = node("Archive", (0, 400, 500, 500), rid="com.app:id/archive")
D = node("Next", (0, 600, 500, 700), rid="com.app:id/next")
E = node("Settings", (0, 1200, 500, 1300), rid="com.app:id/settings")
F = node("Help", (0, 1400, 500, 1500), rid="com.app:id/help")


def view(fake, *nodes: str, show: str = "changes") -> str:
    fake.uis = [ui(*nodes)]
    return server._view(show)[0]  # type: ignore[arg-type]


def act(fake, *steps: Step, then: str = "changes") -> str:
    return server._act(list(steps), then)[-1]  # type: ignore[arg-type]


def test_diff_reports_added_removed_changed_and_counts_the_rest(phone):  # noqa: F811
    view(phone, A, B, C, E, F, show="elements")
    out = view(phone, A, node("4 unread", (0, 200, 500, 300), rid="com.app:id/counter"), E, F, D)
    assert "1 added, 1 removed, 1 changed, 3 unchanged" in out
    assert re.search(r'^\+ \[6\] Button "Next"', out, re.M)  # numéro neuf, après le plus grand
    assert re.search(r'^~ \[2\] Button "4 unread".*\(text\)$', out, re.M)  # même endroit, texte changé
    assert re.search(r'^- \[3\] Button "Archive"$', out, re.M)
    assert "Inbox" not in out  # inchangé : compté, pas répété


def test_moved_and_state_changes(phone):  # noqa: F811
    view(phone, A, B, show="elements")
    moved = node("Inbox", (0, 50, 500, 150), rid="com.app:id/inbox")  # même contenu, 50 px plus bas
    out = view(phone, moved, B)
    assert re.search(r'^~ \[1\] Button "Inbox".*\(moved\)$', out, re.M)
    nudged = node("Inbox", (0, 55, 500, 155), rid="com.app:id/inbox")  # sous la tolérance
    assert "No change since the last list (2 elements, same [N])." in view(phone, nudged, B)


def test_unchanged_screen_gives_an_empty_diff(phone):  # noqa: F811
    view(phone, A, B, C, show="elements")
    out = view(phone, A, B, C)
    assert "No change since the last list (3 elements, same [N])." in out
    assert "[1]" not in out


def test_full_list_without_previous_list(phone):  # noqa: F811
    out = view(phone, A, B)
    assert "Full list (no previous list to compare with):" in out
    assert '[1] Button "Inbox"' in out and '[2] Button "3 unread"' in out


def test_full_list_when_the_foreground_app_changes(phone):  # noqa: F811
    view(phone, A, B, C, show="elements")
    phone.package = "com.other"
    out = view(phone, A, B, C)
    assert "Full list (foreground app changed: com.app → com.other):" in out
    assert out.count("\n[") == 3  # liste complète, numérotée de 1 à n


def test_full_list_when_most_of_the_screen_changes(phone):  # noqa: F811
    view(phone, A, B, C, D, show="elements")
    others = [node(f"Item {i}", (0, 1000 + 100 * i, 500, 1080 + 100 * i)) for i in range(4)]
    out = view(phone, A, *others)
    assert re.search(r"Full list \(screen changed almost entirely: \d+ % of elements differ\):", out)
    assert re.search(r'^\[1\] Button "Inbox"', out, re.M) and re.search(r'^\[5\] Button "Item 3"', out, re.M)


def test_targets_resolve_on_the_current_screen_after_a_diff(phone):  # noqa: F811
    view(phone, A, B, C, show="elements")
    phone.uis = [ui(A, B, D)]  # C remplacé par D : D prend le numéro 4
    out = act(phone, Step(do="wait", text="Next"))
    assert re.search(r'^\+ \[4\] Button "Next"', out, re.M) and re.search(r'^- \[3\]', out, re.M)
    act(phone, Step(do="tap", target="[4]"))
    assert phone.inputs[-1] == ("input", "tap", "250", "650")  # centre de D
    act(phone, Step(do="tap", target="[1]"))  # inchangé : même numéro
    assert phone.inputs[-1] == ("input", "tap", "250", "50")
    taps = len(phone.inputs)
    refused = act(phone, Step(do="tap", target="[3]"))  # retiré : n'existe plus
    assert "✗" in refused and "[3] does not exist" in refused and len(phone.inputs) == taps


def test_numbers_stay_coherent_over_several_diffs(phone):  # noqa: F811
    view(phone, A, B, show="elements")
    assert "+ [3]" in view(phone, A, B, C)
    out = view(phone, A, C, D)  # B retiré, D ajouté : numéro neuf, jamais celui de B
    assert "+ [4]" in out and "- [2]" in out
    assert [e.index for e in server._init()[1].elements] == [1, 3, 4]


def test_masked_codes_are_never_revealed_in_a_diff(phone):  # noqa: F811
    first = node("Your verification code is 482913", (0, 800, 900, 900), rid="com.app:id/body")
    full = view(phone, A, first, show="elements")
    assert MASK in full and "482913" not in full
    # Le code change sous son masque : rien ne bouge dans le rapport, ni l'ancien ni le nouveau code.
    second = node("Your verification code is 731205", (0, 800, 900, 900), rid="com.app:id/body")
    out = view(phone, A, second)
    assert "No change since the last list" in out and "731205" not in out and "482913" not in out
    # Un nouveau code qui apparaît : ajouté, masqué.
    extra = node("Login code: 905617", (0, 1000, 900, 1100), rid="com.app:id/body2")
    out = view(phone, A, second, extra)
    assert re.search(r"^\+ \[3\]", out, re.M) and MASK in out and "905617" not in out


def test_diff_elements_keeps_numbers_and_ratio():
    old = screen_mod.parse_ui(ui(A, B, C))
    new = screen_mod.parse_ui(ui(A, D, C))
    diff = screen_mod.diff_elements(old, new)
    assert [e.index for e in diff.elements] == [1, 4, 3]
    assert [e.text for e in diff.added] == ["Next"] and [e.text for e in diff.removed] == ["3 unread"]
    assert diff.unchanged == 2 and abs(diff.ratio - 1 / 3) < 1e-9


def test_act_default_still_returns_the_full_list(phone):  # noqa: F811
    view(phone, A, B, show="elements")
    phone.uis = [ui(A, B)]
    out = act(phone, Step(do="wait", text="Inbox"), then="elements")
    assert '[1] Button "Inbox"' in out and "Changes since" not in out and "No change" not in out
