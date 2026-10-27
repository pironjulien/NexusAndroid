// Signatures d'Android pour la compilation seulement (jamais dans le dex : le téléphone fournit les vraies classes).
package android.graphics;

public final class Bitmap {
    public enum Config { ARGB_8888, HARDWARE }
    public enum CompressFormat { JPEG, PNG, WEBP_LOSSLESS }
    public int getWidth() { throw new RuntimeException("stub"); }
    public int getHeight() { throw new RuntimeException("stub"); }
    public Config getConfig() { throw new RuntimeException("stub"); }
    public Bitmap copy(Config config, boolean isMutable) { throw new RuntimeException("stub"); }
    public void recycle() { throw new RuntimeException("stub"); }
    public boolean compress(CompressFormat format, int quality, java.io.OutputStream stream) { throw new RuntimeException("stub"); }
    public static Bitmap createScaledBitmap(Bitmap src, int dstWidth, int dstHeight, boolean filter) { throw new RuntimeException("stub"); }
}
