#Requires -Version 7
# Installs NexusAndroid from a clone of this repository on Windows (safe to run again after an update).
#  - Python environment outside the sources: ~\.nexus\nexus-android\venv (uv)
#  - pinned scrcpy server (typing accents and emoji), SHA-256 checked
#  - registers the stdio server with Codex (~\.codex\config.toml, backed up first) and Antigravity
#    (~\.gemini\config\mcp_config.json, backed up first) when they are installed; prints the Claude Code command
#  - offline tests (test.ps1); -Live: a run on the connected phone (it types and clears a draft SMS, never sends)
# Usage: pwsh -NoProfile -File install.ps1 [-Live] [-SkipTests] [-NoRegister]
param([switch]$Live, [switch]$SkipTests, [switch]$NoRegister)
$ErrorActionPreference = 'Stop'

$project = $PSScriptRoot
$state = Join-Path $HOME '.nexus\nexus-android'
$venv = Join-Path $state 'venv'
$backups = Join-Path $state 'backups'
New-Item -ItemType Directory -Force $state | Out-Null

$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) { throw 'uv not found: install it (https://docs.astral.sh/uv/), then run this script again.' }
$env:UV_PROJECT_ENVIRONMENT = $venv
$env:UV_LINK_MODE = 'copy'  # hard links fail in synced folders (OneDrive) and across drives
$env:PYTHONUTF8 = '1'

# Installed as a copy (not editable): the server keeps working if this clone is moved; run again to update.
& $uv sync --frozen --no-dev --no-editable --reinstall-package nexus-android --project $project
if ($LASTEXITCODE) { throw 'uv sync failed.' }
$python = Join-Path $venv 'Scripts\python.exe'
& $python -c 'import nexus_android'
if ($LASTEXITCODE) { throw 'The installed package does not import.' }

