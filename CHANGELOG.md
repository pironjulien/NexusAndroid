# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.12.0] - 2026-10-25

### Added

- Claude Desktop extension: `nexus-android.mcpb` (MCPB, server type `uv`) attached to every release, the host
  installing the same version from PyPI. Download link and *Install* buttons for VS Code and Cursor at the top of
  the README.
- Compatibility list: what has been tested (Galaxy Fold `SM-F971B`, Windows 11) and what is only expected, with a
  device report form for issues.

## [0.11.0] - 2026-10-25

### Added

- `setup` declares the server to the AI apps found on the PC: Claude Code (`claude mcp add --scope user`, nothing
  when a nexus-android plugin is enabled), Claude Desktop (classic and Store installs), Codex
  (`[mcp_servers.nexus-android]`, nothing when a nexus-android plugin is enabled) and Antigravity (a
  `nexus-android` plugin with the server and the agent guide, never a global entry; an older global entry of ours is
  removed). Each runs `uvx --python 3.14 nexus-android==<version>`; entries written otherwise by the user are kept,
  every edited file is backed up in `~/.nexus/nexus-android/backups` first.
- Nexus pack mode: `NEXUS_PACK=1` (or `setup --no-wait`) gets `adb` and declares the server without any question or
  waiting for the phone; exit code 0 on success, last line `NEXUS_PACK_RESULT Client=state; ...`.
  `--no-register` leaves the AI apps alone.
- The Antigravity plugin and the agent guide ship inside the package (`nexus_android/plugin`).

## [0.10.0] - 2026-10-25

### Added

- `adb` is downloaded when the PC has none: the server takes the current stable Platform Tools for this system from
  Google's SDK repository (the manifest `sdkmanager` reads), checks the size and SHA-1 Google publishes and unpacks
  them in `~/.nexus/nexus-android/tools/platform-tools`. An installed `adb` comes first (`NEXUS_ANDROID_ADB`, `PATH`,
  winget, Android SDK); `NEXUS_ANDROID_ADB_DOWNLOAD=0` turns the download off.
- `nexus-android setup`: first connection guided in a terminal. It gets `adb`, shows the USB debugging steps, waits
  for the phone (authorization prompt included) and registers it for this PC.
- `nexus-android --version`.

### Changed

- No phone set up yet: the error now lists the steps for the agent to relay (Build number, USB debugging, data
  cable and File transfer, the authorization prompt) with Android's official guide and the makers' USB drivers.
  Unauthorized phone: what to do when the prompt does not show. Linux "no permissions": the udev rules to add.
