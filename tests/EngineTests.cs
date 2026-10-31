using System.Diagnostics;
using System.Net;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace NexusAndroid.Tests;

/// <summary>Former `NexusAndroid.exe --selftest`: image budget, screen parsing, protected apps, exact agreements, MCP
/// tool list. Pure in-process checks, no phone.</summary>
public sealed class SelfTests
{
    [Fact]
    public void ImageChainKeepsAScreenshotUnderBudgetAndMaximumSize()
    {
        const int w = 1080, h = 1920;
        var raw = new byte[w * h * 4];
        for (var i = 0; i < raw.Length; i += 4) { raw[i] = (byte)(i % 255); raw[i + 1] = (byte)(i / 4 % 255); raw[i + 2] = 128; raw[i + 3] = 255; }
        var result = ImageChain.ProcessChain(new PixelBuffer(w, h, raw), maxBytes: 200 * 1024);
        Assert.InRange(result.Bytes.Length, 101, 200 * 1024);
        Assert.True(result.Width <= 2000 && result.Height <= 2000);
    }

    private const string Sample = """
        <?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
        <hierarchy rotation='0'>
          <node index='0' text='' resource-id='' class='android.widget.FrameLayout' package='com.example.app' content-desc='' clickable='false' enabled='true' focused='false' scrollable='false' bounds='[0,0][1080,1920]'>
            <node index='0' text='OK' resource-id='com.example.app:id/btn_ok' class='android.widget.Button' package='com.example.app' content-desc='Accept button' clickable='true' enabled='true' focused='false' scrollable='false' bounds='[100,200][300,280]' />
            <node index='1' text='secret_pass' resource-id='com.example.app:id/user_password' class='android.widget.EditText' package='com.example.app' content-desc='' clickable='true' enabled='true' focused='true' scrollable='false' password='true' bounds='[100,300][980,400]' />
          </node>
        </hierarchy>
        """;

    [Fact]
    public void ScreenParserNumbersTargetsAndMasksPasswords()
    {
        var elements = ScreenParser.ParseXml(Sample);
        Assert.Equal(2, elements.Count);
        var button = Assert.Single(elements, e => e.Text == "OK");
        Assert.Equal((200, 240), (button.CenterX, button.CenterY));
        Assert.Equal("[MASKED]", Assert.Single(elements, e => e.IsPassword).Text);
        var compact = ScreenParser.FormatCompactTree(elements);
        Assert.Contains("[1]", compact);
        Assert.Contains("@200,240", compact);
        Assert.Contains("\"OK\"", compact);
    }

    [Theory]
    [InlineData("fr.creditagricole.androidapp", true)]
    [InlineData("com.x8bit.bitwarden", true)]
    [InlineData("com.android.calculator2", false)]
    public void ProtectedPackages(string package, bool expected) => Assert.Equal(expected, AndroidPolicy.IsProtectedPackage(package));

    [Fact]
    public void ExactAgreementIsSingleUseAndCannotBeForged()
    {
        var request = new JsonObject { ["confirm"] = true };
        var proposal = Assert.Throws<InvalidOperationException>(() => NativeSafety.Require(request, "send to synthetic recipient", true));
        var accord = Regex.Match(proposal.Message, "accord=([a-f0-9]{48})").Groups[1].Value;
        Assert.Equal(48, accord.Length);
        request["accord"] = accord;
        NativeSafety.Require(request, "send to synthetic recipient", true);
        Assert.Throws<InvalidOperationException>(() => NativeSafety.Require(request, "send to synthetic recipient", true));
        Assert.Throws<InvalidOperationException>(() => NativeSafety.Require(
            new JsonObject { ["confirm"] = true, ["accord"] = "ACCORD:forged:9999999999:send:recipient" }, "send to synthetic recipient", true));
    }

