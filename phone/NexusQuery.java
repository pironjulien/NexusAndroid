// Serveur de lectures gardé en vie sur le téléphone : chaque `content query` lancé par adb shell démarre sa propre
// machine Java (environ 1 s) ; ici une seule machine, démarrée par app_process sous l'identité shell (comme le serveur
// de scrcpy), exécute les requêtes en parallèle par le même chemin que la commande `content` (fournisseur obtenu de
// l'ActivityManager, appel query) et sérialise elle-même les lignes du curseur : aucun texte d'un SMS ne peut imiter
// une ligne (« Row: 1 … » dans un message). Rien à installer, aucune permission en plus de celles d'adb.
//
// Lancement : CLASSPATH=<ce dex> app_process / NexusQuery <socket> <secondes d'inactivité> <jeton>
// Joint par adb forward tcp:0 localabstract:<socket>. Le socket porte le jeton aléatoire tiré par le PC à chaque
// lancement (nom imprévisible) et le serveur le renvoie en tête de chaque réponse : le PC refuse tout autre serveur.
// Garde-fous : seules les connexions de l'utilisateur shell (adbd) ou root sont servies (identité donnée par le noyau,
// SO_PEERCRED : une application du téléphone ne peut pas l'avoir) ; seule la lecture (`query`) est acceptée ; arrêt
// après le délai d'inactivité.
//
// Lecteur d'écran (requête `screen`) : une seule connexion UiAutomation, ouverte à la première lecture et gardée
// (`uiautomator dump` en ouvre une à chaque commande, puis attend une seconde de calme ; screencap s'y ajoute). Android
// n'accepte qu'un client UiAutomation à la fois : ce serveur est unique par téléphone (même jeton, même reprise par les
// autres processus), il la rend sur demande (`release`, avant tout repli vers `uiautomator dump`) et seul après
// UI_IDLE_MS sans lecture d'écran, pour laisser la place aux autres outils. Lecture seule : ni geste, ni action
// globale, ni saisie ; les services d'accessibilité de l'utilisateur ne sont pas suspendus
// (FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES).
//
// Protocole (entiers sur 4 octets, gros-boutiens ; textes en UTF-8 précédés de leur longueur, -1 pour NULL) :
//   PC → téléphone : "NXQ2", nombre de requêtes, puis pour chacune : nombre d'arguments, arguments
//   téléphone → PC : "NXQ2", jeton, puis pour chaque requête dans l'ordre :
//     0, nombre de colonnes, noms des colonnes, nombre de lignes, valeurs (ligne par ligne)   lecture réussie
//     1, message                                                                             erreur
//     2, taille, octets                                                                      lecture d'écran
//   Lecture d'écran (screen [--image none|scaled|full] [--max côté] [--format jpeg|lossless] [--quality q] [--quiet ms]),
//   dans les octets :
//     état (sortie de STATUS_SH), rotation, largeur, hauteur de l'affichage 0,
//     arbre : 0, nombre de nœuds, puis par nœud (ordre du document de `uiautomator dump`) texte, resource-id, classe,
//             paquet, content-desc, drapeaux (bits dans l'ordre de walk), limites visibles x1 y1 x2 y2, profondeur
//             (0 pour la racine), ordre de dessin parmi ses frères (getDrawingOrder) ; ou 1, message
//     image : 0, largeur et hauteur de la capture, taille, octets (JPEG, ou avec --format lossless WebP sans perte dès
//             Android 11, PNG avant ; --quality est alors l'effort de compression) ; 1, message ; 2 (non demandée)

import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.UiAutomation;
import android.graphics.Bitmap;
import android.graphics.Point;
import android.graphics.Rect;
import android.net.LocalServerSocket;
import android.os.Build;
import android.net.LocalSocket;
import android.os.HandlerThread;
import android.os.Looper;
import android.view.Display;
import android.view.accessibility.AccessibilityNodeInfo;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

public final class NexusQuery {
    private static final byte[] MAGIC = "NXQ2".getBytes(StandardCharsets.US_ASCII);
    private static final int MAX_QUERIES = 512;
    private static final int MAX_ARGS = 32;
    private static final int MAX_ARG_BYTES = 64 * 1024;
    private static final long QUERY_WAIT_MS = 8_000;  // sous les 10 s du PC : un fournisseur figé rend une erreur, pas une coupure
    private static final int SHELL_UID = 2000;
    private static final int USER_SYSTEM = 0;

