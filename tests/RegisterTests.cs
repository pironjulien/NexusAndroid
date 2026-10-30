using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace NexusAndroid.Tests.Registration;

/// <summary>Server declaration to the AI apps (Register, native/Setup.cs; former test_register.py). Every test runs in a
/// temporary home under TestPaths.Root (APPDATA and LOCALAPPDATA inside it), with a PATH holding no `claude` or `codex`
/// CLI: the real user profile and the real CLIs are never touched.</summary>
public sealed class RegisterTests : IDisposable
{
    private static int number;
    private readonly string home;
    private readonly string executable;
    private readonly (string Home, string Executable, string PluginSource) saved = (Register.Home, Register.Executable, Register.PluginSource);
    private readonly Dictionary<string, string?> environment = new(StringComparer.Ordinal);

    public RegisterTests()
    {
        home = Path.Combine(TestPaths.Root, "register", Interlocked.Increment(ref number).ToString());
        Directory.CreateDirectory(home);
        var noTools = Path.Combine(home, "empty-path");
        Directory.CreateDirectory(noTools);
        executable = Path.Combine(home, "Program Files", "NexusAndroid", "NexusAndroid.exe");
        Set("APPDATA", Path.Combine(home, "AppData", "Roaming"));
        Set("LOCALAPPDATA", Path.Combine(home, "AppData", "Local"));
        Set("PATH", noTools);  // never the real `claude` or `codex` CLI
        foreach (var name in new[] { "NEXUS_PACK", "NEXUS_PACK_APPS", "NEXUS_PACK_TARGETS" }) Set(name, null);
        Register.Home = home;
        Register.Executable = executable;
        Register.PluginSource = PluginFiles();
    }

    public void Dispose()
    {
        (Register.Home, Register.Executable, Register.PluginSource) = saved;
        foreach (var (name, value) in environment) Environment.SetEnvironmentVariable(name, value);
    }

    private void Set(string name, string? value)
    {
        if (!environment.ContainsKey(name)) environment[name] = Environment.GetEnvironmentVariable(name);
        Environment.SetEnvironmentVariable(name, value);
    }

    /// <summary>Antigravity plugin files as shipped next to the executable, else the repository's plugin folder.</summary>
    private static string PluginFiles([CallerFilePath] string source = "")
    {
        var shipped = Path.Combine(AppContext.BaseDirectory, "plugin");
        if (File.Exists(Path.Combine(shipped, "plugin.json"))) return shipped;
        var repository = Path.GetFullPath(Path.Combine(Path.GetDirectoryName(source)!, "..", "plugin"));
        Assert.True(File.Exists(Path.Combine(repository, "plugin.json")), "plugin files not found: " + repository);
        return repository;
    }

    private string At(params string[] parts) => Path.Combine([home, .. parts]);

