using System.Diagnostics;
using System.Globalization;
using System.Net;
using System.Net.Sockets;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json.Nodes;

namespace NexusAndroid.Tests.Registration;

/// <summary>`NexusAndroid.exe --bridge` (StdioBridge), the Antigravity Marketplace plugin's relay from stdio to the
/// shared HTTP service, with the behavior of the former scripts/bridge.ps1: one output line per JSON reply or per
/// event of a stream, the session id taken from the initialize reply, the bearer token on every request, a JSON-RPC
/// error for a request the service cannot answer, and the session closed when stdin ends. Offline: the service is a
/// fixture (an HTTP handler, or a loopback socket for the end-to-end launcher test).</summary>
public sealed class StdioBridgeTests
{
    private static readonly Uri Service = new("http://127.0.0.1:16180/mcp");

    /// <summary>Recorded request: method, Authorization, Mcp-Session-Id, Accept, body.</summary>
    private sealed record Seen(string Method, string? Authorization, string? Session, string? Accept, string Body);

    private sealed class FakeService(Func<Seen, HttpResponseMessage> answer) : HttpMessageHandler
    {
        public readonly List<Seen> Requests = [];

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            static string? Header(HttpRequestMessage r, string name) => r.Headers.TryGetValues(name, out var v) ? string.Join(", ", v) : null;
            var body = request.Content is null ? "" : await request.Content.ReadAsStringAsync(cancellationToken);
            var seen = new Seen(request.Method.Method, Header(request, "Authorization"), Header(request, "Mcp-Session-Id"), Header(request, "Accept"), body);
            lock (Requests)
                Requests.Add(seen);
            return answer(seen);
        }
    }

    private static HttpResponseMessage Json(string text, string? session = null)
    {
        var response = new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(text, Encoding.UTF8, "application/json") };
        if (session is not null)
            response.Headers.Add("Mcp-Session-Id", session);
        return response;
    }

    private static async Task<(List<string> Lines, string Errors)> Relay(FakeService service, string? token, params string[] input)
    {
        using var output = new MemoryStream();
        using var errors = new StringWriter(CultureInfo.InvariantCulture);
        using (var bridge = new StdioBridge(Service, "NexusAndroid", token, output, errors, service))
            Assert.Equal(0, await bridge.RunAsync(new StringReader(string.Join("\n", input) + "\n")));
        var text = Encoding.UTF8.GetString(output.ToArray());
        Assert.DoesNotContain('\r', text);
        return (text.Split('\n', StringSplitOptions.RemoveEmptyEntries).ToList(), errors.ToString());
    }

    [Fact]
    public async Task RepliesComeBackOneLineEachWithTheSessionAndTokenThenTheSessionIsClosed()
    {
        var service = new FakeService(seen => seen.Method switch
        {
            "POST" when seen.Body.Contains("\"initialize\"", StringComparison.Ordinal) => Json("{\"jsonrpc\":\"2.0\",\"id\":1,\n  \"result\":{\"protocolVersion\":\"2025-06-18\"}}\r\n", "s-42"),
            "POST" when seen.Body.Contains("notifications/", StringComparison.Ordinal) => new HttpResponseMessage(HttpStatusCode.Accepted),
            "POST" => Json("{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":[]}}"),
            _ => new HttpResponseMessage(HttpStatusCode.NoContent),
        });
        var (lines, errors) = await Relay(service, "secret",
            "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{}}",
            "   ",
            "{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\"}",
            "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\"}");

        Assert.Equal(["{\"jsonrpc\":\"2.0\",\"id\":1,  \"result\":{\"protocolVersion\":\"2025-06-18\"}}", "{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":[]}}"], lines);
        Assert.Equal("", errors);
        Assert.Equal(4, service.Requests.Count);  // blank line skipped; initialize, notification, call, then DELETE
        Assert.All(service.Requests, r => Assert.Equal("Bearer secret", r.Authorization));
        Assert.Null(service.Requests[0].Session);
        Assert.All(service.Requests.Skip(1), r => Assert.Equal("s-42", r.Session));
        Assert.All(service.Requests.Take(3), r => Assert.Equal("application/json, text/event-stream", r.Accept));
        Assert.Equal("DELETE", service.Requests[^1].Method);
    }

    [Fact]
    public async Task EachEventOfAStreamBecomesOneLine()
    {
        var service = new FakeService(_ =>
        {
            const string stream = "event: message\ndata: {\"jsonrpc\":\"2.0\",\"method\":\"notifications/progress\"}\n\n" +
                "data:{\"jsonrpc\":\"2.0\",\ndata: \"id\":7,\"result\":{}}\n\n: comment\n";
            return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(stream, Encoding.UTF8, "text/event-stream") };
        });
        var (lines, _) = await Relay(service, null, "{\"jsonrpc\":\"2.0\",\"id\":7,\"method\":\"tools/call\"}");
        Assert.Equal(["{\"jsonrpc\":\"2.0\",\"method\":\"notifications/progress\"}", "{\"jsonrpc\":\"2.0\",\"id\":7,\"result\":{}}"], lines);
        Assert.Null(service.Requests[0].Authorization);  // no token: no Authorization header
        Assert.Single(service.Requests);  // no session id given: nothing to close
    }

    [Fact]
    public async Task AServiceThatIsMissingAnswersEachRequestWithHowToInstallIt()
    {
        var service = new FakeService(_ => throw new HttpRequestException("outer", new SocketException((int)SocketError.ConnectionRefused)));
        var (lines, errors) = await Relay(service, "t",
            "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"initialize\"}",
            "{\"jsonrpc\":\"2.0\",\"id\":\"é-4\",\"method\":\"tools/list\"}",
            "{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\"}",
            "not json");

        Assert.Equal(2, lines.Count);  // the notification and the unreadable line get no reply
        var replies = lines.Select(l => JsonNode.Parse(l)!.AsObject()).OrderBy(r => r["id"]!.GetValueKind() == System.Text.Json.JsonValueKind.String).ToList();
        Assert.Equal(3, (int)replies[0]["id"]!);
        Assert.Equal("é-4", (string?)replies[1]["id"]);
        foreach (var reply in replies)
        {
            Assert.Equal("2.0", (string?)reply["jsonrpc"]);
            Assert.Equal(-32000, (int)reply["error"]!["code"]!);
            var message = (string)reply["error"]!["message"]!;
            Assert.StartsWith("NexusAndroid is not reachable on this PC (", message, StringComparison.Ordinal);
            Assert.DoesNotContain("outer", message, StringComparison.Ordinal);  // the innermost cause is shown
            Assert.EndsWith("). Install or start it: ask the agent to follow the NexusAndroid skill.", message, StringComparison.Ordinal);
        }
        Assert.Equal(4, errors.Split('\n', StringSplitOptions.RemoveEmptyEntries).Length);  // every failure on stderr
    }

    [Fact]
    public async Task AnHttpErrorIsReportedWithItsStatus()
    {
        var service = new FakeService(_ => new HttpResponseMessage(HttpStatusCode.Unauthorized) { Content = new StringContent("denied") });
        var (lines, errors) = await Relay(service, "wrong", "{\"jsonrpc\":\"2.0\",\"id\":9,\"method\":\"tools/list\"}");
        Assert.Equal("{\"jsonrpc\":\"2.0\",\"id\":9,\"error\":{\"code\":-32000,\"message\":\"NexusAndroid answered HTTP 401.\"}}", Assert.Single(lines));
        Assert.Equal("NexusAndroid answered HTTP 401.", errors.Trim());
    }

    [Fact]
    public void TheTokenComesFromTheVariableElseTheProfileFile()
    {
        var saved = (Token: Environment.GetEnvironmentVariable("NEXUS_MCP_TOKEN"), Profile: Environment.GetEnvironmentVariable("USERPROFILE"));
        var home = Path.Join(TestPaths.Root, "bridge-token-" + Guid.NewGuid().ToString("N"));
        try
        {
            Environment.SetEnvironmentVariable("USERPROFILE", home);
            Environment.SetEnvironmentVariable("NEXUS_MCP_TOKEN", null);
            Assert.Null(StdioBridge.ReadToken());
            Directory.CreateDirectory(Path.Join(home, ".nexus"));
            File.WriteAllText(Path.Join(home, ".nexus", "mcp-token.txt"), "  from-file\r\n");
            Assert.Equal("from-file", StdioBridge.ReadToken());
            Environment.SetEnvironmentVariable("NEXUS_MCP_TOKEN", "from-variable");
            Assert.Equal("from-variable", StdioBridge.ReadToken());
        }
        finally
        {
            Environment.SetEnvironmentVariable("NEXUS_MCP_TOKEN", saved.Token);
            Environment.SetEnvironmentVariable("USERPROFILE", saved.Profile);
        }
    }

    [Fact]
    public void SetupRecordsTheEngineRelativeToItsFolderWhenInside()
    {
        var saved = (Register.Home, Register.Executable);
        var home = Path.Join(TestPaths.Root, "bridge-pointer-" + Guid.NewGuid().ToString("N"));
        try
        {
            Register.Home = home;
            Register.Executable = Path.Join(home, ".nexus", "nexus-android", "releases", "1.2.3", "NexusAndroid.exe");
            var file = Register.WriteEnginePointer();
            Assert.Equal(Path.Join(home, ".nexus", "nexus-android", "engine.txt"), file);
            Assert.Equal("releases\\1.2.3\\NexusAndroid.exe\r\n", File.ReadAllText(file));
            Register.Executable = @"D:\Portable\NexusAndroid\NexusAndroid.exe";
            Register.WriteEnginePointer();
            Assert.Equal("D:\\Portable\\NexusAndroid\\NexusAndroid.exe\r\n", File.ReadAllText(file));
        }
        finally
        {
            (Register.Home, Register.Executable) = saved;
        }
    }

    [Fact]
    public void SetupRecordsTheEngineBeforeLookingForAdb()
    {
        // get.ps1 removes older versions once engine.txt names the new one: a setup that stops on adb must still
        // record it, or the Marketplace launcher keeps pointing at a version about to be (or already) removed.
        var saved = (Register.Home, Register.Executable, Setup.FindAdb);
        var home = Path.Join(TestPaths.Root, "bridge-noadb-" + Guid.NewGuid().ToString("N"));
        var state = Path.Join(home, ".nexus", "nexus-android");
        Directory.CreateDirectory(state);
        File.WriteAllText(Path.Join(state, "engine.txt"), "releases\\1.0.0\\NexusAndroid.exe\r\n");
        try
        {
            Register.Home = home;
            Register.Executable = Path.Join(state, "releases", "2.0.0", "NexusAndroid.exe");
            Setup.FindAdb = _ => throw new IOException("adb not found (test)");
            Assert.Equal(Setup.PhoneFailed, Setup.Run(["setup", "--no-wait", "--no-register"]));
            Assert.Equal("releases\\2.0.0\\NexusAndroid.exe\r\n", File.ReadAllText(Path.Join(state, "engine.txt")));
        }
        finally
        {
            (Register.Home, Register.Executable, Setup.FindAdb) = saved;
        }
    }

    // ---- End to end: Antigravity's command line (cmd.exe /d /c start.cmd ...) on real stdio, to a loopback service.

    private static string Launcher([CallerFilePath] string source = "") =>
        Path.GetFullPath(Path.Join(Path.GetDirectoryName(source)!, "..", "plugin", "antigravity", "scripts", "start.cmd"));

    private static Process StartLauncher(string home, string url)
    {
        var start = new ProcessStartInfo(Path.Join(Environment.GetFolderPath(Environment.SpecialFolder.System), "cmd.exe"))
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardInputEncoding = new UTF8Encoding(false),
            StandardOutputEncoding = Encoding.UTF8,
        };
        foreach (var arg in new[] { "/d", "/c", Launcher(), "--url", url, "--product", "NexusAndroid" })
            start.ArgumentList.Add(arg);
        start.Environment["USERPROFILE"] = home;
        start.Environment["NEXUS_MCP_TOKEN"] = "e2e-token";
        return Process.Start(start)!;
    }

    [Theory]
    [InlineData("", false)]
    [InlineData("", true)]
    // cmd.exe special characters in the profile path: & ( ) ^ ! % ; , and spaces stay inside quotes.
    [InlineData(" A&B (x86) ^!100% ;,", false)]
    [InlineData(" A&B (x86) ^!100% ;,", true)]
    public async Task TheMarketplaceLauncherRunsTheInstalledEngineAsTheBridge(string suffix, bool relative)
    {
        var engine = Path.Join(AppContext.BaseDirectory, "NexusAndroid.exe");
        Assert.True(File.Exists(engine), "engine not built next to the tests: " + engine);
        var home = Path.Join(TestPaths.Root, "bridge-e2e-" + Guid.NewGuid().ToString("N") + suffix);
        var state = Path.Join(home, ".nexus", "nexus-android");
        Directory.CreateDirectory(state);
        // Relative: the launcher joins the profile folder and the recorded path (get.ps1's releases\<version>).
        File.WriteAllText(Path.Join(state, "engine.txt"), (relative ? Path.GetRelativePath(state, engine) : engine) + "\r\n");

        var cancel = TestContext.Current.CancellationToken;
        using var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;
        var seen = new List<string>();
        var server = Task.Run(async () =>
        {
            for (var i = 0; i < 2; i++)  // initialize (POST), then the session's DELETE
            {
                using var client = await listener.AcceptTcpClientAsync(cancel);
                using var stream = client.GetStream();
                var head = await ReadHead(stream, cancel);
                lock (seen)
                    seen.Add(head);
                var length = head.Split("\r\n").Select(h => h.Split(':', 2)).Where(h => h.Length == 2 && h[0].Equals("Content-Length", StringComparison.OrdinalIgnoreCase)).Select(h => int.Parse(h[1].Trim(), CultureInfo.InvariantCulture)).FirstOrDefault();
                await stream.ReadExactlyAsync(new byte[length], cancel);
                var body = head.StartsWith("POST", StringComparison.Ordinal) ? "{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"serverInfo\":{\"name\":\"fixture\"}}}" : "";
                var reply = $"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nMcp-Session-Id: e2e-session\r\nContent-Length: {Encoding.UTF8.GetByteCount(body)}\r\nConnection: close\r\n\r\n{body}";
                await stream.WriteAsync(Encoding.UTF8.GetBytes(reply), cancel);
            }
        }, cancel);

        using var process = StartLauncher(home, $"http://127.0.0.1:{port}/mcp");
        try
        {
            await process.StandardInput.WriteLineAsync("{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{}}".AsMemory(), cancel);
            await process.StandardInput.FlushAsync(cancel);
            var line = await process.StandardOutput.ReadLineAsync(cancel).AsTask().WaitAsync(TimeSpan.FromSeconds(30), cancel);
            Assert.Equal("{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"serverInfo\":{\"name\":\"fixture\"}}}", line);
            process.StandardInput.Close();
            Assert.True(process.WaitForExit(15_000));
            Assert.Equal(0, process.ExitCode);
            await server.WaitAsync(TimeSpan.FromSeconds(10), cancel);
        }
        finally
        {
            if (!process.HasExited)
                process.Kill(true);
        }
        Assert.StartsWith("POST /mcp HTTP/1.1", seen[0], StringComparison.Ordinal);
        Assert.Contains("Authorization: Bearer e2e-token", seen[0], StringComparison.Ordinal);
        Assert.StartsWith("DELETE /mcp HTTP/1.1", seen[1], StringComparison.Ordinal);
        Assert.Contains("Mcp-Session-Id: e2e-session", seen[1], StringComparison.Ordinal);
    }

    [Theory]
    [InlineData("", null)]
    [InlineData(" A&B (x86) ^!100% ;,", null)]
    [InlineData(" A&B (x86) ^!100% ;,", "releases\\0.0.1\\NexusAndroid.exe")]  // pointer on a removed version
    public async Task TheMarketplaceLauncherSaysHowToInstallWhenNoEngineIsRecorded(string suffix, string? recorded)
    {
        var home = Path.Join(TestPaths.Root, "bridge-none-" + Guid.NewGuid().ToString("N") + suffix);
        Directory.CreateDirectory(home);
        if (recorded is not null)
        {
            Directory.CreateDirectory(Path.Join(home, ".nexus", "nexus-android"));
            File.WriteAllText(Path.Join(home, ".nexus", "nexus-android", "engine.txt"), recorded + "\r\n");
        }
        using var process = StartLauncher(home, "http://127.0.0.1:9/mcp");
        process.StandardInput.Close();
        var output = await process.StandardOutput.ReadToEndAsync(TestContext.Current.CancellationToken);
        var errors = await process.StandardError.ReadToEndAsync(TestContext.Current.CancellationToken);
        Assert.True(process.WaitForExit(15_000));
        Assert.Equal(1, process.ExitCode);
        Assert.Equal("", output);  // stdout carries the protocol only
        Assert.Contains("NexusAndroid is not installed on this PC", errors, StringComparison.Ordinal);
    }

    private static async Task<string> ReadHead(NetworkStream stream, CancellationToken cancel)
    {
        var head = new List<byte>();
        var one = new byte[1];
        while (!(head.Count >= 4 && head[^4] == '\r' && head[^3] == '\n' && head[^2] == '\r' && head[^1] == '\n'))
        {
            if (await stream.ReadAsync(one, cancel) == 0)
                break;
            head.Add(one[0]);
        }
        return Encoding.ASCII.GetString(head.ToArray());
    }
}