$adb = (Get-Command adb -ErrorAction SilentlyContinue).Source
$adbOnPath = [bool]$adb
if (-not $adb) {
    $adb = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages') -Filter adb.exe -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $adb) { Write-Warning 'adb not found: winget install Google.PlatformTools, then run this script again.' }

# scrcpy server (Genymobile, Apache 2.0): typing accents and emoji through the clipboard. Same pinned version and
# SHA-256 as src\nexus_android\clip.py (which downloads it itself at the first accented text if it is missing).
$scrcpyVersion = '5.0.1'
$scrcpySha = '764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536'
$scrcpy = Join-Path $state "tools\scrcpy-server-v$scrcpyVersion"
if (-not (Test-Path $scrcpy) -or (Get-FileHash $scrcpy -Algorithm SHA256).Hash -ne $scrcpySha.ToUpper()) {
    New-Item -ItemType Directory -Force (Split-Path $scrcpy) | Out-Null
    $tmp = "$scrcpy.download"
    try {
        Invoke-WebRequest "https://github.com/Genymobile/scrcpy/releases/download/v$scrcpyVersion/scrcpy-server-v$scrcpyVersion" -OutFile $tmp
        if ((Get-FileHash $tmp -Algorithm SHA256).Hash -ne $scrcpySha.ToUpper()) {
            Remove-Item $tmp -Force
            Write-Warning 'scrcpy server: unexpected SHA-256, file rejected (typing accents unavailable).'
        } else { Move-Item $tmp $scrcpy -Force; Write-Host "scrcpy server v$scrcpyVersion verified." }
    } catch {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        Write-Warning "scrcpy server not downloaded ($($_.Exception.Message)); the server will try again at the first accented text."
    }
}

# What every client starts: the environment's Python, stdio transport.
$env_ = [ordered]@{ PYTHONUTF8 = '1' }
if ($adb -and -not $adbOnPath) { $env_['NEXUS_ANDROID_ADB'] = $adb }
$args_ = @('-m', 'nexus_android')

function Save-Backup([string]$file) {
    New-Item -ItemType Directory -Force $backups | Out-Null
    $copy = Join-Path $backups ("{0}.{1}" -f (Split-Path $file -Leaf), (Get-Date -Format 'yyyyMMdd-HHmmss'))
    Copy-Item $file $copy
    return $copy
}

function ConvertTo-TomlString([string]$value) {
    if ($value -notmatch "['\r\n]") { return "'$value'" }  # literal string: Windows paths without escaping
    return '"' + ($value -replace '\\', '\\' -replace '"', '\"' -replace "`r", '\r' -replace "`n", '\n') + '"'
}

if (-not $NoRegister) {
    # --- Codex: [mcp_servers.nexus-android] table in ~\.codex\config.toml (other tables untouched)
    $codexDir = Join-Path $HOME '.codex'
    if (Test-Path $codexDir) {
        $codexConfig = Join-Path $codexDir 'config.toml'
        $lines = @()
        if (Test-Path $codexConfig) {
            $backup = Save-Backup $codexConfig
            Write-Host "Codex: backup in $backup"
            $lines = [IO.File]::ReadAllLines($codexConfig)
        }
        # Previous nexus-android table and its sub-tables removed, up to the next table header.
        $own = '^\s*\[\s*mcp_servers\s*\.\s*("nexus-android"|''nexus-android''|nexus-android)\s*(\.[^\]]*)?\]\s*(#.*)?$'
        $kept = [Collections.Generic.List[string]]::new()
        $skip = $false
        foreach ($line in $lines) {
            if ($line -match '^\s*\[') { $skip = $line -match $own }
            if (-not $skip) { $kept.Add($line) }
        }
        while ($kept.Count -and -not $kept[$kept.Count - 1].Trim()) { $kept.RemoveAt($kept.Count - 1) }
        $envToml = ($env_.GetEnumerator() | ForEach-Object { "$($_.Key) = $(ConvertTo-TomlString $_.Value)" }) -join ', '
        $table = @(
            '[mcp_servers.nexus-android]'
            "command = $(ConvertTo-TomlString $python)"
            "args = [$(($args_ | ForEach-Object { ConvertTo-TomlString $_ }) -join ', ')]"
            "env = { $envToml }"
        )
        $text = (@($kept) + $(if ($kept.Count) { @('') } else { @() }) + $table) -join "`n"
        [IO.File]::WriteAllText($codexConfig, $text + "`n", [Text.UTF8Encoding]::new($false))
        Write-Host 'Codex: nexus-android registered.'
    } else { Write-Host 'Codex not found (no ~\.codex): skipped.' }

    # --- Antigravity: "nexus-android" entry in ~\.gemini\config\mcp_config.json (other entries untouched)
    $agyDir = Join-Path $HOME '.gemini\config'
    $agyConfig = Join-Path $agyDir 'mcp_config.json'
    if ((Test-Path $agyConfig) -or (Test-Path (Join-Path $HOME '.gemini\antigravity'))) {
        $json = [ordered]@{}
        if (Test-Path $agyConfig) {
            $backup = Save-Backup $agyConfig
            Write-Host "Antigravity: backup in $backup"
            $raw = Get-Content $agyConfig -Raw
            if ($raw -and $raw.Trim()) { $json = $raw | ConvertFrom-Json -AsHashtable }
        }
        if (-not $json.Contains('mcpServers') -or $null -eq $json['mcpServers']) { $json['mcpServers'] = [ordered]@{} }
        $json['mcpServers']['nexus-android'] = [ordered]@{ command = $python; args = $args_; env = $env_ }
        New-Item -ItemType Directory -Force $agyDir | Out-Null
        [IO.File]::WriteAllText($agyConfig, ($json | ConvertTo-Json -Depth 100), [Text.UTF8Encoding]::new($false))
        Write-Host 'Antigravity: nexus-android registered.'
    } else { Write-Host 'Antigravity not found (no ~\.gemini\antigravity): skipped.' }

    # --- Claude Code: registered by its own CLI; this script does not call it.
    $envFlags = ($env_.GetEnumerator() | ForEach-Object { "-e $($_.Key)=`"$($_.Value)`"" }) -join ' '
    Write-Host ''
    Write-Host 'Claude Code: run this command once to register the same server:'
    Write-Host "  claude mcp add nexus-android --scope user $envFlags -- `"$python`" -m nexus_android"
    Write-Host ''
}

if (-not $SkipTests) {
    # Tests under the same Python version as the environment just created.
    $version = & $python -c "import sys; print('%d.%d' % sys.version_info[:2])"
    & (Join-Path $project 'test.ps1') -Python $version
    if ($LASTEXITCODE) { throw 'Tests failed.' }
}
if ($Live) {
    & $python (Join-Path $project 'tests\live_smoke.py')
    if ($LASTEXITCODE) { throw 'Run on the phone failed.' }
}
Write-Host 'NexusAndroid installed. Start a new conversation in each client to load nexus-android.'
