using System.Diagnostics;
using System.Runtime.CompilerServices;

namespace NexusAndroid.Tests;

/// <summary>get.ps1's clean-up of older versions (Remove-OldReleases) in a test home, under PowerShell 7 and Windows
/// PowerShell 5.1. A version folder an engine may still use stays whole: before 0.24.2 the clean-up deleted it file by
/// file, and a running engine was left with its executable alone (releases\0.24.0 seen holding only NexusAndroid.exe).
/// In use: its engine.lock held, a running engine's image in it, or a running engine that cannot be read. Unused
/// folders are renamed aside then deleted, and a retired-* folder that did not go at once goes on a later run.</summary>
public sealed class ReleaseCleanupTests : IDisposable
{
    private readonly string root = Path.Join(TestPaths.Root, "cleanup-" + Guid.NewGuid().ToString("N"));
    private string Home => Path.Join(root, "home");
    private string State => Path.Join(Home, ".nexus", "nexus-android");
    private string Releases => Path.Join(State, "releases");
    private string Current => Path.Join(Releases, "2.0.0", "NexusAndroid.exe");

    private static string InstallerSource([CallerFilePath] string source = "") =>
        File.ReadAllText(Path.Join(Path.GetDirectoryName(source)!, "..", "get.ps1"));

    public ReleaseCleanupTests()
    {
        Version("2.0.0");
        File.WriteAllText(Path.Join(State, "engine.txt"), Current);  // what setup writes before get.ps1 removes anything
    }

    public void Dispose()
    {
        try
        {
            Directory.Delete(root, true);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { /* removed with TestPaths.Root at exit */ }
    }

    /// <summary>A version folder as get.ps1 installs it: the engine, its other files, and an engine.lock.</summary>
    private string Version(string name, bool locked = true)
    {
        string folder = Path.Join(Releases, name);
        Directory.CreateDirectory(Path.Join(folder, "plugin"));
        File.WriteAllText(Path.Join(folder, "NexusAndroid.exe"), "engine " + name);
        File.WriteAllText(Path.Join(folder, "README.md"), "readme");
        File.WriteAllText(Path.Join(folder, "plugin", "plugin.json"), "{}");
        if (locked)
            File.WriteAllText(Path.Join(folder, "engine.lock"), "");
        return folder;
    }

    private string Retired(string name)
    {
        string folder = Path.Join(State, "retired-" + name);
        Directory.CreateDirectory(folder);
        File.WriteAllText(Path.Join(folder, "NexusAndroid.exe"), "left over");
        return folder;
    }

    /// <summary>Remove-OldReleases on the test home; <paramref name="images"/> replaces the process scan when given.</summary>
    private void Clean(string shell, string? images = null)
    {
        string installer = InstallerSource();
        string definitions = installer[..installer.IndexOf("$Results = [ordered]@{}", StringComparison.Ordinal)];
        string script = Path.Join(root, "clean-" + Guid.NewGuid().ToString("N") + ".ps1");
        File.WriteAllText(script, definitions + "\n" +
            (images is null ? "" : "function Get-EngineImages { " + images + " }\n") +
            $"Remove-OldReleases '{Current}'\n'done'\n");
        var info = new ProcessStartInfo(shell)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            RedirectStandardInput = true,
        };
        foreach (string a in new[] { "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script })
            info.ArgumentList.Add(a);
        info.Environment["NEXUS_ANDROID_HOME"] = Home;
        info.Environment["NEXUS_PACK"] = "1";
        using var process = Process.Start(info)!;
        process.StandardInput.Close();
        var stdout = process.StandardOutput.ReadToEndAsync(TestContext.Current.CancellationToken);
        var stderr = process.StandardError.ReadToEndAsync(TestContext.Current.CancellationToken);
        if (!process.WaitForExit(60_000))
        {
            process.Kill(true);
            Assert.Fail("Remove-OldReleases did not finish");
        }
        string output = stdout.Result + stderr.Result;
        Assert.True(process.ExitCode == 0 && output.Trim().EndsWith("done", StringComparison.Ordinal), output);
    }

    private static string None => "[pscustomobject]@{ Paths = @(); Unknown = $false }";

    private static string Snapshot(string folder) => string.Join('\n', Directory.GetFiles(folder, "*", SearchOption.AllDirectories)
        .Select(f => Path.GetRelativePath(folder, f) + "|" + File.ReadAllText(f)).Order(StringComparer.OrdinalIgnoreCase));

    [Theory]
    [InlineData("pwsh")]
    [InlineData("powershell")]
    public void FoldersInUseStayWholeAndUnusedOnesGo(string shell)
    {
        string locked = Version("1.0.0");
        string running = Version("0.9.0", locked: false);  // installed before engine.lock existed, its engine running
        string unused = Version("0.8.0");
        string lockedBefore = Snapshot(locked), runningBefore = Snapshot(running);
        using (new FileStream(Path.Join(locked, "engine.lock"), FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            Clean(shell, $"[pscustomobject]@{{ Paths = @('{Path.Join(running, "NexusAndroid.exe")}'); Unknown = $false }}");
        Assert.Equal(lockedBefore, Snapshot(locked));
        Assert.Equal(runningBefore, Snapshot(running));
        Assert.False(Directory.Exists(unused));
        Assert.True(File.Exists(Current));
        Assert.Empty(Directory.GetDirectories(State, "retired-*"));

        // Once nothing uses them, the next run removes them.
        Clean(shell, None);
        Assert.Equal(new[] { "2.0.0" }, Directory.GetDirectories(Releases).Select(Path.GetFileName));
    }

    [Theory]
    [InlineData("pwsh")]
    [InlineData("powershell")]
    public void AnEngineThatCannotBeReadKeepsEverything(string shell)
    {
        // An elevated or other-session engine the scan cannot read could run from any folder, retired ones included.
        string old = Version("1.0.0");
        string left = Retired("unreadable");
        Clean(shell, "[pscustomobject]@{ Paths = @(); Unknown = $true }");
        Assert.True(File.Exists(Path.Join(old, "README.md")));
        Assert.True(File.Exists(Path.Join(left, "NexusAndroid.exe")));
    }

    [Theory]
    [InlineData("pwsh")]
    [InlineData("powershell")]
    public void ARetiredFolderThatDidNotGoAtOnceGoesOnTheNextRun(string shell)
    {
        // A file of a retired folder held open (a scanner, an editor) at the time of the clean-up: it stays, and the next
        // run removes it instead of leaving it for ever.
        string left = Retired("held");
        using (new FileStream(Path.Join(left, "NexusAndroid.exe"), FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            Clean(shell, None);
        Assert.True(Directory.Exists(left));
        Clean(shell, None);
        Assert.False(Directory.Exists(left));
    }
}
