# Full offline check, no phone needed: the xUnit v3 suite of the native engine (tests/). Exit code 0 when it passes.
# Build and test output go to ~\.nexus\build\nexus-android (Directory.Build.props), never into the sources.
# The CI runs exactly this script.
$ErrorActionPreference = 'Stop'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
Push-Location $PSScriptRoot  # dotnet test reads global.json (Microsoft.Testing.Platform) from here
try {
    dotnet test --project tests/NexusAndroid.Tests.csproj -c Release
    if ($LASTEXITCODE) { throw 'Tests failed.' }
} finally { Pop-Location }
exit 0
