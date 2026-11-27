# Development instructions

## Pitfalls

- Tests never reach a phone or the network: they drive the real engine and MCP server against the fixture phones, adb
  transports and DevTools pages of `tests/`. Build output and test data go to `~\.nexus\build\nexus-android`, never
  into the clone. A real phone is only for the bench (`benchmarks/`), whose raw results stay local.
- Outward gestures (send, call, pay, publish, transfer) stay behind `confirm=true` plus a single-use accord bound to
  the exact action, re-read and compared right before each ADB injection; codes stay masked in reads, OCR and
  screenshots. Any change to gestures, targets or masking keeps these checks and comes with a test that fails without
  it.
- One version everywhere: `native/NexusAndroid.csproj` sets it; `mcpb/manifest.json`, the plugin manifests (Claude
  Code, Codex and Antigravity share `plugin/`) and `server.json` follow, and the release workflow refuses a mismatch.
  Every change gets its `CHANGELOG.md` entry under `[Unreleased]`; each version heading needs its comparison link
  (`ChangelogTests`).
- AI apps keep the engine running for hours while updates land: an install goes to a new
  `~\.nexus\nexus-android\native\<fingerprint>` folder, never over a running one, and older versions are removed only
  once `engine.txt` names the new install and no AI app runs them. Releases ship the Native AOT engine, so renaming a
  running exe aside is safe (`UpdateWhileRunningTests`); keep it AOT-clean (zero AOT analyzer warnings,
  source-generated JSON).
- Every build, tests included, compiles the phone helper `phone/NexusQuery.java` with `phone/build.ps1` (JDK 17+). A
  JDK update moves `JAVA_HOME` while running apps keep the old value: the script ignores a `JAVA_HOME` without
  `javac.exe`.
- `Microsoft.DotNet.ILCompiler` and `Microsoft.NET.ILLink.Tasks` follow the runtime of the SDK pinned by
  `global.json`: they move together and never to a new major alone (Dependabot ignores their majors).
- Android allows one accessibility automation client at a time: an empty UI tree on a real phone can mean another
  tool (mobile-mcp, Appium, uiautomator2...) holds it, not an engine bug.