    [Theory]
    [InlineData("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")]
    [InlineData("0123456789abcdef0123456789abcdef0123456789abcdef")]
    public void UnregisteredAgreementTokensAreAlwaysMasked(string token)
    {
        // Low-entropy values and the `accord=` prefix must not let foreign tokens bypass the generic secret scan.
        foreach (var text in new[] { token, "accord=" + token, "Verify recipient accord=" + token })
        {
            Assert.DoesNotContain(token, NativeSafety.Mask(text));
            Assert.DoesNotContain(token, NativeSafety.Mask(text, codes: false));
            Assert.DoesNotContain(token, NativeSafety.MaskCodes(text));
        }
    }

    [Fact]
    public void McpServerInitializesAndListsEighteenTools()
    {
        var server = new McpServer();
        var init = server.HandleRequest(new JsonObject { ["jsonrpc"] = "2.0", ["id"] = 1, ["method"] = "initialize" });
        Assert.Equal("nexus-android", init?["result"]?["serverInfo"]?["name"]?.GetValue<string>());
        var tools = server.HandleRequest(new JsonObject { ["jsonrpc"] = "2.0", ["id"] = 2, ["method"] = "tools/list" })?["result"]?["tools"] as JsonArray;
        Assert.Equal(18, tools?.Count);
    }
}

/// <summary>The built executable itself (former tests/test_native_csharp.py): version, stdio MCP, and the HTTP daemon
/// the supervisor runs (health, token, sessions). Ephemeral port, synthetic token, no phone query or gesture.</summary>
public sealed class ExecutableTests
{
    private static readonly string Exe = Path.Join(AppContext.BaseDirectory, "NexusAndroid.exe");
    private static readonly string[] Expected = ["act", "apps", "capture", "checkup", "clipboard", "draft", "files", "functions", "info",
        "launch", "logs", "look", "map", "messages", "notifications", "phone", "settings", "web"];

    private static string TokenFile(string token)
    {
        var path = Path.Join(TestPaths.Root, "token-" + Guid.NewGuid().ToString("N") + ".txt");
        File.WriteAllText(path, token);
        return path;
    }

    private static int FreePort()
    {
        using var allocation = new TcpListener(IPAddress.Loopback, 0);
        allocation.Start();
        var port = ((IPEndPoint)allocation.LocalEndpoint).Port;
        allocation.Stop();
        return port;
    }

