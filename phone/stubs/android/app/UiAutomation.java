// Signatures d'Android pour la compilation seulement (jamais dans le dex : le téléphone fournit les vraies classes).
package android.app;

public class UiAutomation {
    public interface OnAccessibilityEventListener {
        void onAccessibilityEvent(android.view.accessibility.AccessibilityEvent event);
    }
    public android.view.accessibility.AccessibilityNodeInfo getRootInActiveWindow() { throw new RuntimeException("stub"); }
    public void waitForIdle(long idleTimeoutMillis, long globalTimeoutMillis) throws java.util.concurrent.TimeoutException { throw new RuntimeException("stub"); }
    public android.graphics.Bitmap takeScreenshot() { throw new RuntimeException("stub"); }
    public final android.accessibilityservice.AccessibilityServiceInfo getServiceInfo() { throw new RuntimeException("stub"); }
    public final void setServiceInfo(android.accessibilityservice.AccessibilityServiceInfo info) { throw new RuntimeException("stub"); }
    public void setOnAccessibilityEventListener(OnAccessibilityEventListener listener) { throw new RuntimeException("stub"); }
}
