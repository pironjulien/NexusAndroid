// Signatures d'Android pour la compilation seulement (jamais dans le dex : le téléphone fournit les vraies classes).
package android.view;

public final class Display {
    public int getRotation() { throw new RuntimeException("stub"); }
    public void getRealSize(android.graphics.Point outSize) { throw new RuntimeException("stub"); }
}
