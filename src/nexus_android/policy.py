"""Garde-fous : ce que l'outil refuse de faire seul, quel que soit l'agent ou le texte lu à l'écran."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import unicodedata
from pathlib import Path

from .screen import Element

# Applications où l'outil peut regarder mais jamais agir : banque, paiement, crypto, jeux d'argent, identité, codes.
# Noms de paquets relevés sur Google Play (France) ou le Galaxy Store, pas devinés.
PROTECTED_PACKAGES = {
    "com.fullsix.android.labanquepostale.accountaccess": "La Banque Postale",
    "fr.creditagricole.androidapp": "Ma Banque (Crédit Agricole)",
    "net.bnpparibas.mescomptes": "Mes Comptes BNP Paribas",
    "mobi.societegenerale.mobile.lappli": "L'Appli SG",
    "com.caisseepargne.android.mobilebanking": "Banxo (Caisse d'Epargne)",
    "fr.banquepopulaire.cyberplus": "Banque Populaire",
    "com.cm_prod.bad": "Crédit Mutuel",
    "com.cic_prod.bad": "CIC",
    "fr.lcl.android.customerarea": "Mes Comptes LCL",
    "com.boursorama.android.clients": "BoursoBank",
    "com.fortuneo.android": "Fortuneo",
    "fr.bnpp.digitalbanking": "Hello bank!",
    "eu.qonto.qonto": "Qonto",
    "com.shine.app": "Shine",
    "de.number26.android": "N26",
    "com.revolut.revolut": "Revolut",
    "com.transferwise.android": "Wise",
    "com.lydia.app": "Lydia",
    "com.lydia": "Sumeria",
    "com.paypal.android.p2pmobile": "PayPal",
    "eu.epicompany.wero.wallet": "Wero",
    "com.google.android.apps.walletnfcrel": "Google Wallet",
    "com.samsung.android.spay": "Samsung Wallet",
    "com.samsung.android.samsungpass": "Samsung Pass",
    "com.samsung.android.samsungpassautofill": "Samsung Pass",
    "com.binance.dev": "Binance",
    "com.coinbase.android": "Coinbase",
    "com.kraken.invest.app": "Kraken",
    "com.kraken.trade": "Kraken Pro",
    "com.ledger.live": "Ledger Wallet",
    "io.metamask": "MetaMask",
    "com.wallet.crypto.trustapp": "Trust Wallet",
    "fr.fdj.apps.fdj": "FDJ",
    "fr.gouv.franceidentite": "France Identité",
    "fr.laposte.idn": "L'Identité Numérique La Poste",
    "fr.gouv.finances.smartphone.android": "impots.gouv",
    "fr.cnamts.it.activity": "Compte ameli",
    "com.google.android.apps.authenticator2": "Google Authenticator",
    "com.azure.authenticator": "Microsoft Authenticator",
    "com.authy.authy": "Authy",
}
# Filet pour les applications absentes de la liste (autres banques, portefeuilles, applications de codes).
_PROTECTED_PATTERN = re.compile(r"bank|banque|wallet|crypto|authenticator|binance|(?:^|[._])(?:pay|auth)", re.I)

# Confirmation : politique commune de Claude, Antigravity et Codex (accord du 18/10/2026, `garde-fous.json`, copie
# identique de NexusHarness\NexusWindows\commun vérifiée par les tests). Payer, envoyer, publier, supprimer définitivement, formater,
# désinstaller, réinitialiser, résilier, rendre un dépôt public : confirm=true, que l'agent met sur l'ordre explicite de
# l'utilisateur pour ce geste ou après son oui ; appeler aussi (appel réel sur un téléphone). Tout le reste est libre
# (supprimer vers la corbeille, accepter, partager...).
POLICY_FILE = Path(__file__).resolve().with_name("garde-fous.json")


def _load() -> dict:
    """Politique embarquée ; illisible → PolicyError à l'import (échec fermé : le serveur ne démarre pas sans elle)."""
    try:
        data = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
        confirmation = data["confirmation"]
        motifs = [str(m) for m in confirmation["motifs"]]
        for context in confirmation["contextes"]:
            re.compile(context["titre"])
            re.compile(context["verbes"])
        re.compile("|".join(motifs))
    except (OSError, ValueError, KeyError, TypeError, re.error) as error:
        raise PolicyError(f"guardrail policy unreadable ({POLICY_FILE}): {error!r}") from error
    return data