- README: Quick start rewritten for beginners (uv, `setup`, one command per client, Antigravity's raw config);
  every `uvx` command runs the tested Python 3.14.

## [0.9.12] - 2026-10-25

### Added

- `NEXUS_JOURNAL=0` (also `off`, `false`, `no`) in the server's environment turns the shared journal off: nothing
  written, no folder created.

### Changed

- CI: the private development copy tests Python 3.14 only and skips Scorecard; the public repository keeps the full
  3.12–3.14 matrix. A newer push cancels the running test (Windows minutes count double on private repositories).
  The computed Python version reaches the scripts through the environment, never expanded inside them.
- Workspace reorganisation: the shared guardrail policy, the shared image rule and the comparison benchmark moved to
  their new locations; paths and links updated accordingly.
- Skill: `look` is described as text first (element list alone; the image is joined only when needed or asked for).
- The MCP session to AI mapping used by the journal keeps the 32 most recent sessions, like the per-session screen
  views (it grew without limit on the shared daemon).
- Real-phone smoke run, `pytest` marker and the `map note` error message in English.
- Benchmark: the test environment path is computed like `test.ps1` does instead of a fixed key.

### Fixed

- `install.ps1` no longer fails on a new machine when it runs the offline tests: it first prepares the development
  dependencies in the uv cache (online), and skips the tests with a warning when Node.js is missing.
- Real-phone smoke run counts unread conversations with the `UNREAD` marker the server actually writes.
- README, Privacy: the journal written since 0.9.8 under `%LOCALAPPDATA%\NexusJournal\android\` is documented (what
  it holds, how to delete it or turn it off); it used to say no log was written.
- Text first: server instructions, `act`, `draft`, `launch`, skill and README no longer promise the image after an
  unverified step (`~`). Since 0.9.10 it is joined only when the screen did not change; otherwise
  `look(show="both")`. Server instructions and `act` stay under 2048 characters.
- Tests: the wait for the user to let go of the phone is checked every 0.25 s, and MMS photos are read in one batch
  then one by one when missing (EXIF orientation applied, unreadable ones flagged); the journal tests no longer leak
  session state.

## [0.9.11] - 2026-10-24

### Fixed

- Changelog of 0.9.9: the text-first benchmark is 12 out of 12 in each mode (24 trials in total), and the token
  saving is measured in uncached tokens for Codex (13,050 instead of 32,314), as in the shared benchmark record.

## [0.9.10] - 2026-10-24

### Changed

- `key home` and `key back` are read back from Android's own state (`dumpsys`), never from screen text or the UI
  tree: home is `✓` when the launcher is in the foreground; back is `✓` when another app or another activity of the
  app comes to the front, both known before and after (a ticking counter, a clock or a tree that becomes readable is
  no proof). Otherwise `~` says what was seen: same app and activity (nothing to go back to, ignored, or a page
  inside the activity, not compared). Nothing is read through accessibility, so protected apps stay untouched.
- Text first after a gesture that cannot be read back (`~`, such as a tap): the image is joined when the final screen
  is the one read during that step, before its gesture (nothing changed), or when that step read nothing; a changed
  screen shows the effect in the list. A failed step (`✗`) or a lost link (`?`) still joins the image.

## [0.9.9] - 2026-10-24

### Changed

- Text first by default (shared Nexus image rule, section 2): `look`, `act` and `launch` return the element list
  alone; the image is joined when a step is not read back (`~`, `✗`, `?`), when the screen has no readable element
  (canvas, game, video), for a `region` zoom, or on request (`show`/`then` = `both` or `image`). Measured on a real
  Galaxy Z Fold before switching: 4 read-only tasks, each with and without the image, by Claude, Codex and
  Antigravity: 12 out of 12 in each mode (24 trials), with fewer tool calls (Claude: 13 instead of 18) and fewer
  uncached tokens for Codex (13,050 instead of 32,314).

## [0.9.8] - 2026-10-24

### Added

- Shared Nexus tool journal (same format as Nexus Windows, Browser and Relay): one line per gesture of a batch and per
  tool call in `%LOCALAPPDATA%\NexusJournal\android\<agent>\<YYYY-MM>.jsonl`, read by the monthly review. The calling
  AI is recognised from its MCP client name (then the session it opened, then its User-Agent); an unknown client of
  the shared daemon is logged for nobody. `app` is the package name only; the error is a fixed cause from a closed
  list, never a piece of the message (even its start can carry a contact name, a number or a path); no screen text,
  SMS, typed text or code. Lines go through a bounded queue written by a separate thread: a slow disk never holds a
  gesture or the phone lock; a full queue drops the line. A batch refused before its first step gets one `act` line.

## [0.9.7] - 2026-10-24

### Changed

- Images sent to the agent follow the shared Nexus image rule: longest side at most 1568 px and at most 1.15
  megapixels (never upscaled), WebP lossless when it fits in 200 KB, otherwise quality 85 then 70, never above 500 KB
  (Antigravity rejects larger requests). On the Fold: Settings 45 KB, home screen 67 KB (JPEG before: about 72 and
  105 KB), 853×1348 instead of 810×1280. MMS photos too, turned upright from their EXIF orientation.
- A look whose image is the same as the last one this client received (same area, same [N] marks, same screen size,
  same content including the system bar and text read by OCR, and a 64×64 colour fingerprint within noise) does not
  send it again and says "image unchanged"; coordinates keep the scale of the image the agent has. A client of the shared
  daemon without its own session always gets the image (another agent may have received the last one).

## [0.9.6] - 2026-10-24

### Changed

- Faster on the phone without dropping any safeguard: the screen is read once it has been still for 500 ms
  (UiAutomator's own idle wait) instead of 1 s; the wait while the user touches the phone ends as soon as they let go
  (checked every 0.25 s instead of every second); no pause after the last step of a batch (the pause before
  re-reading the screen stays); accented text is pasted through the scrcpy server already open for gestures instead
  of starting one for each paste (about 2 s saved, the user's clipboard is still given back). Measured on the Fold
  (8 rounds): opening Settings 5.7 s to 1.8 s, reading the screen 1.2 s to 0.7 s, a full Settings walk-through
  19.6 s to 13.8 s. Typing keeps its checks: the field is read before and after.
- `launch` takes `then` like `act` (`both` by default: image and elements; `elements` or `none` when the agent does
  not need the image).

### Fixed

- A named target half hidden behind another clickable drawn in front of it (Settings' floating search bar over the
  last row) is touched where it shows, not at its center, which hit the bar; with no free part, it is refused ("scroll it
  into view first"). A label drawn on its own button is still touched there.

## [0.9.5] - 2026-10-24

### Security

- Screen recording: the foreground watch now starts the moment `screenrecord` is launched, before its pid is read
  and its start check (up to about 11 s). A banking, payment or identity app shown briefly while the recording was
  starting was filmed without being seen, and that time was counted as watched; it now discards the video (start
  refused, file erased from the phone), and unwatched time is counted from the launch. A recording whose pid is not
  read yet is stopped by finding its own `screenrecord` process (never `pkill -f`, which also matches the shell
  citing the file). When the encoder refuses the 720p size, the retry gets its own file and its own unwatched time,
  and the failed try's pending reading is settled first: a late bank reading refuses the start instead of erasing
  the retry's file. Codex reviews of 0.9.4 and 0.9.5.

## [0.9.4] - 2026-10-23

### Fixed

- `test.ps1` now forces uv offline and forbids automatic Python downloads. Missing interpreters or cached locked
  dependencies fail with preparation instructions. Pyright uses its bundled JavaScript and an already installed
  Node.js, avoiding its Python wrapper's update checks and automatic Node installation. CI prepares the cache
  separately before the offline check; the Python matrix and real-phone option are preserved.

- Screen recording: the end of `screenrecord` is now watched every 50 ms (a free local check) instead of only at the
  next foreground reading (every second). A banking app read by a tool just after a recording ended on its own no
  longer discards the sound video; the remaining uncertainty, under 50 ms, still discards it to be safe (Codex
  review, round 4). The end is watched in its own thread, so a slow foreground reading (up to 5 s of ADB) no longer
  delays it, and a reading that started during the recording but returns after its end no longer counts the wait
  after the end as unwatched time (a sound video was discarded) (Codex review of 0.9.4).

### Changed

- Development dependencies: pyright 1.1.407 to 1.1.414; build backend accepts uv_build up to 0.13.x.
- Pillow 12.3 to 13.0 (minimum raised to 13.0.0); ruff 0.17.0 to 0.17.2. Checked under Python 3.12 and 3.14 and on
  the real phone (screenshot, reading, prepared reply with accents, no sending).

## [0.9.3] - 2026-10-20

### Fixed

- Windows installer of the maintainer's own setup no longer creates the shared daemon token when it is missing (it stops and points to the sync script, the only place the token is created).

- Screen recording: readings and recording start and end times are dated with a fine clock (`time.perf_counter`).
  Under Python 3.12 on Windows, `time.monotonic` only ticks every ~16 ms, so a banking app read just after a video
  ended got the same date as that end and wrongly discarded a sound video (CI failure under Python 3.12 only).

## [0.9.2] - 2026-10-20

### Security

- Threat model written down (README, Safeguards): the agent must never be able to make an outward action (send,
  call, pay, publish, transfer) leave that differs from the one the user approved. The contract sits at the boundary
  of each physical injection: every tap (each tap of a double tap), every swipe of a scroll and every submit key is
  sent right after its own fresh reading of the screen, judged on it and compared with the approved identity. A
  change faster than the gap between that reading and the ADB injection is stated as out of reach.
- One canonical identity of the approved action, the same however the target is aimed (tree, coordinates, OCR):
  foreground app, what the tree elements say in document order (texts, or a keyed seal of the raw text when masked:
  HMAC with a per-process secret, never shown), and the complete target of the gesture (resource id, bounds and
  drawing order of the element that receives the tap, the tap point, start and end of a swipe or drag, direction and
  count of a scroll, the field that has the focus for a key). A token for one IBAN or card no longer sends another
  that masks the same way, two fields that swap their values or another foreground app with the same texts are
  refused, a token for one of two Send buttons no longer taps the other, a swipe token no longer works with another
  end point, a token for Enter no longer works once the focus moved, and two stacked buttons whose drawing order
  changed are not the same action. Texts are bound raw (leading and trailing spaces included), selected states like
  checked ones. The token is bound to that identity only; the readable summary (recipients,
  content, attachments) is shown with it but is not compared, so a 2-pixel shift that moves a text from the header
  to the body does not void it.
- A submit key (Enter and equivalents) must be alone in its step (`enter enter`, `tab enter` are refused). After a
  key that can move the focus (tab, arrows), the focused field and the app are read again before the next one:
  `tab delete` no longer clears the next field when it is a password.
- `type` only types into an editable field that has the focus, even with `confirm=true` (a focused Send button would
  be clicked by a typed space), and every control character (line break, tab) is pasted as text, never sent as the
  Enter or Tab key; the same holds for routines and their parameters.
- A scroll is judged where the finger really lands (its start, a third of the screen away from the aimed point), on
  what the finger actuates there (a button or a slider, not a text bubble it crosses); its end is judged too when the
  start is on a slider. A scroll without `confirm` no longer starts on a "slide up to send" slider; in a banking,
  payment or identity app, a scroll whose start is on a "slide to pay" slider is refused even with `confirm=true`.
  With an accord, one scroll per step. Only up, down, left and right are scroll directions (in and out made a
  motionless swipe, a disguised tap).
- The second tap of a double tap is judged again after the first, on a quick reading of the screen that does not
  wait for it to settle (`--quiet 0` in the phone reader, about 120 ms measured on the Fold); if that reading fails,
  has no tree or takes more than 250 ms, the second tap is not sent and the agent is told that the first one was.
  Without the phone's quick reader, a double tap is refused before any tap. No double tap on a send, call or pay
  button.
- What the UI tree does not describe is never bound to an accord: a target found only by OCR, a drawn surface of any
  size over the target or the recipient, a drawn surface without tree text in the header, or a tree without text.
  Such an outward action is refused, the user does it.
- OCR codes are blacked out on `look` images and in saved screenshots, before encoding. A saved screenshot reads
  every large drawn surface (labelled or not) seen by either of its two tree readings, judges the OCR need on each
  reading, and is refused without `unmasked=true` when the OCR is unavailable or fails.
- A screenshot is refused before anything is written when a banking, payment or identity app is seen by any reading
  (foreground read again after the image even when the first tree failed; an app whose elements cover at least a
  quarter of the screen), and `unmasked=true` never allows it.
- Screen recording: a reading naming a protected app taints the video for good, even when it is partial; any
  reading of the screen state by any tool (a screenshot that sees a bank included) feeds the video guard right away;
  a reading started while recording counts even if it ends after screenrecord stopped (the evidence is dated by the
  start of its reading), while one started before the recording or after its noted end never taints a sound video.
  A banking app seen while measuring the display, after the first check, stops a recording from starting. A reading counts as watched only when it names the
  foreground app, or the screen is off (`Asleep`, or Always On Display: Dozing with every display in DOZE or OFF);
  Dreaming does not.
- Masking: card numbers and IBANs are found by a candidate scan on the original text; every start is tried, the
  union of all valid windows is masked (an IBAN next to a card, or two in a row, leave nothing out). An IBAN must
  have the exact length of its country (SWIFT IBAN registry) and a valid mod-97 key; groups may be separated by one
  or two spaces, no-break spaces, dashes, dots or a line break (CRLF included), in any case, even glued to a word
  ("…189EUR"). Cards
  followed by an expiry date or a CVV, after a decimal number, before a full stop, with a letter glued in front, or
  grouped with double spaces, dots or a line break are masked.

### Changed

- Multi-line text is pasted through the clipboard: a line break is text, never the Enter key (which can send).
- A swipe or drag is judged on one reading for both ends (a session timer ticking between two readings no longer
  refuses it); positions of the elements other than the target are left out of the identity (a 4-pixel shift or the
  keyboard opening no longer void an accord). A text that changes on screen ("typing…", a timer) still voids it, on
  purpose: the approved screen is no longer the one shown.
- A video or photo preview beside a send button and a recipient that the tree describes no longer blocks the accord.
- `look` without OCR (outside Windows, or engine failure) returns the image with a clear warning that drawn codes are
  not masked, and only runs the OCR for a large drawn surface without tree text (latency).
- Card masking only accepts real card groupings (4-4-4-4 followed or not by 1 to 3 digits, 4-6-5, 4-6-4, 4-4-5,
  4-3-3-3, or one block of 15 to 19 digits) and card first digits (2 to 6): calendars, lottery draws, number tables,
  SIRET (14 digits) and EAN-13 barcodes are no longer masked; an IBAN-like start in ordinary text ("Vol AF12 …") is
  no longer masked either.
- Screenshots no longer refuse the home screen because of the small Samsung Wallet handle.
- `launch`: the accord shows the resolved package, not the fragment typed by the agent.

### Fixed

- Screen recording: a video that ended by itself before the watch saw it gets its end time at the first collection,
  so a collection retried after a failed pull no longer erases a sound video.

## [0.9.1] - 2026-10-20

### Security

- Banking, payment and identity apps: when the UI tree cannot be read, a gesture aimed by coordinates is refused even
  with `confirm=true` (the protected app is now taken from the foreground state, not only from a readable tree).
- Keys: in a protected app only navigation and safe keys go through; Enter, D-pad center and any other validation
  key are refused even with `confirm=true` and an accord, and so is a typed line break. With a password field
  focused, no key but navigation (back, tab, arrows). Keys other than navigation need a readable tree. The accord of
  a key now covers the whole key sequence: a token for `enter` is refused for `tab enter` or `delete enter`.
- A swipe that starts on an outward label ("Glisser pour envoyer", "Slide to pay") needs the accord like a tap, not
  `confirm=true` alone; one accord per swipe or drag.
- The action bound to an accord now covers every phone number and e-mail address shown on screen (the dialer number
  anywhere, not only in the top 18 %), named attachments and the rest of the app's text (body, preview). A token for
  number A no longer calls number B. When the UI tree cannot be read, no token is given and the outward gesture is
  refused.
- Screen recording: the foreground app is checked about every second while recording; a banking, payment or
  identity app seen even once marks the video for good, which is erased from the phone without being pulled. A gap
  of more than 5 s in that check discards the video too. `stop`, the collection of a finished video by the next
  `start`, and the server shutdown all go through the same check; `start` checks the foreground before collecting
  the previous video.
- OCR: security codes found on the image (UI tree without text, or a large drawn surface such as a SurfaceView or
  TextureView without text) are blacked out on the image `look` returns and in saved screenshots, before encoding.
- Masking: bank card numbers (Luhn) and IBANs (mod 97) are masked by the same function as security codes, so in every
  text output (screen, notifications, SMS, clipboard, calendar title and place, logs, web, map).
- `web`: the banking, payment, tax and identity site rule is checked again on the address returned with the page
  text; a page that moved to such a site, or that does not give its address, returns nothing.
- No typing when the active field cannot be read, even with `confirm=true` (it could be a password field).

### Fixed

- A finished video whose collection fails no longer loses its handle: `start` stops, nothing new is recorded, and
  `stop` tries again.
- After a failure, the scrcpy gesture channel waits 60 s before trying again (`input` meanwhile), or retries at once
  after a reconnection, instead of paying the start-up delay on every gesture.
- Screen size and fold posture are read again on every screen reading, image or not (rotation, folding).
- Waiting for an app to come to the foreground is a real 5 s deadline, slow reads included.
- `act` says when the phone does not report its last touch (no `lastUserActivityTime`): the "user is using the phone"
  check is then inactive.
- Settings map: the phone's language falls back to `ro.product.locale` when `persist.sys.locale` is empty.
- Xiaomi, Redmi, POCO: a gesture refused by Android (`INJECT_EVENTS`) now says to turn on "USB debugging (Security
  settings)" and is not counted as sent.
- Clipboard: a phone that stops answering in the middle of a reply gives a clear error instead of a raw socket
  timeout.
- `look` reads the routines file once instead of once per routine.

## [0.9.0] - 2026-10-20

### Added

- `map` tool: the agents know the whole phone instead of searching on screen. `find` answers "where is X set" from
  the user's words with the path from Settings, the current value and a direct way in (`launch(action=…)` with the
  row highlighted, or `map(action="open", target=…)`; for a sub-screen without its own entry, the nearest screen to
  open and what to tap). The phone's search index is reserved for system apps (`READ_SEARCH_INDEXABLES`, refused to
  ADB), so the map is rebuilt offline from the phone's own Settings APK (pulled compressed, about 10 s over Wi-Fi):
  every screen (`res/xml`, about 420 on One UI 8) and row, titles in the phone's language, the fragment each screen
  belongs to (read from the app's code) and the exported activity that opens it. It is cached on the PC and rebuilt
  by itself when the Settings app or the system build changes. Screens drawn by code (Galaxy Buds, Galaxy Wearable)
  are added with `note`, which records the screen being shown under the path the agent gives (switch states
  included). `snapshot` and `diff` save and compare the phone state: settings values (`system`, `secure`, `global`),
  apps and versions, sensitive permissions granted, default apps, device admins, paired Bluetooth devices; secrets,
  identifiers, addresses and numbers are masked before anything is written, with a short fingerprint so a change is
  still seen. Pure Python readers for resource tables, binary XML and dex files (`apkres`), no Android tool needed:
  sizes and offsets checked, dex decoded at instruction boundaries, a truncated or forged file is refused
  (`FormatError`), never a crash. English titles are indexed too ("Extend Unlock" finds "Prolonger le
  déverrouillage"). `open` only opens a screen the map gives (with the row highlighted); `note` refuses banking,
  payment, identity and password manager apps, checked on the screen read, the foreground activity and every
  element, never records a password field, and refuses when the foreground changed during the read.

- Gestures go through scrcpy's control channel instead of one `adb shell input` per gesture: a tap leaves in under
  1 ms instead of about 150 ms (measured on the Fold), the channel opens once (about 1 s) and closes after 60 s
  without a gesture; `input` remains the fallback. Two new gestures need several fingers: `zoom` (in or out around a
  target, strength 1 to 3; the fingers stay out of the status bar, the edges and the search bars, a pinch is
  horizontal when there is room) and `drag` (held, moved, dropped: icons, files, list items; guarded at both ends,
  `input draganddrop` as fallback).

- Going in circles is reported: the same gestures on the same screen 3 times with nothing changed, or 3 batches in a
  row that leave the screen unchanged, add a warning at the top of the response with what to do instead (map,
  look, another target, ask the user). Screens are compared by a short fingerprint of their elements, without the
  status bar clock and the keyboard focus; each agent session keeps its own count.

- Routines shared by the three agents (`~/.nexus/nexus-android/routines.json`): `act(remember="name")` saves a
  batch that went through every step, with stable targets (resource id, then visible text, then accessibility
  description; never `[N]` or coordinates, a coordinate tap is saved by the element it hit); `act(routine="name",
  params={...})` replays it; a typed text made only of `{{x}}` is a parameter, and while recording `params` gives the
  values actually typed. Guardrails apply on replay (confirm and accord are never saved); `look` lists the routines
  of the app in the foreground. New stable targets for any gesture: `id:<resource id>` and `desc:<description>`.

- `apps` tool: `info` (version, dates, source), `stop` (force-stop, free), `clear` (all the app's data),
  `uninstall` (a system app is only hidden for the user, `restore` brings it back), `install` (an .apk from the PC,
  sensitive folders refused, update keeping the data). `clear`, `uninstall` and `install` need `confirm=true`; the
  system's own parts (phone, interface, Settings, store, keyboard, Google services) are never cleared or
  uninstalled, nor banking, payment and identity apps (the user does it).

- `web` tool: web pages on the phone read through Chrome's DevTools protocol (`adb forward` to
  `chrome_devtools_remote`, or an app's `webview_devtools_remote_<pid>` when its WebView allows debugging), without
  scrolling or touching the screen: whole text of the page (also outside the screen), title, address, links;
  default = the page of the app in the foreground; `query` keeps the matching lines; `tabs` lists the pages. Read
  only (`innerText` and links, never a field value); codes masked line by line, tokens, e-mails and numbers masked;
  banking, payment and identity apps never read. Minimal WebSocket client in the standard library (no dependency).
  Chrome answers only in the foreground (Android freezes it in the background).

- Visual fallback when the UI tree has no text (games, Flutter or hand-drawn apps, map labels): the text seen on
  the image is read by Windows' own OCR engine (`Windows.Media.Ocr`, the user's installed languages; local, no
  online service, no dependency; about 0.5 s), listed by `look`, and a gesture aimed at a visible text missing from
  the tree finds it on the image, tolerating OCR noise (at least 75 % alike: « Champ-de-Mars » read « *dhamp7de-Mars »
  on the satellite view of Maps, tapped). OCR text goes through the same guardrails (sending or paying words, protected apps)
  and the same code masking as the tree.

- `functions` tool: the structured actions apps expose to assistants (Android 16+ AppFunctions), reached through
  `cmd app_function`, reserved to shell and root, so to adb: 99 on the Fold (Samsung Clock alarms and timers,
  Reminder, Notes, Calendar, Contacts, Gallery, My Files, Phone; some Google ones), without the screen and even with
  the phone locked. `list` shows them per app; `run` calls one by its short name with the app's parameters
  (`{"findAlarmsParams": {}}`; the app's error names a missing field). Reading is free; delete, accept or reject a
  call, block a number need `confirm=true`; making a call also needs the accord for that exact call; banking apps
  never. Android 17's "computer control" (agents acting on a virtual screen) exists but is closed by an allowlist of
  agents: not used.

### Changed

- `launch` of a screen whose app is already open on another page no longer fails with "brought to the front": it
  opens again closing the pages above the asked screen (`--activity-clear-top`), and says so.
- `act type` compares the field with the text after typing: ✓ only when it reads exactly the text (or the old
  content followed by it); a field that differs (autocorrection, length limit, another field) is a ✗ that stops the
  batch, so a following "Send" is never tapped on a wrong text; a suggestion added after the text is reported (~).
- A lock screen that Android itself keeps unlocked (Extend Unlock: trusted place or device, `dumpsys trust` reports
  `deviceLocked=0` for the current user) no longer blocks the agents: `look`, `act` and `capture` wake the phone,
  lift the lock screen (`KEYCODE_WAKEUP`, `wm dismiss-keyguard`), read the state again and go on, saying so in the
  response. A phone that is really locked is refused as before; no code is ever typed.
  A `look` that lifts it does not keep the screen on (only a gesture does), and an unreadable state after the lift
  is never taken for a lifted lock screen.

### Fixed

- Antigravity's review of `capture` and `logs`: password fields are blacked out on screenshots like codes; the UI
  tree is read before and after the screenshot, so a code that appears or goes away in between stays covered; a
  screenshot without masking (tree unreadable) needs its own `unmasked=true`, a `confirm=true` given for the folder
  or a protected app no longer allows it; a video is erased from the phone, never saved on the PC, when a banking,
  payment or identity app comes to the foreground while recording (checked after every tool and at `stop`); card
  numbers (Luhn) and IBANs (mod 97) masked in logs; a crash whose block has no process name takes the name of its
  still running pid, so a protected app keeps its reduced view; Android's own shell commands (`am`, `input`, `pm`…,
  also run by `app_process`) are no longer taken for another tool holding accessibility; native crashes that only
  differ by addresses (`0x…`) are grouped.
- Typing into a search field works: `act type` answered "no active input field" in the Settings search and the
  Galaxy Buds search, because only classes named `…EditText` counted as input fields while these are
  `AutoCompleteTextView`. The phone's screen reader now sends Android's own `isEditable` flag, and the
  `uiautomator dump` fallback also knows the usual subclasses (`AutoCompleteTextView`, `SearchAutoComplete`).
- `[N]` targets work again on the shared HTTP daemon: each call used to start from an empty view, because the MCP SDK
  2.x builds a new session object per request, so `act(target="[18]")` right after a `look` answered "no element
  list". Views are now keyed by the transport session id (`Mcp-Session-Id`), stable for the whole session; the 32
  most recent sessions are kept. When there really is no list yet, the message says why (`look` with `show=image` or
  `none` lists nothing) and what to do, and such a `look` says so too.
- The guardrail judges what actually receives the tap, not everything behind it: the phone's screen reader now sends
  each node's depth and drawing order, and only the clickable element drawn in front, its clickable ancestors and its
  own label are judged. A promotional banner passing under the Galaxy Store bottom bar ("Économisez jusqu'à 43 €")
  no longer blocks the "Menu" tab, by name or by coordinates; the banner stays guarded where it is visible, and a
  banner drawn in front of a button is still judged. A tree read without that information keeps the previous, wider
  rule.

## [0.8.0] - 2026-10-19

### Changed

- Sending, calling, paying, publishing and transferring need the user's accord bound to the EXACT action, on top of
  `confirm=true` (shared guardrail policy, version 2). The first confirmed attempt is refused with the action as the
  screen shows it (app, button, the header where the recipient appears, the text of the fields) and a single-use
  token valid 10 minutes; the gesture goes through only when sent again with `accord="<token>"`, after the user's yes.
  Any change in between (other conversation, edited text, other gesture) voids the token. Covered paths: tap,
  double tap, long press, tap-like swipe, unnamed send icons and unreadable points in messaging apps, Enter, call and
  other risky keys, typed line breaks, `open_url` payment links, and `launch` intents that call, send or pay (new
  `accord` parameter). Uninstalling, resetting and deleting for good keep `confirm=true` alone.
- `draft` returns the accord token for the draft it opened, with its exact summary, so sending it needs no extra
  round trip; a draft edited after that is refused.

### Added

- `messages` and `notifications` note what they read: a recipient found in a recently read text is flagged in the
  accord summary, and text that looks like instructions aimed at an AI gets a warning in the response (a signal
  only, never a block or a guarantee).
- `garde_fous.py`, the shared policy module, is embedded next to `garde-fous.json` (identical to the shared copy,
  checked by the tests).

### Security

- Supply chain hardened (OpenSSF): every GitHub Action pinned by commit SHA, no persisted checkout credentials, no
  template injection, no cache in release builds (zizmor clean); Dependabot keeps the actions and the `uv`
  dependencies up to date with a 7-day cooldown; OpenSSF Scorecard workflow and badge. The MCP registry publication
  is now a job of `publish.yml` instead of a separate `workflow_run` workflow.

## [0.7.2] - 2026-10-19

### Added

- `capture` tool, to show a problem to a support service: `screenshot` saves the current screen at full resolution
  (PNG, or JPEG when the name ends in `.jpg`) with security codes blacked out as on `look` images; `record` films the
  next `seconds` (default 30), `start` and `stop` film while gestures go on (Android's `screenrecord`, MP4, 720p,
  4 Mbit/s, 180 s at most, one recording at a time). The video is pulled to the PC then deleted from the phone; the
  response gives the path, the length and the size. Files go to `~\Downloads\Telephone` by default, with the same
  folder rules as `files pull`, and never overwrite. Videos cannot be masked: no recording starts while a banking,
  payment or identity app is in the foreground, and the response says codes may appear in the video. A screenshot
  of such an app, or one without code masking (UI tree unreadable), needs `confirm=true`. Black images (FLAG_SECURE,
  screen off) are reported, and so is a video shorter than the recording time (Android adds frames only when the
  screen changes, none while it is off). A recording still running when the server stops is stopped and saved.
  Before any screenshot or video, a sleeping screen on an unlocked phone is turned on (`KEYCODE_WAKEUP`, counted as an
  agent gesture and kept on like after `act`); a locked phone, screen on or off, is refused without capturing, with a
  message to ask the user to unlock it (the agent never types a code): no more black screenshots or empty videos.
- `logs` tool (read-only), to understand why an app crashed or misbehaves: `kind="crash"` groups the crash log one
  block per crash (time, package, Java exception and each `Caused by`, the top frame and the first app frames; native
  crashes with signal, cause and backtrace); `kind="anr"` lists "app not responding" events from Android's drop box
  (kept for days) and the event log, without duplicates, with their reason; `kind="recent"` gives the last `lines`
  log lines (200 by default, 2000 at most), for one app with `app` (resolved like `launch`; its running processes,
  those started earlier, and lines naming it; an uninstalled app by its full package name). `since` (`15m`, `2h`,
  `13:05`, `2026-10-19 13:05`, phone time) and `query` (case-insensitive, applied after masking) filter; newest
  last, about 30,000 characters. Any security code, tokens and secrets (known key prefixes, JWT, random hex or
  base64, `Bearer`, `password=`…), e-mail addresses and phone numbers are masked; stack frames are kept as is.
  Banking, payment and identity apps: `recent` is refused for them and their lines are left out of the general
  view; their crashes and ANRs show only exception types and app frames, no message.
- `logs kind="crash"`: identical crashes (same process, type and text once object hashes, pids, bracketed numbers
  and times are ignored) are shown as one block, `×N (first …, last …, pids …)`, placed at the newest one; the
  exception Android re-prints after "Couldn't report crash" (processes without a package) is dropped, and the
  "Error reporting crash" cause is one short line. 21 crashes, 19 of them identical, went from 22,500 to 2,100 characters.

### Fixed

- When another automation tool holds the phone's single accessibility connection (UiAutomation), such as the
  mobile-mcp server `com.mobilenext.mobilecli.DeviceServer` that stays alive 30 minutes, Appium UiAutomator2,
  uiautomator2/atx-agent or an instrumentation run, `look` and the gestures aimed by text now say so at once, with
  the tool and its pid: "Another automation tool is using the phone's accessibility connection (…, pid N): close it
  or stop it, then retry." One `ps` call, no more 3 to 4 seconds of `uiautomator dump` retries ending in a misleading
  "screen moving or protected". The other tool's process is never stopped automatically.

## [0.7.1] - 2026-10-19

### Added

- `act(then="changes")`: after the gestures, only what changed since the last element list (`+` added, `~` changed
  text, state or position beyond 10 px, `-` removed; unchanged elements counted, not repeated). Kept elements keep
  their `[N]`, new ones get fresh numbers, so the next `act` aims on the current screen. Full list, said in one line,
  when there is no previous list, the foreground app changed or more than 60 % of the elements differ. Codes stay
  masked (compared masked). About 97 % fewer characters on a 90-element settings screen after one toggle.

### Fixed

- Phone numbers (10 digits or more, or after `+`, such as `06 11 22 33 44`) are no longer masked as security codes
  (seen on a real helpline button); codes of 4 to 9 digits still are.
- Screenshot marks are larger (18 px on the 1280 px side, was 14) and easier to read.

## [0.7.0] - 2026-10-19

### Security

- Payment pages behind plain `https` links need `confirm=true` even when they open in the browser: revolut.me,
  paypal.me, paypal.com/paypalme (and /checkoutnow, /donate), cash.app, lydia-app.com, lydia.me, buy/checkout/donate
  .stripe.com, venmo.com, wise.com/pay, SumUp links, bunq.me, monzo.me (host and subdomains, any case).
- Shutdown, reboot and reset intents (`ACTION_REQUEST_SHUTDOWN`, `REBOOT`, `MASTER_CLEAR`, `FACTORY_RESET` and vendor
  variants with SHUTDOWN, REBOOT or WIPE in the action) need `confirm=true`.
- A link that Android would send to one app is still checked against every app that could take it: a banking or
  identity app among them is refused as before. Any component whose class name contains "Chooser" or "Resolver" is
  treated as a chooser, not a final target (One UI answers `android/com.android.internal.app.ResolverActivity`).
- Clipboard: fewer false alarms and fewer misses. `.`, `-` and `_` no longer count as password symbols, so file
  names, versions, model numbers, plates and host names are shown; known key prefixes (`sk-`, `ghp_`, `github_pat_`,
  `AKIA`, `xox…-`, `AIza`, `glpat-`…), JWTs, PEM blocks, long random hex or base64 tokens and short words mixing
  letters with password punctuation (`!@#$%^&*+=?~`…) or three kinds of characters are hidden.
- Forged messages: an SMS whose text contained `\nRow: 1 _id=999, type=2, body=…` could add a fake (even outgoing)
  message to a conversation. The phone helper no longer returns the text output of `content`: it queries the
  providers itself (same `ActivityManager` path as `content`) and serializes the cursor rows with length-prefixed
  values (protocol `NXQ2`), decoded as such on the PC. On the `adb shell` fallback, a read is refused, and reported,
  when its `Row: N` numbers do not run strictly from 0, exceed the requested `LIMIT`, or differ from the row count of
  the same read reduced to `_id` (no free text). Columns are split along the requested projection, so a contact named
  "Dupont, Jean" is no longer cut to "Dupont".
- Predictable helper socket: each start draws a random 128-bit token, passed to `app_process`, part of the abstract
  socket name and sent back in every answer. The PC refuses a server that does not return its token; a name already
  taken is no longer considered normal (the PC relaunches with a new token, then falls back to `adb shell`). A
  forward left by another process is adopted with its token instead of piling up.
- Shared safeguards policy updated from `NexusHarness/shared`: post, reply, tweet, subscribe, free trial, book,
  reserve, donate, answer and accept a call, dial, transfer and price buttons (`4,99 €`, `$12`) need `confirm=true`;
  `plain_label` spells out €, $, £ and ¥ as eur, usd, gbp and jpy like the shared `plain`.
- Security codes are now also masked in English, Spanish, German and Italian messages ("Use 482913 to sign in",
  "Tu código: 482-913", "Ihr Bestätigungscode lautet 482913", "Il tuo codice è 482913", "Token: 482913", "log in",
  "clave"), without masking dates (12/10/2026, 2026-10-12) or amounts followed by a currency (1 250 €).
- A short swipe whose start and end fall inside the same clickable, non-scrollable element is judged as a tap, with
  the strict safeguard (confirmation word, unnamed icon in a messaging app, nothing readable).
- `files` on the PC side: keys, secrets and papers (`.ssh`, `.gnupg`, `.aws`, `.azure`, `.nexus`, `NexusSecure`,
  `Administratif`, `.env` files) and the Windows Startup folders are never read or written, even with
  `confirm=true`; outside Downloads, Documents, Desktop, Pictures and Videos (OneDrive included), `confirm=true` is
  required. The real path (symlinks, `..`) is resolved before the check.
- User back on the phone during a batch: before every gesture after the first, a light activity read
  (`dumpsys power` filtered on the phone) stops the batch at once if the user touched the screen since the agent's last
  gesture, and the report lists the steps already done. A one-gesture batch costs nothing more.
- Screen kept on: the raised `screen_off_timeout` is now 5 minutes instead of 30 (Android counts it again from every
  injected gesture; always longer than the idle delay). A server killed outright leaves the phone unlocked 5 minutes
  at most. The 30-minute value set by older versions is still recognised and restored. During a long `look`-only
  phase, more than 3 minutes after the last gesture or poke, a screen that is on gets `KEYCODE_WAKEUP` (no visible
  effect, counted by Android as activity), recorded as an agent gesture, never as the user's touch.

### Added

- `look(marks=...)`: with `show=both`, the image carries small `[N]` badges (the element numbers of the list) at the
  top-left of each element, with a thin outline of its bounds. `auto` (default) numbers every element when there are
  21 or fewer, otherwise only the unnamed ones (icons without text or description); `all`; `none`. Drawn after the
  security codes are blacked out (a badge only covers, never reveals), never on an image that is refused, and not
  with `show=image` (no list for the numbers to refer to). About 4 ms for 80 elements on a Fold screenshot.
- `clipboard(action="read"|"write", text)`: reads the phone's clipboard (security codes masked like messages; a lone
  code, or a single word that looks like a password or a token, is never shown; says when it is empty or not text)
  or sets it without pasting, through the same scrcpy server used to type accents (started for the call, stopped
  after). Typing accents still gives the user their previous clipboard back.
