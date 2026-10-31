using System.Diagnostics;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using System.Xml.Linq;

namespace NexusAndroid;

/// <summary>Helper files the engine needs on this PC, outside the binary: Google's Platform Tools (adb) when none is
/// installed, and the pinned scrcpy server (typing accents and emoji). Both are verified against published digests.</summary>
public static class Tools
{
    public const string Repository = "https://dl.google.com/android/repository/";
    public const string Manifest = Repository + "repository2-3.xml";  // read by sdkmanager: version, size, SHA-1
    public const string ScrcpyVersion = "5.0.1";
    public const string ScrcpySha256 = "764EB6F79811D5211FE9DF341120882BA9994C7A61B897D7BF3FB662E53BC536";
    private const long ManifestMax = 8L * 1024 * 1024, ArchiveMax = 64L * 1024 * 1024;

    public static string Home => Environment.GetEnvironmentVariable("USERPROFILE") is { Length: > 0 } home ? home : Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
    public static string Folder => Path.Join(Home, ".nexus", "nexus-android", "tools");
    public static string Scrcpy => Path.Join(Folder, "scrcpy-server-v" + ScrcpyVersion);

    /// <summary>adb downloaded by this tool earlier (tools\platform-tools-&lt;version&gt;), newest version first.</summary>
    public static string? Downloaded()
    {
        if (!Directory.Exists(Folder)) return null;
        return Directory.EnumerateDirectories(Folder, "platform-tools*")
            .Select(d => (Dir: d, Version: VersionOf(Path.GetFileName(d))))
            .OrderByDescending(d => d.Version)
            .Select(d => Path.Join(d.Dir, "adb.exe")).FirstOrDefault(File.Exists);
    }

    private static Version VersionOf(string name) =>
        Version.TryParse(name.Replace("platform-tools-", "", StringComparison.Ordinal), out var v) ? v : new Version(0, 0);

    /// <summary>(url, sha1, size, version) of the stable Windows Platform Tools archive in Google's SDK manifest.</summary>
    public static (string Url, string Sha1, long Size, string Version) Latest(string manifest)
    {
        var root = XDocument.Parse(manifest);
        foreach (var package in root.Descendants().Where(e => e.Name.LocalName == "remotePackage" && (string?)e.Attribute("path") == "platform-tools"))
        {
            var channel = package.Elements().FirstOrDefault(e => e.Name.LocalName == "channelRef");
            if (channel is not null && (string?)channel.Attribute("ref") != "channel-0") continue;
            var revision = package.Elements().FirstOrDefault(e => e.Name.LocalName == "revision");
            string Part(string name) => revision?.Elements().FirstOrDefault(e => e.Name.LocalName == name)?.Value.Trim() is { Length: > 0 } v ? v : "0";
            var version = revision is null ? "0" : $"{Part("major")}.{Part("minor")}.{Part("micro")}";
            foreach (var archive in package.Descendants().Where(e => e.Name.LocalName == "archive" && e.Elements().FirstOrDefault(h => h.Name.LocalName == "host-os")?.Value.Trim() == "windows"))
            {
                var complete = archive.Elements().FirstOrDefault(e => e.Name.LocalName == "complete");
                string? Field(string name) => complete?.Elements().FirstOrDefault(e => e.Name.LocalName == name)?.Value.Trim();
                var (url, sha1, size) = (Field("url"), Field("checksum"), Field("size"));
                if (url is { Length: > 0 } && sha1 is { Length: 40 } && long.TryParse(size, out var bytes) && !url.Contains('/') && url.EndsWith(".zip", StringComparison.Ordinal))
                    return (Repository + url, sha1.ToLowerInvariant(), bytes, version);
            }
        }
        throw new InvalidDataException("No stable Windows Platform Tools archive in the SDK repository manifest");
    }

