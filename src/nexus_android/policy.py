"""Garde-fous : ce que l'outil refuse de faire seul, quel que soit l'agent ou le texte lu à l'écran."""

from __future__ import annotations

import re

from .messages import plain
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

# Boutons dont l'effet part chez quelqu'un d'autre ou ne se rattrape pas : touchés seulement avec confirm=true,
# que l'agent ne met qu'après un « oui » explicite de l'utilisateur pour cette action précise. Cherchés n'importe où dans
# le libellé (« Tout supprimer », « Oui, envoyer »), sans accents ni apostrophe courbe.
_IRREVERSIBLE = re.compile(
    r"\b(?:passer la commande|commander|order|payer|pay|acheter|buy|reserver|souscrire|subscribe|s'abonner|abonner|"
    r"resilier|envoyer|send|publier|post|poster|partager|share|valider|confirmer|confirm|soumettre|submit|"
    r"j'accepte|accepter|accept|autoriser|allow|signer|supprimer|delete|effacer|vider|corbeille|trash|"
    r"desinstaller|uninstall|reinitialiser|reset|transferer|virement|appeler|call)\b",
    re.I,
)
# Icônes sans texte des messageries (bouton d'envoi).
_SEND_IDS = re.compile(r"send|compose_message_send|send_message_button|send_button", re.I)


def protected_app(package: str) -> str | None:
    if not package:
        return None
    if name := PROTECTED_PACKAGES.get(package):
        return name
    return package if _PROTECTED_PATTERN.search(package) else None


def irreversible(element: Element) -> str | None:
    for value in (element.text, element.desc):
        if value and _IRREVERSIBLE.search(plain(value)):
            return value
    if element.rid and _SEND_IDS.search(element.rid):
        return element.rid
    return None