- `launch(action, data, extras, confirm)`: opens a precise screen (`android.settings.WIFI_SETTINGS`, a deep link,
  extras as string, integer or boolean; `app` then limits the target to that app). The target is resolved first
  (`cmd package resolve-activity`), started by its exact component, and reported with the foreground read back.
  Calling, sending or paying directly (`CALL`, `RESPOND_VIA_MESSAGE`, uninstall and reset actions, payment actions
  such as `org.chromium.intent.action.PAY`, `upi:`/`bitcoin:`/`payto:`… links, auto-send extras) needs
  `confirm=true`; a banking, payment or identity app, as the resolved target or as one of the candidates of
  Android's chooser, is refused even with `confirm=true`. `act open_url` refuses payment links without `confirm=true`
  too.
- Tests that tool descriptions, titles and schema descriptions contain no French, that offline errors and refusals
  (phone absent, unknown target, step needing `confirm=true`, missing or invalid arguments, locked phone) are in
  English, and that the instructions and descriptions never compare the server with other tools.

- Typing accents and emoji without `install.ps1`: when the pinned scrcpy server (v5.0.1) is missing or altered, the
  server downloads it once from the official Genymobile/scrcpy GitHub release into `~/.nexus/nexus-android/tools`,
  checks it against the SHA-256 written in the code and writes it atomically. If the download fails or the digest
  differs, the step fails with a message giving the URL, the expected digest and the folder; nothing is typed, and
  the text is never transliterated.
