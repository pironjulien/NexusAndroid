using System.Diagnostics;
using System.IO.Compression;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Text.Json.Nodes;

namespace NexusAndroid.Tests;

/// <summary>An AI session keeps its stdio engine for hours while get.ps1 installs a newer one. A self-contained
/// single-file engine (the release build) loads each assembly from its own file on first use: had an update replaced
/// that file in place, every later call of the session would fail with an empty error (NexusFree before 1.3.3). get.ps1
/// installs each version in its own folder (releases\&lt;version&gt;), never written again, and removes older folders
/// only after engine.txt names the new one; Windows refuses to delete the file of a running program, so a running engine
/// keeps its file. Here the real release build is installed by get.ps1 and started, a newer version is installed by the
/// same script (pointer then clean-up), and the running engine must still answer its tools.</summary>
public sealed class UpdateWhileRunningTests : IDisposable
{
    private static readonly Lazy<string> SingleFilePackage = new(PublishSingleFile, LazyThreadSafetyMode.ExecutionAndPublication);
    private readonly string root = Path.Join(TestPaths.Root, "update-" + Guid.NewGuid().ToString("N"));
    private Process? engine;
    private string Home => Path.Join(root, "home");
    private string Script => Path.Join(root, "update-fixture.ps1");

    private static string Repository([CallerFilePath] string source = "") => Path.GetFullPath(Path.Join(Path.GetDirectoryName(source)!, ".."));

    public UpdateWhileRunningTests()
    {
        Directory.CreateDirectory(Home);
        // Everything get.ps1 defines before its main block, then its installation step and its clean-up of older
        // versions once engine.txt names the new install (what `setup` writes before get.ps1 removes anything).
        string installer = File.ReadAllText(Path.Join(Repository(), "get.ps1"));
        string definitions = installer[..installer.IndexOf("$Results = [ordered]@{}", StringComparison.Ordinal)];
        File.WriteAllText(Script, definitions + """

            $exe = Install-NexusAndroid
            if ($env:NEXUS_TEST_POINT -eq '1') {
                [IO.File]::WriteAllText((Join-Path $UserHome '.nexus\nexus-android\engine.txt'), $exe)
                Remove-OldReleases $exe
            }
            $exe

            """);
    }

    public void Dispose()
    {
        if (engine is { HasExited: false })
            engine.Kill(true);
        engine?.Dispose();
        try
        {
            Directory.Delete(root, true);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { /* removed with TestPaths.Root at exit */ }
    }

    /// <summary>The release build (self-contained, single file, compressed) under an older version number, packaged as
    /// a release zip; published once for the class, with its own build folder.</summary>
    private static string PublishSingleFile()
    {
        string rid = RuntimeInformation.OSArchitecture == Architecture.Arm64 ? "win-arm64" : "win-x64";
        string work = Path.Join(TestPaths.Root, "single-file");
        string output = Path.Join(work, "publish");
        string project = Path.Join(Repository(), "native", "NexusAndroid.csproj");
        string build = $"-p:NexusBuild={Path.Join(work, "build")}{Path.DirectorySeparatorChar}";
        // As release.yml does: one restore for both architectures (the committed packages.lock.json, never rewritten),
        // then the publish without restoring.
        Dotnet("restore", project, "-p:SelfContained=true", build, "--nologo");
        Dotnet("publish", project, "-c", "Release", "-r", rid, "--self-contained", "true", "-p:PublishSingleFile=true",
            "-p:Version=0.0.1", build, "--no-restore", "-o", output, "--nologo");
        Assert.Empty(Directory.GetFiles(output, "*.dll"));  // single file: no assembly next to the executable
        File.WriteAllText(Path.Join(output, "README.md"), "readme");  // as the release zip carries it next to the engine
        string zip = Path.Join(work, $"NexusAndroid-{rid}.zip");
        ZipFile.CreateFromDirectory(output, zip);
        return zip;
    }

    private static void Dotnet(params string[] arguments)
    {
        var info = new ProcessStartInfo("dotnet")
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (string a in arguments)
            info.ArgumentList.Add(a);
        using var process = Process.Start(info)!;
        var stdout = process.StandardOutput.ReadToEndAsync();
        var stderr = process.StandardError.ReadToEndAsync();
        if (!process.WaitForExit(900_000))
        {
            process.Kill(true);
            Assert.Fail($"dotnet {arguments[0]} did not finish");
        }
        Assert.True(process.ExitCode == 0, stdout.Result + stderr.Result);
    }

    /// <summary>The engine the tests run (framework-dependent, the repository's version): the "newer" package.</summary>
    private string CurrentPackage()
    {
        string zip = Path.Join(root, "NexusAndroid-win-x64.zip");
        string built = AppContext.BaseDirectory;
        using var archive = ZipFile.Open(zip, ZipArchiveMode.Create);
        foreach (string file in Directory.GetFiles(built, "*", SearchOption.AllDirectories))
            if (!Path.GetFileName(file).StartsWith("NexusAndroid.Tests", StringComparison.OrdinalIgnoreCase))
                archive.CreateEntryFromFile(file, Path.GetRelativePath(built, file).Replace('\\', '/'));
        return zip;
    }

    private string Install(string shell, string package, bool point)
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
        info.Environment["NEXUS_ANDROID_SOURCE"] = package;
        info.Environment["NEXUS_ANDROID_HOME"] = Home;
        info.Environment["NEXUS_PACK"] = "1";
        info.Environment["NEXUS_TEST_POINT"] = point ? "1" : "0";
        using var process = Process.Start(info)!;
        process.StandardInput.Close();
        var stdout = process.StandardOutput.ReadToEndAsync(TestContext.Current.CancellationToken);
        var stderr = process.StandardError.ReadToEndAsync(TestContext.Current.CancellationToken);
        if (!process.WaitForExit(120_000))
        {
            process.Kill(true);
            Assert.Fail("get.ps1 did not finish");
        }
        string output = stdout.Result + stderr.Result;
        Assert.True(process.ExitCode == 0, output);
        return output.Trim().Split('\n')[^1].Trim();
    }

