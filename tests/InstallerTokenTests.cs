using System.Diagnostics;
using System.Runtime.CompilerServices;

namespace NexusAndroid.Tests;

/// <summary>Run the installer's real token function in separate Windows PowerShell 5.1 processes, without installing
/// anything or changing the user's environment. A barrier makes both installers enter token initialization together.</summary>
public sealed class InstallerTokenTests
{
    private static string InstallerSource([CallerFilePath] string source = "") =>
        File.ReadAllText(Path.Combine(Path.GetDirectoryName(source)!, "..", "get.ps1"));

    private static string TokenFunction()
    {
        var installer = InstallerSource();
        var start = installer.IndexOf("function Get-SharedToken(", StringComparison.Ordinal);
        var end = installer.IndexOf("function Install-Daemon(", start, StringComparison.Ordinal);
        return installer[start..end];
    }

    [Fact]
    public async Task WindowsPowerShellPrefersItsOwnStandardModulesOverInheritedCoreModules()
    {
        var home = Path.Combine(TestPaths.Root, "install-modules-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(home);
        var script = Path.Combine(home, "module-fixture.ps1");
        var installer = InstallerSource();
        var prefix = installer[..installer.IndexOf("$Repository =", StringComparison.Ordinal)];
        File.WriteAllText(script, prefix + "\n(Get-FileHash -LiteralPath $env:NEXUS_TEST_READY -Algorithm SHA256).Hash | Set-Content -LiteralPath $env:NEXUS_TEST_RESULT\n");
        var input = Path.Combine(home, "input.txt");
        File.WriteAllText(input, "synthetic checksum input");
        var result = Path.Combine(home, "result.txt");
        var desktopModules = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell", "v1.0", "Modules");
        var coreModules = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "PowerShell", "7", "Modules");
        Assert.True(Directory.Exists(coreModules), "PowerShell 7 is required for the inherited-module regression fixture.");
        var start = new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell", "v1.0", "powershell.exe"))
        { UseShellExecute = false, CreateNoWindow = true, RedirectStandardError = true };
        foreach (var arg in new[] { "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script }) start.ArgumentList.Add(arg);
        start.Environment["PSModulePath"] = coreModules + ";" + desktopModules;
        start.Environment["NEXUS_TEST_READY"] = input;
        start.Environment["NEXUS_TEST_RESULT"] = result;
        using var process = Process.Start(start)!;
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(TestContext.Current.CancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(30));
        try { await process.WaitForExitAsync(timeout.Token); }
        finally { if (!process.HasExited) process.Kill(true); }
        Assert.True(process.ExitCode == 0, await process.StandardError.ReadToEndAsync(TestContext.Current.CancellationToken));
        Assert.Equal(Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(input))), File.ReadAllText(result).Trim());
    }

    private static Process Start(string script, string home, string ready, string go, string result)
    {
        var executable = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell", "v1.0", "powershell.exe");
        var start = new ProcessStartInfo(executable) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (var arg in new[] { "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script }) start.ArgumentList.Add(arg);
        start.Environment["NEXUS_TEST_HOME"] = home;
        start.Environment["NEXUS_TEST_READY"] = ready;
        start.Environment["NEXUS_TEST_GO"] = go;
        start.Environment["NEXUS_TEST_RESULT"] = result;
        start.Environment["NEXUS_MCP_TOKEN_FILE"] = Path.Combine(home, ".nexus", "mcp-token.txt");
        return Process.Start(start)!;
    }

    private static async Task WaitForFile(string file)
    {
        var deadline = DateTime.UtcNow.AddSeconds(30);
        while (!File.Exists(file) && DateTime.UtcNow < deadline) await Task.Delay(10, TestContext.Current.CancellationToken);
        Assert.True(File.Exists(file), "PowerShell fixture did not reach the initialization barrier.");
    }

    [Theory]
    [InlineData(null, false)]
    [InlineData("", false)]
    [InlineData("preexisting-shared-token", false)]
    [InlineData("preexisting-shared-token", true)]
    public async Task SimultaneousInstallersKeepOneSharedToken(string? existing, bool daemonReading)
    {
        var home = Path.Combine(TestPaths.Root, "install-token-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(Path.Combine(home, ".nexus"));
        var file = Path.Combine(home, ".nexus", "mcp-token.txt");
        if (existing is not null) File.WriteAllText(file, existing);
        // A daemon or caller may read the existing token while another installer resolves that same path.
        using var daemonRead = daemonReading ? File.Open(file, FileMode.Open, FileAccess.Read, FileShare.Read) : null;
        var script = Path.Combine(home, "token-fixture.ps1");
        File.WriteAllText(script, """
            $ErrorActionPreference = 'Stop'
            $Sandbox = $true
            $UserHome = $env:NEXUS_TEST_HOME
            """ + "\n" + TokenFunction() + "\n" + """
            [IO.File]::WriteAllText($env:NEXUS_TEST_READY, 'ready')
            $deadline = [DateTime]::UtcNow.AddSeconds(30)
            while (-not (Test-Path -LiteralPath $env:NEXUS_TEST_GO)) {
                if ([DateTime]::UtcNow -ge $deadline) { throw 'Fixture barrier timed out.' }
                Start-Sleep -Milliseconds 10
            }
            $file = Get-SharedToken $false
            [IO.File]::WriteAllText($env:NEXUS_TEST_RESULT, [IO.File]::ReadAllText($file))
            """);
        var go = Path.Combine(home, "go");
        var ready = new[] { Path.Combine(home, "ready-1"), Path.Combine(home, "ready-2") };
        var results = new[] { Path.Combine(home, "result-1"), Path.Combine(home, "result-2") };
        using var first = Start(script, home, ready[0], go, results[0]);
        using var second = Start(script, home, ready[1], go, results[1]);
        try
        {
            await Task.WhenAll(ready.Select(WaitForFile));
            File.WriteAllText(go, "go");
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(TestContext.Current.CancellationToken);
            timeout.CancelAfter(TimeSpan.FromSeconds(30));
            await Task.WhenAll(first.WaitForExitAsync(timeout.Token), second.WaitForExitAsync(timeout.Token));
            foreach (var process in new[] { first, second })
                Assert.True(process.ExitCode == 0, await process.StandardError.ReadToEndAsync(TestContext.Current.CancellationToken));
            var token = File.ReadAllText(file);
            if (!string.IsNullOrEmpty(existing)) Assert.Equal(existing, token);
            else Assert.Matches("^[a-f0-9]{64}$", token);
            Assert.All(results, result => Assert.Equal(token, File.ReadAllText(result)));
        }
        finally
        {
            foreach (var process in new[] { first, second }) if (!process.HasExited) process.Kill(true);
        }
    }
}
