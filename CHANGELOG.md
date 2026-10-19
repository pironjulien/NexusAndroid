# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-10-19

### Performance

- Message, contact, call log and calendar reads no longer start a Java process on the phone for each `content
  query` (about 1 s per wave of queries). At the first read the server pushes a 7 KB helper
  (`phone/NexusQuery.java`, built into `src/nexus_android/nexus-query.dex` by `phone/build.ps1`) to
  `/data/local/tmp` and starts it once with `app_process` under the shell identity, reached through `adb forward`
  on a local abstract socket: the scrcpy server's method (mobile-mcp's device server works the same way; DroidMind
  stays on plain `adb shell`). No app to install. It runs each query with the code of Android's own `content`
  command (`/system/framework/content.jar`), so the output is identical to the character. Measured on a Galaxy
  Fold over USB: conversation list 3.1 s → 0.07 s (0.14 s from a fresh process, 0.73 s when the helper must first
  be pushed and started), one conversation 2.1 s → 0.03 s.
- One port forward per phone, reused by every process and after a daemon restart.

### Security

- The helper is read-only (only `query` is served; insert, update, delete and call are refused), answers only
  connections from the shell user or root (adbd; kernel-checked peer credentials, which no app on the phone can
  have), and stops by itself after 10 minutes without a request. Each dex version has its own socket name, so an
  old helper is never taken for the current one; older ones are stopped and removed.
- If the helper cannot be started or answers badly, reads fall back to the 0.5 method (`adb shell`), with the same
  output and the same masking of security codes, and the helper is retried a minute later.

### Changed (Codex plugin, 2026-10-19)
- `install.ps1` no longer writes `[mcp_servers.nexus-android]` to Codex's `config.toml`: Codex gets the server from a
  personal Codex plugin.

## [0.5.1] - 2026-10-19

### Security

- Shared policy (`garde-fous.json`) synced with NexusHarness: every "Réinitialiser…" / reset button needs
  `confirm=true` again, including the bare "Réinitialiser" confirmation button of One UI and Windows and
  "Réinitialiser les paramètres réseau". Only harmless resets stay free (filters, zoom, view, sort, search, layout,
  form, fields, text, selection, colours). This reverts the 0.5.0 narrowing, which let the final One UI reset button
  through without confirmation.

## [0.5.0] - 2026-10-19

### Performance

- `messages()` is about 5 times faster. Every `content query` starts a Java process on the phone (about 1 s), and
  the tool used to run them one after another: 34 for the conversation list, 7 to 11 per conversation. Queries now
  run in parallel on the phone inside a single `adb shell` (30 cost about as much as one), the phone returns only the
  last N rows (`LIMIT`), MMS parts and addresses are chained on the phone instead of going back to the PC, and
  contact names are cached for 10 minutes, shared by all sessions of the daemon. Measured on a Galaxy Fold over USB:
  conversation list 35 s → 4.6 s (3.1 s with names cached), one conversation 7-11 s → 2.1 s, with 4 images
  11.6 s → 3.3 s, by contact name 11.4 s → 4.3 s. Output is byte-for-byte identical, security codes still masked.
- `info(kind="calls")` reads only the last N calls and resolves their names in one exchange.

### Security

- Shared policy (`garde-fous.json`) synced with NexusHarness: "reset" needs `confirm=true` only for the whole device
  ("Réinitialiser le téléphone", "Réinitialisation d'usine"); other "Réinitialiser…" buttons inside an app are free.

### Changed (Claude Code plugin, 2026-10-19)
- `install.ps1` no longer registers the server with Claude Code and never calls a Claude CLI: Claude Code gets
  `nexus-android` from a Claude Code plugin (shared daemon URL, token read from an environment variable).

### Fixed

- `install.ps1`: without `claude` on the PATH, Claude Code was skipped on a new PC. Falls back to the `claude.exe`
  shipped with the Claude desktop app, like NexusClaude and NexusBrowser.

### Changed

- Repository renamed to `pironjulien/NexusAndroid`; GitHub redirects the old address.
- Published on PyPI (`nexus-android`) and in the official MCP registry (`io.github.pironjulien/nexus-android`);
  the quick start installs with `uvx --python 3.14 nexus-android`.

