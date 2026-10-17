# Installe NexusAndroid sur ce PC (à relancer sans risque après une mise à jour).
#  - environnement Python hors des sources : ~\.nexus\nexus-android\venv
#  - serveur MCP « nexus-android » inscrit pour Claude Code, Codex et Antigravity (inscription remplacée, rien d'autre
#    n'est touché dans leurs configurations)
#  - tests sans téléphone ; -Live : essai sur le téléphone branché
# Usage : pwsh -NoProfile -File install.ps1 [-Live] [-SkipTests]
param([switch]$Live, [switch]$SkipTests)
$ErrorActionPreference = 'Stop'

$project = $PSScriptRoot
$state = Join-Path $HOME '.nexus\nexus-android'
$venv = Join-Path $state 'venv'
$pycache = Join-Path $state 'cache\pycache'
New-Item -ItemType Directory -Force $state | Out-Null

$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) { throw "uv introuvable : l'installer (https://docs.astral.sh/uv/) puis relancer." }
$env:UV_PROJECT_ENVIRONMENT = $venv
$env:UV_LINK_MODE = 'copy'
$env:PYTHONPYCACHEPREFIX = $pycache
$env:PYTHONUTF8 = '1'

# Python système de préférence : celui de uv sous %AppData% est virtualisé par l'application Claude.
$systemPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'
if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) {
    if (Test-Path $systemPython) { & $uv venv --python $systemPython $venv } else { & $uv venv --python 3.14 $venv }
}
& $uv sync --frozen --project $project
if ($LASTEXITCODE) { throw 'uv sync a échoué.' }