    private static volatile long lastUse = System.currentTimeMillis();

    // Lecteur d'écran.
    private static final long UI_IDLE_MS = 45_000;  // sans lecture d'écran : UiAutomation rendue aux autres outils
    private static final long QUIET_MS = 1_000;  // calme attendu avant de lire l'arbre, comme `uiautomator dump`
    private static final long SETTLE_MAX_MS = 3_000;  // écran jamais calme (vidéo) : arbre lu quand même après ce délai
    private static final long STATUS_WAIT_MS = 3_000;
    private static final int MAX_NODES = 20_000;
    private static final int FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES = 1;  // UiAutomation
    private static final int FLAG_INCLUDE_NOT_IMPORTANT_VIEWS = 2;  // AccessibilityServiceInfo, comme dump (non compressé)
    // État lu dans le même échange : la commande STATE_SH de screen.py (mot pour mot, vérifié par les tests), puis le
    // pliage. Texte fixe : la requête n'y met rien.
    static final String STATE_SH = "dumpsys power | grep mWakefulness=; { dumpsys window displays; dumpsys window "
            + "policy; } | grep -E 'mCurrentFocus=|mFocusedApp=|isKeyguardShowing=|mShowingLockscreen=|"
            + "KeyguardShowing=|mIsShowing='; true";
    static final String POSTURE_MARK = "@@NEXUS-POSTURE@@";
    private static final String STATUS_SH = STATE_SH + "; echo " + POSTURE_MARK
            + "; dumpsys device_state | grep mCommittedState=; true";
    private static final Object SCREEN_LOCK = new Object();
    private static HandlerThread uiThread;
    private static volatile UiAutomation automation;
    private static volatile long lastScreen;

    // API cachées, résolues une fois au démarrage (mêmes appels que com.android.commands.content.Content).
    private static Object activityManager;
    private static Method getProvider;
    private static Method removeProvider;
    private static boolean removeAsUser;
    private static Method providerQuery;
    private static int queryShape; // 31+ : AttributionSource ; 30 : paquet + attribution ; 26-29 : paquet
    private static Object attribution;
    private static String callingPackage;
    private static Method uriParse;
    private static Method sqlBundle;
    private static Constructor<?> binder;
    private static Method columnNames, moveToNext, getType, getString, getLong, getDouble, close;

    public static void main(String[] args) throws Exception {
        String name = args[0];
        long idleMs = Long.parseLong(args[1]) * 1000L;
        byte[] token = args[2].getBytes(StandardCharsets.UTF_8);
        if (token.length < 32 || !name.endsWith(args[2])) {
            throw new IllegalArgumentException("jeton absent ou trop court");
        }
        prepare();
        try {
            Looper.prepareMainLooper();  // attendu par le client d'accessibilité d'UiAutomation (comme uiautomator)
        } catch (Throwable e) {
            // déjà prêt
        }
        // Nom déjà pris : quelqu'un d'autre l'occupe. Exception, arrêt : le PC relance avec un nouveau jeton.
        LocalServerSocket server = new LocalServerSocket(name);
        ExecutorService pool = Executors.newFixedThreadPool(8);

        Thread watchdog = new Thread(() -> {
            while (true) {
                try {
                    Thread.sleep(5000);
                } catch (InterruptedException e) {
                    return;
                }
                if (System.currentTimeMillis() - lastUse > idleMs) {
                    System.exit(0);
                }
                if (automation != null && System.currentTimeMillis() - lastScreen > UI_IDLE_MS) {
                    release();
                }
            }
        });
        watchdog.setDaemon(true);
        watchdog.start();

        while (true) {
            LocalSocket client = server.accept();
            lastUse = System.currentTimeMillis();
            Thread handler = new Thread(() -> serve(client, token, pool));
            handler.setDaemon(true);
            handler.start();
        }
    }

