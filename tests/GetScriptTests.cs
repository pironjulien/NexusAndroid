using System.Diagnostics;
using System.IO.Compression;
using System.Runtime.CompilerServices;

namespace NexusAndroid.Tests;

/// <summary>get.ps1's real installation step (Install-NexusAndroid and the functions before it) on a local package
/// (NEXUS_ANDROID_SOURCE) in a test home (NEXUS_ANDROID_HOME), under PowerShell 7 and Windows PowerShell 5.1. Setup,
/// the daemon and the AI apps are never reached; never the network nor the real home.</summary>
public sealed class GetScriptTests : IDisposable
{
    private readonly string root = Path.Join(TestPaths.Root, "get-" + Guid.NewGuid().ToString("N"));
    private string Home => Path.Join(root, "home");
    private string Releases => Path.Join(Home, ".nexus", "nexus-android", "releases");
    private string Zip => Path.Join(root, "NexusAndroid-win-x64.zip");
    private string Script => Path.Join(root, "install-fixture.ps1");

    private static string InstallerSource([CallerFilePath] string source = "") =>
        File.ReadAllText(Path.Join(Path.GetDirectoryName(source)!, "..", "get.ps1"));

    public GetScriptTests()
    {
        Directory.CreateDirectory(Home);
        // The engine the tests run, with what it needs next to it (framework-dependent here, self-contained in a release).
        string engine = AppContext.BaseDirectory;
        using (var zip = ZipFile.Open(Zip, ZipArchiveMode.Create))
        {
            foreach (string file in Directory.GetFiles(engine, "*", SearchOption.AllDirectories))
            {
                string relative = Path.GetRelativePath(engine, file);
                if (!Path.GetFileName(file).StartsWith("NexusAndroid.Tests", StringComparison.OrdinalIgnoreCase))
                    zip.CreateEntryFromFile(file, relative.Replace('\\', '/'));
            }
        }
        // Everything get.ps1 defines before its main block, then its installation step alone.
        string installer = InstallerSource();
        string definitions = installer[..installer.IndexOf("$Results = [ordered]@{}", StringComparison.Ordinal)];
        File.WriteAllText(Script, definitions + "\nInstall-NexusAndroid\n");
    }

    public void Dispose()
    {
        try
        {
            Directory.Delete(root, true);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { /* removed with TestPaths.Root at exit */ }
    }

    private (int Code, string Out) Install(string shell)
    {
        var info = new ProcessStartInfo(shell)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            RedirectStandardInput = true,
        };
        foreach (string a in new[] { "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", Script })
            info.ArgumentList.Add(a);
        info.Environment["NEXUS_ANDROID_SOURCE"] = Zip;
        info.Environment["NEXUS_ANDROID_HOME"] = Home;
        info.Environment["NEXUS_PACK"] = "1";
        using var process = Process.Start(info)!;
        process.StandardInput.Close();
        var stdout = process.StandardOutput.ReadToEndAsync(TestContext.Current.CancellationToken);
        var stderr = process.StandardError.ReadToEndAsync(TestContext.Current.CancellationToken);
        if (!process.WaitForExit(120_000))
        {
            process.Kill(true);
            Assert.Fail("get.ps1 did not finish");
        }
        return (process.ExitCode, stdout.Result + stderr.Result);
    }

    private static string PackageVersion() =>
        $"{FileVersionInfo.GetVersionInfo(Path.Join(AppContext.BaseDirectory, "NexusAndroid.exe")).ProductVersion}".Split('+')[0];

    [Theory]
    [InlineData("pwsh")]
    [InlineData("powershell")]
    public void ScannerBrieflyHoldingTheFreshExecutableDoesNotFailTheInstallation(string shell)
    {
        // What an antivirus does to a freshly written executable: opens it for a moment (every sharing mode allowed),
        // which blocks renaming its folder. get.ps1 waits that hold out instead of failing ("being used by another
        // process", seen in NexusAGYmail's installer on a fresh Windows runner).
        string? held = null;
        bool stagedWhileHeld = false;
        using var released = new ManualResetEventSlim();
        using var watcher = new FileSystemWatcher(Home, "NexusAndroid.exe") { IncludeSubdirectories = true };
        // Subscribed before the watcher starts raising events and before the installer runs: WaitForChanged inside the
        // thread would subscribe only once the thread runs, and a creation in between was lost (held stayed null).
        var created = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
        watcher.Created += (_, e) => created.TrySetResult(e.FullPath);
        var scanner = new Thread(() =>
        {
            string path = created.Task.Wait(60_000) ? created.Task.Result : "";
            for (int i = 0; i < 400 && held is null && path.Length > 0; i++)
            {
                try
                {
                    using var scan = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
                    held = path;
                    Thread.Sleep(2_000);
                    stagedWhileHeld = File.Exists(path);
                }
                catch (IOException) { Thread.Sleep(5); }  // still being extracted
            }
            released.Set();
        });
        scanner.IsBackground = true;
        watcher.EnableRaisingEvents = true;
        scanner.Start();

        var (code, output) = Install(shell);
        Assert.True(released.Wait(60_000, TestContext.Current.CancellationToken));
        Assert.True(code == 0, output);
        Assert.NotNull(held);
        Assert.Contains("staging-", held, StringComparison.OrdinalIgnoreCase);
        Assert.True(stagedWhileHeld, "the hold must have overlapped the staged folder");
        string installed = Path.Join(Releases, PackageVersion(), "NexusAndroid.exe");
        Assert.True(File.Exists(installed), output);
        Assert.Equal(installed, output.Trim().Split('\n')[^1].Trim(), ignoreCase: true);
        Assert.Empty(Directory.GetDirectories(Path.Join(Home, ".nexus", "nexus-android"), "staging-*"));

        // A second run (the same version already installed) keeps that folder and still checks it starts.
        (code, output) = Install(shell);
        Assert.True(code == 0, output);
        Assert.Single(Directory.GetDirectories(Releases));
        Assert.Empty(Directory.GetDirectories(Path.Join(Home, ".nexus", "nexus-android"), "staging-*"));
    }
}
