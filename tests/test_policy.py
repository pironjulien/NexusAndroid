"""Politique commune des garde-fous (accord Claude–Antigravity du 18/10/2026) et corrections de l'audit du 18/10.
adb simulé : aucun vrai téléphone."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_guards import FakePhone, accorded, act, field, node, ui

from nexus_android import policy, screen, server
from nexus_android.screen import Element, Screen
from nexus_android.server import Step

SHARED = Path(__file__).resolve().parents[2] / "NexusWindows" / "commun" / "garde-fous.json"

# Un exemple réel de libellé par motif de la politique commune : tout nouveau motif doit recevoir le sien.
EXAMPLES = {
    "pay(er|ez)?": "Payez maintenant",
    "paiement": "Confirmer le paiement",
    "payment": "Make payment",
    "achet(er|ez)": "Achetez",
    "buy": "Buy now",
    "purchase": "Purchase",
    "checkout": "Checkout",
    "command(er|ez)": "Commandez",
    "(valider|passer|confirmer|finaliser) (la |ma |votre )?commande": "Finaliser ma commande",
    "place (the |your )?order": "Place your order",
    "virement": "Faire un virement",
    "vir(er|ez)": "Virer 50 €",
    "transfer(er|ez)? (de l |l )?argent": "Transférer de l’argent",
    "transfer money": "Transfer money",
    "envoy(er|ez)": "Envoyez",
    "envoie": "Envoie",
    "appel(er|ez)": "Appeler Alice",
    "appel (video|vocal|audio)": "Appel vidéo",
    "call": "Call",
    "send": "Send",
    "publi(er|ez)": "Publiez",
    "publish": "Publish",
    "post(er|ez)": "Poster",
    "supprim(er|ez) definitivement": "Supprimer définitivement",
    "delete (permanently|forever)": "Delete forever",
    "permanently delete": "Permanently delete",
    "vider (la )?corbeille": "Vider la corbeille",
    "empty (the )?(trash|recycle bin)": "Empty trash",
    "desinstall(er|ez)": "Désinstaller",
    "uninstall": "Uninstall",
    "^format(er|ez)$": "Formater",
    "format(er|ez) (le |ce |la |cette |un |une )?(disque|lecteur|volume|partition|cle|carte)": "Formater la carte SD",
    "format (the |this )?(disk|drive|partition|volume)": "Format the drive",
    "format(er|ez) (le |la |l |ce |cette |votre )?(telephone|appareil|tablette)": "Formater le téléphone",
    "format (the |this |your )?(phone|device|tablet)": "Format phone",
    "reinitialis(er|ez)(?! (les |la |le |l |ce |cette |vos |mes )?(filtres?|zoom|affichage|vue|recherche|tri|mise en page"
    "|formulaire|champs?|zone|texte|selection|couleurs?)\\b)": "Réinitialiser les paramètres réseau",
    "reinitialisation (des donnees )?d usine": "Réinitialisation des données d'usine",
    "factory reset": "Factory reset",
    "reset (this pc|(the )?app|to factory( settings)?|all settings)": "Reset all settings",
    "reset (this |the |your )?(pc|phone|device|tablet)": "Reset phone",
    "resili(er|ez)": "Résiliez",
    "(cancel|terminate) (my |your |the )?(subscription|contract|plan|membership)": "Cancel subscription",
    "(supprim(er|ez)|ferm(er|ez)) (mon |votre |le )compte": "Supprimer mon compte",
    "(delete|close) (my |your |the )?account": "Delete account",
    "(effac(er|ez)|supprim(er|ez)) (toutes )?(les |mes |vos )?donnees": "Effacer toutes les données",
    "(erase|wipe|clear) (all )?(data|everything|device)": "Erase all data",
    "(rendre|passer) (le |ce )?(depot |repository |repo )?(en )?public": "Rendre le dépôt public",
    "(make|change to|set to) (this |the )?(repository |repo )?public": "Make this repository public",
    "^(post|reply|repondre|repost)$": "Répondre",
    "(re)?tweet(er|ez)?": "Retweet",
    "s abonn(er|ez)": "S’abonner",
    "subscribe": "Subscribe",
    "(start (my |your |a |the )?)?free trial": "Start free trial",
    "start (my |your |the )?trial": "Start my trial",
    "essai gratuit": "Essai gratuit",
    "commenc(er|ez) (l |mon |votre |un )?essai": "Commencer l'essai",
    "annul(er|ez) (l |mon |votre )?abonnement": "Annuler l'abonnement",
    "^book( now| it)?$": "Book",
    "book now": "Book now for 2",
    "reserv(er|ez)": "Réservez",
    "donate": "Donate",
    "faire un don": "Faire un don",
    "^answer$": "Answer",
    "decroch(er|ez)": "Décrocher",
    "accept(er|ez)? (l |the )?(appel|call)": "Accepter l’appel",
    "^dial$": "Dial",
    "transfer(er|ez)": "Transférer",
    "\\d+( \\d{1,2})? (eur|euros?|usd|gbp|jpy|chf)": "4,99 €",
    "(eur|usd|gbp|jpy|chf) \\d+( \\d{1,2})?": "$12",
}


def el(text: str = "", desc: str = "", rid: str = "", package: str = "com.app") -> Element:
    return Element(1, "Button", text, desc, rid, package, (0, 0, 1, 1), True)


@pytest.fixture
def phone(monkeypatch):
    fake = FakePhone()
    scr = Screen(fake)  # type: ignore[arg-type]
    scr.size = (1080, 2000)
    monkeypatch.setattr(server, "ADB", fake)
    monkeypatch.setattr(server, "SCREEN", scr)
    monkeypatch.setattr(server, "MESSAGES", object())
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    monkeypatch.setattr(screen.time, "sleep", lambda s: None)
    return fake


# ---------------------------------------------------------------- 1. politique commune, copie identique


def test_embedded_policy_is_the_shared_one():
    if not SHARED.exists():
        pytest.skip("hors de NexusHarness (version publique) : la copie embarquée fait foi")
    assert policy.POLICY_FILE.read_text(encoding="utf-8") == SHARED.read_text(encoding="utf-8"), (
        "src/nexus_android/garde-fous.json diffère de NexusHarness/NexusWindows/commun/garde-fous.json : recopier le fichier commun")


def test_every_motif_has_an_example():
    assert set(EXAMPLES) == set(json.loads(policy.POLICY_FILE.read_text(encoding="utf-8"))["confirmation"]["motifs"])


@pytest.mark.parametrize("motif", list(EXAMPLES))
def test_motif_needs_confirmation(motif):
    label = EXAMPLES[motif]
    assert re.search(r"\b(" + motif + r")\b", policy.plain_label(label)), (motif, label)  # son propre motif
    assert policy.confirm_word(label), label
    assert policy.irreversible(el(label)) == label
    assert policy.irreversible(el(desc=label)) == label  # icône décrite


@pytest.mark.parametrize("label", ["Envoyez", "Payez", "Paiement", "Achetez", "Commandez", "Publish", "Purchase",
                                   "Checkout", "Virer", "Transfer money", "ENVOYER", "Oui, envoyer", "Appelez",
                                   "Appel vocal", "Video call", "Erase everything",
                                   "Reset phone", "Reset device", "Reset tablet", "Reset PC", "Factory reset",
                                   "Formater le téléphone", "Formater l’appareil", "Formater la tablette",
                                   "Format phone", "Format the device"])
def test_audit_conjugations_caught(label):
    assert policy.confirm_word(label), label


@pytest.mark.parametrize("label", ["Supprimer", "Tout supprimer", "Accepter", "J’accepte", "Partager", "Share",
                                   "Journal d'appels", "Calls", "Callback", "Valider", "OK", "Autoriser", "Commandes passées",
                                   "Boîte d'envoi", "Éléments envoyés", "Format", "Réinitialiser les filtres",
                                   "Paypal", "Poste", "Publication", "Reset filters", "Effacer", "Tout sélectionner"])
def test_free_labels(label):
    assert policy.confirm_word(label) == "", label


@pytest.mark.parametrize(("label", "plain"), [("4,99 €", "4 99 eur"), ("€4.99", "eur 4 99"), ("$12", "usd 12"),
                                              ("£3.99", "gbp 3 99"), ("¥500", "jpy 500"), ("Payer 12 €", "payer 12 eur")])
def test_currency_symbols_spelled_out_like_the_shared_plain(label, plain):
    assert policy.plain_label(label) == plain


def test_generic_verb_on_payment_or_format_screen():
    assert policy.confirm_word("Valider", "Paiement par carte · 12,00 €")
    assert policy.confirm_word("OK", "Formatage de la carte SD")
    assert policy.confirm_word("Valider", "Paramètres du compte") == ""


def test_wipe_all_only_on_a_reset_screen(phone):
    """« Tout effacer » : libre ailleurs (calculatrice, notifications), confirmé sur l'écran de réinitialisation."""
    settings = "com.android.settings"
    phone.package = settings
    phone.uis = [ui(node("Réinitialisation des données d'usine", (0, 0, 1080, 200), clickable=False, package=settings),
                    node("Tout effacer", (0, 1700, 1080, 1900), package=settings))]
    assert "confirm=true" in act(Step(do="tap", target="Tout effacer"))
    phone.uis = [ui(node("Calculatrice", (0, 0, 1080, 200), clickable=False, package=settings),
                    node("Tout effacer", (0, 1700, 1080, 1900), package=settings))]
    assert act(Step(do="tap", target="Tout effacer")).startswith("~")
    for label in ("Tout effacer", "Effacer tout", "Tout supprimer"):
        assert policy.confirm_word(label, "Effacer toutes les données") and not policy.confirm_word(label), label


