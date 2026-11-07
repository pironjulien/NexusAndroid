# Full offline check, no phone needed: the xUnit v3 suite of the native engine (tests/). Exit code 0 when it passes.
# Build and test output go to ~\.nexus\build\nexus-android (Directory.Build.props), never into the sources.
# The CI runs exactly this script.
# Nexus pre-push gate: on a Nexus workstation the run goes through ~\.nexus\git\Invoke-NexusTest.ps1 (NexusInfra), which
# records the tested tree when it passes; the pre-push hook refuses a pushed tree that has not passed this script.
if (-not $env:NEXUS_TEST_EMPREINTE -and (Test-Path -LiteralPath "$HOME\.nexus\git\Invoke-NexusTest.ps1")) { & "$HOME\.nexus\git\Invoke-NexusTest.ps1" -Script $PSCommandPath -Parametres $PSBoundParameters -Arguments $args; exit $LASTEXITCODE }
$ErrorActionPreference = 'Stop'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
Push-Location $PSScriptRoot  # dotnet test reads global.json (Microsoft.Testing.Platform) from here
try {
    dotnet test --project tests/NexusAndroid.Tests.csproj -c Release
    if ($LASTEXITCODE) { throw 'Tests failed.' }
    # The real-phone bench uses the engine's internals: build it (never run it, no phone here) so an engine change
    # cannot break it unseen.
    dotnet build benchmarks/Banc/Banc.csproj -c Release
    if ($LASTEXITCODE) { throw 'Bench build failed.' }
} finally { Pop-Location }
exit 0
