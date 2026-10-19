"""Relecture du lot 4a : liens de paiement en https, intentions d'arrêt et d'effacement, application protégée parmi les
candidates d'un lien, sélecteurs d'Android, presse-papiers qui ressemble à un secret (dans les deux sens)."""

from __future__ import annotations

import pytest
from test_guards import FakePhone, phone  # noqa: F401  (fixture phone)

from nexus_android import server
from nexus_android.adb import PhoneError
from nexus_android.messages import secret_like
from nexus_android.server import _chooser, intent_risk, pay_link

# ---------------------------------------------------------------- 1. liens de paiement


@pytest.mark.parametrize("url", [
    "https://revolut.me/someone", "https://REVOLUT.ME/someone", "http://pay.revolut.me/x", "https://paypal.me/bob/10",
    "https://www.paypal.com/paypalme/bob", "https://www.paypal.com/checkoutnow?token=1", "https://cash.app/$bob",
    "https://lydia-app.com/collect/x", "https://pay.lydia.me/l?t=1", "https://buy.stripe.com/test_123",
    "https://checkout.stripe.com/c/pay/cs_1", "https://venmo.com/u/bob", "https://account.venmo.com/pay",
    "https://wise.com/pay/r/abc", "https://pay.sumup.com/b2c/Q1", "https://bunq.me/bob/5", "https://sumup.link/x",
])
def test_payment_links_need_confirm(url):
    assert pay_link(url), url
    assert intent_risk("android.intent.action.VIEW", url, {}), url


@pytest.mark.parametrize("url", [
    "https://www.paypal.com/fr/home", "https://wise.com/fr/", "https://wise.com/payments-info", "https://stripe.com/",
    "https://example.com/revolut.me", "https://notrevolut.me/x", "https://revolut.com/fr-FR/", "geo:0,0?q=paris",
    "https://cash.apple.com/", "mailto:paypal.me@example.com",
])
def test_ordinary_links_stay_free(url):
    assert pay_link(url) is None, url
    assert intent_risk("android.intent.action.VIEW", url, {}) is None, url


def test_payment_link_by_open_url_is_refused(phone: FakePhone):  # noqa: F811
    out = server._act([server.Step(do="open_url", text="https://revolut.me/someone")], "none")[0]
    assert out.startswith("✗ 1.") and "confirm=true" in out and not [c for c in phone.calls if c[0] == "am"]


# ---------------------------------------------------------------- 2. arrêt, redémarrage, effacement


@pytest.mark.parametrize("action", [
    "android.intent.action.ACTION_REQUEST_SHUTDOWN", "android.intent.action.REBOOT", "android.intent.action.MASTER_CLEAR",
    "android.intent.action.FACTORY_RESET", "com.android.internal.intent.action.REQUEST_SHUTDOWN",
    "com.samsung.android.intent.action.WIPE_DATA", "com.vendor.action.REBOOT_NOW",
])
def test_power_and_reset_actions_need_confirm(action):
    assert intent_risk(action, None, {}), action


@pytest.mark.parametrize("action", ["android.settings.WIFI_SETTINGS", "android.intent.action.VIEW",
                                    "android.intent.action.MAIN", "android.settings.DATE_SETTINGS"])
def test_ordinary_actions_stay_free(action):
    assert intent_risk(action, None, {}) is None


# ---------------------------------------------------------------- 3 et 4. candidates d'un lien, sélecteurs


def _resolving(phone: FakePhone, resolved: str, candidates: str) -> None:  # noqa: F811
    original = phone.run

    def run(*argv, timeout=30.0):
        if argv[:3] == ("cmd", "package", "resolve-activity"):
            phone.calls.append(argv)
            return 0, resolved, ""
        if argv[:3] == ("cmd", "package", "query-activities"):
            phone.calls.append(argv)
            return 0, candidates, ""
        return original(*argv, timeout=timeout)

    phone.run = run  # type: ignore[method-assign]


