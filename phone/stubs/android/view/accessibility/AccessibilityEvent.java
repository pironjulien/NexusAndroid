// Signatures d'Android pour la compilation seulement (jamais dans le dex : le téléphone fournit les vraies classes).
package android.view.accessibility;

public final class AccessibilityEvent {
    public static final int TYPE_NOTIFICATION_STATE_CHANGED = 64;
    public int getEventType() { throw new RuntimeException("stub"); }
    public CharSequence getPackageName() { throw new RuntimeException("stub"); }
    public java.util.List<CharSequence> getText() { throw new RuntimeException("stub"); }
    public android.os.Parcelable getParcelableData() { throw new RuntimeException("stub"); }
}