class PolicyError(RuntimeError):
    """Politique commune absente ou invalide."""


POLICY = _load()
MOTIFS: tuple[str, ...] = tuple(POLICY["confirmation"]["motifs"])
_CONFIRM = re.compile(r"\b(" + "|".join(MOTIFS) + r")\b")
# Verbe générique (valider, OK...) à confirmer seulement quand l'écran parle d'argent ou de formatage (sur le bureau :
# le titre de la fenêtre ; ici : les textes de l'écran de la même application).
CONTEXTS = tuple((re.compile(r"\b(" + c["titre"] + r")\b"), re.compile(r"\b(" + c["verbes"] + r")\b"), c["quoi"])
                 for c in POLICY["confirmation"]["contextes"])
# Libellés anglais des « quoi » de garde-fous.json (le JSON reste en français, partagé entre outils).
_WHAT_EN = {
    "paiement ou virement": "payment or transfer",
    "formatage": "formatting",
    "réinitialisation de l'appareil": "device reset",
}
# Icônes sans texte des messageries (bouton d'envoi).
_SEND_IDS = re.compile(r"send|compose_message_send|send_message_button|send_button", re.I)
# Messageries, réseaux sociaux, courriel et téléphone : une icône cliquable sans nom y est souvent « Envoyer »,
# « Publier » ou le bouton vert d'appel.
COMMUNICATION_PACKAGES = {
    "com.google.android.dialer", "com.samsung.android.dialer", "com.android.dialer", "com.android.phone",
    "com.android.server.telecom", "com.samsung.android.incallui", "com.android.incallui",
    "com.google.android.contacts", "com.samsung.android.app.contacts", "com.google.android.apps.tachyon",
    "com.whatsapp", "com.whatsapp.w4b", "com.google.android.apps.messaging", "com.samsung.android.messaging",
    "org.telegram.messenger", "org.thunderdog.challegram", "org.thoughtcrime.securesms", "com.facebook.orca",
    "com.facebook.mlite", "com.facebook.katana", "com.instagram.android", "com.instagram.barcelona", "com.discord",
    "com.Slack", "com.microsoft.teams", "com.snapchat.android", "com.google.android.gm",
    "com.microsoft.office.outlook", "com.samsung.android.email.provider", "ch.protonmail.android", "com.twitter.android",
    "com.linkedin.android", "com.zhiliaoapp.musically", "ch.threema.app", "com.viber.voip", "jp.naver.line.android",
    "com.skype.raider", "com.google.android.apps.dynamite", "im.vector.app", "com.reddit.frontpage",
    "org.joinmastodon.android", "xyz.blueskyweb.app", "com.tencent.mm",
}
_COMMUNICATION_PATTERN = re.compile(r"messag|messenger|chat|mail|sms|dialer|telecom|incallui|contacts", re.I)


CURRENCIES = str.maketrans({"€": " eur ", "$": " usd ", "£": " gbp ", "¥": " jpy "})


def plain_label(text: object) -> str:
    """Texte comparable, comme garde_fous.plain : sans accents ni casse, ponctuation ramenée à des espaces ; symboles
    de devise écrits en toutes lettres (« 4,99 € » → « 4 99 eur ») pour que les motifs reconnaissent un bouton prix."""
    decomposed = unicodedata.normalize("NFKD", str(text or "")).translate(CURRENCIES)
    bare = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()
    return " ".join(re.sub(r"[^0-9a-z]+", " ", bare).split())


def confirm_word(label: object, screen_text: object = "") -> str:
    """Mot à confirmer dans ce libellé : un motif commun, ou un verbe générique (valider, OK...) sur un écran qui parle
    de paiement ou de formatage ; « » sinon."""
    text = plain_label(label)
    if found := _CONFIRM.search(text):
        return found.group(0)
    if screen_text:
        heading = plain_label(screen_text)
        for context, verbs, what in CONTEXTS:
            if (verb := verbs.search(text)) and context.search(heading):
                return f"{verb.group(0)} ({_WHAT_EN.get(what, what)} screen)"
    return ""