    private static void prepare() throws Exception {
        activityManager = Class.forName("android.app.ActivityManager").getMethod("getService").invoke(null);
        Class<?> binderClass = Class.forName("android.os.IBinder");
        for (Method m : activityManager.getClass().getMethods()) {
            Class<?>[] p = m.getParameterTypes();
            if (m.getName().equals("getContentProviderExternal") && p.length == 4 && p[0] == String.class
                    && p[1] == int.class && p[2] == binderClass && p[3] == String.class) {
                getProvider = m;
            } else if (m.getName().equals("removeContentProviderExternalAsUser") && p.length == 3) {
                removeProvider = m;
                removeAsUser = true;
            } else if (m.getName().equals("removeContentProviderExternal") && p.length == 2 && removeProvider == null) {
                removeProvider = m;
            }
        }
        if (getProvider == null) {
            throw new NoSuchMethodException("getContentProviderExternal");
        }
        int uid = (int) Class.forName("android.os.Process").getMethod("myUid").invoke(null);
        callingPackage = uid == 0 ? "root" : uid == SHELL_UID ? "com.android.shell" : null;
        Class<?> uriClass = Class.forName("android.net.Uri");
        Class<?> bundleClass = Class.forName("android.os.Bundle");
        for (Method m : Class.forName("android.content.IContentProvider").getMethods()) {
            Class<?>[] p = m.getParameterTypes();
            if (!m.getName().equals("query")) {
                continue;
            }
            if (p.length == 5 && p[0].getName().equals("android.content.AttributionSource") && p[1] == uriClass
                    && p[3] == bundleClass) {
                providerQuery = m;
                queryShape = 31;
                attribution = attributionSource(p[0], uid);
            } else if (p.length == 6 && p[0] == String.class && p[1] == String.class && p[2] == uriClass
                    && providerQuery == null) {
                providerQuery = m;
                queryShape = 30;
            } else if (p.length == 5 && p[0] == String.class && p[1] == uriClass && providerQuery == null) {
                providerQuery = m;
                queryShape = 26;
            }
        }
        if (providerQuery == null) {
            throw new NoSuchMethodException("IContentProvider.query");
        }
        uriParse = uriClass.getMethod("parse", String.class);
        sqlBundle = Class.forName("android.content.ContentResolver").getMethod(
                "createSqlQueryBundle", String.class, String[].class, String.class);
        binder = Class.forName("android.os.Binder").getConstructor();
        Class<?> cursor = Class.forName("android.database.Cursor");
        columnNames = cursor.getMethod("getColumnNames");
        moveToNext = cursor.getMethod("moveToNext");
        getType = cursor.getMethod("getType", int.class);
        getString = cursor.getMethod("getString", int.class);
        getLong = cursor.getMethod("getLong", int.class);
        getDouble = cursor.getMethod("getDouble", int.class);
        close = cursor.getMethod("close");
    }

    private static Object attributionSource(Class<?> type, int uid) throws Exception {
        try {
            return type.getConstructor(int.class, String.class, String.class).newInstance(uid, callingPackage, null);
        } catch (NoSuchMethodException e) {
            Class<?> builder = Class.forName("android.content.AttributionSource$Builder");
            Object b = builder.getConstructor(int.class).newInstance(uid);
            builder.getMethod("setPackageName", String.class).invoke(b, callingPackage);
            return builder.getMethod("build").invoke(b);
        }
    }

    private static void serve(LocalSocket client, byte[] token, ExecutorService pool) {
        try (LocalSocket socket = client) {
            int uid = socket.getPeerCredentials().getUid();
            if (uid != SHELL_UID && uid != 0) {
                return;
            }
            DataInputStream in = new DataInputStream(socket.getInputStream());
            byte[] magic = new byte[4];
            in.readFully(magic);
            if (!Arrays.equals(magic, MAGIC)) {
                return;
            }
            int count = in.readInt();
            if (count < 0 || count > MAX_QUERIES) {
                return;
            }
            List<String[]> queries = new ArrayList<>();
            for (int i = 0; i < count; i++) {
                int argc = in.readInt();
                if (argc < 1 || argc > MAX_ARGS) {
                    return;
                }
                String[] argv = new String[argc];
                for (int j = 0; j < argc; j++) {
                    int size = in.readInt();
                    if (size < 0 || size > MAX_ARG_BYTES) {
                        return;
                    }
                    byte[] data = new byte[size];
                    in.readFully(data);
                    argv[j] = new String(data, StandardCharsets.UTF_8);
                }
                queries.add(argv);
            }
            List<Future<byte[]>> results = new ArrayList<>();
            for (String[] argv : queries) {
                results.add(pool.submit(() -> run(argv)));
            }
            DataOutputStream out = new DataOutputStream(socket.getOutputStream());
            out.write(MAGIC);
            out.writeInt(token.length);
            out.write(token);
            // Fournisseur figé : la lecture rend une erreur au bout de QUERY_WAIT_MS (toutes lectures du lot comprises)
            // et sa tâche est annulée, au lieu de bloquer pour toujours un fil du pool.
            long deadline = System.currentTimeMillis() + QUERY_WAIT_MS;
            for (Future<byte[]> result : results) {
                byte[] answer;
                try {
                    answer = result.get(Math.max(1, deadline - System.currentTimeMillis()), TimeUnit.MILLISECONDS);
                } catch (TimeoutException e) {
                    result.cancel(true);
                    answer = failure("Error: no answer from the provider after " + QUERY_WAIT_MS / 1000 + " s");
                }
                out.write(answer);
            }
            out.flush();
            lastUse = System.currentTimeMillis();
        } catch (Exception e) {
            // client parti ou requête mal formée : connexion fermée, le serveur continue
        }
    }

