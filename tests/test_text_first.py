"""Texte d'abord (NexusHarness/NexusShared/images.md § 2) : par défaut la liste seule ; l'image rejoint la liste quand un
effet n'a pas été relu, quand l'écran n'a aucun élément lisible, ou sur demande. adb simulé (test_guards)."""

from __future__ import annotations

from test_guards import node, phone, ui  # noqa: F401  (fixture phone)

from nexus_android import screen as screen_mod
from nexus_android import server
from nexus_android.server import Image, Step

A = node("Inbox", (0, 0, 500, 100), rid="com.app:id/inbox")
B = node("Archive", (0, 200, 500, 300), rid="com.app:id/archive")


def images(out: list) -> int:
    return sum(isinstance(part, Image) for part in out)


def test_look_by_default_sends_the_list_without_the_image(phone):  # noqa: F811
    phone.uis = [ui(A, B)]
    out = server._view("auto")
    assert images(out) == 0 and '"Inbox"' in out[0] and '"Archive"' in out[0]


def test_asking_for_the_image_still_gets_it(phone):  # noqa: F811
    phone.uis = [ui(A, B)]
    assert images(server._view("both")) == 1
    phone.uis = [ui(A, B)]
    assert images(server._view("auto", region=[0, 0, 200, 200])) == 1  # zoom : c'est une image qu'on demande


def test_a_screen_without_readable_element_joins_the_image(phone):  # noqa: F811
    phone.uis = [ui()]  # canevas, jeu, vidéo : l'arbre n'a rien
    out = server._view("auto")
    assert images(out) == 1


def test_a_screen_of_unnamed_buttons_joins_the_image(phone, monkeypatch):  # noqa: F811
    """Relecture Codex : un arbre non vide mais sans aucun libellé (appli dessinée, OCR absent) ne dit rien."""
    monkeypatch.setattr(server, "_ocr_elements", lambda screen, image: None)
    phone.uis = [ui(node("", (0, 0, 1080, 2000)))]
    out = server._view("auto")
    assert images(out) == 1


def test_the_status_bar_alone_does_not_count_as_readable(phone, monkeypatch):  # noqa: F811
    monkeypatch.setattr(server, "_ocr_elements", lambda screen, image: None)
    clock = node("10:42", (0, 0, 200, 40), package="com.android.systemui", clickable=False)
    phone.uis = [ui(clock, node("", (0, 40, 1080, 2000)))]  # jeu au premier plan (com.app), barre d'état lisible
    assert images(server._view("auto")) == 1


def test_an_unreadable_tree_follows_the_same_guard_as_both(phone):  # noqa: F811
    """Relecture Codex : arbre en erreur, l'image n'est envoyée qu'avec confirm=true (codes non masqués)."""
    phone.uis = [""]  # ERROR: null root node
    refused = server._view("auto")
    assert images(refused) == 0 and "look(confirm=true)" in refused[0]
    phone.uis = [""]
    agreed = server._view("auto", confirm=True)
    assert images(agreed) == 1 and "without code masking (confirm=true)" in agreed[0]


def test_an_unverified_effect_joins_the_image(phone, monkeypatch):  # noqa: F811
    phone.uis = [ui(A, B)]
    monkeypatch.setattr(server, "_step", lambda adb, screen, step: ("~", "sent, not read back"))
    assert images(server._act([Step(do="key", keys="back")], "auto")) == 1


def test_a_verified_effect_keeps_text_only(phone, monkeypatch):  # noqa: F811
    phone.uis = [ui(A, B)]
    monkeypatch.setattr(server, "_step", lambda adb, screen, step: ("✓", "done, read back"))
    out = server._act([Step(do="key", keys="back")], "auto")
    assert images(out) == 0 and '"Inbox"' in out[0]


def test_a_failed_step_joins_the_image(phone, monkeypatch):  # noqa: F811
    phone.uis = [ui(A, B)]

    def refused(adb, screen, step):
        raise server.PhoneError("target not found")

    monkeypatch.setattr(server, "_step", refused)
    assert images(server._act([Step(do="tap", target="Nowhere")], "auto")) == 1


def _tap_read_then_sent(adb, screen, step):
    """Comme un vrai appui : l'écran est relu juste avant le geste (garde-fou), puis le geste part sans relecture."""
    server._fresh(screen)
    return "~", "tap: sent, not verified"