def communication_app(package: str) -> bool:
    return package in COMMUNICATION_PACKAGES or bool(package and _COMMUNICATION_PATTERN.search(package))


def protected_app(package: str) -> str | None:
    if not package:
        return None
    if name := PROTECTED_PACKAGES.get(package):
        return name
    return package if _PROTECTED_PATTERN.search(package) else None


def irreversible(element: Element, screen_text: str = "") -> str | None:
    """Libellé (ou identifiant d'icône d'envoi) qui exige confirm=true, None sinon."""
    for value in (element.text, element.desc):
        if value and confirm_word(value, screen_text):
            return value
    if element.rid and _SEND_IDS.search(element.rid):
        return element.rid
    return None


# ---------------------------------------------------------------- accord lié à l'action exacte (trinité, 19/10/2026)
# Geste SORTANT (envoyer, appeler, payer, publier, transférer) : confirm=true ET accord=<jeton> rendu par l'outil pour
# CETTE action (application, bouton, en-tête de l'écran où figure le destinataire, texte des champs) ; tout changement
# entre l'accord et le geste l'annule. Destructions locales (désinstaller, réinitialiser...) : confirm=true seul.
# `garde_fous.py` : copie identique de NexusHarness\NexusWindows\commun (vérifiée par les tests), comme garde-fous.json.
from . import garde_fous  # noqa: E402

ACCORDS = garde_fous.Accords()
HEADING_SHARE = 0.18  # haut de l'écran lu comme en-tête (nom ou numéro du correspondant dans une messagerie)


def outward(element: Element, screen_text: str = "") -> str | None:
    """Libellé (ou identifiant d'icône d'envoi) d'un geste sortant, qui exige l'accord lié à l'action ; None sinon."""
    for value in (element.text, element.desc):
        if value and garde_fous.outward_word(value, screen_text):
            return value
    if element.rid and _SEND_IDS.search(element.rid):
        return element.rid
    return None


# Pièce jointe nommée à l'écran (« photo-a.jpg », « Facture 12.pdf ») ; adresse e-mail n'importe où à l'écran.
_ATTACHMENT = re.compile(r"[^\s/\\:*?\"<>|][^/\\:*?\"<>|]{0,120}\.(?:jpe?g|png|gif|webp|heic|bmp|mp4|mov|3gp|webm|mkv|"
                         r"avi|mp3|m4a|aac|ogg|opus|wav|amr|pdf|docx?|xlsx?|pptx?|odt|ods|odp|txt|csv|zip|rar|7z|apk|"
                         r"vcf|ics|json|xml|html?)", re.I)
