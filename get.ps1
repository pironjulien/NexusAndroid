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
# NEXUS_RELEASE_TAG: install that release (vX.Y.Z, or a development build vX.Y.Z-dev.N) instead of the latest.
# For tests only: NEXUS_ANDROID_SOURCE (a local NexusAndroid-win-*.zip instead of the latest release) and
# NEXUS_ANDROID_HOME (another home folder: installation AND declarations go there, the real AI apps are never touched).
# A local package aimed at the real home folder is refused unless -Real is given.
[CmdletBinding()]
param([switch]$NoWait, [switch]$NoDaemon, [switch]$Real)

$ErrorActionPreference = 'Stop'
# A Windows PowerShell child of PowerShell 7 inherits Core-only module folders ahead of its own modules.
# Restore the current host's standard module directory first (Get-FileHash, Unblock-File...), retaining user modules.
if ($PSVersionTable.PSEdition -eq 'Desktop') {
    $desktopModules = Join-Path $PSHOME 'Modules'
    $env:PSModulePath = (@($desktopModules) + @($env:PSModulePath -split ';' | Where-Object {
        $_ -and $_.TrimEnd('\') -ine $desktopModules.TrimEnd('\')
    })) -join ';'
}
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
    # NEXUS_RELEASE_TAG (set by NexusSystem for its development channel): that release instead, a stable vX.Y.Z or a
    # development vX.Y.Z-dev.N build, checked against its own SHA256SUMS.txt like any other.
    if ($env:NEXUS_RELEASE_TAG) {
        if ($env:NEXUS_RELEASE_TAG -notmatch '^v\d+\.\d+\.\d+(-dev\.\d+)?$') { throw "NEXUS_RELEASE_TAG is not a release tag: $env:NEXUS_RELEASE_TAG" }
        return $env:NEXUS_RELEASE_TAG
    }
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

function Move-StagedFolder([string]$From, [string]$To) {
    # A folder cannot be renamed while any file inside it is open, whoever holds it. This installer never runs or keeps
    # open anything in the staged folder, but antivirus and indexing services open freshly written executables for a
    # moment to scan them ("being used by another process", seen in NexusAGYmail's installer on a fresh Windows runner).
    # Only that external, transient hold (sharing, lock or access violation while the staged folder is still there) is
    # waited out, at most 10 s; any other error, or a longer hold, fails.
    $transient = 0x80070020, 0x80070021, 0x80070005  # ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION, ERROR_ACCESS_DENIED
    for ($attempt = 1; ; $attempt++) {
        try { [IO.Directory]::Move($From, $To); return }
        catch {
            $failure = $_.Exception
            while ($failure.InnerException -and $failure -is [Management.Automation.MethodInvocationException]) { $failure = $failure.InnerException }
            $held = ($failure -is [IO.IOException] -or $failure -is [UnauthorizedAccessException]) -and
                ($transient -contains $failure.HResult) -and (Test-Path -LiteralPath $From) -and -not (Test-Path -LiteralPath $To)
            if (-not $held -or $attempt -ge 40) { throw "Cannot move $From to $($To): $($failure.Message)" }
            Start-Sleep -Milliseconds 250
        }
    }
}

function Get-EngineImages {
    # Image paths of the running NexusAndroid processes, read by QueryFullProcessImageNameW with
    # PROCESS_QUERY_LIMITED_INFORMATION: unlike Get-Process .Path (empty for an elevated process), it reads every process
    # of this user. Unknown: one runs but cannot be read at all; no folder is then taken as unused.
    if (-not ('NexusAndroidProcessImage' -as [type])) {
        Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices; using System.Text;
public static class NexusAndroidProcessImage {
    [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr OpenProcess(uint access, bool inherit, int id);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll")] static extern bool GetExitCodeProcess(IntPtr handle, out uint code);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool QueryFullProcessImageNameW(IntPtr process, uint flags, StringBuilder name, ref uint size);
    // Image path of a process; "" once it has exited; null while it runs but cannot be read.
    public static string Read(int id) {
        IntPtr handle = OpenProcess(0x1000, false, id);  // PROCESS_QUERY_LIMITED_INFORMATION
        if (handle == IntPtr.Zero) return Marshal.GetLastWin32Error() == 87 ? "" : null;  // 87: no such process
        try {
            StringBuilder name = new StringBuilder(32768); uint size = (uint)name.Capacity;
            if (QueryFullProcessImageNameW(handle, 0, name, ref size)) return name.ToString(0, (int)size);
            uint code; return GetExitCodeProcess(handle, out code) && code != 259 ? "" : null;  // 259: STILL_ACTIVE
        } finally { CloseHandle(handle); }
    }
}
'@
    }
    $paths = New-Object Collections.Generic.List[string]; $unknown = $false
    foreach ($process in @(Get-Process -Name NexusAndroid -ErrorAction SilentlyContinue)) {
        $path = [NexusAndroidProcessImage]::Read($process.Id)
        if ($null -eq $path) { $unknown = $true } elseif ($path) { $paths.Add($path) }
    }
    [pscustomobject]@{ Paths = $paths.ToArray(); Unknown = $unknown }
}

function Test-FolderInUse([string]$Folder, $Images = (Get-EngineImages)) {
    # A folder may still be used when a NexusAndroid process cannot be read, when one runs from it (its executable is
    # in it), or when its engine.lock is held: every engine from 0.24.2 holds the one of its folder for its whole life
    # (EngineLock.cs), whatever spelling of the path started it. Windows renames the folder of a running single-file
    # program and deletes every file beside it (only mapped, no file open), so neither a rename nor a delete can tell.
    if ($Images.Unknown) { return $true }
    $prefix = $Folder.TrimEnd('\') + '\'
    if ($Images.Paths | Where-Object { $_.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) }) { return $true }
    $lock = Join-Path $Folder 'engine.lock'
    if (Test-Path -LiteralPath $lock -PathType Leaf) {
        try { [IO.File]::Open($lock, 'Open', 'ReadWrite', 'None').Dispose() } catch { return $true }
    }
    return $false
}

function Install-NexusAndroid {
    $rid = Get-Architecture
    $name = "NexusAndroid-$rid.zip"
    $work = Join-Path ([IO.Path]::GetTempPath()) "nexusandroid-get-$PID"
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    # Staged next to the releases folder (same volume: the final move is a rename), never inside it: Remove-OldReleases
    # removes every other folder of releases.
    $state = Join-Path $UserHome '.nexus\nexus-android'
    $releases = Join-Path $state 'releases'
    New-Item -ItemType Directory -Force -Path $releases | Out-Null
    $staged = Join-Path $state ('staging-' + [guid]::NewGuid().ToString('N'))
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
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($package, $staged)
        $exe = Join-Path $staged 'NexusAndroid.exe'
        if (-not (Test-Path -LiteralPath $exe)) { throw 'Not a NexusAndroid package.' }
        Get-ChildItem -LiteralPath $staged -Recurse -File | Unblock-File
        # Held by each engine run from the installed folder (EngineLock.cs): Remove-OldReleases sees it in use.
        [IO.File]::WriteAllText((Join-Path $staged 'engine.lock'), '')
        # The version from the executable's version resource, without running it: a program just run from the staged
        # folder is what scanners open right after, when the folder has to be renamed.
        $version = "$((Get-Item -LiteralPath $exe).VersionInfo.ProductVersion)".Split('+')[0].Trim()
        if ($version -notmatch '^\d+\.\d+\.\d+$') { throw 'Not a NexusAndroid package (no version).' }
        # One folder per version, never overwritten: a running server keeps its own executable.
        $target = Join-Path $releases $version
        $installed = Join-Path $target 'NexusAndroid.exe'
        $moved = $false
        if (-not (Test-Path -LiteralPath $installed)) {
            if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }  # an interrupted install
            Move-StagedFolder $staged $target
            $moved = $true
        }
        # Run once in place: the package must start on this PC's architecture and report the version it carries.
        $output = @(& $installed --version)
        $exit = $LASTEXITCODE
        $answer = "$($output | Select-Object -First 1)" -replace '^NexusAndroid v(\S+).*$', '$1'
        if ($exit -or $answer -ne $version) {
            if ($moved) { Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue }
            throw "The package does not start on this PC ($rid)."
        }
        Say "NexusAndroid $version is in $target." Green
        return $installed
    } finally {
        Remove-Item -LiteralPath $work, $staged -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Remove-OldReleases([string]$Exe) {
    # Older versions go only once ~\.nexus\nexus-android\engine.txt (written by setup, read by the Antigravity
    # Marketplace launcher) names this install: removing them earlier would leave that pointer on a deleted folder
    # whenever setup stops first. A version an AI app still runs stays until next time.
    $state = Join-Path $UserHome '.nexus\nexus-android'
    $pointer = Join-Path $state 'engine.txt'
    $recorded = if (Test-Path -LiteralPath $pointer) { ([IO.File]::ReadAllText($pointer)).Trim() } else { '' }
    if ($recorded -and -not [IO.Path]::IsPathRooted($recorded)) { $recorded = Join-Path $state $recorded }
    if (-not $recorded -or [IO.Path]::GetFullPath($recorded) -ine [IO.Path]::GetFullPath($Exe)) {
        Say 'Older NexusAndroid versions kept: engine.txt does not name this install yet (run setup again).' Yellow
        return
    }
    # A version folder still used (Test-FolderInUse) stays whole: deleting it file by file would leave a running engine
    # with its executable alone (Windows only refuses to delete the mapped program), every other file gone. An unused
    # one is renamed aside first (refused while a file in it is open, as the lock of an engine starting meanwhile), then
    # deleted; a retired-* folder that did not go at once goes on a later run.
    $keep = Split-Path -Leaf (Split-Path -Parent $Exe)
    $images = Get-EngineImages
    foreach ($left in @(Get-ChildItem -LiteralPath $state -Directory -Filter 'retired-*' -ErrorAction SilentlyContinue)) {
        if (-not (Test-FolderInUse $left.FullName $images)) { Remove-Item -LiteralPath $left.FullName -Recurse -Force -ErrorAction SilentlyContinue }
    }
    foreach ($old in @(Get-ChildItem -LiteralPath (Join-Path $state 'releases') -Directory -ErrorAction SilentlyContinue)) {
        if ($old.Name -eq $keep -or (Test-FolderInUse $old.FullName $images)) { continue }
        $retired = Join-Path $state ('retired-' + [guid]::NewGuid().ToString('N'))
        try { [IO.Directory]::Move($old.FullName, $retired) } catch { continue }
        Remove-Item -LiteralPath $retired -Recurse -Force -ErrorAction SilentlyContinue
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
    try { Remove-OldReleases $exe } catch { Write-Warning "Older versions not removed ($($_.Exception.Message))." }
} catch {
    Write-Host "NexusAndroid: $($_.Exception.Message)" -ForegroundColor Red
    if ($Pack) { Write-Output 'NEXUS_PACK_RESULT NexusAndroid=failed' }
    $code = 1
}
# Exit code: 0 ready, 1 adb or the phone not ready, 2 an AI app not configured, 3 both.
exit $code