    private async Task<JsonObject> Call(int id, string method, JsonObject? parameters = null)
    {
        var request = new JsonObject { ["jsonrpc"] = "2.0", ["id"] = id, ["method"] = method };
        if (parameters is not null)
            request["params"] = parameters;
        await engine!.StandardInput.WriteLineAsync(request.ToJsonString());
        await engine.StandardInput.FlushAsync();
        string? line = await engine.StandardOutput.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(60), TestContext.Current.CancellationToken);
        return Assert.IsType<JsonObject>(JsonNode.Parse(line ?? "null"));
    }

    [Theory]
    [InlineData("pwsh")]
    [InlineData("powershell")]
    public async Task RunningEngineKeepsAnsweringAfterGetInstallsNewerVersion(string shell)
    {
        string old = Install(shell, SingleFilePackage.Value, point: false);
        Assert.EndsWith(Path.Join("releases", "0.0.1", "NexusAndroid.exe"), old, StringComparison.OrdinalIgnoreCase);

        // Offline: the test home, and an adb that does not exist (every phone tool then names it in its error).
        string missingAdb = Path.Join(Home, "no-adb", "adb.exe");
        var info = new ProcessStartInfo(old, "--mcp")
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
        };
        info.Environment["USERPROFILE"] = Home;
        info.Environment["NEXUS_ANDROID_ADB"] = missingAdb;
        info.Environment["NEXUS_ANDROID_ADB_DOWNLOAD"] = "0";
        info.Environment["NEXUS_ANDROID_KEEP_AWAKE"] = "0";
        info.Environment["NEXUS_JOURNAL"] = "0";
        engine = Process.Start(info)!;
        Assert.Equal("0.0.1", (await Call(1, "initialize", new JsonObject { ["protocolVersion"] = "2025-11-25" }))["result"]?["serverInfo"]?["version"]?.ToString());

        // The update, while that session is open: a newer version installed, engine.txt pointed at it, older folders
        // removed (the running one keeps its executable).
        string current = Install(shell, CurrentPackage(), point: true);
        Assert.NotEqual(Path.GetDirectoryName(old), Path.GetDirectoryName(current), StringComparer.OrdinalIgnoreCase);
        Assert.Equal(current, File.ReadAllText(Path.Join(Home, ".nexus", "nexus-android", "engine.txt")).Trim(), ignoreCase: true);
        Assert.True(File.Exists(old), "the running engine's file must survive the clean-up");
        // Its whole folder too: before 0.24.2 the clean-up deleted every other file (releases\0.24.0 left holding only
        // NexusAndroid.exe). The engine holds that folder's engine.lock.
        Assert.True(File.Exists(Path.Join(Path.GetDirectoryName(old)!, "README.md")), "the running engine's folder must stay whole");
        Assert.Throws<IOException>(() => new FileStream(Path.Join(Path.GetDirectoryName(old)!, "engine.lock"), FileMode.Open, FileAccess.ReadWrite, FileShare.None).Dispose());

        // Calls that load code the session had not used yet: each answers with its real error (the missing adb), never
        // the empty error of an assembly read from a replaced file.
        int id = 2;
        Assert.NotEmpty(Assert.IsType<JsonArray>((await Call(id++, "tools/list"))["result"]?["tools"]));
        foreach (string tool in new[] { "phone", "look", "messages", "map" })
        {
            JsonObject reply = await Call(id++, "tools/call", new JsonObject { ["name"] = tool, ["arguments"] = new JsonObject() });
            string text = reply["result"]?["content"]?[0]?["text"]?.ToString() ?? reply.ToJsonString();
            Assert.Contains("adb.exe", text, StringComparison.OrdinalIgnoreCase);
        }
        Assert.False(engine.HasExited);
    }
}
