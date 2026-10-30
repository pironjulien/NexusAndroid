using System.Text.Json.Nodes;

namespace NexusAndroid.Tests.Engine;

/// <summary>One MCP call on the real engine and server against a configured phone (former NativeContracts
/// --fixture-json, now in process). Command: {tool, args, fixture} or {rpc, fixture}; fixture keys: state, xml,
/// shell, queries, exec_out… (ConfiguredPhone, ConfiguredAdb). Result: {response, commands, query_calls}.</summary>
internal static class FixtureDriver
{
    private static int caseNumber;

    public static JsonObject Call(JsonObject command)
    {
        var number = Interlocked.Increment(ref caseNumber);
        var phone = new ConfiguredPhone(command["fixture"]?.DeepClone().AsObject() ?? new());
        var transport = new ConfiguredAdb(phone);
        var directory = Path.Combine(TestPaths.Root, "fixture-cases", number.ToString());
        var server = new McpServer(new NativeEngine(transport, phone, directory, () => new ConfiguredControl(phone)));
        var request = command["rpc"]?.DeepClone().AsObject() ?? new JsonObject
        {
            ["jsonrpc"] = "2.0", ["id"] = number, ["method"] = "tools/call",
            ["params"] = new JsonObject { ["name"] = command["tool"]?.DeepClone(), ["arguments"] = command["args"]?.DeepClone() ?? new JsonObject() },
        };
        var result = server.HandleRequest(request);
        return new JsonObject
        {
            ["response"] = result?.DeepClone(),
            ["commands"] = new JsonArray(transport.Commands.Select(c => (JsonNode?)JsonValue.Create(c)).ToArray()),
            ["query_calls"] = new JsonArray(phone.QueryCalls.Select(c => c.DeepClone()).ToArray()),
        };
    }

    /// <summary>Tool call shortcut: {tool, args, fixture}.</summary>
    public static JsonObject Call(string tool, JsonObject args, JsonObject fixture) =>
        Call(new JsonObject { ["tool"] = tool, ["args"] = args, ["fixture"] = fixture });
}