    private static byte[] Get(HttpClient http, string url, long limit)
    {
        using var response = http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead).GetAwaiter().GetResult();
        response.EnsureSuccessStatusCode();
        if (response.Content.Headers.ContentLength > limit) throw new InvalidDataException($"{url}: larger than expected");
        using var stream = response.Content.ReadAsStream();
        using var copy = new MemoryStream();
        var chunk = new byte[81920];
        int read;
        while ((read = stream.Read(chunk)) > 0) { copy.Write(chunk, 0, read); if (copy.Length > limit) throw new InvalidDataException($"{url}: larger than expected"); }
        return copy.ToArray();
    }

    private static HttpClient Client() => new(new SocketsHttpHandler { AllowAutoRedirect = true }) { Timeout = TimeSpan.FromSeconds(120), DefaultRequestHeaders = { { "User-Agent", "NexusAndroid/" + Program.Version } } };

    /// <summary>adb already on this PC (NEXUS_ANDROID_ADB, PATH, SDK, winget, earlier download), else Google's
    /// official Platform Tools, downloaded with their published size and SHA-1 checked (NEXUS_ANDROID_ADB_DOWNLOAD=0:
    /// never).</summary>
    public static string FindOrFetchAdb(Action<string> say)
    {
        if (Environment.GetEnvironmentVariable("NEXUS_ANDROID_ADB") is { Length: > 0 } set && File.Exists(set)) return set;
        if (AdbClient.FindAdb() is { } found) return found;
        if (Environment.GetEnvironmentVariable("NEXUS_ANDROID_ADB_DOWNLOAD")?.Trim().ToLowerInvariant() is "0" or "off" or "false" or "no")
            throw new IOException("adb not found and NEXUS_ANDROID_ADB_DOWNLOAD=0: install Google's Platform Tools (winget install Google.PlatformTools), then run setup again.");
        using var http = Client();
        string url, sha1, version; long size;
        try { (url, sha1, size, version) = Latest(Encoding.UTF8.GetString(Get(http, Manifest, ManifestMax))); }
        catch (Exception e) when (e is HttpRequestException or IOException or InvalidDataException or System.Xml.XmlException or TaskCanceledException)
        { throw new IOException($"adb not found and the automatic download failed ({e.Message}): install Google's Platform Tools (winget install Google.PlatformTools), then run setup again."); }
        var target = Path.Join(Folder, "platform-tools-" + version);
        if (File.Exists(Path.Join(target, "adb.exe"))) return Path.Join(target, "adb.exe");
        say($"Downloading Google's Android Platform Tools {version} ({size / 1_000_000} MB) from {url} ...");
        var data = Get(http, url, ArchiveMax);
        if (data.LongLength != size || Convert.ToHexStringLower(SHA1.HashData(data)) != sha1)
            throw new IOException("The downloaded Platform Tools archive does not match the size and SHA-1 published by Google; it was rejected.");
        var part = Path.Join(Folder, $"platform-tools.{Environment.ProcessId}.part");
        try
        {
            if (Directory.Exists(part)) Directory.Delete(part, true);
            Directory.CreateDirectory(part);
            using (var archive = new ZipArchive(new MemoryStream(data)))
                archive.ExtractToDirectory(part);  // rejects entries outside the folder
            var inner = Path.Join(part, "platform-tools");
            if (!File.Exists(Path.Join(inner, "adb.exe"))) throw new InvalidDataException("adb.exe missing from the archive");
            if (!File.Exists(Path.Join(target, "adb.exe")))
            {
                if (Directory.Exists(target)) Directory.Delete(target, true);
                Directory.Move(inner, target);
            }
        }
        finally { try { if (Directory.Exists(part)) Directory.Delete(part, true); } catch (IOException) { /* best-effort cleanup of the partial download */ } }
        say($"Platform Tools {version} installed in {target}.");
        return Path.Join(target, "adb.exe");
    }

    /// <summary>Pinned scrcpy server (Genymobile, Apache 2.0): kept only when its SHA-256 is the published one.</summary>
    public static bool EnsureScrcpy(Action<string> say)
    {
        if (File.Exists(Scrcpy) && Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(Scrcpy))) == ScrcpySha256) return true;
        try
        {
            using var http = Client();
            var data = Get(http, $"https://github.com/Genymobile/scrcpy/releases/download/v{ScrcpyVersion}/scrcpy-server-v{ScrcpyVersion}", 4L * 1024 * 1024);
            if (Convert.ToHexString(SHA256.HashData(data)) != ScrcpySha256) { say("scrcpy server: unexpected SHA-256, file rejected (typing accents unavailable)."); return false; }
            Directory.CreateDirectory(Folder);
            var temporary = Scrcpy + ".download";
            File.WriteAllBytes(temporary, data);
            File.Move(temporary, Scrcpy, true);
            say($"scrcpy server v{ScrcpyVersion} verified.");
            return true;
        }
        catch (Exception e) when (e is HttpRequestException or IOException or InvalidDataException or TaskCanceledException or UnauthorizedAccessException)
        {
            say($"scrcpy server not downloaded ({e.Message}); typing accents and emoji stays unavailable until setup runs again.");
            return false;
        }
    }
}