- Windows installer for the public repository (`install.ps1`): uv environment under `~\.nexus\nexus-android\venv`
  (package copied, not editable), pinned scrcpy server, stdio registration with Codex (`~\.codex\config.toml`,
  previous `nexus-android` table replaced, file backed up first) and Antigravity (`~\.gemini\config\mcp_config.json`,
  backed up first) when they are installed, the `claude mcp add` command printed for Claude Code (no Claude CLI call),
  then `test.ps1`. No OneDrive, daemon supervisor or author-specific path; `-NoRegister`, `-SkipTests`, `-Live`.
- Python 3.12 and 3.13 support: `requires-python = ">=3.12"` (3.11 lacks the `type` statement); the CI runs
  `test.ps1 -Python 3.12|3.13|3.14` on Windows. `test.ps1 -Python X.Y` keeps a separate test environment per version.
- `server.json` lists `NEXUS_ANDROID_AWAKE_IDLE`.
- `messages(before=...)`: reads older messages (or older conversations) from the ISO date given at the end of the
  previous answer. Answers of `messages`, `notifications` and `files list` are capped at about 60,000 characters, cut
  between two messages or lines, and say how to read the rest.
- A call cancelled by the client (MCP `notifications/cancelled`, or the client disconnecting) stops the batch before
  its next step, stops a `wait` step, and stops waiting for the phone.

