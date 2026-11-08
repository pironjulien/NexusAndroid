using System.Globalization;
using System.Text.Json.Nodes;
using NexusAndroid.Tests.Engine;

namespace NexusAndroid.Tests.Journal;

/// <summary>The common Nexus journal (NexusShared\journal\FORMAT.md, v1): a real MCP tool call through the server writes
/// its line under NEXUS_JOURNAL_HOME (a folder of this run, never the real ~\.nexus\journal), in the shared format and
/// without the text the agent passed. A journal that goes silent breaks this test instead of going unnoticed.</summary>
public sealed class JournalTests
{
    private const string Typed = "Typed words that never reach the journal";
    private static readonly string[] Outcomes = ["✓", "=", "~", "✗", "refus", "différé", "arrêt"];

    private static JsonObject Call(McpServer server, int id, string method, JsonObject parameters) =>
        server.HandleRequest(new JsonObject { ["jsonrpc"] = "2.0", ["id"] = id, ["method"] = method, ["params"] = parameters })!;

    [Fact]
    public void AToolCallWritesOneLineInTheCommonFormatWithoutTheTypedText()
    {
        var home = Path.Join(TestPaths.Root, "journal-" + Guid.NewGuid().ToString("N"));
        var saved = (Switch: Environment.GetEnvironmentVariable("NEXUS_JOURNAL"), Home: Environment.GetEnvironmentVariable("NEXUS_JOURNAL_HOME"));
        try
        {
            Environment.SetEnvironmentVariable("NEXUS_JOURNAL", "1");
            Environment.SetEnvironmentVariable("NEXUS_JOURNAL_HOME", home);
            var screen = "<hierarchy><node text='' content-desc='' resource-id='field' package='com.example.app' class='android.widget.EditText' bounds='[0,200][1080,400]' enabled='true' clickable='true' focusable='true'/></hierarchy>";
            var phone = new ConfiguredPhone(new JsonObject { ["xml"] = screen, ["shell"] = new JsonObject() });
            var server = new McpServer(new NativeEngine(new ConfiguredAdb(phone), phone, Path.Join(home, "engine"), () => new ConfiguredControl(phone)));
            Call(server, 1, "initialize", new JsonObject { ["protocolVersion"] = "2025-11-25", ["clientInfo"] = new JsonObject { ["name"] = "claude-code", ["version"] = "1" } });
            // A failing action (no recipient) carrying an SMS body, then a typed text: both must stay out of the journal.
            Call(server, 2, "tools/call", new JsonObject { ["name"] = "draft", ["arguments"] = new JsonObject { ["to"] = "", ["text"] = Typed } });
            Call(server, 3, "tools/call", new JsonObject { ["name"] = "act", ["arguments"] = new JsonObject { ["steps"] = new JsonArray(new JsonObject { ["do"] = "type", ["target"] = "id:field", ["text"] = Typed }) } });

            var folder = Path.Join(home, "android", "claude");
            var file = Path.Join(folder, DateTimeOffset.UtcNow.ToString("yyyy-MM", CultureInfo.InvariantCulture) + ".jsonl");
            Assert.True(File.Exists(file), "no journal line written under " + folder);
            var content = File.ReadAllText(file);
            Assert.DoesNotContain(Typed, content, StringComparison.Ordinal);
            var rows = content.Split('\n', StringSplitOptions.RemoveEmptyEntries).Select(line => JsonNode.Parse(line)!.AsObject()).ToList();
            foreach (var row in rows)
            {
                Assert.Equal(1, row["v"]!.GetValue<int>());
                Assert.Contains(row["agent"]!.GetValue<string>(), new[] { "claude", "codex", "antigravity" });
                Assert.Equal(Program.Version, row["outil"]!.GetValue<string>());
                Assert.Contains(row["issue"]!.GetValue<string>(), Outcomes);
                Assert.True(row["ms"]!.GetValue<int>() >= 0, "negative duration");
                foreach (var key in new[] { "appel", "app", "erreur", "action" })
                    Assert.True(row.ContainsKey(key), "missing key " + key);
                Assert.True(DateTimeOffset.TryParseExact(row["ts"]!.GetValue<string>(), "yyyy-MM-dd'T'HH:mm:ss.fff'Z'", CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out _), "ts is not ISO 8601 UTC");
            }
            var draft = Assert.Single(rows, r => r["action"]!.GetValue<string>() == "draft");
            Assert.Equal("✗", draft["issue"]!.GetValue<string>());
            var error = draft["erreur"]!.GetValue<string>();
            Assert.InRange(error.Length, 1, 80);
            Assert.Single(rows, r => r["action"]!.GetValue<string>() == "act");
        }
        finally
        {
            Environment.SetEnvironmentVariable("NEXUS_JOURNAL", saved.Switch);
            Environment.SetEnvironmentVariable("NEXUS_JOURNAL_HOME", saved.Home);
        }
    }
}