    private static void WriteText(string path, string text)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, text);
    }

    private static string ReadText(string path) => File.ReadAllText(path);

    private static JsonObject ReadJson(string path) => JsonNode.Parse(ReadText(path))!.AsObject();

    private static void AssertJson(string expected, JsonNode? actual) =>
        Assert.True(JsonNode.DeepEquals(JsonNode.Parse(expected), actual), $"expected {expected}\nactual   {actual?.ToJsonString() ?? "null"}");

    private JsonObject Entry() => new() { ["command"] = executable, ["args"] = new JsonArray("--mcp") };

    private static void AssertResults(Dictionary<string, string> actual, params (string Client, string Result)[] expected) =>
        Assert.Equal(expected.ToDictionary(e => e.Client, e => e.Result).OrderBy(p => p.Key, StringComparer.Ordinal), actual.OrderBy(p => p.Key, StringComparer.Ordinal));

    // ------------------------------------------------------------------ scenarios

    [Fact]
    public void NothingInstalled()
    {
        Assert.Empty(Register.DeclareAll());
    }

    [Fact]
    public void EveryClientDeclared()
    {
        WriteText(At(".claude.json"), """{"mcpServers": {"other": {"command": "x"}}}""");
        WriteText(At(".codex", "config.toml"), "model = \"gpt\"\n\n[mcp_servers.other]\ncommand = \"x\"\n");
        Directory.CreateDirectory(At(".gemini", "antigravity"));
        Directory.CreateDirectory(At("AppData", "Roaming", "Claude"));
        var store = At("AppData", "Local", "Packages", "AnthropicPBC.Claude_abc", "LocalCache", "Roaming", "Claude");
        Directory.CreateDirectory(store);

        AssertResults(Register.DeclareAll(), ("Claude Code", "declared"), ("Codex", "declared"), ("Antigravity", "plugin"),
            ("Claude Desktop", "declared"), ("Claude Desktop (2)", "declared"));

        var claude = ReadJson(At(".claude.json"))["mcpServers"]!.AsObject();
        AssertJson("""{"command": "x"}""", claude["other"]);
        var declared = Entry();
        declared.Insert(0, "type", "stdio");
        Assert.True(JsonNode.DeepEquals(declared, claude["nexus-android"]), claude["nexus-android"]?.ToJsonString());

        var codex = Toml.Parse(ReadText(At(".codex", "config.toml")));
        Assert.Equal("gpt", codex[""]["model"]);
        Assert.Equal(new Dictionary<string, object> { ["command"] = "x" }, codex["mcp_servers.other"]);
        var server = codex["mcp_servers.nexus-android"];
        Assert.Equal(["args", "command", "startup_timeout_sec"], server.Keys.Order(StringComparer.Ordinal));
        Assert.Equal(executable, server["command"]);
        Assert.Equal(new List<string> { "--mcp" }, server["args"]);
        Assert.Equal(60L, server["startup_timeout_sec"]);

        var plugin = At(".gemini", "config", "plugins", "nexus-android");
        Assert.True(File.Exists(Path.Combine(plugin, "plugin.json")));
        Assert.True(File.Exists(Path.Combine(plugin, "skills", "nexus-android", "SKILL.md")));
        Assert.Equal(new byte[] { 0x89, (byte)'P', (byte)'N', (byte)'G', 0x0D, 0x0A, 0x1A, 0x0A }, File.ReadAllBytes(Path.Combine(plugin, "logo.png"))[..8]);
        Assert.True(JsonNode.DeepEquals(Entry(), ReadJson(Path.Combine(plugin, "mcp_config.json"))["mcpServers"]!["nexus-android"]));
        Assert.False(File.Exists(At(".gemini", "config", "mcp_config.json")));  // never a global entry
        Assert.True(JsonNode.DeepEquals(Entry(), ReadJson(Path.Combine(store, "claude_desktop_config.json"))["mcpServers"]!["nexus-android"]));

        var codexText = ReadText(At(".codex", "config.toml"));
        var again = Register.DeclareAll();
        Assert.Equal("already declared", again["Codex"]);
        Assert.Equal("plugin (already installed)", again["Antigravity"]);
        Assert.Equal("already declared", again["Claude Code"]);
        Assert.Equal("already declared", again["Claude Desktop"]);
        Assert.Equal(codexText, ReadText(At(".codex", "config.toml")));  // idempotent
        Assert.NotEmpty(Directory.EnumerateFiles(At(".nexus", "nexus-android", "backups")));
    }

    [Fact]
    public void ClaudeDesktopStoreDataFolder()
    {
        var left = At("AppData", "Local", "Packages", "AnthropicPBC.Claude_x", "LocalCache", "Roaming", "Claude");
        WriteText(Path.Combine(left, ".user-data-left.anthropicpbc.claude_x"), "");
        var data = At("AppData", "Local", "Claude-Data", "anthropicpbc.claude_x");
        WriteText(Path.Combine(data, "claude_desktop_config.json"), """{"preferences": {"theme": "dark"}}""");
        AssertResults(Register.DeclareAll(), ("Claude Desktop", "declared"));
        var config = ReadJson(Path.Combine(data, "claude_desktop_config.json"));
        AssertJson("""{"theme": "dark"}""", config["preferences"]);
        Assert.True(JsonNode.DeepEquals(Entry(), config["mcpServers"]!["nexus-android"]));
        Assert.False(File.Exists(Path.Combine(left, "claude_desktop_config.json")));
    }

    [Fact]
    public void UserEntriesAndPluginsKept()
    {
        WriteText(At(".claude", "settings.json"), """{"enabledPlugins": {"nexus-android@nexus-plugins": true}}""");
        const string toml = "[mcp_servers.nexus-android]\ncommand = \"python\"\nargs = [\"-m\", \"nexus_android\"]\n";
        WriteText(At(".codex", "config.toml"), toml);
        const string daemon = """{"mcpServers": {"nexus-android": {"serverUrl": "http://127.0.0.1:16180/mcp"}}}""";
        var plugin = At(".gemini", "config", "plugins", "nexus-android");
        WriteText(Path.Combine(plugin, "mcp_config.json"), daemon);
        var results = Register.DeclareAll();
        Assert.Equal(Register.Plugin, results["Claude Code"]);
        Assert.Equal(Register.Kept, results["Codex"]);
        Assert.StartsWith("kept", results["Antigravity"]);
        Assert.Equal(toml, ReadText(At(".codex", "config.toml")));
        Assert.Equal(daemon, ReadText(Path.Combine(plugin, "mcp_config.json")));
    }

    [Fact]
    public void CodexPluginAndOldEntryReplaced()
    {
        WriteText(At(".codex", "config.toml"), "[plugins.\"nexus-android@personal\"]\nenabled = true\n");
        Assert.Equal(Register.Plugin, Register.Codex());
        const string old = "[mcp_servers.nexus-android]\ncommand = \"uvx\"\nargs = [\"nexus-android\"]\n\n[mcp_servers.nexus-android.env]\nA = \"1\"\n";
        WriteText(At(".codex", "config.toml"), old + "\n[other]\nx = 1\n");
        Assert.Equal("declared", Register.Codex());
        var text = ReadText(At(".codex", "config.toml"));
        var data = Toml.Parse(text);
        Assert.Equal(new Dictionary<string, object> { ["x"] = 1L }, data["other"]);
        Assert.False(data.ContainsKey("mcp_servers.nexus-android.env"));
        Assert.Equal(executable, data["mcp_servers.nexus-android"]["command"]);
        Assert.Equal("already declared", Register.Codex());  // idempotent: nothing rewritten
        Assert.Equal(text, ReadText(At(".codex", "config.toml")));
        Assert.NotEmpty(Directory.EnumerateFiles(At(".nexus", "nexus-android", "backups")));
    }

    [Fact]
    public void CodexPluginTurnedOffIsNotTheServer()
    {
        WriteText(At(".codex", "config.toml"), "[plugins.\"nexus-android@personal\"]\nenabled = false\n");
        Assert.Equal("declared", Register.Codex());
        var data = Toml.Parse(ReadText(At(".codex", "config.toml")));
        Assert.Equal(false, data["plugins.nexus-android@personal"]["enabled"]);
        Assert.Equal(executable, data["mcp_servers.nexus-android"]["command"]);
    }

    /// <summary>Claude Desktop entry already present: an old `uvx nexus-android` entry (or ours) is replaced by the
    /// executable entry, a foreign one is kept untouched.</summary>
    [Theory]
    [InlineData("""{"command": "uvx", "args": ["nexus-android"]}""", true)]
    [InlineData("""{"command": "C:\\uv\\uvx.exe", "args": ["nexus-android==0.20.3"]}""", true)]
    [InlineData("""{"command": "D:\\Old\\NexusAndroid.exe", "args": ["--mcp"]}""", true)]
    [InlineData("""{"command": "python", "args": ["-m", "nexus_android"]}""", false)]
    [InlineData("""{"command": "uvx", "args": ["nexus-android-fork"]}""", false)]
    [InlineData("""{"serverUrl": "http://127.0.0.1:16180/mcp"}""", false)]
    public void ExistingEntryReplacedOnlyWhenOurs(string existing, bool ours)
    {
        Assert.Equal(ours, Register.Ours(JsonNode.Parse(existing)));
        var folder = At("AppData", "Roaming", "Claude");
        var original = "{\"mcpServers\": {\"nexus-android\": " + existing + ", \"x\": {\"command\": \"y\"}}}";
        WriteText(Path.Combine(folder, "claude_desktop_config.json"), original);
        AssertResults(Register.DeclareAll(new HashSet<string> { "claude-desktop" }), ("Claude Desktop", ours ? "declared" : Register.Kept));
        var config = ReadJson(Path.Combine(folder, "claude_desktop_config.json"));
        AssertJson("""{"command": "y"}""", config["mcpServers"]!["x"]);
        if (ours) Assert.True(JsonNode.DeepEquals(Entry(), config["mcpServers"]!["nexus-android"]));
        else Assert.Equal(original, ReadText(Path.Combine(folder, "claude_desktop_config.json")));
    }

    [Fact]
    public void OldGlobalAntigravityEntryRemoved()
    {
        var root = At(".gemini", "config");
        WriteText(Path.Combine(root, "mcp_config.json"), """{"mcpServers": {"nexus-android": {"command": "uvx", "args": ["nexus-android"]}, "x": {"command": "y"}}}""");
        WriteText(Path.Combine(root, "config.json"), """{"plugins": {"nexus-android": {"enabled": false}}}""");
        Assert.Equal("plugin, turned off in Antigravity's settings", Register.Antigravity());
        AssertJson("""{"x": {"command": "y"}}""", ReadJson(Path.Combine(root, "mcp_config.json"))["mcpServers"]);
    }

    [Fact]
    public void UnreadableFileReportedNotTouched()
    {
        WriteText(At(".codex", "config.toml"), "[broken");
        Assert.StartsWith("failed", Register.DeclareAll()["Codex"]);
        Assert.Equal("[broken", ReadText(At(".codex", "config.toml")));
    }

    [Fact]
    public void ScopeLeavesOtherAppsAlone()
    {
        Directory.CreateDirectory(At(".codex"));
        Directory.CreateDirectory(At(".gemini", "antigravity"));
        Directory.CreateDirectory(At("AppData", "Roaming", "Claude"));
        AssertResults(Register.DeclareAll(new HashSet<string> { "codex" }), ("Codex", "declared"));
        Assert.False(Directory.Exists(At(".gemini", "config", "plugins")));
        Assert.False(File.Exists(At("AppData", "Roaming", "Claude", "claude_desktop_config.json")));
        var error = Assert.Throws<ArgumentException>(() => Register.ParseApps("codex,chatgpt"));
        Assert.Contains("unknown AI app", error.Message);
    }

    /// <summary>Scope of `setup` from the Nexus pack: NEXUS_PACK_APPS, `--apps`, and NEXUS_PACK_TARGETS (pack 1.6.7
    /// name) first. The "defined but empty" NEXUS_PACK_TARGETS case is not portable in process: on Windows, setting a
    /// variable to "" removes it.</summary>
    [Fact]
    public void SetupScopeFromThePack()
    {
        Set("NEXUS_PACK", "1");
        Set("NEXUS_PACK_APPS", "claude-desktop, antigravity");
        Assert.Equal(new HashSet<string> { "claude-desktop", "antigravity" }, Setup.AppsScope([]));
        Assert.Equal(new HashSet<string> { "codex" }, Setup.AppsScope(["--apps", "codex"]));
        Assert.Throws<ArgumentException>(() => Setup.AppsScope(["--apps", "nope"]));
        Set("NEXUS_PACK_TARGETS", "codex,claude-code");
        Assert.Equal(new HashSet<string> { "codex", "claude-code" }, Setup.AppsScope([]));
        Set("NEXUS_PACK_TARGETS", null);
        Set("NEXUS_PACK_APPS", null);
        Assert.Null(Setup.AppsScope([]));  // no scope: every AI app
    }
}