### Changed

- Private installer: Antigravity now gets nexus-android as a plugin (`~\.gemini\config\plugins\nexus-android`, with
  the skill and its own `mcp_config.json` pointing at the shared daemon with the token, written on the PC only), like
  the other Nexus tools. The old entry in the global `mcp_config.json` is removed after a backup; the plugin is
  enabled in `config.json` only while Antigravity is closed.
- `look` in about 0.17 s instead of 3 s over Wi-Fi: the phone helper keeps one accessibility connection open and
  returns the UI tree, the screenshot (JPEG made on the phone, already at the size sent to the agent) and the screen
  state with the fold posture in one exchange, instead of `uiautomator dump`, `screencap` and several `dumpsys`. The
  tree is read like `uiautomator dump` does (active window, visible nodes, bounds clipped to the display, same
  attributes), so the element list, numbering, masking and refusals do not change; the target check before each
  gesture uses it too. As with `uiautomator dump`, the tree waits for one second without accessibility events, but
  only after a change: a still screen is read at once, and a screen that never settles (video) is read after 3 s
  instead of failing. Read-only (no gesture, no global action); the user's accessibility services are not suspended.
  The connection is released before any `uiautomator dump` fallback, after 45 s without a screen read, and right after a read of a protected (banking or identity) app, whose security SDKs watch for an active accessibility connection.