def test_generic_verb_judged_with_screen_text(phone):
    phone.uis = [ui(node("Paiement sécurisé", (0, 0, 1080, 200), clickable=False), node("Valider", (0, 1700, 1080, 1900)))]
    assert "confirm=true" in act(Step(do="tap", target="Valider"))
    phone.uis = [ui(node("Préférences", (0, 0, 1080, 200), clickable=False), node("Valider", (0, 1700, 1080, 1900)))]
    assert act(Step(do="tap", target="Valider")).startswith("~")


def test_free_actions_are_not_refused(phone):
    phone.uis = [ui(node("Supprimer", (0, 0, 500, 200)), node("Partager", (0, 300, 500, 500)),
                    node("Archiver", (0, 600, 500, 800)), node("Accepter", (0, 900, 500, 1100)))]
    for label in ("Supprimer", "Partager", "Archiver", "Accepter"):
        assert act(Step(do="tap", target=label)).startswith("~"), label


# ---------------------------------------------------------------- 2. icône d'envoi sans nom


def test_unnamed_icon_in_messaging_needs_confirmation(phone):
    phone.package = "com.whatsapp"
    icon = node("", (950, 1700, 1060, 1800), rid="com.whatsapp:id/btn_x", package="com.whatsapp")
    row = node("", (0, 300, 1080, 500), package="com.whatsapp")
    name = node("Alice", (150, 320, 600, 380), clickable=False, package="com.whatsapp")
    phone.uis = [ui(icon, row, name)]
    report = act(Step(do="tap", target="1000,1750"))
    assert "unnamed icon" in report and "confirm=true" in report and phone.inputs == []
    assert act(Step(do="tap", target="300,450")).startswith("~")  # ligne de conversation : nommée par son texte
    request = act(Step(do="tap", target="1000,1750", confirm=True))
    assert "btn_x" in request and len(phone.inputs) == 1
    assert act(Step(do="tap", target="1000,1750", confirm=True, accord=accorded(request))).startswith("~")
    assert len(phone.inputs) == 2


