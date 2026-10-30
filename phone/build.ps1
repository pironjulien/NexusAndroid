# Rebuilds native\nexus-query.dex from NexusQuery.java (only needed after changing the Java source; the
# dex is committed and embedded in the engine). Needs a JDK 17+ (winget install Microsoft.OpenJDK.21); d8 comes from
# Google's Maven repository, pinned and checked, cached under ~\.nexus\nexus-android\tools\build.
$ErrorActionPreference = 'Stop'
$r8Version = '9.5.26'
$r8Sha = '3502F087388CE95BBF5254555927F09385DC2636873964D0BD53F3EAF4FB49AD'
$tools = Join-Path $HOME '.nexus\nexus-android\tools\build'
$r8 = Join-Path $tools "r8-$r8Version.jar"
if (-not (Test-Path $r8) -or (Get-FileHash $r8 -Algorithm SHA256).Hash -ne $r8Sha) {
    New-Item -ItemType Directory -Force $tools | Out-Null
    Invoke-WebRequest "https://dl.google.com/dl/android/maven2/com/android/tools/r8/$r8Version/r8-$r8Version.jar" -OutFile "$r8.download" -UseBasicParsing
    if ((Get-FileHash "$r8.download" -Algorithm SHA256).Hash -ne $r8Sha) { Remove-Item "$r8.download"; throw 'r8: unexpected SHA-256.' }
    Move-Item "$r8.download" $r8 -Force
}
$jdk = if ($env:JAVA_HOME) { $env:JAVA_HOME } else {
    (Get-ChildItem 'C:\Program Files\Microsoft', 'C:\Program Files\Eclipse Adoptium', 'C:\Program Files\Java' -Directory -Filter 'jdk-*' -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -First 1).FullName
}
if (-not $jdk) { throw 'No JDK found: winget install Microsoft.OpenJDK.21' }
$work = Join-Path $tools ("work-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
try {
    New-Item -ItemType Directory -Force "$work\stubs", "$work\classes", "$work\dex" | Out-Null
    $stubs = Get-ChildItem "$PSScriptRoot\stubs" -Recurse -Filter *.java | ForEach-Object FullName
    & "$jdk\bin\javac.exe" --release 11 -nowarn -d "$work\stubs" @stubs
    if ($LASTEXITCODE) { throw 'javac (stubs) failed.' }
    & "$jdk\bin\javac.exe" --release 11 -encoding UTF-8 -cp "$work\stubs" -d "$work\classes" "$PSScriptRoot\NexusQuery.java"
    if ($LASTEXITCODE) { throw 'javac failed.' }
    $classes = @(Get-ChildItem "$work\classes" -Recurse -Filter *.class | ForEach-Object FullName)  # tableau même pour une seule classe
    & "$jdk\bin\java.exe" -cp $r8 com.android.tools.r8.D8 --release --min-api 24 --lib $jdk --output "$work\dex" @classes
    if ($LASTEXITCODE) { throw 'd8 failed.' }
    Copy-Item "$work\dex\classes.dex" (Join-Path $PSScriptRoot '..\native\nexus-query.dex') -Force
    Write-Host 'nexus-query.dex rebuilt.'
} finally {
    $workAbsolute = [IO.Path]::GetFullPath($work)
    $toolsAbsolute = [IO.Path]::GetFullPath($tools).TrimEnd('\')
    if (-not $workAbsolute.StartsWith($toolsAbsolute + '\', [StringComparison]::OrdinalIgnoreCase) -or $workAbsolute -eq $toolsAbsolute) { throw 'Build cleanup target outside its workspace refused.' }
    if (Test-Path -LiteralPath $workAbsolute) { Remove-Item -LiteralPath $workAbsolute -Recurse -Force -ErrorAction SilentlyContinue }
}
