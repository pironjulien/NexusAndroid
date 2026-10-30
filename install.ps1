#Requires -Version 7
# Installs NexusAndroid from a clone of this repository on Windows (safe to run again after an update). Needs the .NET 10
# SDK (https://dot.net); no Python. Without a clone, use get.ps1 instead (prebuilt, nothing to install).
#  - offline tests (test.ps1), then `dotnet publish` of the native engine (build output in ~\.nexus\build\nexus-android),
#    installed into ~\.nexus\nexus-android\native\<source fingerprint> (never overwritten while an AI app runs it)
#  - `NexusAndroid.exe setup`: adb found or downloaded from Google (size and SHA-1 checked), pinned scrcpy server, the
#    stdio server declared to the AI apps found (Claude Code, Claude Desktop, Codex, Antigravity; files backed up first),
#    then the phone connection followed until it answers (-NoWait: not waited for)
# Usage: pwsh -NoProfile -File install.ps1 [-SkipTests] [-NoRegister] [-NoWait]
param([switch]$SkipTests, [switch]$NoRegister, [switch]$NoWait)
$ErrorActionPreference = 'Stop'

$project = $PSScriptRoot
$state = Join-Path $HOME '.nexus\nexus-android'
if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { throw '.NET SDK not found: install the .NET 10 SDK (https://dot.net), or use get.ps1 (prebuilt).' }
if (-not $SkipTests) {
    & (Join-Path $project 'test.ps1')
    if ($LASTEXITCODE) { throw 'Tests failed: nothing installed.' }
}
$sources = @(Get-ChildItem -LiteralPath (Join-Path $project 'native'), (Join-Path $project 'plugin') -File -Recurse)
$sourceHash = ($sources | Sort-Object FullName | ForEach-Object { (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }) -join ''
$revision = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($sourceHash))).Substring(0, 16).ToLowerInvariant()
$target = Join-Path $state "native\$revision"
$exe = Join-Path $target 'NexusAndroid.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    $rid = if ([Runtime.InteropServices.RuntimeInformation]::OSArchitecture -eq 'Arm64') { 'win-arm64' } else { 'win-x64' }
    $part = "$target.part"
    Remove-Item -LiteralPath $part -Recurse -Force -ErrorAction SilentlyContinue
    & dotnet publish (Join-Path $project 'native\NexusAndroid.csproj') -c Release -r $rid --self-contained false -p:PublishSingleFile=true -o $part
    if ($LASTEXITCODE) { throw 'dotnet publish failed: nothing installed.' }
    Get-ChildItem -LiteralPath $part -Filter *.pdb | Remove-Item
    Move-Item -LiteralPath $part -Destination $target
}
Write-Host "NexusAndroid installed: $exe"
$setup = @('setup')
if ($NoRegister) { $setup += '--no-register' }
if ($NoWait) { $setup += '--no-wait' }
& $exe @setup
exit $LASTEXITCODE
