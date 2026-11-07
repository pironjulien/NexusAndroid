using System.Net;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace NexusAndroid;

/// <summary>`NexusAndroid.exe --bridge`: the Antigravity Marketplace plugin's stdio server. Antigravity starts plugins
/// over stdio and expands only ${PLUGIN_ROOT}, so a Marketplace copy cannot name this PC's token; the shared service
/// runs once per PC (MCP Streamable HTTP on 127.0.0.1, bearer token of NEXUS_MCP_TOKEN, else
/// %USERPROFILE%\.nexus\mcp-token.txt). Each JSON-RPC line read on stdin is posted to that service and every reply,
/// JSON or event stream, is written back as one line. A request the service cannot answer gets a JSON-RPC error saying
/// how to install or start the product (formerly scripts/bridge.ps1, which compiled this same relay at every start).</summary>
public sealed class StdioBridge : IDisposable
{
    private static readonly UTF8Encoding Utf8 = new(false);
    private static readonly JsonSerializerOptions Compact = new() { Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping };

    private readonly HttpClient http;
    private readonly Uri url;
    private readonly string product;
    private readonly string? token;
    private readonly Stream output;
    private readonly TextWriter errors;
    private readonly object gate = new();
    private volatile string? session;

    /// <param name="handler">HTTP transport (tests); by default a direct connection, never through a proxy.</param>
    public StdioBridge(Uri url, string product, string? token, Stream output, TextWriter errors, HttpMessageHandler? handler = null)
    {
        http = new HttpClient(handler ?? new HttpClientHandler { UseProxy = false }) { Timeout = Timeout.InfiniteTimeSpan };
        this.url = url;
        this.product = product;
        this.token = token;
        this.output = output;
        this.errors = errors;
    }

    /// <summary>The service's session id, once its initialize reply has given one.</summary>
    public string? Session => session;

    /// <summary>NEXUS_MCP_TOKEN when set, else the content of %USERPROFILE%\.nexus\mcp-token.txt, else none.</summary>
    public static string? ReadToken()
    {
        var token = Environment.GetEnvironmentVariable("NEXUS_MCP_TOKEN");
        if (!string.IsNullOrWhiteSpace(token))
            return token;
        var file = Path.Combine(Environment.GetEnvironmentVariable("USERPROFILE") ?? "", ".nexus", "mcp-token.txt");
        return File.Exists(file) ? File.ReadAllText(file).Trim() : null;
    }

    /// <summary>`--bridge [--url http://127.0.0.1:16180/mcp] [--product NexusAndroid]` on this process's stdio.</summary>
    public static async Task<int> RunConsoleAsync(string[] args)
    {
        string Option(string name, string fallback)
        {
            var index = Array.FindIndex(args, a => a.Equals(name, StringComparison.OrdinalIgnoreCase));
            return index >= 0 && index + 1 < args.Length ? args[index + 1] : fallback;
        }
        if (!Uri.TryCreate(Option("--url", "http://127.0.0.1:16180/mcp"), UriKind.Absolute, out var url))
        {
            await Console.Error.WriteLineAsync("--bridge: --url needs an absolute address.").ConfigureAwait(false);
            return 2;
        }
        using var input = new StreamReader(Console.OpenStandardInput(), Utf8);
        using var stdout = Console.OpenStandardOutput();
        using var bridge = new StdioBridge(url, Option("--product", "NexusAndroid"), ReadToken(), stdout, Console.Error);
        return await bridge.RunAsync(input).ConfigureAwait(false);
    }

    /// <summary>Relays every line of input until it ends, then waits up to 5 s for the replies still running and closes
    /// the service session (DELETE). Exit code 0.</summary>
    public async Task<int> RunAsync(TextReader input)
    {
        var pending = new List<Task>();
        while (await input.ReadLineAsync().ConfigureAwait(false) is { } line)
        {
            if (line.Trim().Length == 0)
                continue;
            var message = line;
            var task = Task.Run(() => ForwardAsync(message));
            // The session id comes with the initialize reply: nothing else is sent before it.
            if (session is null && message.Contains("\"initialize\"", StringComparison.Ordinal))
                await task.ConfigureAwait(false);
            pending.RemoveAll(t => t.IsCompleted);
            pending.Add(task);
        }
        await Task.WhenAny(Task.WhenAll(pending), Task.Delay(TimeSpan.FromSeconds(5))).ConfigureAwait(false);
        if (session is not null)
        {
            try
            {
                using var bye = new HttpRequestMessage(HttpMethod.Delete, url);
                Authorize(bye);
                using var closed = await http.SendAsync(bye).WaitAsync(TimeSpan.FromSeconds(2)).ConfigureAwait(false);
            }
            catch (Exception)
            {
                // The service may already be gone: its session ends with it.
            }
        }
        return 0;
    }

