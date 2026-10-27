// Signatures d'Android pour la compilation seulement (jamais dans le dex : le téléphone fournit les vraies classes).
package android.os;

public final class Build {
    public static final class VERSION {
        // Pas une constante de compilation : javac ne doit pas figer la valeur du bouchon dans le dex.
        public static final int SDK_INT = Integer.parseInt("0");
    }
}