def test_unnamed_icon_elsewhere_is_free(phone):
    phone.uis = [ui(node("", (950, 1700, 1060, 1800), package="com.android.settings"))]
    phone.package = "com.android.settings"
    assert act(Step(do="tap", target="1000,1750")).startswith("~")


def test_unnamed_call_button_in_dialer(phone):
    phone.package = "com.samsung.android.dialer"
    phone.uis = [ui(node("", (440, 1700, 640, 1900), package="com.samsung.android.dialer"),
                    node("06 11 22 33 44", (0, 300, 1080, 450), clickable=False, package="com.samsung.android.dialer"))]
    report = act(Step(do="tap", target="540,1800"))
    assert "unnamed icon" in report and phone.inputs == []


def test_communication_apps():
    for package in ("com.whatsapp", "com.google.android.gm", "com.example.chatapp", "com.google.android.dialer",
                    "com.samsung.android.dialer", "com.android.server.telecom"):
        assert policy.communication_app(package), package
    assert not policy.communication_app("com.android.settings")


# ---------------------------------------------------------------- 3. banques : lecture permise


def test_protected_app_reads_freely_navigates_on_order_never_moves_money(phone):
    phone.package = "com.revolut.revolut"
    bank = "com.revolut.revolut"
    phone.uis = [ui(node("Solde", (0, 0, 1080, 400), package=bank, clickable=False),
                    node("Comptes", (0, 400, 540, 500), package=bank), node("Virer", (540, 400, 1080, 500), package=bank),
                    node("", (900, 1700, 1060, 1860), package=bank),
                    node("Liste", (0, 500, 1080, 1600), package=bank, clickable=False))]
    assert act(Step(do="scroll", direction="down")).startswith("~")
    assert act(Step(do="wait", text="Solde")).startswith("✓")
    assert act(Step(do="key", keys="back")).startswith("~")
    before = len(phone.inputs)
    for step in (Step(do="tap", target="Comptes"), Step(do="swipe", target="500,1500", to="500,800"),
                 Step(do="type", text="1")):
        report = act(step)
        assert report.startswith("✗") and "read-only" in report and "confirm=true" in report, (step, report)
    assert len(phone.inputs) == before
    # Ordre explicite de l'utilisateur : naviguer (onglet), mais jamais un bouton d'argent ni une icône sans nom.
    assert act(Step(do="tap", target="Comptes", confirm=True)).startswith("~")
    for step in (Step(do="tap", target="Virer", confirm=True), Step(do="tap", target="980,1780", confirm=True),
                 Step(do="tap", target="500,1950", confirm=True)):
        report = act(step)
        assert report.startswith("✗") and "Revolut" in report, (step, report)
    assert len(phone.inputs) == before + 1