- `phone`: model, Android version, boot id, fold posture and screen state are read in one shell command, and an
  already known link is no longer searched again: 0.19 s instead of 0.34 to 0.42 s per call over Wi-Fi (1 adb round
  trip instead of 4 or 5), 0.43 s instead of 0.51 to 0.57 s for the first call of a process.
- `info(kind="location")`: phone clock and known positions in one shell command, `dumpsys location` filtered on the
  phone: 0.11 to 0.15 s instead of 0.20 to 0.23 s.
- The SIM country (used to compare numbers) is read once per phone for all the sessions of the shared daemon,
  instead of once per session.
- Tool descriptions, parameter descriptions and every message returned to the model (results, per-step reports,
  refusals, errors, hints, warnings, element labels, phone, settings, info, files, messages and notifications
  outputs) are now in English, with the same meaning and the same guardrails. Data patterns stay multilingual: the
  shared safeguards file, keyword lists and regular expressions over screen text, SMS and notifications, Android UI
  labels and bank app names; the safeguards file's French screen descriptions are translated by the server.

- Quick start, `server.json` and client configs: `uvx nexus-android` (no more `--python 3.14`).
- Classifiers: Python 3.12 to 3.14, Telephony and AI topics; the unrelated "Testing" topic and the license
  classifier (redundant with the SPDX `license` field, PEP 639) removed.