$adb = (Get-Command adb -ErrorAction SilentlyContinue).Source
if (-not $adb) {
    $adb = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages') -Filter adb.exe -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $adb) { Write-Warning 'adb introuvable : winget install Google.PlatformTools' }

# Serveur scrcpy (Genymobile, Apache 2.0) : saisie avec accents par le presse-papiers. Version et empreinte figées,
# identiques à src\nexus_android\clip.py ; le fichier n'est gardé que si son SHA-256 est celui publié par GitHub.
$scrcpyVersion = '5.0.1'
$scrcpySha = '764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536'
$scrcpy = Join-Path $state "tools\scrcpy-server-v$scrcpyVersion"
if (-not (Test-Path $scrcpy) -or (Get-FileHash $scrcpy -Algorithm SHA256).Hash -ne $scrcpySha.ToUpper()) {
    New-Item -ItemType Directory -Force (Split-Path $scrcpy) | Out-Null
    $tmp = "$scrcpy.download"
    Invoke-WebRequest "https://github.com/Genymobile/scrcpy/releases/download/v$scrcpyVersion/scrcpy-server-v$scrcpyVersion" -OutFile $tmp
    if ((Get-FileHash $tmp -Algorithm SHA256).Hash -ne $scrcpySha.ToUpper()) {
        Remove-Item $tmp -Force
        Write-Warning 'scrcpy-server : empreinte inattendue, fichier rejeté (saisie avec accents indisponible).'
    } else { Move-Item $tmp $scrcpy -Force; Write-Host "scrcpy-server v$scrcpyVersion vérifié." }
}

$python = Join-Path $venv 'Scripts\python.exe'
$env_ = [ordered]@{ PYTHONUTF8 = '1'; PYTHONPYCACHEPREFIX = $pycache }
if ($adb) { $env_['NEXUS_ANDROID_ADB'] = $adb }
$args_ = @('-m', 'nexus_android')

# --- Claude Code (portée utilisateur)
$claude = (Get-Command claude -ErrorAction SilentlyContinue).Source
if ($claude) {
    $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & $claude mcp remove nexus-android -s user *> $null
    $ErrorActionPreference = $previous
    $envArgs = foreach ($k in $env_.Keys) { '-e'; "$k=$($env_[$k])" }
    & $claude mcp add @envArgs --scope user --transport stdio nexus-android '--' $python @args_
    if ($LASTEXITCODE) { throw 'claude mcp add a échoué.' }
    Write-Host 'Claude Code : nexus-android inscrit.'
} else { Write-Warning 'CLI claude introuvable : Claude Code non inscrit.' }

# --- Codex : table [mcp_servers.nexus-android] de ~\.codex\config.toml, remplacée en place
$codexConfig = Join-Path $HOME '.codex\config.toml'
if (Test-Path $codexConfig) {
    $q = { param($s) "'" + $s + "'" }  # chaîne TOML littérale : antislashs Windows conservés
    $block = @('[mcp_servers.nexus-android]', "command = $(& $q $python)",
        "args = [$(($args_ | ForEach-Object { & $q $_ }) -join ', ')]", '',
        '[mcp_servers.nexus-android.env]') + ($env_.Keys | ForEach-Object { "$_ = $(& $q $env_[$_])" })
    $lines = [Collections.Generic.List[string]](Get-Content $codexConfig)
    $start = $lines.FindIndex({ param($l) $l -match '^\s*\[mcp_servers\.nexus-android\]\s*$' })
    if ($start -ge 0) {
        $end = $start + 1
        while ($end -lt $lines.Count -and ($lines[$end] -notmatch '^\s*\[' -or $lines[$end] -match '^\s*\[mcp_servers\.nexus-android\.')) { $end++ }
        $lines.RemoveRange($start, $end - $start)
        $lines.InsertRange($start, [string[]]($block + ''))
    } else {
        if ($lines.Count -and $lines[$lines.Count - 1] -ne '') { $lines.Add('') }
        $lines.AddRange([string[]]$block)
    }
    $backup = Join-Path $state ('backups\codex-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.toml')
    New-Item -ItemType Directory -Force (Split-Path $backup) | Out-Null
    Copy-Item $codexConfig $backup
    [IO.File]::WriteAllText($codexConfig, ($lines -join "`n") + "`n", [Text.UTF8Encoding]::new($false))
    Write-Host 'Codex : nexus-android inscrit.'
} else { Write-Warning 'Codex absent de ce PC : non inscrit.' }

# --- Antigravity : ~\.gemini\config\mcp_config.json
$agyConfig = Join-Path $HOME '.gemini\config\mcp_config.json'
if (Test-Path $agyConfig) {
    $json = Get-Content $agyConfig -Raw | ConvertFrom-Json
    if (-not $json.PSObject.Properties['mcpServers']) { $json | Add-Member mcpServers ([PSCustomObject]@{}) }
    $server = [PSCustomObject]@{ command = $python; args = $args_; env = [PSCustomObject]$env_ }
    $json.mcpServers | Add-Member -NotePropertyName 'nexus-android' -NotePropertyValue $server -Force
    [IO.File]::WriteAllText($agyConfig, ($json | ConvertTo-Json -Depth 100), [Text.UTF8Encoding]::new($false))
    Write-Host 'Antigravity : nexus-android inscrit.'
} else { Write-Warning 'Antigravity absent de ce PC : non inscrit.' }

if (-not $SkipTests) {
    & (Join-Path $project 'test.ps1')
}
if ($Live) {
    & $uv run --frozen --project $project python (Join-Path $project 'tests\live_smoke.py')
    if ($LASTEXITCODE) { throw 'Essai sur le téléphone en échec.' }
}
# Empreinte de l'installation : comparée aux sources par un outil de synchronisation pour savoir s'il faut réinstaller.
$stamp = (Get-FileHash (Join-Path $project 'uv.lock')).Hash + ' ' + (Get-FileHash (Join-Path $project 'install.ps1')).Hash
Set-Content (Join-Path $state 'installed.stamp') $stamp -Encoding utf8NoBOM
Write-Host 'NexusAndroid installé. Ouvrir une nouvelle conversation dans chaque client pour charger nexus-android.'