def test_an_unverified_tap_that_changed_the_screen_keeps_text_only(phone, monkeypatch):  # noqa: F811
    """Un appui (~) suivi d'un écran neuf : l'effet se lit dans la liste, pas d'image."""
    monkeypatch.setattr(server, "_step", _tap_read_then_sent)
    phone.uis = [ui(A, B), ui(node("Inbox, 3 messages", (0, 0, 500, 100)))]  # avant l'appui, puis après
    out = server._act([Step(do="tap", target="Inbox")], "auto")
    assert images(out) == 0 and "Inbox, 3 messages" in out[0]


def test_an_unverified_tap_on_an_unchanged_screen_joins_the_image(phone, monkeypatch):  # noqa: F811
    monkeypatch.setattr(server, "_step", _tap_read_then_sent)
    phone.uis = [ui(A, B)]
    assert images(server._act([Step(do="tap", target="Inbox")], "auto")) == 1  # rien n'a bougé : image


def test_an_old_look_does_not_hide_a_tap_without_effect(phone, monkeypatch):  # noqa: F811
    """Relecture Codex : look sur A, l'écran est déjà B (autre agent, utilisateur) ; un appui sans effet sur B."""
    phone.uis = [ui(A)]
    server._view("elements")
    monkeypatch.setattr(server, "_step", _tap_read_then_sent)
    phone.uis = [ui(B)]
    assert images(server._act([Step(do="tap", target="Archive")], "auto")) == 1


def test_a_verified_step_does_not_hide_a_later_tap_without_effect(phone, monkeypatch):  # noqa: F811
    """Relecture Codex : accueil relu (✓) puis appui ignoré (~) : comparé à l'écran d'avant l'appui, pas du lot."""
    steps = iter([("✓", "key home: home screen in the foreground (read back)"), None])

    def step(adb, screen, s):
        return next(steps) or _tap_read_then_sent(adb, screen, s)

    phone.uis = [ui(A)]
    server._view("elements")
    monkeypatch.setattr(server, "_step", step)
    phone.uis = [ui(B)]
    assert images(server._act([Step(do="key", keys="home"), Step(do="tap", target="Archive")], "auto")) == 1


def test_an_unverified_step_that_read_nothing_joins_the_image(phone, monkeypatch):  # noqa: F811
    phone.uis = [ui(A, B)]
    server._view("elements")
    monkeypatch.setattr(server, "_step", lambda adb, screen, step: ("~", "key recent: sent, not verified"))
    assert images(server._act([Step(do="key", keys="recent")], "auto")) == 1


def test_home_is_read_back_from_the_foreground(phone, monkeypatch):  # noqa: F811
    monkeypatch.setattr(server, "_HOME", ["com.launcher"])
    phone.uis = [ui(node("Clock", package="com.launcher"))]
    phone.package = "com.launcher"
    out = server._act([Step(do="key", keys="home")], "auto")
    assert "✓ 1. key home: home screen in the foreground (read back)" in out[0] and images(out) == 0


def test_home_not_reached_stays_unverified_and_joins_the_image(phone, monkeypatch):  # noqa: F811
    monkeypatch.setattr(server, "_HOME", ["com.launcher"])
    phone.uis = [ui(A, B)]
    out = server._act([Step(do="key", keys="home")], "auto")
    assert "~ 1. key home: sent, not verified" in out[0] and images(out) == 1


def test_the_home_package_is_resolved_once(phone, monkeypatch):  # noqa: F811
    monkeypatch.setattr(server, "_HOME", [])
    answers = iter([(0, "priority=0 preferredOrder=0\ncom.sec.android.app.launcher/.activities.LauncherActivity\n", "")])
    run = phone.run
    monkeypatch.setattr(phone, "run", lambda *a, **k: next(answers) if a[:2] == ("cmd", "package") else run(*a, **k))
    assert server._home_package(phone) == "com.sec.android.app.launcher"  # type: ignore[arg-type]
    assert server._home_package(phone) == "com.sec.android.app.launcher"  # type: ignore[arg-type]  # en cache


def _on_back(fake, package: str | None = None, activity: str | None = None) -> None:
    """Téléphone simulé : la touche Retour change (ou non) l'appli et l'activité que rend dumpsys."""
    run = fake.run

    def back(*argv: str, timeout: float = 30.0):
        if argv[:3] == ("input", "keyevent", "KEYCODE_BACK"):
            if package is not None:
                fake.package = package
            if activity is not None:
                fake.lock = f"isKeyguardShowing=false\n  mFocusedApp=ActivityRecord{{{activity} u0 com.app/.Main t5}}"
        return run(*argv, timeout=timeout)

    fake.run = back