def test_foreground_change_inside_a_batch_is_seen(phone):
    """Un seul état lu par lot : l'appli protégée ouverte par l'étape 1 est vue par l'arbre relu avant l'étape 2."""
    phone.uis = [ui(node("Ouvrir", (0, 0, 500, 200))),
                 ui(node("Payer", (0, 0, 500, 200), package="com.revolut.revolut"))]
    original = phone.state_text

    def state_after_tap() -> str:
        if phone.inputs:
            phone.package = "com.revolut.revolut"
        return original()
    phone.state_text = state_after_tap  # type: ignore[method-assign]
    report = act(Step(do="tap", target="Ouvrir"), Step(do="tap", target="100,100", confirm=True))
    assert report.startswith("~ 1.") and "✗ 2." in report and "Revolut" in report
    assert len(phone.inputs) == 1


# ---------------------------------------------------------------- 4. image sans arbre


def test_image_withheld_when_tree_unreadable(phone):
    phone.uis = [""]
    out = server._view("both")
    assert len(out) == 1 and "Image not sent" in out[0]
    out = server._view("both", confirm=True)
    assert len(out) == 2 and "without code masking" in out[0]
    phone.uis = [ui(node("Bonjour"))]
    assert len(server._view("both")) == 2


# ---------------------------------------------------------------- 5. lien vers une appli protégée


def test_open_url_to_protected_app_refused(phone):
    original = phone.run

    def run(*argv: str, timeout: float = 30.0):
        if argv[:3] == ("cmd", "package", "resolve-activity"):
            url = argv[-1]
            if url.startswith("lydia"):
                return 0, "priority=0 preferredOrder=0\ncom.lydia.app/.MainActivity", ""
            if url.startswith("https://bank"):
                return 0, "android/com.android.internal.app.ResolverActivity", ""
            return 0, "com.android.chrome/com.google.android.apps.chrome.Main", ""
        if argv[:3] == ("cmd", "package", "query-activities"):  # la banque ne prend que ses propres liens
            bank = "\nfr.creditagricole.androidapp/.Link" if argv[-1].startswith("https://bank") else ""
            return 0, "com.android.chrome/.Main" + bank, ""
        return original(*argv, timeout=timeout)
    phone.run = run  # type: ignore[method-assign]
    for url in ("lydia://pay?amount=50", "https://bank.example/virement"):
        report = act(Step(do="open_url", text=url, confirm=True))
        assert report.startswith("✗") and "read-only" in report, url
    assert not [c for c in phone.calls if c[0] == "am"]
    assert act(Step(do="open_url", text="https://example.org")).startswith("~")


# ---------------------------------------------------------------- 6. un seul état par lot


