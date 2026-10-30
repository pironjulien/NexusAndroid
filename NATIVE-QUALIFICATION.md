# Native C# qualification — 29–30 October 2026

The qualification covers the local sources and their .NET 10 executable, with no Python in the production process.
The eighteen tools keep the input schemas, output schemas and annotations of the Python engine 0.20.3. The product
compatibility number stays 0.20.3; the native install is identified by the hash of the sources, the Java reader and
the embedded policy. Builds, proofs and test files live outside OneDrive.

## Automated checks

- `test.ps1`: Ruff, Pyright, the Python suite and native tests built from the current sources.
  Last full run: **1,070 passed, eight live/emulator scenarios outside the offline suite**, Ruff and Pyright
  without errors; the 51 native contracts and the 22 advanced contracts are part of this check.
- `tests/native`: 51 behaviour contracts, including exact accords, sessions, passwords, presence, transport
  interruption, Unicode keyboard, clipboard restore, standard images and video cancellation. The latest cases also
  exercise a banking button out of focus, a bank in split screen, the Wallet handle, a banking read by another
  session during a video, and the real arguments of a process for Android paths with spaces, quotes and Unicode.
- `tests/native_advanced`: 22 contracts for AppFunctions, protected Web before/after navigation, search after
  masking, per-device maps, permissions and state-capture differences.
- `tests/native_connection`: 47 ADB selector scenarios and 21 comparisons with the Python algorithm; hardware
  identity checked, USB/Wi-Fi of the same phone, reconnection, remembered absence and persistent boot detection.
- `tests/test_native_differential.py`: 58 comparisons of contracts and checked typed outputs; the eighteen MCP
  definitions are compared in full with those actually exported by the Python server.
- Fold Settings APK: independent comparison of 3,169 preferences, including the 63 duplicated titles, and 407
  standalone activities. Destinations, parent/child openers, actions, components, paths, English and keywords
  preserved. Explicit additions: 34 root openers and seven keys the Python reader had not resolved.
- HTTP: authentication, negotiation, ping, isolated sessions, session deletion and explicit GET/SSE refusal.

## Real runs on the Fold

Fresh reads of the Galaxy Fold SM-F971B, Android 17, Wi-Fi, cover screen 1,248 × 1,972. Gestures stay local, with no
message sent and no code typed; one launch attempt was refused while the user was present.

- Home and Settings: navigation, search, exact typing of "Nexus été 🐱", clearing verified despite the Android hint,
  and back to Home. References that went stale are refused.
- Real structured `phone` read, selection by hardware identity and reliable lock state.
- `look(show=image)`: JPEG of 182,717 bytes, 1,248 × 1,972, decoded by Pillow independently of the engine.
- Full-resolution PNG capture, real Windows OCR masking pass and independent decoding succeeded.
- Home screen video: `record` then `start`/`stop`, real H.264 MP4 at 720 × 1,136, analysed by FFprobe, a frame
  extracted by FFmpeg and inspected. No `screenrecord` producer nor private Nexus temporary file left after stopping.
- A synthetic Unicode file sent then fetched back with the `files` tools, identical SHA-256; only that test file was
  deleted from the phone after reading its exact destination.
- Real `map(find=wifi)` search, from the APK and current values; no setting changed.
- Normal shutdown of the test engine: initial screen timeout restored to 30,000 ms, state `awake.active=false`.

These runs revealed and fixed two real video defects: `dumpsys window windows` does not report the Fold's
foreground while `dumpsys window` does; `pkill -f` can kill the parent shell whose command mentions the file. Stopping
now targets only the PIDs of real `screenrecord` processes carrying the exact private argument, and waits for the
fifteen-second MP4 finalisation window of the reference engine.

## Installation and limits

### Targeted latency measurement

Same Fold on Wi-Fi, same loopback HTTP client, one persistent session initialised per engine, one warm-up read then
three measured reads; the bench shows no media nor response content.

| Read | Python | C# before the fix | C# after the fix |
|---|---:|---:|---:|
| `phone`, medians of two pairs | 0.177 s before / 0.202–0.214 s after | 1.141 s | 0.201–0.241 s |
| `look(show=elements)`, median of the last pair | 0.136 s | 0.355 s in the first pair | 0.215 s |

The native status had multiplied ADB processes and read the UI tree for nothing. It now reads model, version, boot,
posture, trust, state and hardware identity in a single fresh batch. The hardware and boot proof is checked against the
recorded identity before any answer; no old content is returned from a cache. Other calls check their current
transport with a grouped fresh proof rather than a repeated full discovery. The identity is reused only during the
operation that holds the phone lock, then cleared.

`look` keeps about 80 ms of extra cost in this short measurement: it checks hardware and boot with a fresh round trip
on every call, where Python reuses its established selection. No general speed-up is claimed. The first reads are kept
separately in the local receipts; they are not a fair startup comparison, especially when the first read lifts a lock
without a code.

`install.ps1 -Runtime Native` publishes an immutable runtime under `%USERPROFILE%\.nexus\nexus-android\native`, then
registers its spec. This shared path avoids the AppData redirection of MSIX clients: the independent supervisor and the
three AI apps find the same executable. The supervisor coordinates its activation; the installer stops no running
service. `-Runtime Auto` keeps that choice; `-Runtime Python` stays an explicit return to the reference engine.

### Activation delivered on 30 October

The supervisor was restarted in a coordinated way at **00:48:53, Paris time**. The native Android process **34428** is
loaded from the configured runtime, on port **16180**:

- executable: `%USERPROFILE%\.nexus\nexus-android\native\9679ec50fd231094\NexusAndroid.exe`;
- SHA-256: `EDAF07B5A0DAC284DF1823FA98B096751666FD185A28A7739348F9E399C45C51`;
- engine: 0.20.3, sources `64f844d`; shared installer fixed in `9388883`.

The production receipt of **30 October at 00:59:18, Paris time** confirms `runtime=native`, PID 34428, discovery of
the **18 tools** by a new authenticated MCP HTTP session and a real successful `phone` call on the Fold **SM-F971B over
Wi-Fi**. The qualification session was then closed with HTTP status **204**.
Local proof: `%USERPROFILE%\.nexus\nexus-android\verification\production-native-20261030.json`.
The executable actually loaded, the spec and the hash were cross-checked when this report was updated.

The MCP client of the already open Codex chat, however, kept its old transport and returned `TransportSenderror`.
The receipt therefore records `current_chat_transport_reload_required=true`: that client needs a Codex restart. The
new production HTTP connection is verified; no successful Android call from that chat's earlier MCP client is claimed.

The real runs cover a single Fold and its cover screen, in its current software state. No real sending, call, account
change, app removal, bank or password manager was used as a test. Those sensitive paths are exercised on isolated
transports. USB variants, reconnection after a physical restart and other hardware are covered by the controlled
contracts, with no claim of a universal physical run. An isolated MCP discovery does not prove that the cache of an
already open Codex chat was renewed.

An additional video run was discarded: the phone's last foreground was another app than the expected Home screen. Its
content was neither opened nor used as proof. Deleting that single generated cache file was refused by the automatic
check ("blocked by policy"); it stays local under
`%LOCALAPPDATA%\NexusAndroid\live-qa\native-fold-frozen-20261030.mp4`. No attempt was made to work around the refusal.
Home was then restored and verified, with no typing and nothing sent.
The QA driver now requires an explicit `expect_foreground` and an immediately preceding, consistent `phone` read: same
app, identified model, screen on and unlocked. Any difference refuses the run before the media is requested. Eight
driver regressions cover these preconditions without a phone or side effects.
