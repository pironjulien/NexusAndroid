# NexusAndroid for Windows 10/11 (Windows PowerShell 5.1 or PowerShell 7), no administrator rights, nothing else to
# install (no Python, no .NET: the engine is one self-contained executable). Always run as a file, never from memory:
# download get.ps1 and SHA256SUMS.txt from the latest release, compare get.ps1's SHA-256 with its line in
# SHA256SUMS.txt (provenance: `gh attestation verify get.ps1 --repo pironjulien/NexusAndroid`), then
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\get.ps1
#
# It downloads the latest release's NexusAndroid-win-x64.zip or NexusAndroid-win-arm64.zip (this PC's architecture),
# checks its SHA-256 against the release's SHA256SUMS.txt before extracting anything (build provenance:
# `gh attestation verify <file> --repo pironjulien/NexusAndroid`), installs it into
# %USERPROFILE%\.nexus\nexus-android\releases\<version> (one folder per version, never overwritten while running), then
# runs `NexusAndroid.exe setup`: adb found or downloaded from Google (size and SHA-1 checked), the server declared to the
# AI apps found (Claude Code, Claude Desktop, Codex, Antigravity; each file backed up first), then the USB debugging
# steps, followed until the phone answers. Run it again to update.
#   -NoWait   declare the server but do not wait for the phone (run setup again later)
#   -NoDaemon do not install the shared HTTP daemon (127.0.0.1:16180, bearer token NEXUS_MCP_TOKEN, hidden logon task;
#             on a Nexus workstation, declared to the daemon supervisor instead)
# NEXUS_PACK=1 (the Nexus pack): no question, no wait; exit code 0 on success; a last "NEXUS_PACK_RESULT ..." line.
# For tests only: NEXUS_ANDROID_SOURCE (a local NexusAndroid-win-*.zip instead of the latest release) and
# NEXUS_ANDROID_HOME (another home folder: installation AND declarations go there, the real AI apps are never touched).
# A local package aimed at the real home folder is refused unless -Real is given.
[CmdletBinding()]
param([switch]$NoWait, [switch]$NoDaemon, [switch]$Real)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest is many times slower with its progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$Repository = 'pironjulien/NexusAndroid'
$Pack = $env:NEXUS_PACK -eq '1'
$Sandbox = [bool]$env:NEXUS_ANDROID_HOME
$UserHome = if ($Sandbox) { $env:NEXUS_ANDROID_HOME } else { $env:USERPROFILE }

function Say([string]$Text, [string]$Color = 'Gray') { if (-not $Pack) { Write-Host $Text -ForegroundColor $Color } }

function Get-Architecture {
    try {
        $os = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
        if ($os -eq 'Arm64') { return 'win-arm64' }
        if ($os -eq 'X64') { return 'win-x64' }
    } catch { Write-Verbose 'RuntimeInformation unavailable: environment variables used.' }
    $native = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    if ($native -eq 'ARM64') { return 'win-arm64' }
    if ($native -eq 'AMD64') { return 'win-x64' }
    throw "Unsupported Windows architecture ($native): NexusAndroid ships for x64 and Arm64."
}

function Get-LatestTag {
    # The latest release's tag from the redirect of github.com/<repo>/releases/latest: no GitHub API (60 anonymous
    # requests an hour per IP); the API stays as a fallback.
    try {
        $request = [Net.HttpWebRequest]::Create("https://github.com/$Repository/releases/latest")
        $request.AllowAutoRedirect = $false
        $request.UserAgent = 'NexusAndroid-get'
        $response = $request.GetResponse()
        try { $location = $response.Headers['Location'] } finally { $response.Close() }
        if ($location -match '/releases/tag/(v[0-9][^/?#]*)$') { return $Matches[1] }
    } catch { Write-Verbose "Redirect lookup failed: $($_.Exception.Message)" }
    return (Invoke-RestMethod -UseBasicParsing "https://api.github.com/repos/$Repository/releases/latest").tag_name
}