    private void Authorize(HttpRequestMessage request)
    {
        if (!string.IsNullOrEmpty(token))
            request.Headers.TryAddWithoutValidation("Authorization", "Bearer " + token);
        if (session is { } id)
            request.Headers.TryAddWithoutValidation("Mcp-Session-Id", id);
    }

    private async Task ForwardAsync(string message)
    {
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Post, url) { Content = new StringContent(message, Utf8, "application/json") };
            request.Headers.TryAddWithoutValidation("Accept", "application/json, text/event-stream");
            Authorize(request);
            using var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead).ConfigureAwait(false);
            if (response.Headers.TryGetValues("Mcp-Session-Id", out var ids))
            {
                foreach (var id in ids)
                    session = id;
            }
            if (!response.IsSuccessStatusCode && response.StatusCode != HttpStatusCode.Accepted)
            {
                Fail(message, $"{product} answered HTTP {(int)response.StatusCode}.");
                return;
            }
            var type = response.Content.Headers.ContentType?.MediaType ?? "";
            using var body = new StreamReader(await response.Content.ReadAsStreamAsync().ConfigureAwait(false), Utf8);
            if (type.Equals("text/event-stream", StringComparison.OrdinalIgnoreCase))
                await RelayEventsAsync(body).ConfigureAwait(false);
            else
            {
                var text = (await body.ReadToEndAsync().ConfigureAwait(false)).Trim();
                if (text.Length > 0)
                    Write(text.Replace("\r", "", StringComparison.Ordinal).Replace("\n", "", StringComparison.Ordinal));
            }
        }
        catch (Exception e)
        {
            var root = e;
            while (root.InnerException is not null)
                root = root.InnerException;
            Fail(message, $"{product} is not reachable on this PC ({root.Message}). Install or start it: ask the agent to follow the {product} skill.");
        }
    }

    /// <summary>Server-sent events: the data lines of each event, joined, become one output line.</summary>
    private async Task RelayEventsAsync(StreamReader body)
    {
        var data = new StringBuilder();
        while (await body.ReadLineAsync().ConfigureAwait(false) is { } row)
        {
            if (row.StartsWith("data:", StringComparison.Ordinal))
                data.Append(row.AsSpan(row.Length > 5 && row[5] == ' ' ? 6 : 5));
            else if (row.Length == 0 && data.Length > 0)
            {
                Write(data.ToString());
                data.Clear();
            }
        }
        if (data.Length > 0)
            Write(data.ToString());
    }

    /// <summary>The reason goes to stderr; a request (id and method) also gets a JSON-RPC error reply. A notification
    /// or a line that is not a JSON object gets nothing.</summary>
    private void Fail(string message, string reason)
    {
        lock (gate)
            errors.WriteLine(reason);
        JsonNode? id;
        try
        {
            if (JsonNode.Parse(message) is not JsonObject parsed || !parsed.ContainsKey("id") || !parsed.ContainsKey("method"))
                return;
            id = parsed["id"]?.DeepClone();
        }
        catch (JsonException)
        {
            return;
        }
        var reply = new JsonObject
        {
            ["jsonrpc"] = "2.0",
            ["id"] = id,
            ["error"] = new JsonObject { ["code"] = -32000, ["message"] = reason },
        };
        Write(reply.ToJsonString(Compact));
    }

    private void Write(string json)
    {
        var bytes = Utf8.GetBytes(json + "\n");
        lock (gate)
        {
            output.Write(bytes, 0, bytes.Length);
            output.Flush();
        }
    }

    public void Dispose() => http.Dispose();
}