    private static void text(DataOutputStream out, String value) throws IOException {
        if (value == null) {
            out.writeInt(-1);
            return;
        }
        byte[] data = value.getBytes(StandardCharsets.UTF_8);
        out.writeInt(data.length);
        out.write(data);
    }

    private static byte[] failure(String message) {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        try (DataOutputStream out = new DataOutputStream(buffer)) {
            out.writeInt(1);
            text(out, message);
        } catch (IOException e) {
            // tampon en mémoire : n'arrive pas
        }
        return buffer.toByteArray();
    }

    /** Une lecture, arguments de la commande `content` : query --uri U [--projection a:b] [--where W] [--sort S]. */
    private static byte[] run(String[] argv) {
        if ("screen".equals(argv[0])) {
            return screen(argv);
        }
        if ("release".equals(argv[0])) {
            release();
            return empty();
        }
        if (!"query".equals(argv[0])) { // lecture seule : ni insert, ni update, ni delete, ni call
            return failure("Error: only `query`, `screen` and `release` are served");
        }
        String uri = null, where = null, sort = null;
        String[] projection = null;
        for (int i = 1; i < argv.length; i += 2) {
            if (i + 1 >= argv.length) {
                return failure("Error: missing value for " + argv[i]);
            }
            switch (argv[i]) {
                case "--uri": uri = argv[i + 1]; break;
                case "--projection": projection = argv[i + 1].split(":"); break;
                case "--where": where = argv[i + 1]; break;
                case "--sort": sort = argv[i + 1]; break;
                default: return failure("Error: unsupported option " + argv[i]);
            }
        }
        if (uri == null) {
            return failure("Error: --uri required");
        }
        String authority = null;
        Object token = null;
        try {
            Object parsed = uriParse.invoke(null, uri);
            authority = (String) parsed.getClass().getMethod("getAuthority").invoke(parsed);
            token = binder.newInstance();
            Object holder = getProvider.invoke(activityManager, authority, USER_SYSTEM, token, "*cmd*");
            if (holder == null) {
                return failure("Error while accessing provider:" + authority + " (provider not found)");
            }
            Object provider = holder.getClass().getField("provider").get(holder);
            Object args = sqlBundle.invoke(null, where, null, sort);
            Object cursor;
            if (queryShape == 31) {
                cursor = providerQuery.invoke(provider, attribution, parsed, projection, args, null);
            } else if (queryShape == 30) {
                cursor = providerQuery.invoke(provider, callingPackage, null, parsed, projection, args, null);
            } else {
                cursor = providerQuery.invoke(provider, callingPackage, parsed, projection, args, null);
            }
            return rows(cursor);
        } catch (InvocationTargetException e) {
            return failure("Error while accessing provider:" + authority + " (" + e.getCause() + ")");
        } catch (Throwable e) {
            return failure("Error while accessing provider:" + authority + " (" + e + ")");
        } finally {
            if (token != null && authority != null && removeProvider != null) {
                try {
                    if (removeAsUser) {
                        removeProvider.invoke(activityManager, authority, token, USER_SYSTEM);
                    } else {
                        removeProvider.invoke(activityManager, authority, token);
                    }
                } catch (Throwable e) {
                    // fournisseur déjà relâché
                }
            }
        }
    }