### Added

- Shared daemon mode: `nexus-android --http [port]` serves MCP Streamable HTTP on 127.0.0.1 (default port 16180),
  so Claude Code, Codex and Antigravity share one process instead of one per session. A bearer token is required
  (`NEXUS_MCP_TOKEN` or `NEXUS_MCP_TOKEN_FILE`, 32 characters or more); each MCP session keeps its own `[N]`
  numbering and conversation list, while the ADB link and the phone lock stay shared. Clients reconnect on their
  own after a daemon restart.
- `install.ps1` declares the daemon to the Nexus daemon supervisor and registers `nexus-android` by URL for Claude Code,
  Codex and Antigravity (instead of a stdio process per session).
- README: animated demo recorded from the real tool on a real phone (docs/assets/demo.webp).

## [0.4.1] - 2026-10-18

### Security

- Wiping, resetting or formatting the whole device now needs `confirm=true`: "Reset phone", "Reset device",
  "Reset tablet", "Reset PC", "Formater le téléphone", "Formater l'appareil", "Format phone" (shared policy). "Tout
  effacer", "Effacer tout" and "Tout supprimer" need it on a reset screen (factory reset, erase all data) and stay free
  elsewhere (calculator, notifications). One test per label.

## [0.4.0] - 2026-10-18 — Shared safeguard policy

Fixes from the 18/10 audit, aligned on the safeguard agreement between Claude and Antigravity.

### Security