/// <summary>Declares the server to the AI apps found on this PC: an stdio entry running this executable with --mcp.
/// Claude Code: nothing when a nexus-android plugin is enabled, else `claude mcp add --scope user` (or ~/.claude.json).
/// Claude Desktop: claude_desktop_config.json (classic and Store installs). Codex: [mcp_servers.nexus-android] of
/// config.toml, unless a nexus-android plugin is enabled. Antigravity: the ~/.gemini/config/plugins/nexus-android
/// plugin (guide and server), never an entry in the global mcp_config.json (an old entry of ours is removed there).
/// An entry of ours (NexusAndroid.exe, or the former uvx nexus-android) is replaced; one written otherwise is kept.
/// Each file is backed up to ~/.nexus/nexus-android/backups first, then written atomically.</summary>
public static class Register
{
    public const string Name = "nexus-android";
    public const string Kept = "kept (an entry of yours already exists)";
    public const string Plugin = "provided by the nexus-android plugin";
    public static readonly string[] Apps = ["claude-code", "claude-desktop", "codex", "antigravity"];

    public static string Home { get; set; } = Tools.Home;  // settable for tests
    public static string Executable { get; set; } = Environment.ProcessPath ?? "NexusAndroid.exe";
    /// <summary>Antigravity plugin files (plugin.json, logo, skill), shipped next to the executable.</summary>
    public static string PluginSource { get; set; } = Path.Join(AppContext.BaseDirectory, "plugin");

    public static JsonObject Entry() => new() { ["command"] = Executable, ["args"] = new JsonArray("--mcp") };

    public static bool Ours(JsonNode? existing)
    {
        if (existing is null) return true;
        if (existing is not JsonObject entry) return false;
        var command = Path.GetFileName(entry["command"]?.ToString() ?? "").ToLowerInvariant();
        var args = entry["args"] is JsonArray a ? a.Select(x => x?.ToString() ?? "").ToList() : [];
        if (command == "nexusandroid.exe") return true;
        return command is "uvx" or "uvx.exe" && args.Any(x => Regex.IsMatch(x, @"\Anexus-android(==[\w.]+)?\z"));
    }

