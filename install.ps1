# Installe NexusAndroid sur ce PC (à relancer sans risque après une mise à jour).
#  - environnement Python hors des sources : ~\.nexus\nexus-android\venv
#  - démon partagé déclaré au superviseur Nexus (127.0.0.1:16180, jeton NexusSecure) ; « nexus-android » inscrit par URL
#    pour Antigravity (inscription remplacée, rien d'autre n'est touché) ; Codex par son plugin perso nexus-android ;
#    Claude Code le reçoit par le plugin nexus-android du catalogue « nexus » (NexusClaude)
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

# Démon partagé (un seul processus pour Claude, Codex et Antigravity) : déclaré au superviseur des démons Nexus
# (NexusInfra\ops\workstation\nexus-mcp-daemons.py, tâche « Nexus MCP Daemons »), joint par URL avec un jeton.
$port = 16180
$url = "http://127.0.0.1:$port/mcp"
$tokenFile = Join-Path $env:OneDrive 'Documents\NexusSecure\agents\nexus-mcp-token.txt'
if (-not (Test-Path $tokenFile)) {
    $bytes = [byte[]]::new(32); [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    New-Item -ItemType Directory -Force (Split-Path $tokenFile) | Out-Null
    [IO.File]::WriteAllText($tokenFile, [Convert]::ToHexString($bytes).ToLower())
}
$token = (Get-Content $tokenFile -Raw).Trim()
$specs = Join-Path $HOME '.nexus\daemons\specs'
New-Item -ItemType Directory -Force $specs | Out-Null
$spec = [ordered]@{ port = $port; python = $python; args = $args_; env = $env_ }
[IO.File]::WriteAllText((Join-Path $specs 'nexus-android.json'), ($spec | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
# Nouveau code : arrêter le démon en place, le superviseur le relance (les clients se reconnectent seuls).
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -match 'nexus_android --http' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
if (-not (Get-ScheduledTask -TaskName 'Nexus MCP Daemons' -ErrorAction SilentlyContinue)) {
    Write-Warning 'Superviseur des démons absent : lancer NexusInfra\ops\workstation\Sync-NexusPoste.ps1 -Apply.'
}

# --- Claude Code : par le plugin nexus-android du catalogue « nexus » (NexusClaude\plugins, activé par
#     NexusClaude\claude-settings.json ; jeton dans la variable NEXUS_MCP_TOKEN des réglages, posée par Sync-NexusPoste).
#     Aucune inscription ici (aucune CLI Claude).

# --- Codex : serveur par le plugin perso nexus-android de Codex (NexusCodex\scripts\installer-plugins-nexus.ps1,
#     lancé par Sync-NexusPoste) ; plus de table [mcp_servers] ici.

# --- Antigravity : ~\.gemini\config\mcp_config.json
$agyConfig = Join-Path $HOME '.gemini\config\mcp_config.json'
if (Test-Path $agyConfig) {
    $json = Get-Content $agyConfig -Raw | ConvertFrom-Json
    if (-not $json.PSObject.Properties['mcpServers']) { $json | Add-Member mcpServers ([PSCustomObject]@{}) }
    $server = [PSCustomObject]@{ serverUrl = $url; headers = [PSCustomObject]@{ Authorization = "Bearer $token" } }
    $json.mcpServers | Add-Member -NotePropertyName 'nexus-android' -NotePropertyValue $server -Force
    $json.mcpServers.PSObject.Properties.Remove('nexus-android-http')
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