_MAIL = re.compile(r"[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


# Claviers (suggestions qui changent d'elles-mêmes) : hors de l'identité, comme la barre d'état.
_KEYBOARD = re.compile(r"inputmethod|honeyboard|keyboard|swiftkey", re.I)


def _words(e: Element) -> list[str]:
    """Ce qu'un élément dit : son texte et sa description, ou le sceau de son texte brut s'il est masqué (sans sceau :
    valeur tirée au hasard, qu'aucun accord ne retrouvera), et ses cases cochées."""
    words = ["sealed", e.sealed or secrets.token_hex(10)] if e.masked else list(e.raw if any(e.raw) else
                                                                                   (e.text, e.desc))
    return words + [s for s in ("checked", "selected") if s in e.states]


def _target(e: Element) -> list:
    """L'élément CIBLE complet : appli, rôle, ressource, limites, place dans l'arbre (ordre de dessin : deux boutons
    superposés dont seul l'ordre change ne sont pas la même cible) et ce qu'il dit."""
    return [e.package, e.role, e.rid, list(e.bounds), [list(p) for p in e.path], *_words(e)]


def outward_identity(elements: list[Element] | None, front: str, aimed: list[Element] | None = None) -> str:
    """Identité canonique de l'action sortante, la même quelle que soit la façon de viser (arbre, coordonnées, OCR) :
    appli au premier plan ; suite, dans l'ordre du document, de ce que disent les éléments de l'arbre (texte, ou sceau
    du texte brut d'un élément masqué : deux champs qui échangent deux IBAN changent la suite), sans leurs limites
    (un décalage de quelques pixels, le clavier qui s'ouvre ne change pas l'action) ; et la CIBLE complète du geste
    (aimed : éléments qui reçoivent l'appui, ou champ qui a le focus pour une touche, avec ressource, limites et ordre
    de dessin). Hors identité : éléments lus par OCR, barre d'état, claviers. Arbre illisible : identité unique,
    qu'aucun accord ne retrouvera."""
    if elements is None:
        return "unreadable " + secrets.token_hex(12)
    said = [[e.package, *_words(e)] for e in elements
            if e.role != "OCR" and e.package != "com.android.systemui" and not _KEYBOARD.search(e.package)]
    target = [_target(e) for e in aimed or []]
    canonical = json.dumps([front, said, target], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def screen_action(gesture: str, label: str, package: str, elements: list[Element], height: int,
                  front: str = "", aimed: list[Element] | None = None, where: str = "") -> dict:
    """L'action telle que l'écran la montre, liée tout entière au jeton d'accord : bouton visé ; destinataires (en-tête
    de l'écran, plus tout numéro de téléphone ou adresse e-mail affiché ailleurs : composeur, champ « À ») ; texte des
    champs ; pièces jointes nommées ; et le reste du texte de l'application à l'écran (aperçu, corps, vignettes
    décrites) en contexte. Un seul de ces textes change entre l'accord et le geste : l'accord ne vaut plus.
    aimed, where : cible effective et paramètres du geste (« at 250,575 », « from 60,1000 to 1040,1000 »), liés aussi."""
    same = sorted((e for e in elements if e.package == package and not e.password),
                  key=lambda e: (e.bounds[1], e.bounds[0]))
    top = height * HEADING_SHARE
    heading = [e.label for e in same if e.label and not e.editable and e.bounds[3] <= top]
    shown = [v for e in same if not e.editable and e.bounds[3] > top for v in dict.fromkeys((e.text, e.desc)) if v]
    numbers = [m for v in shown for m in (*garde_fous.PHONE.findall(v), *_MAIL.findall(v))]
    attachments = [v for v in shown if _ATTACHMENT.fullmatch(v.strip())]
    fields = [e.text for e in same if e.editable and e.text]
    rest = [v for v in shown if v not in attachments]
    target = f"{label} ({package})" + (f" {where}" if where else "")
    shown = garde_fous.action("android", gesture, target, recipients=heading + numbers,
                              content=" | ".join(fields), attachments=attachments, context=" | ".join(rest))
    # Résumé lisible ci-dessus (destinataires d'après les positions, à montrer à l'utilisateur) ; le jeton, lui, est
    # lié à la seule identité canonique stable (appli, textes dans l'ordre, cible complète) : un décalage de 2 px qui
    # fait passer un texte de l'en-tête au contexte ne change pas l'action.
    shown["identity"] = outward_identity(elements, front or package, aimed)
    return shown


def _bound(shown: dict) -> dict:
    """L'action telle que le jeton la lie : geste, cible (libellé, appli, point ou départ et arrivée) et identité."""
    return garde_fous.action("android", shown["gesture"], shown["target"], context="identity " + shown["identity"])


def accord_check(shown: dict, accord: object) -> str:
    """« » : accord valable (jeton consommé). Sinon le refus, avec un jeton neuf, et l'action telle que l'écran la
    montre (destinataires, texte, pièces jointes) pour l'utilisateur."""
    refusal = ACCORDS.check(_bound(shown), True, accord)
    if not refusal:
        return ""
    return "\n".join([refusal, f"As the screen shows it: {garde_fous.describe(shown)}", *ACCORDS.warnings(shown)])


def accord_propose(shown: dict) -> str:
    return ACCORDS.propose(_bound(shown))
