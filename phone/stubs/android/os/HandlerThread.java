// Signatures d'Android pour la compilation seulement (jamais dans le dex : le téléphone fournit les vraies classes).
package android.os;

public class HandlerThread extends Thread {
    public HandlerThread(String name) { throw new RuntimeException("stub"); }
    public Looper getLooper() { throw new RuntimeException("stub"); }
    public boolean quit() { throw new RuntimeException("stub"); }
}
