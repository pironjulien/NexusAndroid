using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace NexusAndroid;

public sealed class McpServer
{
    private readonly NativeEngine _engine;
    private readonly string stdioSession=Guid.NewGuid().ToString("N");
    private readonly System.Collections.Concurrent.ConcurrentDictionary<string,DateTimeOffset> httpSessions=new();
    private static readonly HashSet<string> Protocols=["2024-11-05","2025-03-26","2025-06-18","2025-11-25"];
    private readonly System.Collections.Concurrent.ConcurrentDictionary<string,CancellationTokenSource> requests=new();
    private readonly System.Collections.Concurrent.ConcurrentDictionary<string,bool> cancelledRequests=new();
    private readonly System.Collections.Concurrent.ConcurrentDictionary<string,string> agents=new();

    public McpServer(string? customAdbPath = null)
    {
        _engine = new NativeEngine(new AdbClient(customAdbPath));
    }

    public McpServer(NativeEngine engine) { _engine=engine; }

    public void RunStdio()
    {
        var utf8NoBom = new UTF8Encoding(false);
        using var reader = new StreamReader(Console.OpenStandardInput(), utf8NoBom);
        using var writer = new StreamWriter(Console.OpenStandardOutput(), utf8NoBom) { AutoFlush = true };

        var pending=new List<Task>();var outputLock=new object();
        while (reader.ReadLine() is { } line)
        {
            if (string.IsNullOrWhiteSpace(line)) continue;

            JsonObject? request;
            try
            {
                request = JsonNode.Parse(line) as JsonObject;
            }
            catch
            {
                continue;
            }

            if (request == null) continue;
            if(request["method"]?.ToString()=="notifications/cancelled"){HandleRequest(request);continue;}
            var captured=request;pending.Add(Task.Run(()=>{var response=HandleRequest(captured);if(response is not null)lock(outputLock)writer.WriteLine(response.ToJsonString(new JsonSerializerOptions{WriteIndented=false}));}));
            pending.RemoveAll(t=>t.IsCompleted);
        }
        foreach(var request in requests.Where(p=>p.Key.StartsWith(stdioSession+":",StringComparison.Ordinal)))request.Value.Cancel();
        Task.WaitAll(pending.ToArray());_engine.CloseSession(stdioSession);_engine.Shutdown();
    }

