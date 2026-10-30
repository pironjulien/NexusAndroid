using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace NexusAndroid;

public static class AndroidPolicy
{
    private static readonly HashSet<string> ProtectedPackages = new(StringComparer.OrdinalIgnoreCase)
    {
        "com.fullsix.android.labanquepostale.accountaccess",
        "fr.creditagricole.androidapp",
        "net.bnpparibas.mescomptes",
        "mobi.societegenerale.mobile.lappli",
        "com.caisseepargne.android.mobilebanking",
        "fr.banquepopulaire.cyberplus",
        "com.cm_prod.bad",
        "com.cic_prod.bad",
        "fr.lcl.android.customerarea",
        "com.boursorama.android.clients",
        "com.fortuneo.android",
        "fr.bnpp.digitalbanking",
        "eu.qonto.qonto",
        "com.shine.app",
        "de.number26.android",
        "com.revolut.revolut",
        "com.transferwise.android",
        "com.lydia.app",
        "com.lydia",
        "com.paypal.android.p2pmobile",
        "eu.epicompany.wero.wallet",
        "com.google.android.apps.walletnfcrel",
        "com.samsung.android.spay",
        "com.samsung.android.samsungpass",
        "com.samsung.android.samsungpassautofill",
        "com.binance.dev",
        "com.coinbase.android",
        "com.kraken.invest.app",
        "com.kraken.trade",
        "com.ledger.live",
        "io.metamask",
        "com.wallet.crypto.trustapp",
        "fr.fdj.apps.fdj",
        "fr.gouv.franceidentite",
        "fr.laposte.idn",
        "fr.gouv.finances.smartphone.android",
        "fr.cnamts.it.activity",
        "com.google.android.apps.authenticator2",
        "com.azure.authenticator",
        "com.authy.authy",
        "com.x8bit.bitwarden",
        "com.onepassword.android",
        "keepass2android.keepass2android",
    };

    public static bool IsProtectedPackage(string? packageName)
    {
        if (string.IsNullOrWhiteSpace(packageName)) return false;
        return ProtectedPackages.Contains(packageName.Trim()) || System.Text.RegularExpressions.Regex.IsMatch(packageName,@"(?i)bank|banque|wallet|crypto|authenticator|binance|(?:^|[._])(?:pay|auth)");
    }

}
