using System.Diagnostics;

namespace NexusAndroid.Tests;

/// <summary>The engine's own clean-up of older versions (OldVersions.cs), in a test home, never the real profile.
/// get.ps1 removed them only while installing and kept the one an AI app still ran: releases\0.25.3 stayed beside
/// 0.25.4, and install.ps1's native\&lt;fingerprint&gt; folders (0.22.10 to 0.23.0) were never removed. Same rules as
/// get.ps1 (ReleaseCleanupTests): only when engine.txt names the running engine, never a folder in use.</summary>
public sealed class OldVersionsTests : IDisposable
{
    private readonly string home = Path.Join(TestPaths.Root, "old-versions-" + Guid.NewGuid().ToString("N"));
    private readonly DateTime now = DateTime.UtcNow;
    private string State => Path.Join(home, ".nexus", "nexus-android");
    private string Current => Path.Join(State, "releases", "2.0.0", "NexusAndroid.exe");
    private static OldVersions.EngineImages None => new([], false);

    public OldVersionsTests()
    {
        Folder(Path.Join("releases", "2.0.0"));
        // What setup writes: relative to the state folder.
        File.WriteAllText(Path.Join(State, "engine.txt"), @"releases\2.0.0\NexusAndroid.exe" + "\r\n");
    }

    public void Dispose()
    {
        try
        {
            Directory.Delete(home, true);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { /* removed with TestPaths.Root at exit */ }
    }

    /// <summary>A version folder as an install leaves it, created two hours ago unless <paramref name="fresh"/>.</summary>
    private string Folder(string relative, bool locked = true, bool fresh = false)
    {
        string folder = Path.Join(State, relative);
        Directory.CreateDirectory(Path.Join(folder, "plugin"));
        File.WriteAllText(Path.Join(folder, "NexusAndroid.exe"), "engine");
        File.WriteAllText(Path.Join(folder, "plugin", "plugin.json"), "{}");
        if (locked)
            File.WriteAllText(Path.Join(folder, "engine.lock"), "");
        Directory.SetCreationTimeUtc(folder, fresh ? now : now.AddHours(-2));
        return folder;
    }

    private List<string> Prune(OldVersions.EngineImages images, string? running = null) =>
        OldVersions.Prune(home, running ?? Current, () => images, now);

    [Fact]
    public void UnusedOlderVersionsOfBothLayoutsGoAndFoldersInUseStayWhole()
    {
        string unused = Folder(Path.Join("releases", "1.0.0"));
        string locked = Folder(Path.Join("releases", "1.1.0"));
        string running = Folder(Path.Join("releases", "0.9.0"), locked: false);  // before engine.lock, its engine running
        string dev = Folder(Path.Join("native", "aaaaaaaaaaaaaaaa"));
        string left = Path.Join(State, "retired-left");
        Directory.CreateDirectory(left);
        List<string> removed;
        using (new FileStream(Path.Join(locked, "engine.lock"), FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            removed = Prune(new([Path.Join(running, "NexusAndroid.exe")], false));
        Assert.Equal([@"native\aaaaaaaaaaaaaaaa", @"releases\1.0.0"], removed.Order(StringComparer.Ordinal));
        Assert.False(Directory.Exists(unused));
        Assert.False(Directory.Exists(dev));
        Assert.False(Directory.Exists(Path.Join(State, "native")));  // the old layout's folder, left empty
        Assert.True(File.Exists(Path.Join(locked, "plugin", "plugin.json")));
        Assert.True(File.Exists(Path.Join(running, "plugin", "plugin.json")));
        Assert.True(File.Exists(Current));
        Assert.Empty(Directory.GetDirectories(State, "retired-*"));

        // Once nothing uses them, the next start removes them.
        Prune(None);
        Assert.Equal(["2.0.0"], Directory.GetDirectories(Path.Join(State, "releases")).Select(Path.GetFileName));
    }

    [Fact]
    public void NothingGoesUnlessEngineTxtNamesTheRunningEngine()
    {
        // An older engine still started by an open AI app, or a development build: engine.txt names another install.
        string old = Folder(Path.Join("releases", "1.0.0"));
        string dev = Folder(Path.Join("native", "bbbbbbbbbbbbbbbb"));
        Assert.Empty(Prune(None, Path.Join(old, "NexusAndroid.exe")));
        Assert.Empty(Prune(None, Path.Join(TestPaths.Root, "build", "NexusAndroid.exe")));
        File.Delete(Path.Join(State, "engine.txt"));
        Assert.Empty(Prune(None));
        Assert.True(Directory.Exists(old) && Directory.Exists(dev) && File.Exists(Current));
    }

    [Fact]
    public void AnEngineEngineTxtNamesByItsFullPathCleansToo()
    {
        // install.ps1's own folder kept, get.ps1's version removed: whichever install setup last recorded is the current one.
        string dev = Folder(Path.Join("native", "cccccccccccccccc"));
        string exe = Path.Join(dev, "NexusAndroid.exe");
        File.WriteAllText(Path.Join(State, "engine.txt"), exe.ToUpperInvariant() + "\r\n");
        Assert.Equal([@"releases\2.0.0"], Prune(None, exe));
        Assert.True(File.Exists(exe));
    }

    [Fact]
    public void AnEngineThatCannotBeReadKeepsEverything()
    {
        string old = Folder(Path.Join("releases", "1.0.0"));
        string left = Path.Join(State, "retired-unreadable");
        Directory.CreateDirectory(left);
        Assert.Empty(Prune(new([], true)));
        Assert.True(Directory.Exists(old) && Directory.Exists(left));
    }

    [Fact]
    public void AFolderJustInstalledStays()
    {
        // An install puts its folder in place before setup records it in engine.txt: an engine starting meanwhile
        // must not take it for an older version.
        string fresh = Folder(Path.Join("releases", "3.0.0"), fresh: true);
        Assert.Empty(Prune(None));
        Assert.True(Directory.Exists(fresh));
    }

    [Fact]
    public void ARetiredFolderThatDidNotGoAtOnceGoesOnALaterStart()
    {
        string left = Path.Join(State, "retired-held");
        Directory.CreateDirectory(left);
        File.WriteAllText(Path.Join(left, "NexusAndroid.exe"), "left over");
        using (new FileStream(Path.Join(left, "NexusAndroid.exe"), FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            Prune(None);
        Assert.True(Directory.Exists(left));
        Prune(None);
        Assert.False(Directory.Exists(left));
    }

    [Fact]
    public void NothingToRemoveSkipsTheProcessScan()
    {
        // The usual start: only the current version is there; no process is listed.
        Assert.Empty(OldVersions.Prune(home, Current, () => throw new InvalidOperationException("scanned"), now));
    }

    [Fact]
    public void TheProcessScanReadsARunningEngine()
    {
        // A child named NexusAndroid.exe (a copy of cmd.exe, waiting on its stdin) is seen with its path.
        string folder = Path.Join(home, "scan");
        Directory.CreateDirectory(folder);
        string exe = Path.Join(folder, "NexusAndroid.exe");
        File.Copy(Path.Join(Environment.SystemDirectory, "cmd.exe"), exe);
        var info = new ProcessStartInfo(exe) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true, RedirectStandardOutput = true };
        info.ArgumentList.Add("/k");
        using var child = Process.Start(info)!;
        try
        {
            var images = OldVersions.ReadEngineImages();
            Assert.Contains(images.Paths, p => string.Equals(p, exe, StringComparison.OrdinalIgnoreCase));
            Assert.True(OldVersions.InUse(folder, images));
        }
        finally
        {
            child.StandardInput.Close();
            if (!child.WaitForExit(10_000))
                child.Kill(true);
        }
    }
}