/// <summary>Reader for the TOML subset of the Codex config in these tests: [table] headers (dotted, quoted keys), and
/// key = "basic" | 'literal' | integer | boolean | one-line array of strings. Table names are rejoined with '.'
/// (quotes removed); the root table is "". Anything else throws, so a malformed file written by Register fails.</summary>
internal static class Toml
{
    public static Dictionary<string, Dictionary<string, object>> Parse(string text)
    {
        var tables = new Dictionary<string, Dictionary<string, object>>(StringComparer.Ordinal) { [""] = new(StringComparer.Ordinal) };
        var current = tables[""];
        foreach (var raw in text.ReplaceLineEndings("\n").Split('\n'))
        {
            var line = raw.Trim();
            if (line.Length == 0 || line.StartsWith('#')) continue;
            var header = Regex.Match(line, @"^\[(?<name>[^\[\]]+)\]$");
            if (header.Success)
            {
                var name = string.Join(".", Regex.Matches(header.Groups["name"].Value, @"\s*(""[^""]*""|'[^']*'|[A-Za-z0-9_-]+)\s*(\.|$)").Select(m => m.Groups[1].Value.Trim('"', '\'')));
                if (!tables.TryAdd(name, current = new(StringComparer.Ordinal))) throw new FormatException("duplicate table " + name);
                continue;
            }
            var pair = Regex.Match(line, @"^(?<key>[A-Za-z0-9_-]+)\s*=\s*(?<value>.+)$");
            if (!pair.Success) throw new FormatException("not TOML: " + line);
            current.Add(pair.Groups["key"].Value, Value(pair.Groups["value"].Value.Trim()));
        }
        return tables;
    }

    private static object Value(string value)
    {
        if (value.StartsWith('"')) return JsonSerializer.Deserialize<string>(value) ?? throw new FormatException(value);
        if (value.StartsWith('\'') && value.EndsWith('\'') && value.Length >= 2) return value[1..^1];
        if (value is "true" or "false") return value == "true";
        if (long.TryParse(value, out var integer)) return integer;
        if (value.StartsWith('[') && value.EndsWith(']'))
        {
            var items = Regex.Matches(value[1..^1], @"\s*(""(?:[^""\\]|\\.)*""|'[^']*')\s*(,|$)");
            if (string.Concat(items.Select(m => m.Value)).Trim() != value[1..^1].Trim()) throw new FormatException("array: " + value);
            return items.Select(m => (string)Value(m.Groups[1].Value)).ToList();
        }
        throw new FormatException("value: " + value);
    }
}