- Confirmation labels now come from the policy file shared with the desktop tools (`garde-fous.json`, embedded copy;
  a test checks it is identical to the shared one in the author's workspace). Every listed form is caught, accents
  and case ignored: "Envoyez", "Payez", "Paiement", "Achetez", "Commandez", "Publish", "Purchase", "Checkout",
  "Virer" no longer pass without `confirm=true`. One test per pattern.
- A clickable icon with no name at all in a messaging, mail, social or phone app (often the send or call button,
  whose resource id does not always say so) needs `confirm=true`, `x,y` taps included.
- `look` no longer sends the image when the UI tree cannot be read (security codes could not be blacked out), unless
  `confirm=true`.
- `open_url` refuses a link that Android would open in a protected app (banking, payment, identity), or that offers
  one in its app chooser.
- The lock state and the foreground app are read in the same round trip as the UI tree, just before each targeted
  gesture: an app opened by the previous step of a batch is seen before the next one.

### Changed

- Aligned on the shared policy: deleting (to the trash), accepting, sharing, validating and allowing no longer need
  confirmation; formatting, cancelling a subscription and making a repository public do. Calling stays under
  confirmation (a real call on a phone; added to the shared policy). A generic
  "OK"/"Validate" needs it on a screen about a payment or a format. An explicit order from the user for a gesture
  counts as confirmation (tool descriptions and refusals say so).
- Protected apps are readable: `scroll` and `wait` are free in them; taps, typing and swipes need `confirm=true`
  (navigation on the user's order); a money or send button, or an unnamed icon, is refused there even with
  `confirm=true`: the user does it.
- Faster batches: phone state read once per `act` batch, in one adb round trip instead of three, then together with
  each UI read. Measured on a Galaxy Z Fold over Wi-Fi (6 steps): 28 → 8 adb round trips, 11.0 s → 9.9 s.

### Added

- Structured output (`outputSchema` and `structuredContent`) for `phone`, `info` and `settings`, next to the text.

## [0.3.2] - 2026-10-18

Prepared for the first release on PyPI and the official MCP registry.

### Added

- MCP tool annotations on every tool (`title`, `readOnlyHint`, `destructiveHint`, `idempotentHint`,
  `openWorldHint`), so clients can tell read-only tools from the ones that act.
- Startup test: the server answers `initialize` and `tools/list` with no phone and no adb, on Windows and on Linux
  (new Linux CI job), as MCP directories check it.
- PyPI packaging: project URLs, keywords, classifiers, `mcp-name` line for the official MCP registry, and a
  `publish.yml` workflow using Trusted Publishing (no token in the repository), triggered by a GitHub release.
- `glama.json` (maintainer claim on Glama).
- README: Privacy section (what is read, where it goes, what is stored, how to remove it); absolute image and file
  links so the README also renders on PyPI.

- README: quick start with a one-line `uvx` install straight from GitHub, Codex and JSON client configs, example
  prompts, architecture diagram, badges and banner.
- GitHub Actions workflow running `test.ps1` on Windows; `SECURITY.md` with private reporting.

### Changed

- README: "Part of the Nexus family" line with links to NexusWindows (Claude, Codex, Antigravity editions),
  NexusBrowser and their project pages.

## [0.3.1] - 2026-10-17 — First public release

### Changed

- Ready for anyone's phone: messages, docs and tool descriptions speak of "the user" instead of the author.
- `test.ps1` works from any copy of the repository: each checkout gets its own test environment (created on first
  run) and never touches the environment of the installed server.
- Docs and changelog in English; MIT license.

### Security

- Test fixtures anonymized (phone numbers, serial number, display identifier, Wi-Fi addresses, location, contact and
  computer names).
- Protected apps: the apps of the main French retail banks added (Crédit Agricole, BNP Paribas, Société Générale,
  Caisse d'Epargne, Banque Populaire, Crédit Mutuel, CIC, LCL), package names checked on Google Play.

## [0.3.0] - 2026-10-17 — More power once plugged in, safer over Wi-Fi

Everything goes through ADB, with no app on the phone.

### Added

- Typing any text, accents and emoji included: pasted through the clipboard by the scrcpy server (version and SHA-256
  pinned, downloaded by `install.ps1`), started for the paste then stopped; the previous clipboard is put back.
  Verified on the Fold ("Réponse été à ç ô 🧪" read back in the field).
- `notifications`: notifications on screen (app, time, title, text), codes masked; `all` adds ongoing ones; apps of
  recently dismissed notifications.
- `files`: shared storage only (`/sdcard`); list (newest first), pull to the PC (`~\Downloads\Telephone`), push to
  the phone; never overwrites, never deletes.
- `info`: last known location (accuracy, age, Maps link), recent calls, a contact's numbers, calendar.
- `settings`: read, or change then read back, Wi-Fi, Bluetooth, mobile data, location, NFC, do not disturb,
  brightness, media volume; turning Wi-Fi off is refused when the link goes through it.
- `phone` says whether the Fold is folded, half open or open, and whether it rebooted since the last contact.
- Screen kept on while the agents work: at the first gesture on a phone the user unlocked, the screen timeout goes
  to 30 min; 2 min after the last gesture of any agent, the original value comes back and the screen turns off (the
  phone locks again). The screen is left to the user if they are holding the phone. Original value saved once,
  shared by all servers, restored at startup after a crash. The agent never unlocks the phone.

### Security

- Link lost during a gesture: reported as `?` (effect unknown, look again before redoing), no longer as a plain
  failure that would invite a retry and a double send. "adb: device offline" is recognized.
- No gesture while the user is holding the phone: waits up to 10 s, then refuses.
- `x,y` targets refused if the Fold was folded or unfolded since the last image.
- All-black image (protected screen) reported, pointing to the readable elements.
- Other control tools left running on the phone (mobile-mcp, Appium, Shizuku, scrcpy) named by `checkup` and in the
  "UI tree unavailable" error.
- Guard on every gesture aimed at a spot (tap, typing, clearing, swipe, scroll), not only taps. The dangerous label is
  searched anywhere in the text ("Delete all", "Yes, send", "I accept"), accents and curly apostrophes included, with
  a longer list (order, pay, trash, allow, cancel subscription, share…).
- The target is judged on the screen read just before the gesture, not on the last `look`. A changed `[N]` is
  refused; nothing readable at the spot: refused without `confirm=true`. The clickable button around a label is
  judged too.
- Keys: closed list (back, home, recents, delete, ctrl+a, arrows, tab, volume…). Enter, call, hang up and any other
  `KEYCODE_*` need `confirm=true`, like a line break in `type`.
- Protected apps also when they share the screen (Fold); unknown foreground app: no gesture without `confirm=true`.
  Longer list of banking, payment, crypto, identity and authenticator apps, package names taken from Google Play and
  the Galaxy Store, plus a pattern safety net (bank, wallet, pay, crypto, auth).
- Typing refused into a password field, when no field has the focus, or when the lock state cannot be read.
- Codes masked more reliably: "482913, valid 10 min", letter-and-digit codes, 9 digits and more, a code alone under a
  notification title; masked before `query` and `wait`, and blacked out on the image.
- Only the owner's phone is driven (serial number recorded at the first USB connection). Emulators, TV boxes and other
  phones are ignored, and `wifi.json` is never rewritten with another device.
- One agent at a time drives the phone: lock shared by all servers (`~\.nexus\android.lock`), 30 s wait at most then
  a clear message. UI tree file private to each server, deleted as soon as read.

### Fixed

- Sleeping Wi-Fi link ("device offline"): reconnected (`adb reconnect offline`) before looking for the phone elsewhere.
- Truncated screenshot over Wi-Fi: retried up to three times.
- Phone not found: the message reminds that Android turns wireless debugging off after a reboot or a network change.
- "100%" was typed "100\%" and "%s" was impossible: typing now follows Android's `input text`.
- False successes: ✓ means the effect was read back; otherwise "~ sent, not verified". Failures of `launch`,
  `open_url` and `draft` reported.
- `draft(to="36179")` taken for a conversation, target "12" taken for `[12]`: only "[N]" designates a conversation or
  an element.
- Type checking (Pyright) added to `test.ps1`.
- Wi-Fi reconnection stuck when the wireless debugging port changed: the stale address is dropped and the address
  announced over mDNS is used.

## [0.2.1] - 2026-10-14

### Fixed

- RCS groups shown as `…@rcs.google.com`: members found from the addresses of the thread's messages, and sender named
  for each message received in a group.
- Conversations missing from the Google Messages list (archived or trashed) labelled as hidden in the app.

## [0.2.0] - 2026-10-14

### Added

- `messages(contact="Alice")`: conversation found by contact name, up to 500 messages.
- MMS/RCS photos attached when reading (`images`, 4 by default, 12 at most); videos, sounds and files reported.
- `draft` accepts a contact name; sending only after an explicit "send" from the user for that draft (tap Send with
  `confirm=true`).
- Wi-Fi: if the wireless debugging port changed, the new port is searched on the phone's address (about 5 s) and
  recorded.

### Fixed

- Contact name polluted by columns added by the Android provider.
- The SMIL part of MMS (layout) is no longer reported as an attachment.

## [0.1.1] - 2026-10-14

### Fixed

- Cable-free Wi-Fi: One UI does not announce the phone over mDNS. At each USB connection, the server records its Wi-Fi
  address and adbd's TLS port (`~\.nexus\nexus-android\wifi.json`), then reconnects to it once the cable is removed.
- Other Android devices on the network (TV box) ignored when the known phone is reachable; `NEXUS_ANDROID_SERIAL`
  accepts a Wi-Fi address and connects to it.

## [0.1.0] - 2026-10-14

### Added

- MCP server `nexus-android`, shared by Claude Code, Codex and Antigravity: `phone`, `look`, `act`, `launch`,
  `messages`, `draft`, `checkup`.
- ADB link over USB first, then local Wi-Fi with a paired phone, found over mDNS.
- Capture of the Fold's active display, inner or outer, and numbered UI tree with coordinates in the image.
- SMS and RCS/MMS read through Android's messaging provider; contacts resolved and security codes masked.
- Replies prepared without sending: SMS/RCS in the default SMS app, or WhatsApp, accents included.
- Safeguards: confirmation required for irreversible buttons, financial and identity apps read-only, password fields
  excluded, no gesture on a locked phone.
- `install.ps1`: environment outside the sources, registration in the three clients, tests and `-Live` run on the
  phone.

[0.3.1]: https://github.com/pironjulien/android-computer-use/releases/tag/v0.3.1
