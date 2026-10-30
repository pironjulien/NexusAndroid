# Qualification native C# — 29–30 octobre 2026

La qualification porte sur les sources locales et leur exécutable .NET 10, sans Python dans le processus de
production. Les dix-huit outils conservent les schémas d'entrée, de sortie et annotations du moteur Python 0.20.3.
Le numéro de compatibilité produit reste 0.20.3 ; l'installation native est identifiée par l'empreinte des sources,
du lecteur Java et de la politique embarquée. Les builds, preuves et fichiers de test sont hors OneDrive.

## Contrôles automatisés

- `test.ps1` : Ruff, Pyright, suite Python et tests natifs construits depuis les sources courantes.
  Dernier passage complet : **1 070 réussites, huit scénarios live/émulateur hors de la suite offline**, Ruff et
  Pyright sans erreur ; les 51 contrats natifs et les 22 contrats avancés sont inclus dans ce contrôle.
- `tests/native` : 51 contrats de comportement, notamment accords exacts, sessions, mots de passe, présence,
  interruption du transport, clavier Unicode, restauration du presse-papiers, images standard et annulation vidéo.
  Les derniers cas exercent aussi un bouton bancaire hors du focus, une banque en demi-écran, la poignée Wallet,
  une lecture bancaire par une autre session pendant une vidéo et les arguments réels d'un processus pour les
  chemins Android contenant des espaces, guillemets et caractères Unicode.
- `tests/native_advanced` : 22 contrats AppFunctions, Web protégé avant/après navigation, recherche après masquage,
  cartes par appareil, permissions et différences de captures d'état.
- `tests/native_connection` : 47 scénarios du sélecteur ADB et 21 comparaisons avec l'algorithme Python ; identité
  matérielle vérifiée, USB/Wi-Fi du même téléphone, reconnexion, absence mémorisée et détection persistante du boot.
- `tests/test_native_differential.py` : 58 comparaisons de contrats et sorties typées contrôlées ; les dix-huit
  définitions MCP sont comparées intégralement à celles réellement exportées par le serveur Python.
- APK Paramètres du Fold : comparaison indépendante de 3 169 préférences, y compris les 63 titres dupliqués,
  et 407 activités autonomes. Destinations, ouvreurs parent/enfant, actions, composants, chemins, anglais et
  mots-clés préservés. Ajouts explicites : 34 ouvreurs racine et sept clés que le lecteur Python n'avait pas résolues.
- HTTP : authentification, négociation, ping, sessions isolées, suppression de session et refus GET/SSE explicite.

## Essais réels du Fold

Lectures fraîches du Galaxy Fold SM-F971B, Android 17, Wi-Fi, écran extérieur 1 248 × 1 972. Les gestes restent
locaux, sans envoi de message ni saisie de code ; un essai de lancement a été refusé pendant une présence utilisateur.

- Accueil et Paramètres : navigation, recherche, saisie exacte « Nexus été 🐱 », effacement vérifié malgré le hint
  Android et retour à l'accueil. Les références devenues périmées sont refusées.
- Lecture `phone` structurée réelle, sélection par identité matérielle et état de verrouillage fiables.
- `look(show=image)` : JPEG 182 717 octets, dimensions 1 248 × 1 972, décodé par Pillow indépendamment du moteur.
- Capture PNG pleine résolution, passage réel du masquage OCR Windows et décodage indépendant réussis.
- Vidéo de l'accueil : `record` puis `start`/`stop`, vrais MP4 H.264 720 × 1 136, analysés par FFprobe et image
  extraite par FFmpeg puis inspectée. Aucun producteur `screenrecord` ni temporaire privé Nexus restant après arrêt.
- Fichier synthétique Unicode envoyé puis rapatrié par les outils `files`, SHA-256 identique ; seul ce fichier de
  test a été supprimé du téléphone après lecture de sa destination exacte.
- Recherche `map(find=wifi)` réelle, issue de l'APK et des valeurs actuelles ; aucune modification des réglages.
- Fermeture normale du moteur de test : délai d'extinction initial restauré à 30 000 ms, état `awake.active=false`.

Ces essais ont révélé et permis de corriger deux défauts vidéo réels : `dumpsys window windows` ne publie pas le
premier plan du Fold alors que `dumpsys window` le fait ; `pkill -f` peut tuer le shell parent dont la commande
mentionne le fichier. L'arrêt cible maintenant uniquement les PID de vrais processus `screenrecord` possédant
l'argument privé exact et attend la fenêtre de finalisation MP4 de quinze secondes du moteur de référence.

## Installation et limites

### Mesure de latence ciblée

Même Fold en Wi-Fi, même client HTTP loopback, une session persistante initialisée par moteur, une lecture de
chauffe puis trois lectures mesurées ; aucun média ni contenu de réponse n'est affiché par le banc.