    /** Lignes du curseur, valeurs écrites comme les imprime `content` (NULL, BLOB, nombres en texte). */
    private static byte[] rows(Object cursor) throws Exception {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        DataOutputStream out = new DataOutputStream(buffer);
        out.writeInt(0);
        if (cursor == null) { // « No result found. »
            out.writeInt(0);
            out.writeInt(0);
            return buffer.toByteArray();
        }
        try {
            String[] names = (String[]) columnNames.invoke(cursor);
            out.writeInt(names.length);
            for (String n : names) {
                text(out, n);
            }
            ByteArrayOutputStream body = new ByteArrayOutputStream();
            DataOutputStream values = new DataOutputStream(body);
            int count = 0;
            while ((boolean) moveToNext.invoke(cursor)) {
                for (int i = 0; i < names.length; i++) {
                    switch ((int) getType.invoke(cursor, i)) {
                        case 0: text(values, null); break; // FIELD_TYPE_NULL
                        case 1: text(values, String.valueOf((long) getLong.invoke(cursor, i))); break;
                        case 2: text(values, String.valueOf((double) getDouble.invoke(cursor, i))); break;
                        case 4: text(values, "BLOB"); break;
                        default: text(values, (String) getString.invoke(cursor, i)); break;
                    }
                }
                count++;
            }
            out.writeInt(count);
            body.writeTo(out);
            return buffer.toByteArray();
        } finally {
            close.invoke(cursor);
        }
    }

    private static byte[] empty() {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        try (DataOutputStream out = new DataOutputStream(buffer)) {
            out.writeInt(0);
            out.writeInt(0);
            out.writeInt(0);
        } catch (IOException e) {
            // tampon en mémoire : n'arrive pas
        }
        return buffer.toByteArray();
    }

    // ---------------------------------------------------------------- lecteur d'écran

    /** Connexion UiAutomation gardée ; ouverte ici au besoin (comme UiAutomationShellWrapper de `uiautomator`). */
    private static UiAutomation connect() throws Exception {
        if (automation != null) {
            return automation;
        }
        if (uiThread == null) {
            uiThread = new HandlerThread("NexusScreen");
            uiThread.setDaemon(true);
            uiThread.start();
        }
        Object connection = Class.forName("android.app.UiAutomationConnection").getConstructor().newInstance();
        UiAutomation ua = UiAutomation.class.getConstructor(Looper.class,
                Class.forName("android.app.IUiAutomationConnection")).newInstance(uiThread.getLooper(), connection);
        try {
            UiAutomation.class.getMethod("connect", int.class).invoke(ua, FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES);
        } catch (InvocationTargetException e) {
            // Déjà prise (autre outil : Appium, uiautomator2, `uiautomator dump` en cours) : message d'Android.
            throw new IllegalStateException(String.valueOf(e.getCause()));
        }
        AccessibilityServiceInfo info = ua.getServiceInfo();
        if (info != null) {
            info.flags |= FLAG_INCLUDE_NOT_IMPORTANT_VIEWS;
            ua.setServiceInfo(info);
        }
        automation = ua;
        return ua;
    }

    /** Rend la connexion UiAutomation (avant un `uiautomator dump`, ou après UI_IDLE_MS sans lecture d'écran). */
    private static void release() {
        synchronized (SCREEN_LOCK) {
            if (automation == null) {
                return;
            }
            try {
                UiAutomation.class.getMethod("disconnect").invoke(automation);
            } catch (Throwable e) {
                // déjà rompue
            }
            automation = null;
        }
    }

    private static byte[] screen(String[] argv) {
        String image = "none", format = "jpeg";
        int maxSide = 1280, quality = 90;
        long quietMs = QUIET_MS;  // --quiet 0 : arbre de l'instant (relecture entre les deux appuis d'un double appui)
        for (int i = 1; i < argv.length; i += 2) {
            if (i + 1 >= argv.length) {
                return failure("Error: missing value for " + argv[i]);
            }
            try {
                switch (argv[i]) {
                    case "--image": image = argv[i + 1]; break;
                    case "--max": maxSide = Integer.parseInt(argv[i + 1]); break;
                    case "--format": format = argv[i + 1]; break;
                    case "--quality": quality = Integer.parseInt(argv[i + 1]); break;
                    case "--quiet": quietMs = Long.parseLong(argv[i + 1]); break;
                    default: return failure("Error: unsupported option " + argv[i]);
                }
            } catch (NumberFormatException e) {
                return failure("Error: bad number for " + argv[i]);
            }
        }
        if (!image.equals("none") && !image.equals("scaled") && !image.equals("full")
                || !format.equals("jpeg") && !format.equals("lossless")
                || maxSide < 16 || quality < 0 || quality > 100 || quietMs < 0 || quietMs > QUIET_MS) {
            return failure("Error: bad screen options");
        }
        synchronized (SCREEN_LOCK) {
            lastScreen = System.currentTimeMillis();
            try {
                return snapshot(image, maxSide, format, quality, quietMs);
            } catch (Throwable e) {
                return failure("Error: screen read failed (" + e + ")");
            } finally {
                lastScreen = System.currentTimeMillis();
            }
        }
    }

