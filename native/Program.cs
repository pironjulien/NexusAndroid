using System;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;

namespace NexusAndroid;

public static class Program
{
    public const string Version = "0.23.0";

    public static async Task<int> Main(string[] args)
    {
        if (args.Length == 0 || args.Contains("--help") || args.Contains("-h"))
        {
            PrintHelp();
            return 0;
        }

        if (args.Contains("--version") || args.Contains("-v"))
        {
            Console.WriteLine($"NexusAndroid v{Version} (Native C# .NET 10)");
            return 0;
        }

        if (args.Length > 0 && args[0] == "setup")
        {
            return Setup.Run(args[1..]);
        }

        if (args.Contains("status"))
        {
            return RunStatus();
        }

        if (args.Contains("--http", StringComparer.OrdinalIgnoreCase))
        {
            int idx = Array.IndexOf(args, "--http");
            int port = 16180;
            if (idx + 1 < args.Length && int.TryParse(args[idx + 1], out int p))
                port = p;
            PrepareTools();
            var server = new McpServer();
            using var cts = new CancellationTokenSource();
            Console.CancelKeyPress += (_, e) => { e.Cancel = true; cts.Cancel(); };
            await server.RunHttpAsync(port, cts.Token);
            return 0;
        }

        if (args.Contains("--mcp"))
        {
            PrepareTools();
            var server = new McpServer();
            server.RunStdio();
            return 0;
        }

        PrintHelp();
        return 1;
    }

    /// <summary>Server start (stdio or daemon): helper files missing on this PC are fetched in the background, as the
    /// first-run setup would: Google's Platform Tools when no adb is installed, and the pinned scrcpy server. Messages go
    /// to stderr (stdout carries the protocol). NEXUS_ANDROID_ADB_DOWNLOAD=0: nothing is downloaded.</summary>
    private static void PrepareTools()
    {
        if (Environment.GetEnvironmentVariable("NEXUS_ANDROID_ADB_DOWNLOAD")?.Trim().ToLowerInvariant() is "0" or "off" or "false" or "no") return;
        _ = Task.Run(() =>
        {
            try
            {
                if (Environment.GetEnvironmentVariable("NEXUS_ANDROID_ADB") is not { Length: > 0 } && AdbClient.FindAdb() is null) Tools.FindOrFetchAdb(Console.Error.WriteLine);
                Tools.EnsureScrcpy(Console.Error.WriteLine);
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or HttpRequestException or InvalidDataException)
            { Console.Error.WriteLine("nexus-android: " + error.Message); }
        });
    }

    private static void PrintHelp()
    {
        Console.WriteLine($"NexusAndroid v{Version} - Unified Standalone C# .NET 10 Engine");
        Console.WriteLine("Usage:");
        Console.WriteLine("  NexusAndroid.exe --http [port] Start HTTP MCP daemon (default 16180)");
        Console.WriteLine("  NexusAndroid.exe --mcp         Start stdio MCP server (18 tools)");
        Console.WriteLine("  NexusAndroid.exe setup         Find or download adb, declare the server to the AI apps found, connect the phone");
        Console.WriteLine("                  [--no-wait] [--no-register] [--apps claude-code,claude-desktop,codex,antigravity]");
        Console.WriteLine("  NexusAndroid.exe status        Display connected Android devices");
        Console.WriteLine("  NexusAndroid.exe --version     Print version info");
    }

    private static int RunStatus()
    {
        var adb = new AdbClient();
        Console.WriteLine($"ADB path: {adb.AdbPath}");
        var devices = adb.GetDevices();
        Console.WriteLine($"Found {devices.Count} device(s):");
        foreach (var d in devices)
        {
            Console.WriteLine($"  - {d.Serial} [{d.State}] Model: {d.Model} (USB: {d.IsUsb})");
        }
        return 0;
    }
}