- README: absolute image and link URLs (the demo image was broken on PyPI); exact language of the server (everything
  sent to the client in English); first-use download of the scrcpy server, and the one internet
  request in Privacy; files left on the phone (helpers kept; UI dump and `nexus-q-*`/`nexus-p-*` read folders deleted
  after each read) and a cleanup command that removes them all (`rm -rf /data/local/tmp/nexus-*`); public installer;
  shared daemon (`--http`, token, limits); warning that `adb tcpip 5555` is not encrypted; Performance section.
- `SECURITY.md`: what the daemon token does and does not protect against (local malware can use the ADB server on
  port 5037, which has no authentication), the phone helper's shell/root-only peer check and per-launch token, and
  the unencrypted `adb tcpip` mode.
- Shared policy file (`garde-fous.json`): comments made neutral (no names); no pattern or value changed. The public
  copy is embedded by the export with its SHA-256 (`vendored.sha256`), checked by a public CI workflow.
- Finding the phone over Wi-Fi: the last known address is tried first, then mDNS; the port scan runs only when the
  PC is on the same /24 network, checks each open port with an ADB handshake (`CNXN` answered by `CNXN`, `AUTH` or
  `STLS`) and tries 4 ports at most. A failed search is remembered for 120 s (a USB cable is still seen at once), and
  the screen-awake timer never scans.