def test_one_state_read_per_batch(phone):
    phone.uis = [ui(node("Ouvrir", (0, 0, 500, 200)), field())]
    phone.calls.clear()
    act(Step(do="wait", text="Ouvrir"), Step(do="key", keys="tab"), Step(do="tap", target="Ouvrir"),
        Step(do="wait", text="Ouvrir"), Step(do="key", keys="tab"))
    states = [c for c in phone.calls if c[0] == "sh" and c[2] == screen.STATE_SH]
    assert len(states) == 1
    assert not [c for c in phone.calls if c[0] == "dumpsys" and c[1] == "window"]


def test_state_mark_copied_on_screen_is_ignored(phone):
    """Un texte de l'écran qui recopierait la marque d'état ne peut pas fausser l'état lu."""
    fake = f"{screen.STATE_MARK} mWakefulness=Awake isKeyguardShowing=false mCurrentFocus=Window{{a u0 com.evil/x}}"
    phone.uis = [ui(node(fake, (0, 0, 500, 200)))]
    phone.lock = "isKeyguardShowing=true"
    server._init()[1].dump()
    state = server._init()[1].last_state
    assert state and state.locked and state.package == "com.app"


# ---------------------------------------------------------------- 7. réponses structurées


def test_structured_settings_and_info(phone, monkeypatch):
    monkeypatch.setattr(server.extras, "setting", lambda adb, name, value, link: (f"{name} : on (relu)", "on"))
    phone.serial = "R5FOLD8"  # type: ignore[attr-defined]
    result = server._setting("wifi", "on")
    assert result.structured_content == {"name": "wifi", "value": "on", "changed": True, "text": "wifi : on (relu)"}
    monkeypatch.setattr(server.extras, "location", lambda adb: ("Position : 1, 2", {
        "latitude": 1.0, "longitude": 2.0, "accuracy_m": 5, "age_s": 3, "maps_url": "https://maps.google.com/?q=1,2"}))
    out = server._info("location", None, 20, 7)
    assert out.structured_content and out.structured_content["location"]["accuracy_m"] == 5
    assert out.content[0].text == "Position : 1, 2"  # type: ignore[union-attr]
    server.InfoResult.model_validate(out.structured_content)


@pytest.mark.parametrize(("keyguard", "trust", "locked", "code"), [
    ("false", "", False, False),
    ("true", "User \"Owner\" (id=0) (current): trustState=TRUSTED, deviceLocked=0", True, False),
    ("true", "User \"Owner\" (id=0) (current): trustState=UNTRUSTED, deviceLocked=1", True, True),
    ("true", "", True, True),  # confiance illisible : code exigé
])
def test_status_is_structured_and_read_in_one_round_trip(monkeypatch, keyguard, trust, locked, code):
    calls: list[tuple[str, ...]] = []
    mark = server.STATUS_MARK

    class Adb:
        rebooted = False
        serial = "192.0.2.7:5555"
        boots: list[str | None] = []

        def connect(self):
            raise AssertionError("link already known: no new search")

        def note_boot(self, boot=None):
            self.boots.append(boot)

        def shell(self, *argv, check=True, timeout=30.0):
            calls.append(argv)
            return (f"SM-F971B\n{mark}\n17\n{mark}\n8d190673-68dc-4533-a5ac-f55a80c09663\n{mark}\n"
                    "    mCommittedState=Optional[DeviceState{identifier=0, name='CLOSED', app_accessible=true}]\n"
                    f"{mark}\n{trust}\n{mark}\nmWakefulness=Awake\nisKeyguardShowing={keyguard}\n"
                    "  mCurrentFocus=Window{abc u0 com.app/x.Main}\n")

    link, scr = Adb(), screen.Screen(None)  # type: ignore[arg-type]
    monkeypatch.setattr(server, "_init", lambda: (link, scr, None))
    monkeypatch.setattr(server, "_awake_note", lambda adb: "")
    monkeypatch.setattr(server.awake, "ENABLED", False)
    status = server._status()
    assert status.posture == "folded" and status.transport == "wifi" and status.screen_on
    assert (status.locked, status.code_required) == (locked, code)
    assert ("code required" in status.text) == code and ("without code" in status.text) == (locked and not code)
    assert status.foreground == "com.app" and "SM-F971B" in status.text and status.android == "17"
    assert len(calls) == 1 and link.boots == ["8d190673-68dc-4533-a5ac-f55a80c09663"]
    assert scr.last_state and scr.last_state.package == "com.app"
    server.PhoneStatus.model_validate(status.model_dump())
