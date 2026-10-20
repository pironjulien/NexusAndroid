// Signatures d'Android pour la compilation seulement (jamais dans le dex : le téléphone fournit les vraies classes).
package android.view.accessibility;

public class AccessibilityNodeInfo {
    public int getChildCount() { throw new RuntimeException("stub"); }
    public AccessibilityNodeInfo getChild(int index) { throw new RuntimeException("stub"); }
    public boolean isVisibleToUser() { throw new RuntimeException("stub"); }
    public CharSequence getText() { throw new RuntimeException("stub"); }
    public CharSequence getContentDescription() { throw new RuntimeException("stub"); }
    public CharSequence getClassName() { throw new RuntimeException("stub"); }
    public CharSequence getPackageName() { throw new RuntimeException("stub"); }
    public String getViewIdResourceName() { throw new RuntimeException("stub"); }
    public boolean isCheckable() { throw new RuntimeException("stub"); }
    public boolean isChecked() { throw new RuntimeException("stub"); }
    public boolean isClickable() { throw new RuntimeException("stub"); }
    public boolean isEnabled() { throw new RuntimeException("stub"); }
    public boolean isFocusable() { throw new RuntimeException("stub"); }
    public boolean isFocused() { throw new RuntimeException("stub"); }
    public boolean isScrollable() { throw new RuntimeException("stub"); }
    public boolean isLongClickable() { throw new RuntimeException("stub"); }
    public boolean isPassword() { throw new RuntimeException("stub"); }
    public boolean isSelected() { throw new RuntimeException("stub"); }
    public boolean isEditable() { throw new RuntimeException("stub"); }
    public int getDrawingOrder() { throw new RuntimeException("stub"); }
    public void getBoundsInScreen(android.graphics.Rect outBounds) { throw new RuntimeException("stub"); }
}