    public JsonObject? HandleRequest(JsonObject req)
    {
        NativeSafety.Session.Value??=stdioSession;
        string? method = req["method"]?.GetValue<string>();
        var id = req["id"];

        if (string.IsNullOrEmpty(method)) return null;

        if (method == "initialize")
        {
            var agent=NativeJournal.Identify(req["params"]?["clientInfo"]?["name"]?.ToString());if(agent is not null)agents[NativeSafety.Session.Value!]=agent;
            return new JsonObject
            {
                ["jsonrpc"] = "2.0",
                ["id"] = id?.DeepClone(),
                ["result"] = new JsonObject
                {
                    ["protocolVersion"] = Protocols.Contains(req["params"]?["protocolVersion"]?.ToString()??"")?req["params"]!["protocolVersion"]!.ToString():"2025-11-25",
                    ["capabilities"] = new JsonObject
                    {
                        ["tools"] = new JsonObject(),
                    },
                    ["serverInfo"] = new JsonObject
                    {
                        ["name"] = "nexus-android",
                        ["version"] = "0.20.3",
                    }
                }
            };
        }

        if (method == "notifications/initialized") return null;
        if(method=="ping")return new JsonObject{["jsonrpc"]="2.0",["id"]=id?.DeepClone(),["result"]=new JsonObject()};
        if(method=="notifications/cancelled")
        {
            var requestKey=(NativeSafety.Session.Value??stdioSession)+":"+req["params"]?["requestId"]?.ToJsonString();if(requests.TryGetValue(requestKey,out var pending))pending.Cancel();else cancelledRequests[requestKey]=true;return null;
        }

        if (method == "tools/list")
        {
            return new JsonObject
            {
                ["jsonrpc"] = "2.0",
                ["id"] = id?.DeepClone(),
                ["result"] = new JsonObject
                {
                    ["tools"] = NativeSchemas.Tools()
                }
            };
        }

        if (method == "tools/call")
        {
            var paramsObj = req["params"] as JsonObject;
            string toolName = paramsObj?["name"]?.GetValue<string>() ?? "";
            var args = paramsObj?["arguments"] as JsonObject ?? new JsonObject();

            try
            {
                NativeJournal.Agent.Value=agents.GetValueOrDefault(NativeSafety.Session.Value??stdioSession)??(NativeSafety.Session.Value==stdioSession?NativeJournal.Identify(Environment.GetEnvironmentVariable("NEXUS_AGENT")):null);
                var requestKey=(NativeSafety.Session.Value??stdioSession)+":"+id?.ToJsonString();using var cancellation=new CancellationTokenSource();requests[requestKey]=cancellation;if(cancelledRequests.TryRemove(requestKey,out _))cancellation.Cancel();NativeSafety.Cancellation.Value=cancellation.Token;
                JsonObject toolResult;try{toolResult=_engine.Execute(toolName,args);}finally{requests.TryRemove(requestKey,out _);NativeSafety.Cancellation.Value=default;if(httpSessions.ContainsKey(NativeSafety.Session.Value!))httpSessions[NativeSafety.Session.Value!]=DateTimeOffset.UtcNow;}
                var result = new JsonObject { ["content"] = toolResult["content"]?.DeepClone() };
                // MCP: structuredContent is an object or absent; null is rejected by clients (map, apps... without one).
                if (toolResult["structuredContent"] is JsonObject structured) result["structuredContent"] = structured.DeepClone();
                return new JsonObject
                {
                    ["jsonrpc"] = "2.0",
                    ["id"] = id?.DeepClone(),
                    ["result"] = result
                };
            }
            catch (Exception ex)
            {
                return new JsonObject
                {
                    ["jsonrpc"] = "2.0",
                    ["id"] = id?.DeepClone(),
                    ["result"] = new JsonObject
                    {
                        ["isError"] = true,
                        ["content"] = new JsonArray
                        {
                            new JsonObject
                            {
                                ["type"] = "text",
                                ["text"] = $"Error executing {toolName}: {NativeSafety.Mask(ex.Message)}"
                            }
                        }
                    }
                };
            }
        }

        return new JsonObject
        {
            ["jsonrpc"] = "2.0",
            ["id"] = id?.DeepClone(),
            ["error"] = new JsonObject
            {
                ["code"] = -32601,
                ["message"] = $"Method not found: {method}"
            }
        };
    }

    public string CallTool(string name, JsonObject args) => _engine.Execute(name, args).ToJsonString();

    private static string? ConfiguredToken()
    {
        var env = Environment.GetEnvironmentVariable("NEXUS_MCP_TOKEN");
        if (!string.IsNullOrWhiteSpace(env))
            return env.Trim();
        var file = Environment.GetEnvironmentVariable("NEXUS_MCP_TOKEN_FILE");
        if (string.IsNullOrWhiteSpace(file))
        {
            var docs = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
            file = Path.Combine(docs, "NexusSecure", "agents", "nexus-mcp-token.txt");
        }
        if (File.Exists(file))
        {
            try { return File.ReadAllText(file).Trim(); } catch { }
        }
        return null;
    }