def _in_activity(fake, token: str = "111") -> None:
    fake.lock = f"isKeyguardShowing=false\n  mFocusedApp=ActivityRecord{{{token} u0 com.app/.Sub t5}}"


def test_the_focused_activity_is_parsed():
    window = ("  mFocusedApp=null\n  mCurrentFocus=Window{222f01f u0 com.android.settings/com.android.settings.SubSettings}"
              "\n  mFocusedApp=ActivityRecord{57396375 u0 com.android.settings/.SubSettings t511}")
    assert screen_mod.parse_state(window, window).activity == "57396375 com.android.settings/.SubSettings"


def test_back_is_read_back_when_the_activity_changes(phone):  # noqa: F811
    phone.uis = [ui(A, B)]
    _in_activity(phone, "111")
    _on_back(phone, activity="222")
    out = server._act([Step(do="key", keys="back")], "auto")
    assert "✓ 1. key back: page changed (another activity, read back)" in out[0] and images(out) == 0


def test_back_is_read_back_when_the_app_changes(phone):  # noqa: F811
    phone.uis = [ui(A, B)]
    _on_back(phone, package="com.other")
    out = server._act([Step(do="key", keys="back")], "none")
    assert "✓ 1. key back: app changed (read back)" in out[0]


def test_an_unknown_foreground_after_back_is_no_proof(phone):  # noqa: F811
    """Relecture Codex : pendant une transition, dumpsys ne donne plus de fenêtre : ce n'est pas une autre appli."""
    phone.uis = [ui(A, B)]
    _on_back(phone, package="")
    out = server._act([Step(do="key", keys="back")], "none")
    assert "~ 1. key back: sent, not verified" in out[0]


def test_a_counter_ticking_is_not_a_back(phone):  # noqa: F811
    """Relecture Codex : un compteur seul (même majoritaire, même un arbre qui redevient lisible) ne prouve rien."""
    phone.uis = [ui(node("00:01", rid="com.app:id/timer")), ui(node("00:02", rid="com.app:id/timer"))]
    _in_activity(phone)
    out = server._act([Step(do="key", keys="back")], "auto")
    assert "~ 1. key back: sent, same app and activity as before" in out[0] and images(out) == 1


def test_back_without_effect_says_so_and_joins_the_image(phone):  # noqa: F811
    phone.uis = [ui(A, B)]
    _in_activity(phone)
    out = server._act([Step(do="key", keys="back")], "auto")
    assert "~ 1. key back: sent, same app and activity as before" in out[0] and images(out) == 1


def test_back_without_activity_info_stays_unverified(phone):  # noqa: F811
    phone.uis = [ui(A, B)]
    out = server._act([Step(do="key", keys="back")], "auto")
    assert "~ 1. key back: sent, not verified" in out[0] and images(out) == 1


def test_back_in_a_protected_app_never_reads_the_tree(phone):  # noqa: F811
    """Relecture Codex : une appli bancaire repère l'accessibilité ; retour jugé à l'état d'Android (dumpsys)."""
    phone.package = "com.example.mybank"
    phone.uis = [ui(node("Comptes", package="com.example.mybank"))]
    _in_activity(phone)
    phone.calls.clear()
    out = server._act([Step(do="key", keys="back")], "none")
    assert "~ 1. key back: sent, same app and activity as before" in out[0]
    assert not any("uiautomator" in " ".join(c) for c in phone.calls)


def test_leaving_a_protected_app_is_read_back_without_the_tree(phone):  # noqa: F811
    phone.package = "com.example.mybank"
    phone.uis = [ui(node("Comptes", package="com.example.mybank"))]
    _on_back(phone, package="com.other")
    phone.calls.clear()
    out = server._act([Step(do="key", keys="back")], "none")
    assert "✓ 1. key back: app changed (read back)" in out[0]
    assert not any("uiautomator" in " ".join(c) for c in phone.calls)


def test_explicit_elements_never_join_the_image(phone, monkeypatch):  # noqa: F811
    phone.uis = [ui(A, B)]
    monkeypatch.setattr(server, "_step", lambda adb, screen, step: ("~", "sent, not read back"))
    assert images(server._act([Step(do="key", keys="back")], "elements")) == 0  # choix explicite de l'agent