    public static ISet<string> ParseApps(string text)
    {
        var names = text.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).Select(n => n.ToLowerInvariant()).ToHashSet();
        var unknown = names.Except(Apps).Order().ToList();
        if (unknown.Count > 0) throw new ArgumentException($"unknown AI app {string.Join(", ", unknown)} (known: {string.Join(", ", Apps)})");
        return names;
    }

    private static void Backup(string path)
    {
        if (!File.Exists(path)) return;
        var folder = Path.Join(Home, ".nexus", "nexus-android", "backups");
        Directory.CreateDirectory(folder);
        File.Copy(path, Path.Join(folder, $"{Path.GetFileName(Path.GetDirectoryName(path))}-{Path.GetFileName(path)}-{DateTime.Now:yyyyMMdd-HHmmss}"), true);
    }

    private static void Write(string path, string text)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var temporary = path + ".nexus-tmp";
        File.WriteAllText(temporary, text.ReplaceLineEndings("\n"), new UTF8Encoding(false));
        File.Move(temporary, path, true);
    }

    private static readonly JsonSerializerOptions Indented = new() { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping };

    private static JsonObject ReadJson(string path)
    {
        if (!File.Exists(path) || new FileInfo(path).Length == 0) return [];
        return JsonNode.Parse(File.ReadAllText(path, Encoding.UTF8).TrimStart('﻿')) as JsonObject ?? throw new InvalidDataException($"{path}: not a JSON object");
    }

    public static string SetJsonServer(string path, JsonObject value)
    {
        var data = ReadJson(path);
        if (data["mcpServers"] is not JsonObject servers) data["mcpServers"] = servers = [];
        if (!Ours(servers[Name])) return Kept;
        if (JsonNode.DeepEquals(servers[Name], value)) return "already declared";
        Backup(path);
        servers[Name] = value.DeepClone();
        Write(path, data.ToJsonString(Indented) + "\n");
        return "declared";
    }

    private static string? OnPath(string name)
    {
        foreach (var dir in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries))
            foreach (var ext in new[] { ".exe", ".cmd", "" })
                try { var candidate = Path.Join(dir.Trim(), name + ext); if (File.Exists(candidate)) return candidate; } catch (ArgumentException) { /* PATH entry with invalid characters: skip it */ }
        return null;
    }

    public static bool ClaudePluginEnabled()
    {
        try
        {
            return ReadJson(Path.Join(Home, ".claude", "settings.json"))["enabledPlugins"] is JsonObject enabled
                && enabled.Any(p => p.Key.Split('@')[0] == Name && p.Value is JsonValue v && v.TryGetValue<bool>(out var on) && on);
        }
        catch (Exception e) when (e is IOException or JsonException or InvalidDataException) { return false; }
    }

    public static string? ClaudeCode()
    {
        if (ClaudePluginEnabled()) return Plugin;
        var config = Path.Join(Home, ".claude.json");
        var cli = OnPath("claude");
        if (cli is not null)
        {
            var current = File.Exists(config) ? ReadJson(config)["mcpServers"]?[Name] : null;
            if (!Ours(current)) return Kept;
            var value = Entry();
            if (current is JsonObject c && JsonNode.DeepEquals(c["command"], value["command"]) && JsonNode.DeepEquals(c["args"], value["args"])) return "already declared";
            Run(cli, ["mcp", "remove", Name, "--scope", "user"]);
            var (code, output) = Run(cli, ["mcp", "add", Name, "--scope", "user", "--", Executable, "--mcp"]);
            return code == 0 ? "declared" : "failed: " + output.Trim()[..Math.Min(200, output.Trim().Length)];
        }
        if (File.Exists(config)) { var value = Entry(); value.Insert(0, "type", "stdio"); return SetJsonServer(config, value); }
        return null;
    }

    private static (int Code, string Output) Run(string file, string[] args)
    {
        var start = new ProcessStartInfo(file) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (var a in args) start.ArgumentList.Add(a);
        using var process = Process.Start(start)!;
        var output = process.StandardOutput.ReadToEndAsync();
        var error = process.StandardError.ReadToEnd();
        process.WaitForExit();
        return (process.ExitCode, error.Length > 0 ? error : output.Result);
    }

    public static List<string> ClaudeDesktop()
    {
        var roaming = Environment.GetEnvironmentVariable("APPDATA") is { Length: > 0 } r ? r : Path.Join(Home, "AppData", "Roaming");
        var local = Environment.GetEnvironmentVariable("LOCALAPPDATA") is { Length: > 0 } l ? l : Path.Join(Home, "AppData", "Local");
        // Classic install; Store (MSIX): former AppData redirected into the package, then %LOCALAPPDATA%\Claude-Data\<package>.
        // A folder Claude left keeps only a `.user-data-left.<package>` marker: skipped.
        var folders = new List<string> { Path.Join(roaming, "Claude") };
        var packages = Path.Join(local, "Packages");
        if (Directory.Exists(packages))
            folders.AddRange(Directory.EnumerateDirectories(packages, "*Claude_*").Select(p => Path.Join(p, "LocalCache", "Roaming", "Claude")).Order(StringComparer.Ordinal));
        var data = Path.Join(local, "Claude-Data");
        if (Directory.Exists(data)) folders.AddRange(Directory.EnumerateDirectories(data).Order(StringComparer.Ordinal));
        return folders.Where(f => Directory.Exists(f) && !Directory.EnumerateFiles(f, ".user-data-left.*").Any())
            .Select(f => SetJsonServer(Path.Join(f, "claude_desktop_config.json"), Entry())).ToList();
    }

    private static readonly Regex TableHeader = new(@"^\s*\[(?<name>[^\[\]]+)\]\s*(#.*)?$", RegexOptions.Multiline);

    private static string Unquote(string key)
    {
        key = key.Trim();
        return key.Length >= 2 && key[0] is '"' or '\'' && key[^1] == key[0] ? key[1..^1] : key;
    }

    /// <summary>Dotted TOML table name split into keys (quoted keys may contain dots).</summary>
    public static List<string> TableKeys(string name) =>
        Regex.Matches(name, @"\s*(""[^""]*""|'[^']*'|[A-Za-z0-9_-]+)\s*(\.|$)").Select(m => Unquote(m.Groups[1].Value)).ToList();

    private static string? TomlString(string? value)
    {
        if (value is null) return null;
        value = value.Trim();
        if (value.StartsWith('\'') && value.IndexOf('\'', 1) is var end and > 0) return value[1..end];
        if (value.StartsWith('"')) { try { using var doc = JsonDocument.Parse(Regex.Match(value, @"\A""(?:[^""\\]|\\.)*""").Value); return doc.RootElement.GetString(); } catch (JsonException) { return null; } }
        return null;
    }

    private static string Toml(string text) => JsonSerializer.Serialize(text, new JsonSerializerOptions { Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping });

    /// <summary>Codex config.toml: tables ([name]) with their body, in file order; text before the first table first.</summary>
    private static List<(string? Name, string Text)> Tables(string text)
    {
        var parts = new List<(string?, string)>();
        var matches = TableHeader.Matches(text).Where(m => !m.Value.TrimStart().StartsWith("[[", StringComparison.Ordinal)).ToList();
        var position = 0; string? current = null;
        foreach (var match in matches) { parts.Add((current, text[position..match.Index])); current = match.Groups["name"].Value; position = match.Index; }
        parts.Add((current, text[position..]));
        return parts;
    }

    public static string? Codex()
    {
        var folder = Path.Join(Home, ".codex");
        if (!Directory.Exists(folder) && OnPath("codex") is null) return null;
        var config = Path.Join(folder, "config.toml");
        var text = File.Exists(config) ? File.ReadAllText(config, Encoding.UTF8).TrimStart('﻿').ReplaceLineEndings("\n") : "";
        // A table header never closed (`[broken`): the file is not TOML Codex can read; reported, never rewritten.
        if (text.Split('\n').Select(l => l.Trim()).FirstOrDefault(l => l.StartsWith('[') && !l.Contains(']')) is { } broken)
            throw new InvalidDataException($"{config}: unreadable TOML (unclosed table header \"{broken}\")");
        var tables = Tables(text);
        foreach (var (name, body) in tables)
        {
            if (name is null) continue;
            var keys = TableKeys(name);
            if (keys.Count == 2 && keys[0] == "plugins" && keys[1].Split('@')[0] == Name && !Regex.IsMatch(body, @"(?m)^\s*enabled\s*=\s*false\b")) return Plugin;
        }
        string? command = null; var args = new List<string>(); var found = false;
        foreach (var (name, body) in tables)
        {
            if (name is null || TableKeys(name) is not ["mcp_servers", var server] || server != Name) continue;
            found = true;
            command = TomlString(Regex.Match(body, @"(?m)^\s*command\s*=\s*(.+)$").Groups[1].Value);
            var list = Regex.Match(body, @"(?m)^\s*args\s*=\s*\[(.*)\]\s*$").Groups[1].Value;
            args = Regex.Matches(list, @"""(?:[^""\\]|\\.)*""|'[^']*'").Select(m => TomlString(m.Value) ?? "").ToList();
        }
        // Another form (inline table, dotted keys under [mcp_servers]): left as written.
        var otherForm = Regex.IsMatch(text, @"(?m)^\s*(mcp_servers\s*\.\s*)?[""']?nexus-android[""']?\s*(\.|=)") ;
        if (otherForm && !found) return "kept (entry written in a form this installer does not edit)";
        if (found && !Ours(new JsonObject { ["command"] = command ?? "", ["args"] = new JsonArray(args.Select(a => (JsonNode?)a).ToArray()) })) return Kept;
        var block = $"[mcp_servers.{Name}]\ncommand = {Toml(Executable)}\nargs = [{Toml("--mcp")}]\nstartup_timeout_sec = 60\n";
        if (found && command == Executable && args.SequenceEqual(["--mcp"])) return "already declared";
        var kept = tables.Where(t => t.Name is null || !(TableKeys(t.Name) is ["mcp_servers", var s, ..] && s == Name)).Select(t => t.Text);
        var rest = string.Concat(kept).TrimEnd('\n');
        var updated = rest + (rest.Trim().Length > 0 ? "\n\n" : "") + block;
        Backup(config);
        Write(config, updated);
        return "declared";
    }

    public static string? Antigravity()
    {
        var gemini = Path.Join(Home, ".gemini");
        if (!Directory.Exists(Path.Join(gemini, "antigravity")) && !Directory.Exists(Path.Join(gemini, "config"))) return null;
        var root = Path.Join(gemini, "config");
        var target = Path.Join(root, "plugins", Name);
        var mcpFile = Path.Join(target, "mcp_config.json");
        var current = File.Exists(mcpFile) ? ReadJson(mcpFile)["mcpServers"]?[Name] : null;
        // A route to a local HTTP server (the shared daemon) is kept as it is; the plugin's guide is still refreshed.
        var shared = current is JsonObject route && Uri.TryCreate(route["serverUrl"]?.ToString(), UriKind.Absolute, out var url) && url.IsLoopback;
        if (File.Exists(mcpFile) && !Ours(current) && !shared) return "kept (a nexus-android plugin of yours already exists)";
        if (!Directory.Exists(PluginSource)) return $"failed: plugin files missing next to the executable ({PluginSource})";
        var changed = false;
        foreach (var file in Directory.EnumerateFiles(PluginSource, "*", SearchOption.AllDirectories).Order(StringComparer.Ordinal))
        {
            var destination = Path.Join(target, Path.GetRelativePath(PluginSource, file));
            if (shared && Path.GetFullPath(destination) == Path.GetFullPath(mcpFile)) continue;
            if (!File.Exists(destination) || !File.ReadAllBytes(destination).AsSpan().SequenceEqual(File.ReadAllBytes(file)))
            {
                Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
                File.Copy(file, destination, true);
                changed = true;
            }
        }
        if (!shared && !JsonNode.DeepEquals(current, Entry()))
        {
            Write(mcpFile, new JsonObject { ["mcpServers"] = new JsonObject { [Name] = Entry() } }.ToJsonString(Indented) + "\n");
            changed = true;
        }
        var global = Path.Join(root, "mcp_config.json");  // old global entry of ours: duplicated tools with the plugin
        if (File.Exists(global))
        {
            var data = ReadJson(global);
            if (data["mcpServers"] is JsonObject servers && servers.TryGetPropertyValue(Name, out var entry) && Ours(entry))
            {
                Backup(global);
                servers.Remove(Name);
                Write(global, data.ToJsonString(Indented) + "\n");
            }
        }
        bool disabled;
        try { disabled = ReadJson(Path.Join(root, "config.json"))["plugins"]?[Name]?["enabled"] is JsonValue v && v.TryGetValue<bool>(out var on) && !on; }
        catch (Exception e) when (e is IOException or JsonException or InvalidDataException or InvalidOperationException) { disabled = false; }
        return (changed ? "plugin" : "plugin (already installed)") + (disabled ? ", turned off in Antigravity's settings" : "");
    }

    /// <summary>Result per client found (absent ones are not listed); apps: only these AI apps (null: all).</summary>
    public static Dictionary<string, string> DeclareAll(ISet<string>? apps = null)
    {
        var wanted = apps ?? Apps.ToHashSet();
        var results = new Dictionary<string, string?>();
        foreach (var (app, client, declare) in new (string, string, Func<string?>)[] { ("claude-code", "Claude Code", ClaudeCode), ("codex", "Codex", Codex), ("antigravity", "Antigravity", Antigravity) })
        {
            if (!wanted.Contains(app)) continue;
            try { results[client] = declare(); }
            catch (Exception e) when (e is IOException or JsonException or InvalidDataException or UnauthorizedAccessException or System.ComponentModel.Win32Exception) { results[client] = "failed: " + e.Message; }
        }
        if (wanted.Contains("claude-desktop"))
        {
            try { var list = ClaudeDesktop(); for (var i = 0; i < list.Count; i++) results["Claude Desktop" + (i > 0 ? $" ({i + 1})" : "")] = list[i]; }
            catch (Exception e) when (e is IOException or JsonException or InvalidDataException or UnauthorizedAccessException) { results["Claude Desktop"] = "failed: " + e.Message; }
        }
        return results.Where(r => r.Value is not null).ToDictionary(r => r.Key, r => r.Value!);
    }
}

/// <summary>`NexusAndroid.exe setup`: guided first connection in a terminal. adb found or downloaded, scrcpy server
/// verified, server declared to the AI apps found (Register), then the USB debugging steps shown and followed until
/// the phone answers; the phone is then registered for this PC.
/// Nexus pack (NEXUS_PACK=1, or --no-wait): no question and no wait for the phone, and a last line
/// `NEXUS_PACK_RESULT Client=state; …`. --no-register: no AI app touched; --apps claude-code,codex (or
/// NEXUS_PACK_TARGETS of the pack): only these AI apps. Exit code: 0 all ready, 1 adb or phone not ready, 2 an AI app
/// not configured, 3 both.</summary>
public static class Setup
{
    public const int PhoneFailed = 1, AppFailed = 2;
    public const string FirstPlug =
        "1. On the phone: Settings > About phone (Samsung: About phone > Software information) > tap \"Build number\" 7 times, until \"Developer mode has been turned on\".\n" +
        "2. Settings > Developer options (Samsung: at the bottom of Settings) > turn on \"USB debugging\".\n" +
        "3. Plug the phone into this PC with a USB data cable (some cables only charge); if the phone asks what the USB connection is for, choose \"File transfer\".\n" +
        "4. On the phone, accept \"Allow USB debugging?\" (tick \"Always allow from this computer\").";
    public const string Unauthorized = "Phone plugged in but not authorized: accept \"Allow USB debugging?\" on its screen (tick \"Always allow from this computer\"). No prompt? Unlock the phone, unplug and plug the cable again; if it still does not show, Developer options > \"Revoke USB debugging authorizations\", then plug in again.";

    private static string Short(string result) =>
        result.StartsWith("failed", StringComparison.Ordinal) ? "failed" : result.StartsWith("kept", StringComparison.Ordinal) ? "kept"
        : result.StartsWith("provided by", StringComparison.Ordinal) || result.StartsWith("plugin", StringComparison.Ordinal) ? "plugin" : "declared";

    public static ISet<string>? AppsScope(string[] args)
    {
        var index = Array.IndexOf(args, "--apps");
        if (index >= 0) { var scope = index + 1 < args.Length ? args[index + 1] : ""; return scope.Trim().Length > 0 ? Register.ParseApps(scope) : null; }
        if (Environment.GetEnvironmentVariable("NEXUS_PACK_TARGETS") is { } targets) return Register.ParseApps(targets);
        var apps = Environment.GetEnvironmentVariable("NEXUS_PACK_APPS") ?? "";
        return apps.Trim().Length > 0 ? Register.ParseApps(apps) : null;
    }

    public static int Run(string[] args, TimeSpan? wait = null)
    {
        var pack = Environment.GetEnvironmentVariable("NEXUS_PACK")?.Trim().ToLowerInvariant() is "1" or "true" or "yes";
        var noWait = pack || args.Contains("--no-wait");
        ISet<string>? apps;
        try { apps = AppsScope(args); }
        catch (ArgumentException e) { Console.WriteLine("setup: " + e.Message); return AppFailed; }
        Console.WriteLine($"NexusAndroid {Program.Version}: setup on this PC" + (noWait ? "" : " (Ctrl+C to stop)") + ".\n");
        string adbPath;
        try { adbPath = Tools.FindOrFetchAdb(Console.WriteLine); }
        catch (IOException e)
        {
            Console.WriteLine(e.Message);
            if (pack) Console.WriteLine("NEXUS_PACK_RESULT adb=failed");
            return PhoneFailed;
        }
        Console.WriteLine($"adb: {adbPath}");
        Tools.EnsureScrcpy(Console.WriteLine);
        Console.WriteLine();
        var failed = new List<string>();
        if (!args.Contains("--no-register"))
        {
            var results = Register.DeclareAll(apps);
            foreach (var (client, result) in results) Console.WriteLine($"{client}: {result}");
            Console.WriteLine(results.Count > 0 ? "Restart each AI app to load nexus-android.\n" : "No AI client found (Claude, Codex, Antigravity): install one, then run setup again.\n");
            failed = results.Where(r => r.Value.StartsWith("failed", StringComparison.Ordinal)).Select(r => r.Key).ToList();
            if (pack) Console.WriteLine("NEXUS_PACK_RESULT " + string.Join("; ", results.Select(r => $"{r.Key}={Short(r.Value)}")));
        }
        var appsCode = failed.Count > 0 ? AppFailed : 0;
        var appsLine = $"AI apps NOT configured: {string.Join(", ", failed)} (see above); fix it, then run setup again.";
        if (noWait)
        {
            if (!pack) { Console.WriteLine("Phone not checked (--no-wait): run `NexusAndroid.exe setup` to connect it."); if (failed.Count > 0) Console.WriteLine(appsLine); }
            return appsCode;
        }
        var adb = new AdbClient(adbPath);
        var shown = "";
        var deadline = DateTime.UtcNow + (wait ?? TimeSpan.FromMinutes(15));
        using var stop = new CancellationTokenSource();
        Console.CancelKeyPress += (_, e) => { e.Cancel = true; stop.Cancel(); };
        DeviceInfo? device = null;
        while (device is null)
        {
            try { device = adb.ConnectRemembered(scan: false); break; }
            catch (IOException e)
            {
                var states = adb.GetDevices().Select(d => d.State).ToHashSet();
                var message = states.Contains("unauthorized") ? Unauthorized : states.Count == 0 ? "Waiting for the phone. Steps, once per PC:\n" + string.Join('\n', FirstPlug.Split('\n').Select(l => "  " + l)) : e.Message;
                if (message != shown) { Console.WriteLine(message + "\n"); shown = message; }
            }
            if (DateTime.UtcNow > deadline)
            {
                Console.WriteLine("Phone NOT connected after 15 minutes: run `NexusAndroid.exe setup` again when it is plugged in.");
                if (failed.Count > 0) Console.WriteLine(appsLine);
                return PhoneFailed | appsCode;
            }
            if (stop.Token.WaitHandle.WaitOne(2000))
            {
                Console.WriteLine("\nStopped: phone NOT connected." + (failed.Count > 0 ? " " + appsLine : ""));
                return PhoneFailed | appsCode;
            }
        }
        Console.WriteLine($"Phone connected: {(device.Model.Length > 0 ? device.Model : device.Serial)} ({(device.IsUsb ? "USB" : "WI-FI")}), registered on this PC.");
        if (device.IsUsb)
            Console.WriteLine($"Without the cable: Developer options > Wireless debugging > Pair device with pairing code, then \"{adbPath}\" pair <ip>:<port> with the code shown (once per PC, same Wi-Fi).");
        if (failed.Count > 0) { Console.WriteLine(appsLine); return appsCode; }
        Console.WriteLine("Ready. Now ask your AI: \"Look at my phone and tell me what is on the screen.\"");
        return 0;
    }
}