- `launch` resolves the launcher activity (`cmd package resolve-activity`) and starts it with `am start -n`, `monkey`
  only as a fallback; the package list is cached for 60 s; the foreground app is polled every 0.25 s (5 s at most)
  instead of a fixed 2 s wait. `draft` polls the same way instead of waiting 2.5 s.
- The phone helper answers an error when a batch of reads takes more than 8 s (under the PC's 10 s, so a hung
  provider is reported as such, not as a lost link) and cancels the hung read, instead of blocking one of its 8
  threads for good.
- `Element.scrollable` is a typed field; the tap-like swipe check no longer relies on a French state label.
- MCP annotations: `files` is no longer idempotent (a repeated pull creates `name (1)`, `name (2)`) nor destructive
  (it never deletes or overwrites); `checkup` is destructive and not idempotent (`clean=true` clears caches).

### Fixed

- Wi-Fi lost during a read: about 3 minutes of waiting (60 s, three 30 s retries, then the `adb shell` fallback).
  Now a helper read waits 10 s at most and state reads 5 s; a link error goes up at once instead of falling back;
  after a timeout a Wi-Fi transport is dropped from adb and the next call looks for the phone again.
- The in-process lock of the shared daemon waited forever; it now gives up after 30 s with "phone busy", like the
  lock shared between servers.
- A refused step no longer marks the time of the agent's last gesture (a touch by the user just before could pass for
  the agent's at the next batch); only a gesture really sent does.
- `x,y` targets accept the `@540,1100` form printed by `look`.
- `checkup`: a short, empty or wrapped `df` output no longer crashes it; apps installed by hand (`installer=null`)
  are now listed as outside the stores.
- `settings brightness`: an unset (`null`) brightness no longer crashes the read; a non-numeric value for
  brightness or volume is refused instead of being read as 0.
- WhatsApp drafts no longer assume +33: the country code comes from the SIM card (`gsm.sim.operator.iso-country`,
  else the network's), with the national prefix of that country (libphonenumber table). Unknown country and a number
  without country code: refused with a clear message. Phone numbers are compared in the SIM country's national
  format.
- `phone/build.ps1` works when the helper compiles to a single class file.

## [0.6.2] - 2026-10-19

### Changed

- Server instructions sent to every MCP client are now in English and neutral ("the user"): no phone model, no
  mention of a shared policy between named agents. Same rules and flow as before. The startup test checks that the
  instructions stay under the 2048-character client limit and that nothing personal reaches `initialize` or
  `tools/list`.

## [0.6.1] - 2026-10-19

### Added

- Cable-free use without a shared Wi-Fi network: when the phone runs a hotspot (One UI `ap_br_swlan0` / `swlan0`)
  and the PC is connected to it, the address recorded at each USB connection is the hotspot address (`wlan0` still
  comes first). After `adb tcpip 5555`, the server finds the phone again over the hotspot once the cable is removed.
  README section for this setup.

## [0.6.0] - 2026-10-19

### Changed

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
- `install.ps1` no longer writes `[mcp_servers.nexus-android]` to Codex's `config.toml`: Codex gets the server from a
  personal Codex plugin.

### Security

- The helper is read-only (only `query` is served; insert, update, delete and call are refused), answers only
  connections from the shell user or root (adbd; kernel-checked peer credentials, which no app on the phone can
  have), and stops by itself after 10 minutes without a request. Each dex version has its own socket name, so an
  old helper is never taken for the current one; older ones are stopped and removed.
- If the helper cannot be started or answers badly, reads fall back to the 0.5 method (`adb shell`), with the same
  output and the same masking of security codes, and the helper is retried a minute later.

## [0.5.1] - 2026-10-19

### Security

- Shared policy (`garde-fous.json`) synced with NexusHarness: every "Réinitialiser…" / reset button needs
  `confirm=true` again, including the bare "Réinitialiser" confirmation button of One UI and Windows and
  "Réinitialiser les paramètres réseau". Only harmless resets stay free (filters, zoom, view, sort, search, layout,
  form, fields, text, selection, colours). This reverts the 0.5.0 narrowing, which let the final One UI reset button
  through without confirmation.

## [0.5.0] - 2026-10-19

### Changed

- `messages()` is about 5 times faster. Every `content query` starts a Java process on the phone (about 1 s), and
  the tool used to run them one after another: 34 for the conversation list, 7 to 11 per conversation. Queries now
  run in parallel on the phone inside a single `adb shell` (30 cost about as much as one), the phone returns only the
  last N rows (`LIMIT`), MMS parts and addresses are chained on the phone instead of going back to the PC, and
  contact names are cached for 10 minutes, shared by all sessions of the daemon. Measured on a Galaxy Fold over USB:
  conversation list 35 s → 4.6 s (3.1 s with names cached), one conversation 7-11 s → 2.1 s, with 4 images
  11.6 s → 3.3 s, by contact name 11.4 s → 4.3 s. Output is byte-for-byte identical, security codes still masked.
- `info(kind="calls")` reads only the last N calls and resolves their names in one exchange.
- `install.ps1` no longer registers the server with Claude Code and never calls a Claude CLI: Claude Code gets
  `nexus-android` from a Claude Code plugin (shared daemon URL, token read from an environment variable).
- Repository renamed to `pironjulien/NexusAndroid`; GitHub redirects the old address.
- Published on PyPI (`nexus-android`) and in the official MCP registry (`io.github.pironjulien/nexus-android`);
  the quick start installs with `uvx --python 3.14 nexus-android`.

### Security

- Shared policy (`garde-fous.json`) synced with NexusHarness: "reset" needs `confirm=true` only for the whole device
  ("Réinitialiser le téléphone", "Réinitialisation d'usine"); other "Réinitialiser…" buttons inside an app are free.

### Fixed

- `install.ps1`: without `claude` on the PATH, Claude Code was skipped on a new PC. Falls back to the `claude.exe`
  shipped with the Claude desktop app, like NexusClaude and NexusBrowser.

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

[Unreleased]: https://github.com/pironjulien/NexusAndroid/compare/v0.12.0...HEAD
[0.12.0]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.12.0
[0.11.0]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.11.0
[0.10.0]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.10.0
[0.9.12]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.9.12
[0.9.11]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.9.11
[0.9.10]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.9.10
[0.9.9]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.9.9
[0.9.8]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.9.8
[0.9.7]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.9.7
[0.9.6]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.9.6
[0.9.5]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.9.5
[0.9.4]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.9.4
[0.9.3]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.9.3
[0.9.2]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.9.2
[0.9.1]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.9.1
[0.9.0]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.9.0
[0.8.0]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.8.0
[0.7.2]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.7.2
[0.7.1]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.7.1
[0.7.0]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.7.0
[0.6.2]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.6.2
[0.6.1]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.6.1
[0.6.0]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.6.0
[0.5.1]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.5.1
[0.4.1]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.4.1
[0.4.0]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.4.0
[0.3.1]: https://github.com/pironjulien/NexusAndroid/releases/tag/v0.3.1