    private static byte[] snapshot(String image, int maxSide, String format, int quality, long quietMs) throws Exception {
        Process status = null;
        try {
            status = new ProcessBuilder("sh", "-c", STATUS_SH).redirectErrorStream(true).start();
            status.getOutputStream().close();
        } catch (IOException e) {
            // état relu par le PC
        }
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        DataOutputStream body = new DataOutputStream(buffer);
        int rotation = 0, width = 0, height = 0;
        try {
            Object global = Class.forName("android.hardware.display.DisplayManagerGlobal").getMethod("getInstance")
                    .invoke(null);
            Display display = (Display) global.getClass().getMethod("getRealDisplay", int.class).invoke(global, 0);
            rotation = display.getRotation();
            Point size = new Point();
            display.getRealSize(size);
            width = size.x;
            height = size.y;
        } catch (Throwable e) {
            // taille inconnue : le PC repasse par uiautomator (limites non rognables)
        }
        UiAutomation ua = null;
        ByteArrayOutputStream tree = new ByteArrayOutputStream();
        DataOutputStream nodes = new DataOutputStream(tree);
        String treeError = null;
        int[] count = {0};
        try {
            if (width <= 0 || height <= 0) {
                throw new IllegalStateException("display 0 size unknown");
            }
            ua = connect();
            if (quietMs > 0) {
                try {
                    ua.waitForIdle(quietMs, SETTLE_MAX_MS);
                } catch (TimeoutException e) {
                    // écran toujours en mouvement : arbre de l'instant présent
                }
            }
            AccessibilityNodeInfo root = ua.getRootInActiveWindow();
            if (root == null) {
                treeError = "ERROR: null root node returned by UiTestAutomationBridge.";
            } else {
                walk(root, nodes, count, width, height, 0);
            }
        } catch (Throwable e) {
            treeError = "UI tree unavailable (" + e + ")";
            ua = null;
            release();  // connexion douteuse : rouverte à la prochaine lecture, ou laissée à `uiautomator dump`
        }
        byte[] encoded = null;
        int shotWidth = 0, shotHeight = 0;
        String imageError = null;
        if (!image.equals("none")) {
            try {
                if (ua == null) {
                    ua = connect();
                }
                Bitmap shot = ua.takeScreenshot();
                if (shot == null) {
                    imageError = "no screenshot";
                } else {
                    shotWidth = shot.getWidth();
                    shotHeight = shot.getHeight();
                    Bitmap pixels = shot.getConfig() == Bitmap.Config.HARDWARE
                            ? shot.copy(Bitmap.Config.ARGB_8888, false) : shot;
                    double factor = image.equals("full") ? 1.0
                            : Math.min(1.0, (double) maxSide / Math.max(shotWidth, shotHeight));
                    if (factor < 1.0) {
                        // Arrondi au pair le plus proche, comme round() de Python : la taille que viserait le PC.
                        pixels = Bitmap.createScaledBitmap(pixels, (int) Math.rint(shotWidth * factor),
                                (int) Math.rint(shotHeight * factor), true);
                    }
                    ByteArrayOutputStream out = new ByteArrayOutputStream(256 * 1024);
                    // Sans perte : le PC noircit les codes, dessine les numéros puis réencode en WebP ; un JPEG ici
                    // laissait ses artefacts sur le petit texte (double compression). WebP sans perte dès Android 11.
                    Bitmap.CompressFormat how = !format.equals("lossless") ? Bitmap.CompressFormat.JPEG
                            : Build.VERSION.SDK_INT >= 30 ? Bitmap.CompressFormat.WEBP_LOSSLESS : Bitmap.CompressFormat.PNG;
                    pixels.compress(how, quality, out);
                    encoded = out.toByteArray();
                }
            } catch (Throwable e) {
                imageError = "screenshot failed (" + e + ")";
            }
        }
        text(body, status == null ? null : statusOutput(status));
        body.writeInt(rotation);
        body.writeInt(width);
        body.writeInt(height);
        if (treeError == null) {
            body.writeInt(0);
            body.writeInt(count[0]);
            tree.writeTo(body);
        } else {
            body.writeInt(1);
            text(body, treeError);
        }
        if (image.equals("none")) {
            body.writeInt(2);
        } else if (encoded != null) {
            body.writeInt(0);
            body.writeInt(shotWidth);
            body.writeInt(shotHeight);
            body.writeInt(encoded.length);
            body.write(encoded);
        } else {
            body.writeInt(1);
            text(body, imageError);
        }
        body.flush();
        ByteArrayOutputStream answer = new ByteArrayOutputStream(buffer.size() + 8);
        DataOutputStream out = new DataOutputStream(answer);
        out.writeInt(2);
        out.writeInt(buffer.size());
        buffer.writeTo(out);
        return answer.toByteArray();
    }

