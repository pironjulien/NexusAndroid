// Serveur de lectures gardé en vie sur le téléphone : chaque `content query` lancé par adb shell démarre sa propre
// machine Java (environ 1 s) ; ici une seule machine, démarrée par app_process sous l'identité shell (comme le serveur
// de scrcpy), exécute les requêtes en parallèle avec le code même de la commande `content` (/system/framework/
// content.jar) : sorties identiques au caractère près. Rien à installer, aucune permission en plus de celles d'adb.
//
// Lancement : CLASSPATH=<ce dex>:/system/framework/content.jar app_process / NexusQuery <socket> <secondes d'inactivité>
// Joint par adb forward tcp:0 localabstract:<socket>. Garde-fous : seules les connexions de l'utilisateur shell (adbd)
// ou root sont servies (identité donnée par le noyau, SO_PEERCRED : une application du téléphone ne peut pas l'avoir) ;
// seul le sous-commande `query` (lecture) est acceptée ; arrêt après le délai d'inactivité.
//
// Protocole (entiers sur 4 octets, gros-boutiens ; textes en UTF-8 précédés de leur longueur) :
//   PC → téléphone : "NXQ1", nombre de requêtes, puis pour chacune : nombre d'arguments, arguments
//   téléphone → PC : "NXQ1", puis pour chaque requête dans l'ordre : sortie (stdout et stderr mêlés)

import android.net.LocalServerSocket;
import android.net.LocalSocket;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.io.PrintStream;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

public final class NexusQuery {
    private static final byte[] MAGIC = "NXQ1".getBytes(StandardCharsets.US_ASCII);
    private static final int MAX_QUERIES = 512;
    private static final int MAX_ARGS = 32;
    private static final int MAX_ARG_BYTES = 64 * 1024;
    private static final int SHELL_UID = 2000;

    // Sortie de la requête en cours, propre à chaque fil : la commande `content` écrit dans System.out/err.
    private static final ThreadLocal<ByteArrayOutputStream> CAPTURE = new ThreadLocal<>();
    private static volatile long lastUse = System.currentTimeMillis();

    private static final class Dispatch extends OutputStream {
        private final OutputStream fallback;

        Dispatch(OutputStream fallback) {
            this.fallback = fallback;
        }

        private OutputStream target() {
            ByteArrayOutputStream buffer = CAPTURE.get();
            return buffer != null ? buffer : fallback;
        }

        @Override public void write(int b) throws IOException { target().write(b); }
        @Override public void write(byte[] b, int off, int len) throws IOException { target().write(b, off, len); }
        @Override public void flush() throws IOException { target().flush(); }
    }

    public static void main(String[] args) throws Exception {
        String name = args[0];
        long idleMs = Long.parseLong(args[1]) * 1000L;
        Method content = Class.forName("com.android.commands.content.Content").getMethod("main", String[].class);
        System.setOut(new PrintStream(new Dispatch(System.out), true, "UTF-8"));
        System.setErr(new PrintStream(new Dispatch(System.err), true, "UTF-8"));
        LocalServerSocket server = new LocalServerSocket(name); // nom déjà pris : une autre instance sert déjà
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
            }
        });
        watchdog.setDaemon(true);
        watchdog.start();

        while (true) {
            LocalSocket client = server.accept();
            lastUse = System.currentTimeMillis();
            Thread handler = new Thread(() -> serve(client, content, pool));
            handler.setDaemon(true);
            handler.start();
        }
    }

    private static void serve(LocalSocket client, Method content, ExecutorService pool) {
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
                results.add(pool.submit(() -> run(content, argv)));
            }
            DataOutputStream out = new DataOutputStream(socket.getOutputStream());
            out.write(MAGIC);
            for (Future<byte[]> result : results) {
                byte[] data = result.get();
                out.writeInt(data.length);
                out.write(data);
            }
            out.flush();
            lastUse = System.currentTimeMillis();
        } catch (Exception e) {
            // client parti ou requête mal formée : connexion fermée, le serveur continue
        }
    }

    private static byte[] run(Method content, String[] argv) {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        if (!"query".equals(argv[0])) { // lecture seule : ni insert, ni update, ni delete, ni call
            byte[] refused = "Error: only `query` is served\n".getBytes(StandardCharsets.UTF_8);
            return refused;
        }
        CAPTURE.set(buffer);
        try {
            content.invoke(null, (Object) argv);
        } catch (InvocationTargetException e) {
            e.getCause().printStackTrace();
        } catch (Throwable e) {
            e.printStackTrace();
        } finally {
            System.out.flush();
            System.err.flush();
            CAPTURE.remove();
        }
        return buffer.toByteArray();
    }
}
