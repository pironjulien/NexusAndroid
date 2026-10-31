// Signatures d'Android pour la compilation seulement (jamais dans le dex : le téléphone fournit les vraies classes).
package android.content;

public class ClipData {
    public static class Item {
        public CharSequence getText() { throw new RuntimeException("stub"); }
    }
    public static ClipData newPlainText(CharSequence label, CharSequence text) { throw new RuntimeException("stub"); }
    public ClipDescription getDescription() { throw new RuntimeException("stub"); }
    public int getItemCount() { throw new RuntimeException("stub"); }
    public Item getItemAt(int index) { throw new RuntimeException("stub"); }
}