def test_protected_app_among_link_handlers_refused_even_when_one_is_chosen(phone: FakePhone):  # noqa: F811
    # Android désigne le navigateur, mais l'application bancaire pourrait aussi prendre le lien : refusé.
    _resolving(phone, "priority=0 isDefault=true\ncom.android.chrome/com.google.android.apps.chrome.IntentDispatcher\n",
               "2 activities found:\n  Activity #0:\n    com.boursorama.android.clients/.Deeplink\n  Activity #1:\n"
               "    com.android.chrome/com.google.android.apps.chrome.IntentDispatcher\n")
    with pytest.raises(PhoneError, match="BoursoBank.*never opened by an intent"):
        server._launch(action="android.intent.action.VIEW", data="https://clients.boursobank.com/compte", confirm=True)
    assert ("cmd", "package", "query-activities") in [c[:3] for c in phone.calls]
    assert not [c for c in phone.calls if c[0] == "am"]


def test_settings_screen_needs_no_extra_read(phone: FakePhone):  # noqa: F811
    _resolving(phone, "com.android.settings/.Settings$WifiSettingsActivity\n", "")
    server._launch(action="android.settings.WIFI_SETTINGS")
    assert ("cmd", "package", "query-activities") not in [c[:3] for c in phone.calls]


@pytest.mark.parametrize("component,chooser", [
    ("android/com.android.internal.app.ResolverActivity", True),  # relevé sur le Fold (One UI 8, Android 17)
    ("com.android.intentresolver/.ChooserActivity", True),
    ("com.samsung.android.app.sharelive/.SemChooserActivity", True),
    ("com.oem.launcher/com.oem.app.IntentResolverProxy", True),
    ("com.android.chrome/com.google.android.apps.chrome.IntentDispatcher", False),
    ("com.whatsapp/.Main", False),
])
def test_choosers_are_not_final_targets(component, chooser):
    assert _chooser(component) is chooser


def test_chooser_class_falls_back_to_the_candidates(phone: FakePhone):  # noqa: F811
    _resolving(phone, "com.samsung.android.app.sharelive/.SemChooserActivity\n",
               "  com.android.chrome/.Main\n  com.revolut.revolut/.Deeplink\n")
    with pytest.raises(PhoneError, match="Revolut"):
        server._launch(data="https://example.com/x")


# ---------------------------------------------------------------- 5. presse-papiers : secret ou non


@pytest.mark.parametrize("text", [
    "482913", "A-482913", "P@ssw0rd", "hunter2!", "Tr0ub4dor&3", "Xk7pQ2mZ", "correct#horse", "s3cr3t+key",
    "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789", "github_pat_11ABCDEFG0123456789_abcdefghijklmnop",
    "sk-proj-AbCdEf0123456789GhIjKl", "sk_live_51HxYzAbCdEf0123456789", "AKIAIOSFODNN7EXAMPLE",  # gitleaks:allow (valeur factice de test)
    "xoxb-1234567890-0987654321-AbCdEfGh", "AIzaSyA1b2C3d4E5f6G7h8I9j0KlMnOpQrStUv", "glpat-AbCdEf0123456789xyz",  # gitleaks:allow (valeur factice de test)
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",  # gitleaks:allow (valeur factice de test)
    "da39a3ee5e6b4b0d3255bfef95601890afd80709", "dGhpcyBpcyBhIHNlY3JldCBrZXkgMTIz",
    "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH PRIVATE KEY-----",
])
def test_secrets_are_hidden(text):
    assert secret_like(text), text


@pytest.mark.parametrize("text", [
    "image01.jpg", "test_2.py", "python-3.12", "SM-F956B", "AA-123-AA", "api-v2.net", "hello world", "0612345678",
    "MyDoc_v2.pdf", "Release-2026.Q4", "Merci!", "aujourd'hui", "@julienpiron", "#Nexus2026", "bonjour",
    "123e4567-e89b-12d3-a456-426614174000", "my-long-file-name-2024-version-final", "aaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "https://example.com/a?b=c", "bob@example.com", "sk-learn", "Wi-Fi", "v1.2.3-beta", "README.md", "12:30",
    "supercalifragilisticexpialidocious",
])
def test_ordinary_words_are_shown(text):
    assert not secret_like(text), text
