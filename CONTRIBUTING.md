# Contributing

## Environment

- Windows 10 or 11, the .NET SDK pinned by `global.json`, PowerShell 7, a JDK 17+ (every engine build, tests
  included, compiles the phone-side helper with `phone/build.ps1`);
- no phone is needed by the tests; an Android phone with USB or wireless debugging only for the real-phone
  [bench](benchmarks/README.md).

## Rules

1. Open an issue or a pull request against `main`. A change is accepted only when `test.ps1` passes: the CI
   (`.github/workflows/tests.yml`) runs that same script on every push and pull request.
2. **Every new feature and every bug fix comes with its test** in `tests/` (xUnit v3), against the fixture phones, adb
   transports and DevTools pages: nothing reaches a device or the network. A fix starts with a test that fails without
   it.
3. Keep the safeguards: outward gestures (send, call, pay...) stay behind the user's confirmation and a one-time token,
   codes stay masked in reads and screenshots.
4. Never commit a secret, a token, a capture of a real phone, raw bench results or a build output: builds and test
   data go to `~\.nexus\build\nexus-android`, outside the repository.
5. Comments, commit messages and `CHANGELOG.md` entries are written in English. `CHANGELOG.md` follows Keep a Changelog
   1.1.0: add your entry under `[Unreleased]`; every version heading has its comparison link (`ChangelogTests`).

## Checks

```powershell
pwsh -NoProfile -File test.ps1   # dotnet test (xUnit v3), then the build of the real-phone bench; offline, no phone
git diff --check
```

## Version

One version everywhere: `native/NexusAndroid.csproj` sets it; `mcpb/manifest.json`, the plugin manifests and
`server.json` follow. The `Release` workflow (`.github/workflows/release.yml`) checks them, runs `test.ps1`, builds,
attests and publishes a development build on every push to `main`.
