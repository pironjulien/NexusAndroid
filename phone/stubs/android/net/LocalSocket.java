// Signatures d'Android pour la compilation seulement (jamais dans le dex : le téléphone fournit les vraies classes).
package android.net;

public class LocalSocket implements java.io.Closeable {
    public java.io.InputStream getInputStream() throws java.io.IOException { throw new RuntimeException("stub"); }
    public java.io.OutputStream getOutputStream() throws java.io.IOException { throw new RuntimeException("stub"); }
    public Credentials getPeerCredentials() throws java.io.IOException { throw new RuntimeException("stub"); }
    @Override
    public void close() throws java.io.IOException { throw new RuntimeException("stub"); }
}
