# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