| Lecture | Python | C# avant correction | C# après correction |
|---|---:|---:|---:|
| `phone`, médianes de deux paires | 0,177 s avant / 0,202–0,214 s après | 1,141 s | 0,201–0,241 s |
| `look(show=elements)`, médiane de la dernière paire | 0,136 s | 0,355 s dans la première paire | 0,215 s |

Le statut natif avait multiplié les processus ADB et lisait inutilement l'arbre UI. Il lit maintenant modèle,
version, boot, posture, confiance, état et identité matérielle dans un seul lot frais. La preuve du matériel et du
boot est validée contre l'identité enregistrée avant toute réponse ; aucun ancien contenu n'est renvoyé en cache.
Les autres appels vérifient leur transport courant par une preuve fraîche groupée plutôt que par une découverte
complète répétée. L'identité n'est réutilisée que pendant l'opération tenant le verrou du téléphone, puis effacée.

`look` garde environ 80 ms de coût supplémentaire dans cette courte mesure : il vérifie matériel et boot par un
aller-retour frais à chaque appel, là où le Python réutilise sa sélection établie. Aucune accélération générale
n'est revendiquée. Les premières lectures sont conservées séparément dans les reçus locaux ; elles ne constituent
pas une comparaison équitable du démarrage, notamment si la première lecture lève un verrou sans code.

`install.ps1 -Runtime Native` publie un runtime immuable sous `%USERPROFILE%\.nexus\nexus-android\native`, puis
inscrit sa spécification. Ce chemin partagé évite la redirection AppData des clients MSIX : le superviseur
indépendant et les trois applications IA retrouvent le même exécutable. Le superviseur coordonne son activation ;
l'installeur n'arrête aucun service en cours.
`-Runtime Auto` conserve ce choix ; `-Runtime Python` reste un retour explicite au moteur de référence.

### Activation livrée le 30 octobre

Le superviseur a été redémarré de manière coordonnée à **00 h 48 min 53 s, heure de Paris**. Le processus natif
Android **34428** est chargé depuis le runtime configuré, sur le port **16180** :

- exécutable : `%USERPROFILE%\.nexus\nexus-android\native\9679ec50fd231094\NexusAndroid.exe` ;
- SHA-256 : `EDAF07B5A0DAC284DF1823FA98B096751666FD185A28A7739348F9E399C45C51` ;
- moteur : 0.20.3, sources `64f844d` ; installateur partagé corrigé dans `9388883`.

Le reçu de production du **30 octobre à 00 h 59 min 18 s, heure de Paris** confirme `runtime=native`, PID 34428,
la découverte des **18 outils** par une nouvelle session MCP HTTP authentifiée et un vrai appel `phone` réussi
sur le Fold **SM-F971B en Wi-Fi**. La session de qualification a ensuite été fermée avec le statut HTTP **204**.
Preuve locale : `%USERPROFILE%\.nexus\nexus-android\verification\production-native-20261030.json`.
L'exécutable réellement chargé, la spécification et l'empreinte ont été recoupés lors de la mise à jour du rapport.

Le client MCP du chat Codex déjà ouvert conserve cependant son ancien transport et a retourné
`TransportSenderror`. Le reçu marque donc `current_chat_transport_reload_required=true` : une relance de Codex
reste nécessaire pour ce client. La nouvelle connexion HTTP de production est vérifiée ; aucun appel Android
réussi depuis le client MCP historique de ce chat n'est revendiqué.

Les essais réels portent sur un seul Fold et son écran extérieur, dans son état logiciel actuel. Aucun envoi réel,
appel, modification de compte, suppression d'application, banque ou gestionnaire de mots de passe n'a été utilisé
comme test. Ces parcours sensibles sont éprouvés sur des transports isolés. Les variantes USB, reconnexion après
redémarrage physique et autres matériels sont couvertes par les contrats contrôlés, sans revendication d'essai
physique universel. Une découverte MCP isolée ne prouve pas le renouvellement du cache d'un chat Codex déjà ouvert.

Un essai vidéo complémentaire a été écarté : le dernier premier plan du téléphone était une autre application
que l'accueil attendu. Son contenu n'a pas été ouvert ni utilisé comme preuve. La suppression de ce seul fichier
de cache généré a été refusée par le contrôle automatique (« blocked by policy ») ; il reste local sous
`%LOCALAPPDATA%\NexusAndroid\live-qa\native-fold-frozen-20261030.mp4`. Aucun contournement du refus n'a été tenté.
L'accueil a ensuite été rétabli et vérifié, sans frappe ni envoi.
Le driver QA exige désormais un `expect_foreground` explicite et une lecture `phone` immédiatement précédente
cohérente : même application, modèle identifié, écran allumé et déverrouillé. Une différence refuse l'essai avant
de demander le média. Huit régressions du driver couvrent ces préconditions sans téléphone ni effet de bord.