    private static bool CheckAuth(Dictionary<string, string> headers, string? expectedToken)
    {
        if (string.IsNullOrEmpty(expectedToken))
            return true;
        if (!headers.TryGetValue("authorization", out var auth) || string.IsNullOrWhiteSpace(auth))
            return false;
        const string prefix = "Bearer ";
        if (!auth.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            return false;
        var provided = auth[prefix.Length..].Trim();
        var a = Encoding.UTF8.GetBytes(provided);
        var b = Encoding.UTF8.GetBytes(expectedToken);
        return CryptographicOperations.FixedTimeEquals(a, b);
    }

    private static async Task WriteHttpResponseAsync(Stream stream, int status, string contentType, byte[] body)
    {
        var statusText = status switch
        {
            200 => "OK",
            202 => "Accepted",
            204 => "No Content",
            400 => "Bad Request",
            401 => "Unauthorized",
            404 => "Not Found",
            405 => "Method Not Allowed",
            503 => "Service Unavailable",
            _ => "Status"
        };
        var sessionHeader=NativeSafety.Session.Value is {Length:>0} session?$"Mcp-Session-Id: {session}\r\n":"";
        var header = $"HTTP/1.1 {status} {statusText}\r\nContent-Type: {contentType}\r\nContent-Length: {body.Length}\r\n{sessionHeader}Connection: close\r\n\r\n";
        var headerBytes = Encoding.ASCII.GetBytes(header);
        await stream.WriteAsync(headerBytes);
        if (body.Length > 0)
            await stream.WriteAsync(body);
    }

    public async Task RunHttpAsync(int port = 16180, CancellationToken cancellationToken = default)
    {
        var expectedToken = ConfiguredToken();
        if(string.IsNullOrWhiteSpace(expectedToken))throw new InvalidOperationException("NEXUS_MCP_TOKEN or NEXUS_MCP_TOKEN_FILE required: HTTP daemon refuses unauthenticated startup");
        var listener = new TcpListener(IPAddress.Loopback, port);
        listener.Start();
        try
        {
            using var reg = cancellationToken.Register(() => { try { listener.Stop(); } catch { } });
            while (!cancellationToken.IsCancellationRequested)
            {
                TcpClient client;
                try
                {
                    client = await listener.AcceptTcpClientAsync(cancellationToken);
                }
                catch (OperationCanceledException) { break; }
                catch (SocketException) when (cancellationToken.IsCancellationRequested) { break; }
                _ = Task.Run(() => HandleHttpClientAsync(client, expectedToken), CancellationToken.None);
                foreach(var stale in httpSessions.Where(p=>DateTimeOffset.UtcNow-p.Value>TimeSpan.FromSeconds(1618)&&!requests.Keys.Any(k=>k.StartsWith(p.Key+":",StringComparison.Ordinal))))if(httpSessions.TryRemove(stale.Key,out _))_ = Task.Run(()=>CloseSession(stale.Key));
            }
        }
        finally
        {
            try { listener.Stop(); } catch { }
        }
    }

    private async Task HandleHttpClientAsync(TcpClient client, string? expectedToken)
    {
        using (client)
        {
            try
            {
                using var stream = client.GetStream();using var receiving=new CancellationTokenSource(TimeSpan.FromSeconds(10));
                var headerBytes = new List<byte>(1024);
                var buf = new byte[1];
                int matchState = 0;
                while (headerBytes.Count < 65536)
                {
                    int n = await stream.ReadAsync(buf.AsMemory(0, 1),receiving.Token);
                    if (n == 0) return;
                    byte b = buf[0];
                    headerBytes.Add(b);
                    if ((matchState == 0 && b == '\r') ||
                        (matchState == 1 && b == '\n') ||
                        (matchState == 2 && b == '\r') ||
                        (matchState == 3 && b == '\n'))
                    {
                        matchState++;
                        if (matchState == 4) break;
                    }
                    else
                    {
                        matchState = (b == '\r') ? 1 : 0;
                    }
                }

                var headerText = Encoding.ASCII.GetString(headerBytes.ToArray());
                var lines = headerText.Split("\r\n", StringSplitOptions.RemoveEmptyEntries);
                if (lines.Length == 0) return;

                var reqParts = lines[0].Split(' ', 3);
                if (reqParts.Length < 2) return;
                var method = reqParts[0].ToUpperInvariant();
                var rawTarget = reqParts[1];

                var headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                for (int i = 1; i < lines.Length; i++)
                {
                    int colon = lines[i].IndexOf(':');
                    if (colon > 0)
                        headers[lines[i][..colon].Trim()] = lines[i][(colon + 1)..].Trim();
                }

                int contentLength = 0;
                if (headers.TryGetValue("content-length", out var clStr))
                    int.TryParse(clStr, NumberStyles.None, CultureInfo.InvariantCulture, out contentLength);

                if(contentLength<0||contentLength>4*1024*1024){await WriteHttpResponseAsync(stream,400,"text/plain",Encoding.UTF8.GetBytes("Request too large"));return;}
                var bodyBytes = new byte[contentLength];
                int offset = 0;
                while (offset < contentLength)
                {
                    int read = await stream.ReadAsync(bodyBytes.AsMemory(offset, contentLength - offset),receiving.Token);
                    if (read == 0) break;
                    offset += read;
                }

                var qIdx = rawTarget.IndexOf('?');
                var path = qIdx >= 0 ? rawTarget[..qIdx] : rawTarget;

                if (path == "/health")
                {
                    bool hasAuthHeader = headers.ContainsKey("authorization") || headers.ContainsKey("x-nexus-mcp-token");
                    if (hasAuthHeader && !CheckAuth(headers, expectedToken))
                    {
                        await WriteHttpResponseAsync(stream, 401, "text/plain; charset=utf-8", Encoding.UTF8.GetBytes("Token refused"));
                        return;
                    }
                    var healthJson = new JsonObject
                    {
                        ["status"] = "ok",
                        ["service"] = "nexus-android",
                        ["runtime"] = "native",
                        ["pid"] = Environment.ProcessId,
                        ["version"] = "0.20.3",
                    };
                    await WriteHttpResponseAsync(stream, 200, "application/json", Encoding.UTF8.GetBytes(healthJson.ToJsonString()));
                    return;
                }

                if (method == "GET" && path == "/mcp")
                {
                    if (!CheckAuth(headers, expectedToken))
                    {
                        await WriteHttpResponseAsync(stream, 401, "text/plain; charset=utf-8", Encoding.UTF8.GetBytes("Token refused"));
                        return;
                    }
                    await WriteHttpResponseAsync(stream, 405, "text/plain; charset=utf-8", Encoding.UTF8.GetBytes("This MCP endpoint uses JSON responses; an SSE GET stream is not provided"));
                    return;
                }

                if (!CheckAuth(headers, expectedToken))
                {
                    await WriteHttpResponseAsync(stream, 401, "text/plain; charset=utf-8", Encoding.UTF8.GetBytes("Token refused"));
                    return;
                }

                if (path != "/mcp")
                {
                    await WriteHttpResponseAsync(stream, 404, "application/json", Encoding.UTF8.GetBytes("""{"error":"Not found"}"""));
                    return;
                }

                if(method=="DELETE")
                {
                    if(!headers.TryGetValue("mcp-session-id",out var deleted)||!httpSessions.TryRemove(deleted,out _)){await WriteHttpResponseAsync(stream,404,"text/plain",Encoding.UTF8.GetBytes("Unknown or expired session"));return;}
                    CloseSession(deleted);await WriteHttpResponseAsync(stream,204,"application/json",[]);return;
                }
                if(method!="POST"){await WriteHttpResponseAsync(stream,405,"text/plain",Encoding.UTF8.GetBytes("POST or DELETE required"));return;}
                if(offset!=contentLength){await WriteHttpResponseAsync(stream,400,"text/plain",Encoding.UTF8.GetBytes("Truncated body"));return;}

                var bodyStr = Encoding.UTF8.GetString(bodyBytes, 0, offset);
                var reqNode = JsonNode.Parse(bodyStr);
                if (reqNode is JsonObject reqObj)
                {
                    if(reqObj["method"]?.ToString()=="initialize")
                    {
                        NativeSafety.Session.Value=Guid.NewGuid().ToString("N");httpSessions[NativeSafety.Session.Value]=DateTimeOffset.UtcNow;
                    }
                    else
                    {
                        if(!headers.TryGetValue("mcp-session-id",out var session)){await WriteHttpResponseAsync(stream,400,"text/plain",Encoding.UTF8.GetBytes("Initialize an MCP session first, then send its Mcp-Session-Id header"));return;}if(!httpSessions.ContainsKey(session)){await WriteHttpResponseAsync(stream,404,"text/plain",Encoding.UTF8.GetBytes("Unknown or expired MCP session; initialize again"));return;}
                        NativeSafety.Session.Value=session;httpSessions[session]=DateTimeOffset.UtcNow;
                    }
                    var resp = HandleRequest(reqObj);
                    if (resp is null)
                    {
                        await WriteHttpResponseAsync(stream, 202, "application/json", []);
                    }
                    else
                    {
                        await WriteHttpResponseAsync(stream, 200, "application/json", Encoding.UTF8.GetBytes(resp.ToJsonString()));
                    }
                }
                else
                {
                    await WriteHttpResponseAsync(stream, 400, "application/json", Encoding.UTF8.GetBytes("""{"error":"Bad request"}"""));
                }
            }
            catch
            {
                // Ignore client disconnect
            }
        }
    }
    private void CloseSession(string session)
    {
        foreach(var request in requests.Where(p=>p.Key.StartsWith(session+":",StringComparison.Ordinal)))request.Value.Cancel();
        try{_engine.CloseSession(session);}finally{agents.TryRemove(session,out _);foreach(var item in cancelledRequests.Keys.Where(k=>k.StartsWith(session+":",StringComparison.Ordinal)))cancelledRequests.TryRemove(item,out _);}
    }
}