    private static String statusOutput(Process status) {
        try {
            if (!status.waitFor(STATUS_WAIT_MS, TimeUnit.MILLISECONDS)) {
                status.destroy();
                return null;
            }
            InputStream in = status.getInputStream();
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] chunk = new byte[8192];
            int n;
            while ((n = in.read(chunk)) > 0) {
                out.write(chunk, 0, n);
            }
            return new String(out.toByteArray(), StandardCharsets.UTF_8);
        } catch (Exception e) {
            status.destroy();
            return null;
        }
    }

    /** Nœud puis enfants visibles, dans l'ordre et avec les valeurs d'AccessibilityNodeInfoDumper (`uiautomator dump`). */
    private static void walk(AccessibilityNodeInfo node, DataOutputStream out, int[] count, int width, int height,
                             int depth) throws IOException {
        if (count[0] >= MAX_NODES) {
            return;
        }
        count[0]++;
        text(out, xmlSafe(node.getText()));
        text(out, xmlSafe(node.getViewIdResourceName()));
        text(out, xmlSafe(node.getClassName()));
        text(out, xmlSafe(node.getPackageName()));
        text(out, xmlSafe(node.getContentDescription()));
        int flags = (node.isCheckable() ? 1 : 0) | (node.isChecked() ? 1 << 1 : 0) | (node.isClickable() ? 1 << 2 : 0)
                | (node.isEnabled() ? 1 << 3 : 0) | (node.isFocusable() ? 1 << 4 : 0) | (node.isFocused() ? 1 << 5 : 0)
                | (node.isScrollable() ? 1 << 6 : 0) | (node.isLongClickable() ? 1 << 7 : 0)
                | (node.isPassword() ? 1 << 8 : 0) | (node.isSelected() ? 1 << 9 : 0)
                | (node.isEditable() ? 1 << 10 : 0);
        out.writeInt(flags);
        Rect bounds = new Rect();
        node.getBoundsInScreen(bounds);
        if (!bounds.intersect(new Rect(0, 0, width, height))) {
            bounds = new Rect();  // hors de l'affichage : [0,0][0,0], comme getVisibleBoundsInScreen
        }
        out.writeInt(bounds.left);
        out.writeInt(bounds.top);
        out.writeInt(bounds.right);
        out.writeInt(bounds.bottom);
        // Profondeur et ordre de dessin : le PC sait quel élément est devant (il reçoit le toucher) et lequel est
        // derrière (une bannière sous la barre du bas n'est pas ce que l'appui touche).
        out.writeInt(depth);
        out.writeInt(node.getDrawingOrder());
        int children = node.getChildCount();
        for (int i = 0; i < children; i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null && child.isVisibleToUser()) {
                walk(child, out, count, width, height, depth + 1);
            }
        }
    }

    /** Texte comme l'écrit `uiautomator dump` : vide pour null, caractères interdits en XML remplacés par un point. */
    private static String xmlSafe(CharSequence cs) {
        if (cs == null) {
            return "";
        }
        StringBuilder out = new StringBuilder(cs.length());
        for (int i = 0; i < cs.length(); i++) {
            char ch = cs.charAt(i);
            boolean bad = (ch >= 0x1 && ch <= 0x8) || (ch >= 0xB && ch <= 0xC) || (ch >= 0xE && ch <= 0x1F)
                    || (ch >= 0x7F && ch <= 0x84) || (ch >= 0x86 && ch <= 0x9F) || (ch >= 0xFDD0 && ch <= 0xFDDF);
            out.append(bad ? '.' : ch);
        }
        return out.toString();
    }
}
