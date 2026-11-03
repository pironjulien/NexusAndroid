using System.Runtime.CompilerServices;
using System.Text.Json.Nodes;

namespace NexusAndroid.Tests.Registration;

/// <summary>plugin/antigravity is what the Antigravity Marketplace installs: the same manifest, skill and logo as
/// plugin/, and a stdio bridge to the local service, since a Marketplace copy cannot name this PC's token.</summary>
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
        var args = JsonNode.Parse(Text("antigravity", "mcp_config.json"))!["mcpServers"]!["nexus-android"]!["args"]!.AsArray().Select(a => (string?)a).ToList();
        Assert.Equal(@"${PLUGIN_ROOT}\scripts\bridge.ps1", args[args.IndexOf("-File") + 1]);
        Assert.Equal("http://127.0.0.1:16180/mcp", args[args.IndexOf("-Url") + 1]);
        Assert.Contains(@".nexus"", ""mcp-token.txt""", Text("antigravity", "scripts", "bridge.ps1"));
    }
}