    /// <summary>Status code of an authenticated initialize, once the daemon answers its health check.</summary>
    private static async Task<HttpStatusCode> InitializeWith(int port, string token)
    {
        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(3) };
        var deadline = DateTime.UtcNow.AddSeconds(15);
        while (true)
        {
            try { await http.GetStringAsync($"http://127.0.0.1:{port}/health"); break; }
            catch (HttpRequestException) when (DateTime.UtcNow < deadline) { await Task.Delay(50); }
        }
        using var request = new HttpRequestMessage(HttpMethod.Post, $"http://127.0.0.1:{port}/mcp") { Content = new StringContent("""{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}""", Encoding.UTF8, "application/json") };
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        using var response = await http.SendAsync(request);
        return response.StatusCode;
    }

    /// <summary>Shared token rule: the token file is the truth (NEXUS_MCP_TOKEN_FILE, else ~/.nexus/mcp-token.txt);
    /// a different NEXUS_MCP_TOKEN never wins over it, so all Nexus daemons and the user variable agree.</summary>
    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public async Task DaemonTokenFileWinsOverTheVariable(bool declaredFile)
    {
        var home = Path.Join(TestPaths.Root, "home-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(Path.Join(home, ".nexus"));
        File.WriteAllText(Path.Join(home, ".nexus", "mcp-token.txt"), "shared-file-token\n");
        var port = FreePort();
        using var process = Start(s =>
        {
            s.Environment["USERPROFILE"] = home;
            s.Environment["NEXUS_MCP_TOKEN"] = "stale-variable-token";
            if (declaredFile) s.Environment["NEXUS_MCP_TOKEN_FILE"] = TokenFile("declared-file-token");
            else s.Environment.Remove("NEXUS_MCP_TOKEN_FILE");
        }, "--http", port.ToString());
        try
        {
            Assert.Equal(HttpStatusCode.OK, await InitializeWith(port, declaredFile ? "declared-file-token" : "shared-file-token"));
            Assert.Equal(HttpStatusCode.Unauthorized, await InitializeWith(port, "stale-variable-token"));
        }
        finally { if (!process.HasExited) process.Kill(true); }
    }

    [Fact]
    public void InitializeReportsTheEngineVersion()
    {
        var init = new McpServer().HandleRequest(new JsonObject { ["jsonrpc"] = "2.0", ["id"] = 1, ["method"] = "initialize" });
        Assert.Equal(Program.Version, init?["result"]?["serverInfo"]?["version"]?.ToString());
    }

    private static Process Start(Action<ProcessStartInfo>? configure = null, params string[] args)
    {
        var start = new ProcessStartInfo(Exe) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (var a in args) start.ArgumentList.Add(a);
        start.Environment["NEXUS_ANDROID_KEEP_AWAKE"] = "0";
        start.Environment["NEXUS_JOURNAL"] = "0";
        start.Environment["NEXUS_ANDROID_ADB_DOWNLOAD"] = "0";  // never a download from a test
        start.Environment["NEXUS_ANDROID_RESTART_REQUEST"] = Path.Join(TestPaths.Root, "restart-" + Guid.NewGuid().ToString("N") + ".request");
        configure?.Invoke(start);
        return Process.Start(start)!;
    }

    [Fact]
    public void VersionMatchesTheProgram()
    {
        using var process = Start(null, "--version");
        var output = process.StandardOutput.ReadToEnd();
        Assert.True(process.WaitForExit(10_000));
        Assert.Equal(0, process.ExitCode);
        Assert.Contains("NexusAndroid v" + Program.Version, output);
    }

    [Fact]
    public void SelftestOptionIsGone()
    {
        using var process = Start(null, "--selftest");
        process.StandardOutput.ReadToEnd();
        Assert.True(process.WaitForExit(10_000));
        Assert.Equal(1, process.ExitCode);  // unknown option: help and failure
    }

    [Fact]
    public void StdioServerInitializesAndListsTools()
    {
        using var process = Start(null, "--mcp");
        try
        {
            process.StandardInput.WriteLine("""{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}""");
            var init = JsonNode.Parse(process.StandardOutput.ReadLine()!)!;
            Assert.Equal(1, init["id"]!.GetValue<int>());
            Assert.Equal("nexus-android", init["result"]!["serverInfo"]!["name"]!.ToString());
            process.StandardInput.WriteLine("""{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}""");
            var list = JsonNode.Parse(process.StandardOutput.ReadLine()!)!;
            var names = list["result"]!["tools"]!.AsArray().Select(t => t!["name"]!.ToString()).Order().ToArray();
            Assert.Equal(Expected, names);
        }
        finally { if (!process.HasExited) process.Kill(true); }
    }

    [Fact]
    public async Task HttpDaemonStopsWhenIdleOnItsRestartRequest()
    {
        using var allocation = new TcpListener(IPAddress.Loopback, 0);
        allocation.Start();
        var port = ((IPEndPoint)allocation.LocalEndpoint).Port;
        allocation.Stop();
        var request = Path.Join(TestPaths.Root, "restart-own-" + Guid.NewGuid().ToString("N") + ".request");
        using var process = Start(s => { s.Environment["NEXUS_MCP_TOKEN_FILE"] = TokenFile("synthetic-restart-token"); s.Environment["NEXUS_ANDROID_RESTART_REQUEST"] = request; }, "--http", port.ToString());
        try
        {
            using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(2) };
            var deadline = DateTime.UtcNow.AddSeconds(15);
            while (true)
            {
                try { await http.GetStringAsync($"http://127.0.0.1:{port}/health"); break; }
                catch (HttpRequestException) when (DateTime.UtcNow < deadline) { await Task.Delay(50); }
            }
            File.WriteAllText(request, "force pid:1");  // another process: ignored
            await Task.Delay(3000);
            Assert.False(process.HasExited);
            Assert.True(File.Exists(request));
            File.WriteAllText(request, "force");
            Assert.True(process.WaitForExit(15_000), "daemon kept running after its restart request");
            Assert.False(File.Exists(request));
        }
        finally { if (!process.HasExited) process.Kill(true); }
    }

    [Fact]
    public async Task HttpDaemonChecksTokenSessionsAndSchemas()
    {
        using var allocation = new TcpListener(IPAddress.Loopback, 0);
        allocation.Start();
        var port = ((IPEndPoint)allocation.LocalEndpoint).Port;
        allocation.Stop();
        const string token = "synthetic-native-contract-token";
        using var process = Start(s => s.Environment["NEXUS_MCP_TOKEN_FILE"] = TokenFile(token), "--http", port.ToString());
        using var http = new HttpClient { BaseAddress = new Uri($"http://127.0.0.1:{port}"), Timeout = TimeSpan.FromSeconds(5) };
        try
        {
            JsonNode? health = null;
            var deadline = DateTime.UtcNow.AddSeconds(15);
            while (health is null)
            {
                try { health = JsonNode.Parse(await http.GetStringAsync("/health")); }
                catch (HttpRequestException) when (DateTime.UtcNow < deadline) { await Task.Delay(50); }
            }
            Assert.Equal("nexus-android", health["service"]!.ToString());
            Assert.Equal("native", health["runtime"]!.ToString());
            Assert.Equal(process.Id, health["pid"]!.GetValue<int>());

            HttpRequestMessage Post(string body, string? session = null, bool auth = true)
            {
                var request = new HttpRequestMessage(HttpMethod.Post, "/mcp") { Content = new StringContent(body, Encoding.UTF8, "application/json") };
                if (auth) request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
                if (session is not null) request.Headers.Add("Mcp-Session-Id", session);
                return request;
            }
            const string initialize = """{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}""";
            using (var denied = await http.SendAsync(Post(initialize, auth: false))) Assert.Equal(HttpStatusCode.Unauthorized, denied.StatusCode);
            using var opened = await http.SendAsync(Post(initialize));
            var session = opened.Headers.GetValues("Mcp-Session-Id").Single();
            Assert.False(string.IsNullOrEmpty(session));
            Assert.Equal("2025-06-18", JsonNode.Parse(await opened.Content.ReadAsStringAsync())!["result"]!["protocolVersion"]!.ToString());

            const string listing = """{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}""";
            using (var listed = await http.SendAsync(Post(listing, session)))
            {
                var tools = JsonNode.Parse(await listed.Content.ReadAsStringAsync())!["result"]!["tools"]!.AsArray();
                Assert.Equal(Expected, tools.Select(t => t!["name"]!.ToString()).Order().ToArray());
                Assert.NotNull(tools.Single(t => t!["name"]!.ToString() == "act")!["inputSchema"]!["$defs"]!["Step"]!["properties"]!["do"]);
            }
            using (var ping = await http.SendAsync(Post("""{"jsonrpc":"2.0","id":3,"method":"ping"}""", session)))
                Assert.Equal("{}", JsonNode.Parse(await ping.Content.ReadAsStringAsync())!["result"]!.ToJsonString());

            HttpRequestMessage Bare(HttpMethod method)
            {
                var request = new HttpRequestMessage(method, "/mcp");
                request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
                request.Headers.Add("Mcp-Session-Id", session);
                return request;
            }
            using (var get = await http.SendAsync(Bare(HttpMethod.Get))) Assert.Equal(HttpStatusCode.MethodNotAllowed, get.StatusCode);
            using (var delete = await http.SendAsync(Bare(HttpMethod.Delete))) Assert.Equal(HttpStatusCode.NoContent, delete.StatusCode);
            using (var reused = await http.SendAsync(Post(listing, session))) Assert.Equal(HttpStatusCode.NotFound, reused.StatusCode);
        }
        finally { if (!process.HasExited) process.Kill(true); }
    }
}
