using System.Globalization;
using System.Text.Json.Nodes;
using NexusAndroid.Tests.Engine;

namespace NexusAndroid.Tests.Journal;

/// <summary>The common Nexus journal (NexusShared\journal\FORMAT.md, v1): a real MCP tool call through the server writes
/// its line under NEXUS_JOURNAL_HOME (a folder of this run, never the real ~\.nexus\journal), in the shared format and
/// without the text the agent passed, whatever ends the call: a result, a guard-rail refusal, or a failure before any
/// gesture. A journal that goes silent breaks these tests instead of going unnoticed.</summary>
public sealed class JournalTests
{
    private const string Typed = "Typed words that never reach the journal";
    private static readonly string[] Outcomes = ["✓", "=", "~", "✗", "refus", "différé", "arrêt"];
    private const string App = "com.example.app";

    private static string Screen(string node) => $"<hierarchy>{node}</hierarchy>";
    private static readonly string Field = Screen($"<node text='' content-desc='' resource-id='field' package='{App}' class='android.widget.EditText' bounds='[0,200][1080,400]' enabled='true' clickable='true' focusable='true'/>");
    private static readonly string SendButton = Screen($"<node text='Send' content-desc='' resource-id='send' package='{App}' class='android.widget.Button' bounds='[100,1500][500,1600]' enabled='true' clickable='true'/>");

    /// <summary>No phone at all: adb lists no device.</summary>
    private sealed class AbsentAdb() : AdbClient("fixture-adb.exe", ConnectionFixtureState.New())
    {
        public override List<DeviceInfo> GetDevices() => [];
        public override bool EnsureDevice() => false;
        public override DeviceInfo? PickDevice() => null;
        public override string Shell(string command, int timeoutMs = 15000) => throw new IOException("no device");
    }

    private static JsonObject Call(McpServer server, int id, string method, JsonObject parameters) =>
        server.HandleRequest(new JsonObject { ["jsonrpc"] = "2.0", ["id"] = id, ["method"] = method, ["params"] = parameters })!;

    private static JsonObject Tool(string name, JsonObject arguments) => new() { ["name"] = name, ["arguments"] = arguments };

    /// <summary>Runs the tool calls as Claude on a fresh server under a journal folder of its own, then returns every
    /// journal line, each checked against the v1 format, and checks that the typed text never reached the file.</summary>
    private static List<JsonObject> Journal(string screen, bool phoneAbsent, params JsonObject[] calls)
    {
        var home = Path.Join(TestPaths.Root, "journal-" + Guid.NewGuid().ToString("N"));
        var saved = (Switch: Environment.GetEnvironmentVariable("NEXUS_JOURNAL"), Home: Environment.GetEnvironmentVariable("NEXUS_JOURNAL_HOME"));
        try
        {
            Environment.SetEnvironmentVariable("NEXUS_JOURNAL", "1");
            Environment.SetEnvironmentVariable("NEXUS_JOURNAL_HOME", home);
            var phone = new ConfiguredPhone(new JsonObject { ["xml"] = screen, ["shell"] = new JsonObject() });
            AdbClient adb = phoneAbsent ? new AbsentAdb() : new ConfiguredAdb(phone);
            var server = new McpServer(new NativeEngine(adb, phone, Path.Join(home, "engine"), () => new ConfiguredControl(phone)));
            Call(server, 1, "initialize", new JsonObject { ["protocolVersion"] = "2025-11-25", ["clientInfo"] = new JsonObject { ["name"] = "claude-code", ["version"] = "1" } });
            for (var i = 0; i < calls.Length; i++)
                Call(server, i + 2, "tools/call", calls[i]);
        }
        finally
        {
            Environment.SetEnvironmentVariable("NEXUS_JOURNAL", saved.Switch);
            Environment.SetEnvironmentVariable("NEXUS_JOURNAL_HOME", saved.Home);
        }
        var folder = Path.Join(home, "android", "claude");
        var file = Path.Join(folder, DateTimeOffset.UtcNow.ToString("yyyy-MM", CultureInfo.InvariantCulture) + ".jsonl");
        Assert.True(File.Exists(file), "no journal line written under " + folder);
        var content = File.ReadAllText(file);
        Assert.DoesNotContain(Typed, content, StringComparison.Ordinal);
        Assert.DoesNotContain("fixture-device", content, StringComparison.Ordinal);
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
            if (row["erreur"]?.GetValue<string>() is { } error)
                Assert.InRange(error.Length, 1, 80);
        }
        return rows;
    }

    private static JsonObject Line(List<JsonObject> rows, string action) => Assert.Single(rows, r => r["action"]!.GetValue<string>() == action);

    [Fact]
    public void AToolCallWritesOneLineInTheCommonFormatWithoutTheTypedText()
    {
        // A failing action (no recipient) carrying an SMS body, then a typed text: both must stay out of the journal.
        var rows = Journal(Field, false,
            Tool("draft", new JsonObject { ["to"] = "", ["text"] = Typed }),
            Tool("act", new JsonObject { ["steps"] = new JsonArray(new JsonObject { ["do"] = "type", ["target"] = "id:field", ["text"] = Typed }) }));
        var draft = Line(rows, "draft");
        Assert.Equal("✗", draft["issue"]!.GetValue<string>());
        Assert.Equal("invalid arguments", draft["erreur"]!.GetValue<string>());
        Line(rows, "act");
    }

    [Fact]
    public void AFailureBeforeAnyGestureStillWritesItsLine()
    {
        // No phone: the call fails before the tool runs, and the journal still says so (no serial, no text).
        var rows = Journal(Field, true, Tool("act", new JsonObject { ["steps"] = new JsonArray(new JsonObject { ["do"] = "type", ["target"] = "id:field", ["text"] = Typed }) }));
        var act = Line(rows, "act");
        Assert.Equal("✗", act["issue"]!.GetValue<string>());
        Assert.Equal("no device", act["erreur"]!.GetValue<string>());
    }

    [Fact]
    public void AGuardRailRefusalIsRefusNotAFailure()
    {
        // A send gesture without the user's accord: nothing reaches the phone, the step is journaled as refus.
        var rows = Journal(SendButton, false, Tool("act", new JsonObject { ["steps"] = new JsonArray(new JsonObject { ["do"] = "tap", ["target"] = "Send" }) }));
        var tap = Line(rows, "tap");
        Assert.Equal("refus", tap["issue"]!.GetValue<string>());
        Assert.Equal("accord required", tap["erreur"]!.GetValue<string>());
        Line(rows, "act");
    }
}
