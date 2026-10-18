"""Garde-fous : ce que l'outil refuse de faire seul, quel que soit l'agent ou le texte lu à l'écran."""

from __future__ import annotations

import json
import re
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
# identique de NexusHarness\shared vérifiée par les tests). Payer, envoyer, publier, supprimer définitivement, formater,
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
        raise PolicyError(f"politique des garde-fous illisible ({POLICY_FILE}) : {error!r}") from error
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


def plain_label(text: object) -> str:
    """Texte comparable, comme garde_fous.plain : sans accents ni casse, ponctuation ramenée à des espaces."""
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
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
                return f"{verb.group(0)} (écran de {what})"
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