function Get-ExpectedHash([byte[]]$Manifest, [string]$Name) {
    $text = [Text.UTF8Encoding]::new($false, $true).GetString($Manifest).TrimStart([char]0xfeff)
    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    $expected = $null
    foreach ($line in ($text -split '\r?\n' | Where-Object { $_.Trim() })) {
        if ($line -cnotmatch '^([0-9a-f]{64})\s+\*?(\S+)\s*$') { throw 'SHA256SUMS.txt has an invalid entry: installation refused.' }
        if (-not $seen.Add($Matches[2])) { throw 'SHA256SUMS.txt lists a file more than once: installation refused.' }
        if ($Matches[2] -ceq $Name) { $expected = $Matches[1] }
    }
    if (-not $expected) { throw "$Name is absent from SHA256SUMS.txt: installation refused." }
    return $expected
}

function Install-NexusAndroid {
    $rid = Get-Architecture
    $name = "NexusAndroid-$rid.zip"
    $work = Join-Path ([IO.Path]::GetTempPath()) "nexusandroid-get-$PID"
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    try {
        if ($env:NEXUS_ANDROID_SOURCE) {
            $package = (Resolve-Path -LiteralPath $env:NEXUS_ANDROID_SOURCE).Path
            Say "Installing NexusAndroid from $package..."
        } else {
            $tag = Get-LatestTag
            $base = "https://github.com/$Repository/releases/download/$tag"
            Say "Downloading NexusAndroid $tag ($rid)..."
            $package = Join-Path $work $name
            Invoke-WebRequest -UseBasicParsing "$base/$name" -OutFile $package
            $manifest = (Invoke-WebRequest -UseBasicParsing "$base/SHA256SUMS.txt").RawContentStream.ToArray()
            $expected = Get-ExpectedHash $manifest $name
            $actual = (Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($expected -ne $actual) { throw "SHA-256 mismatch for $($name): the download is not the published package." }
        }
        $unpacked = Join-Path $work 'package'
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($package, $unpacked)
        $exe = Join-Path $unpacked 'NexusAndroid.exe'
        if (-not (Test-Path -LiteralPath $exe)) { throw 'Not a NexusAndroid package.' }
        $version = (& $exe --version | Select-Object -First 1) -replace '^NexusAndroid v(\S+).*$', '$1'
        if ($version -notmatch '^\d+\.\d+\.\d+') { throw 'The package does not start on this PC.' }
        $releases = Join-Path $UserHome '.nexus\nexus-android\releases'
        $target = Join-Path $releases $version
        if (-not (Test-Path -LiteralPath (Join-Path $target 'NexusAndroid.exe'))) {
            New-Item -ItemType Directory -Force -Path $releases | Out-Null
            $part = "$target.part-$PID"
            Move-Item -LiteralPath $unpacked -Destination $part
            Get-ChildItem -LiteralPath $part -Recurse -File | Unblock-File
            Move-Item -LiteralPath $part -Destination $target
        }
        # Older versions: removed once no AI app runs them any more (a running one stays until next time).
        Get-ChildItem -LiteralPath $releases -Directory | Where-Object { $_.Name -ne $version } |
            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
        Say "NexusAndroid $version is in $target." Green
        return (Join-Path $target 'NexusAndroid.exe')
    } finally {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Get-SharedToken([bool]$Workstation) {
    # Shared token of the Nexus daemons (rule common to NexusRelay, NexusAndroid, NexusBrowser): one token file is the
    # truth: NEXUS_MCP_TOKEN_FILE when set (a Nexus workstation's NexusSecure file), else %USERPROFILE%\.nexus\mcp-token.txt.
    # Reused when present, never regenerated; created once otherwise (stable sibling lock: two installers, one after the
    # other or at the same time, end with the same token). The user variable NEXUS_MCP_TOKEN gets its content; the AI
    # apps read ${NEXUS_MCP_TOKEN}, the token is never written into their configs. Returns the file's path.
    $file = [Environment]::GetEnvironmentVariable('NEXUS_MCP_TOKEN_FILE', 'Process')
    if (-not $file) { $file = [Environment]::GetEnvironmentVariable('NEXUS_MCP_TOKEN_FILE', 'User') }
    if (-not $file -and $Workstation -and $env:OneDrive) {
        $vault = Join-Path $env:OneDrive 'Documents\NexusSecure\agents\nexus-mcp-token.txt'
        if (Test-Path -LiteralPath $vault) { $file = $vault }
    }
    if (-not $file) { $file = Join-Path $UserHome '.nexus\mcp-token.txt' }
    New-Item -ItemType Directory -Force (Split-Path $file) | Out-Null
    $stream = $null
    foreach ($attempt in 1..50) {
        try {
            # Keep this sibling lock file: deleting it could split installers between different file identities.
            # The token itself stays readable by a daemon or caller while another installer resolves it.
            $stream = [IO.File]::Open("$file.lock", [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
            break
        } catch [IO.IOException] {
            if ($attempt -eq 50) { throw }
            Start-Sleep -Milliseconds 100
        }
    }
    try {
        $token = if (Test-Path -LiteralPath $file) { ([IO.File]::ReadAllText($file)).Trim() } else { '' }
        if (-not $token) {
            $bytes = New-Object byte[] 32
            $random = [Security.Cryptography.RandomNumberGenerator]::Create()
            try { $random.GetBytes($bytes) } finally { $random.Dispose() }
            $token = -join ($bytes | ForEach-Object { $_.ToString('x2') })
            [IO.File]::WriteAllText($file, $token, [Text.UTF8Encoding]::new($false))
        }
    } finally { $stream.Dispose() }
    if (-not $Sandbox -and [Environment]::GetEnvironmentVariable('NEXUS_MCP_TOKEN', 'User') -cne $token) {
        [Environment]::SetEnvironmentVariable('NEXUS_MCP_TOKEN', $token, 'User')
        Say 'User variable NEXUS_MCP_TOKEN set from the shared token file (restart the AI apps to see it).'
    }
    $env:NEXUS_MCP_TOKEN = $token
    return $file
}

function Install-Daemon([string]$Exe) {
    # Shared MCP daemon (127.0.0.1:16180): one nexus-android process for every session of Claude, Codex and Antigravity,
    # MCP Streamable HTTP with a bearer token. On a Nexus workstation, declared to the daemon supervisor (spec with key
    # exe and restart_request: the running daemon stops once idle and the supervisor starts this build). Elsewhere: a
    # hidden logon task. Either way the token is the shared one (Get-SharedToken).
    $port = 16180
    $state = Join-Path $UserHome '.nexus\nexus-android'
    $request = Join-Path $state 'daemon-restart.request'
    $utf8 = New-Object Text.UTF8Encoding $false
    $supervised = [bool](Get-ScheduledTask -TaskName 'Nexus MCP Daemons' -ErrorAction SilentlyContinue)
    $tokenFile = Get-SharedToken $supervised
    if ($supervised) {
        $specFile = Join-Path $UserHome '.nexus\daemons\specs\nexus-android.json'
        $spec = [ordered]@{ port = $port; exe = $Exe; args = @(); env = [ordered]@{ NEXUS_MCP_TOKEN_FILE = $tokenFile }; restart_request = $request }
        $text = $spec | ConvertTo-Json -Depth 5
        $before = if (Test-Path -LiteralPath $specFile) { [IO.File]::ReadAllText($specFile) } else { '' }
        if ($before -ne $text) {
            New-Item -ItemType Directory -Force (Split-Path $specFile) | Out-Null
            [IO.File]::WriteAllText($specFile, $text, $utf8)
            New-Item -ItemType Directory -Force $state | Out-Null
            [IO.File]::WriteAllText($request, 'force get.ps1', $utf8)
        }
        $Results['Daemon'] = 'supervised'
        return
    }
    # The daemon reads the same token file (NEXUS_MCP_TOKEN_FILE, else %USERPROFILE%\.nexus\mcp-token.txt).
    $task = 'NexusAndroid MCP Daemon'
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    # conhost --headless: the engine is a console program; no window ever opens.
    $action = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument "--headless `"$Exe`" --http $port" -WorkingDirectory (Split-Path $Exe)
    $settings = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $existing = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
    if ($existing -and ($existing.Actions | Select-Object -First 1).Arguments -ne $action.Arguments) {
        # New build: the running daemon (0.22.0 and later) stops itself once no call runs; the task is replaced.
        New-Item -ItemType Directory -Force $state | Out-Null
        [IO.File]::WriteAllText($request, 'force get.ps1', $utf8)
        Unregister-ScheduledTask -TaskName $task -Confirm:$false
        $existing = $null
    }
    if (-not $existing) {
        Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
            -Description "NexusAndroid MCP daemon (127.0.0.1:$port) shared by the AI apps, bearer token NEXUS_MCP_TOKEN." | Out-Null
    }
    if ((Get-ScheduledTask -TaskName $task).State -ne 'Running') { Start-ScheduledTask -TaskName $task }
    $Results['Daemon'] = 'task'
    Say "Shared daemon: http://127.0.0.1:$port/mcp (task '$task', token in NEXUS_MCP_TOKEN)."
}

$Results = [ordered]@{}
$code = 1
try {
    if ($env:NEXUS_ANDROID_SOURCE -and -not $Sandbox -and -not $Real) {
        throw 'NEXUS_ANDROID_SOURCE (a test package) is set but NEXUS_ANDROID_HOME is not: refusing to install for real. Set NEXUS_ANDROID_HOME to a test folder, or pass -Real.'
    }
    $exe = Install-NexusAndroid
    $setup = @('setup')
    if ($NoWait) { $setup += '--no-wait' }
    $saved = @{ USERPROFILE = $env:USERPROFILE; APPDATA = $env:APPDATA; LOCALAPPDATA = $env:LOCALAPPDATA; PATH = $env:PATH }
    if ($Sandbox) {
        # Declarations go to the test home, never the real AI apps: profile folders redirected, and no folder holding
        # an AI app's command line (claude, codex) left on the PATH.
        $env:USERPROFILE = $UserHome
        $env:APPDATA = Join-Path $UserHome 'AppData\Roaming'
        $env:LOCALAPPDATA = Join-Path $UserHome 'AppData\Local'
        $env:PATH = (($env:PATH -split ';') | Where-Object {
                $_ -and -not (Test-Path -LiteralPath (Join-Path $_ 'claude.exe')) -and -not (Test-Path -LiteralPath (Join-Path $_ 'claude.cmd')) -and
                -not (Test-Path -LiteralPath (Join-Path $_ 'codex.exe')) -and -not (Test-Path -LiteralPath (Join-Path $_ 'codex.cmd')) }) -join ';'
    }
    try {
        & $exe @setup
        $code = $LASTEXITCODE
    } finally { foreach ($key in $saved.Keys) { Set-Item "env:$key" $saved[$key] } }
    if ($Sandbox) { $null = Get-SharedToken $false }  # test home: the shared token file only, no user variable
    if (-not $Sandbox -and -not $NoDaemon) {
        try { Install-Daemon $exe } catch { Write-Warning "Shared daemon not installed ($($_.Exception.Message)); the stdio servers still work."; $Results['Daemon'] = 'failed' }
    }
} catch {
    Write-Host "NexusAndroid: $($_.Exception.Message)" -ForegroundColor Red
    if ($Pack) { Write-Output 'NEXUS_PACK_RESULT NexusAndroid=failed' }
    $code = 1
}
# Exit code: 0 ready, 1 adb or the phone not ready, 2 an AI app not configured, 3 both.
exit $code
