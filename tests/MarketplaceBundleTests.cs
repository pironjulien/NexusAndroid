using System.Runtime.CompilerServices;
using System.Text.Json.Nodes;

namespace NexusAndroid.Tests.Registration;

/// <summary>plugin/antigravity is what the Antigravity Marketplace installs: the same manifest, skill and logo as
/// plugin/, and a launcher that runs the installed engine as a stdio bridge to the local service (NexusAndroid.exe
/// --bridge), since a Marketplace copy cannot name this PC's token. No script engine and no PowerShell file.</summary>
public sealed class MarketplaceBundleTests
{
    private static string Plugin(string[] parts, [CallerFilePath] string source = "") =>
        Path.GetFullPath(Path.Join([Path.GetDirectoryName(source)!, "..", "plugin", .. parts]));

    private static string Text(params string[] parts) => File.ReadAllText(Plugin(parts));

    [Fact]
    public void BundleMatchesThePluginAndBridgesToTheLocalService()
    {
        Assert.Equal(Text("plugin.json"), Text("antigravity", "plugin.json"));
        Assert.Equal(Text("skills", "nexus-android", "SKILL.md"), Text("antigravity", "skills", "nexus-android", "SKILL.md"));
        Assert.Equal(File.ReadAllBytes(Plugin(["logo.png"])), File.ReadAllBytes(Plugin(["antigravity", "logo.png"])));
        Assert.Equal(Program.Version, (string?)JsonNode.Parse(Text("plugin.json"))!["version"]);
        Assert.InRange(((string)JsonNode.Parse(Text("plugin.json"))!["description"]!).Length, 120, 160);
        Assert.True(File.Exists(Plugin(["antigravity", "README.md"])));
        var server = JsonNode.Parse(Text("antigravity", "mcp_config.json"))!["mcpServers"]!["nexus-android"]!;
        Assert.Equal("cmd.exe", (string?)server["command"]);
        var args = server["args"]!.AsArray().Select(a => (string?)a).ToList();
        Assert.Equal(["/d", "/c", @"${PLUGIN_ROOT}\scripts\start.cmd"], args.Take(3));
        Assert.Equal("http://127.0.0.1:16180/mcp", args[args.IndexOf("--url") + 1]);
        Assert.Equal("NexusAndroid", args[args.IndexOf("--product") + 1]);
        var launcher = File.ReadAllBytes(Plugin(["antigravity", "scripts", "start.cmd"]));
        Assert.Contains("\"%ENGINE%\" --bridge %*", System.Text.Encoding.ASCII.GetString(launcher), StringComparison.Ordinal);
        Assert.Equal(launcher.Count(b => b == (byte)'\n'), launcher.Count(b => b == (byte)'\r'));  // CRLF, as cmd.exe expects
        Assert.Empty(Directory.GetFiles(Plugin(["antigravity"]), "*.ps1", SearchOption.AllDirectories));
    }
}
