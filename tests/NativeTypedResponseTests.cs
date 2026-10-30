using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using NexusAndroid.Tests.Engine;

namespace NexusAndroid.Tests.Typed;

/// <summary>Public MCP contracts and typed (structuredContent) responses of the engine, on configured transports only
/// (former test_native_differential.py). The Python reference engine is gone: its answers were captured once on
/// 30/10/2026 and are frozen below as explicit expectations (contract digests, settings and location values); the
/// other expectations are derived from the reference code (extras.py) with the same local-time formatting.</summary>
public sealed class NativeTypedResponseTests
{
    private static readonly string[] ToolNames =
    [
        "act", "apps", "capture", "checkup", "clipboard", "draft", "files", "functions", "info",
        "launch", "logs", "look", "map", "messages", "notifications", "phone", "settings", "web",
    ];
    private static readonly string[] ContractFields = ["name", "inputSchema", "outputSchema", "annotations"];

    /// <summary>SHA-256 of each tool's {name, inputSchema, outputSchema, annotations} in the Python reference
    /// (json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)), captured on 30/10/2026.</summary>
    private static readonly Dictionary<string, string> ReferenceDigests = new(StringComparer.Ordinal)
    {
        ["look"] = "8c872348a900617534935a4117cab00efc95fee00fc20296e57e57b3ee2a26de",
        ["act"] = "90602f3144ecc96be7d844f3d73c09aebb8a6620932a5b142209392c698554e9",
        ["launch"] = "89f80743ca0223917de14ec698fda9d5d24f817d311ca55e3a17a0006f358571",
        ["messages"] = "9343da1448826ca7fcad794529c301c0b880ee6b8a1bd07d0b6f2249f91e370b",
        ["draft"] = "3d45a1095efc03da151f129c8767003bb168971d51ad8c87175fc67bc22d3cb5",
        ["notifications"] = "7472120017b5bed8516daff87ee5270ba5bcd623eb270d3b8f3858e43f5cd2e4",
        ["files"] = "11da3c26434559089ef600b42f734dfe7142720c132f94a93632654133f05a25",
        ["capture"] = "d5ca727ef4f19e8173260df48f23abfbd019efaa77acf49e660600d3fec97d49",
        ["logs"] = "890b296530cd5a22acaf498b62357010e751049982254a24fa13fa22de92b4c4",
        ["clipboard"] = "d232922fdda1fd15488a091026ba2102f59322e5a52c302fff349c0c60526ed0",
        ["info"] = "7a16694b881e38e625d85ad883273fe588229bdef3f4d3400cc52bc747f37df3",
        ["settings"] = "6357b0d6d8edbc9e1c7ab2017a2bea8dc1363fa9debf545eb8e241e1c6787417",
        ["functions"] = "4ea30c7e8528b6bdd9fd573833db8e93d7081428055867c505f2b870997280b4",
        ["web"] = "4b1951daadafdfe12429cb878d6a973620b74ddb80e301431f1bb1859599a3d5",
        ["apps"] = "846bc556202eef9c9ceb41ffce1af63e4f8dbbdd3c47bbe845e417e7c86d0eee",
        ["map"] = "9e2176b840d7153bdbffa0af9160cb7a7bbd52f08618c8fe320cd7c426bf6a35",
        ["phone"] = "5139bc0fe5709ec789b087c67e1d44e782b0f7a1eda4ee2fd5916f0beb713d68",
        ["checkup"] = "fd2c777961d42251a22c6b3b0749a3d63ffe923ac3c2859707b85e514abf33e7",
    };

    /// <summary>Tools that advertise an outputSchema in the reference; every other tool has none.</summary>
    private static readonly HashSet<string> TypedTools = ["info", "phone", "settings"];

    // ------------------------------------------------------------------ helpers

    private static readonly Lazy<Dictionary<string, JsonObject>> Contracts = new(() =>
    {
        var init = FixtureDriver.Call(new JsonObject
        {
            ["rpc"] = new JsonObject
            {
                ["jsonrpc"] = "2.0", ["id"] = 1, ["method"] = "initialize",
                ["params"] = new JsonObject { ["protocolVersion"] = "2025-06-18", ["capabilities"] = new JsonObject(), ["clientInfo"] = new JsonObject { ["name"] = "offline-typed", ["version"] = "1" } },
            },
        });
        Assert.Equal("2025-06-18", init["response"]?["result"]?["protocolVersion"]?.ToString());
        var listed = FixtureDriver.Call(new JsonObject { ["rpc"] = new JsonObject { ["jsonrpc"] = "2.0", ["id"] = 2, ["method"] = "tools/list", ["params"] = new JsonObject() } });
        var response = listed["response"]!.AsObject();
        Assert.False(response.ContainsKey("error"), response.ToJsonString());
        var tools = response["result"]!["tools"]!.AsArray().Select(t => t!.AsObject()).ToList();
        var result = tools.ToDictionary(t => t["name"]!.ToString(), t => t, StringComparer.Ordinal);
        Assert.Equal(18, tools.Count);
        Assert.Equal(18, result.Count);
        Assert.Equal(ToolNames.Order(StringComparer.Ordinal), result.Keys.Order(StringComparer.Ordinal));
        return result;
    });

    private static JsonObject FixtureCall(string tool, JsonObject args, JsonObject fixture)
    {
        var result = FixtureDriver.Call(tool, args, fixture);
        var response = result["response"]!.AsObject();
        Assert.False(response.ContainsKey("error"), response.ToJsonString());
        Assert.NotEqual(true, response["result"]?["isError"]?.GetValue<bool>());
        return result;
    }

    private static JsonObject Validated(JsonObject result, string tool)
    {
        var response = result["response"]!["result"]!.AsObject();
        Assert.True(response.ContainsKey("structuredContent"), $"{tool} omitted its advertised structuredContent");
        var data = response["structuredContent"]!.AsObject();
        var schema = Contracts.Value[tool]["outputSchema"]!.AsObject();
        var errors = new List<string>();
        SchemaValidator.Validate(schema, schema, data, "$", errors);
        Assert.True(errors.Count == 0, string.Join("\n", errors.Select(e => tool + " " + e)));
        var text = string.Join("\n", response["content"]!.AsArray().Where(b => b?["type"]?.ToString() == "text").Select(b => b!["text"]!.ToString()));
        Assert.True(data["text"]?.ToString() == text, "Structured and human-readable output disagree");
        return data;
    }

    private static readonly Regex Mutation = new(@"(?:^|[;\s])(?:input|svc|reboot|rm|mkdir)(?:\s|$)|\b(?:settings put|am start|am force-stop|set_dnd|set-location-enabled)\b|--set\b");

    private static void AssertReadOnly(JsonObject result)
    {
        foreach (var command in result["commands"]!.AsArray().Select(c => c!.ToString()))
            Assert.False(Mutation.IsMatch(command.Replace("'", "")), "Read-only call dispatched mutation: " + command);
    }

    private static JsonObject Shell(params (string Key, string Value)[] pairs)
    {
        var shell = new JsonObject();
        foreach (var (key, value) in pairs) shell[key] = value;
        return shell;
    }

    private static DateTime Local(long milliseconds) => DateTimeOffset.FromUnixTimeMilliseconds(milliseconds).LocalDateTime;
    private static string IsoSeconds(long milliseconds) => Local(milliseconds).ToString("yyyy-MM-dd'T'HH:mm:ss", CultureInfo.InvariantCulture);
    private static string IsoMinutes(long milliseconds) => Local(milliseconds).ToString("yyyy-MM-dd'T'HH:mm", CultureInfo.InvariantCulture);

    private static void AssertJson(JsonNode? expected, JsonNode? actual) =>
        Assert.True(JsonNode.DeepEquals(expected, actual), $"expected {expected?.ToJsonString() ?? "null"}\nactual   {actual?.ToJsonString() ?? "null"}");

    // ------------------------------------------------------------------ contracts

    public static TheoryData<string> Tools => new(ToolNames);

    /// <summary>Nested enums, defaults, required, $defs and annotations, without normalization: frozen digest of the
    /// Python reference contract (the native canonical JSON is printed on mismatch).</summary>
    [Theory, MemberData(nameof(Tools))]
    public void AllNativeMcpContractsMatchFrozenReference(string name)
    {
        var tool = Contracts.Value[name];
        var subset = new JsonObject();
        foreach (var field in ContractFields)
            if (tool.ContainsKey(field)) subset[field] = tool[field]!.DeepClone();
        Assert.Equal(TypedTools.Contains(name), subset.ContainsKey("outputSchema"));
        var canonical = Canonical.Serialize(subset);
        var digest = Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(canonical)));
        Assert.True(digest == ReferenceDigests[name], $"Public MCP contract differs for {name}: {canonical}");
    }

    /// <summary>30/10: `map` returned structuredContent: null, rejected by Claude Code (object expected or field absent).</summary>
    [Fact]
    public void ToolWithoutOutputSchemaOmitsStructuredContent()
    {
        var result = FixtureCall("functions", new JsonObject(), new JsonObject { ["shell"] = Shell(("cmd app_function list-app-functions", "{}")) });
        Assert.False(result["response"]!["result"]!.AsObject().ContainsKey("structuredContent"), result["response"]!["result"]!.ToJsonString());
    }

    // ------------------------------------------------------------------ phone

    public static TheoryData<string, string, bool, bool> PhoneCases()
    {
        var data = new TheoryData<string, string, bool, bool>();
        foreach (var (posture, expected) in new[] { ("CLOSED", "folded"), ("HALF_OPENED", "half-open"), ("OPENED", "open") })
            foreach (var (locked, trusted) in new[] { (false, true), (true, true), (true, false) })
                data.Add(posture, expected, locked, trusted);
        return data;
    }

    [Theory, MemberData(nameof(PhoneCases))]
    public void PhoneTypedStatusFromControlledTransport(string posture, string expected, bool locked, bool trusted)
    {
        var state = "mCurrentFocus=Window{fixture u0 com.example.app/.Main}\nmWakefulness=Awake\n" +
                    $"isKeyguardShowing={(locked ? "true" : "false")}\n" +
                    $"mCommittedState=Optional[DeviceState{{identifier=1, name='{posture}'}}]";
        var result = FixtureCall("phone", new JsonObject(), new JsonObject
        {
            ["state"] = state,
            ["shell"] = Shell(("getprop ro.product.model", "Fixture Fold"), ("getprop ro.build.version.release", "17"),
                ("dumpsys trust", $"(current): deviceLocked={(trusted ? 0 : 1)}"),
                ("cat /proc/sys/kernel/random/boot_id", "00000000-0000-0000-0000-000000000001")),
        });
        var data = Validated(result, "phone");
        var expectedFields = new JsonObject
        {
            ["model"] = "Fixture Fold", ["android"] = "17", ["serial"] = "fixture-device", ["transport"] = "usb",
            ["foreground"] = "com.example.app", ["posture"] = expected, ["screen_on"] = true,
            ["locked"] = locked, ["code_required"] = locked && !trusted,
            ["kept_awake_until"] = null, ["rebooted"] = false,
        };
        var actual = new JsonObject();
        foreach (var (key, _) in expectedFields) { Assert.True(data.ContainsKey(key), "missing " + key); actual[key] = data[key]?.DeepClone(); }
        AssertJson(expectedFields, actual);
        AssertReadOnly(result);
    }

    // ------------------------------------------------------------------ settings

    /// <summary>name, shell fixture (JSON), reference text and value (Python extras.setting, captured 30/10/2026).
    /// Missing or unknown Android values keep stable semantics, never an invented value.</summary>
    public static TheoryData<string, string, string, string> SettingCases => new()
    {
        { "wifi", """{"wifi_on": "1"}""", "wifi: on", "on" },
        { "wifi", """{"wifi_on": "0"}""", "wifi: off", "off" },
        { "bluetooth", """{"bluetooth_on": "1"}""", "bluetooth: on", "on" },
        { "data", """{"mobile_data": "0"}""", "data: off", "off" },
        { "location", """{"location_mode": "3"}""", "location: on", "on" },
        { "location", """{"location_mode": "0"}""", "location: off", "off" },
        { "nfc", """{"dumpsys nfc": "mState = on"}""", "nfc: on", "on" },
        { "dnd", """{"zen_mode": "1"}""", "dnd: priority", "priority" },
        { "dnd", """{"zen_mode": "3"}""", "dnd: alarms", "alarms" },
        { "brightness", """{"screen_brightness_mode": "1"}""", "brightness: auto", "auto" },
        { "brightness", """{"screen_brightness_mode": "0", "screen_brightness": "128"}""", "brightness: 50 %", "50 %" },
        { "volume", """{"media_session volume": "volume is 7 in range [2..12]"}""", "volume: 50 % (media)", "50 % (media)" },
        { "wifi", "{}", "wifi: on", "on" },
        { "location", "{}", "location: off", "off" },
        { "dnd", """{"zen_mode": "null"}""", "dnd: ?", "?" },
        { "brightness", "{}", "brightness: ?", "?" },
        { "volume", "{}", "volume: ?", "?" },
    };

    [Theory, MemberData(nameof(SettingCases))]
    public void SettingsTypedReadMatchesReference(string name, string shell, string referenceText, string referenceValue)
    {
        var result = FixtureCall("settings", new JsonObject { ["name"] = name }, new JsonObject { ["shell"] = JsonNode.Parse(shell) });
        var data = Validated(result, "settings");
        AssertJson(new JsonObject { ["name"] = name, ["value"] = referenceValue, ["changed"] = false, ["text"] = referenceText }, data);
        AssertReadOnly(result);
    }

    // ------------------------------------------------------------------ info

    /// <summary>dumpsys location fixture, reference text and location object (Python extras.location, captured 30/10/2026).</summary>
    public static TheoryData<string, string, string> LocationCases => new()
    {
        {
            "7200.00 1000.00\nLocation[gps 48.100000,2.100000 hAcc=5.0 et=+1h59m30s]",
            "Location: 48.100000, 2.100000 (within 5 m), recorded 30 s ago · https://maps.google.com/?q=48.100000,2.100000",
            """{"latitude": 48.1, "longitude": 2.1, "accuracy_m": 5, "age_s": 30, "maps_url": "https://maps.google.com/?q=48.100000,2.100000"}"""
        },
        {
            "7200.00 1000.00\nLocation[gps 48.100000,2.100000 hAcc=5.0 et=+1h30m0s]\nLocation[network 48.200000,2.200000 hAcc=30.0 et=+1h59m30s]",
            "Location: 48.200000, 2.200000 (within 30 m), recorded 30 s ago · https://maps.google.com/?q=48.200000,2.200000",
            """{"latitude": 48.2, "longitude": 2.2, "accuracy_m": 30, "age_s": 30, "maps_url": "https://maps.google.com/?q=48.200000,2.200000"}"""
        },
        { "7200.00 1000.00\n", "No known phone location (location off or never computed).", "null" },
    };

    [Theory, MemberData(nameof(LocationCases))]
    public void InfoLocationTypedResponseMatchesReference(string dump, string text, string location)
    {
        var result = FixtureCall("info", new JsonObject { ["kind"] = "location" }, new JsonObject { ["shell"] = Shell(("cat /proc/uptime; dumpsys location", dump)) });
        var data = Validated(result, "info");
        AssertJson(new JsonObject
        {
            ["kind"] = "location", ["text"] = text, ["location"] = JsonNode.Parse(location),
            ["calls"] = new JsonArray(), ["contacts"] = new JsonArray(), ["events"] = new JsonArray(),
        }, data);
        AssertReadOnly(result);
    }

    [Theory]
    [InlineData("1", "incoming")]
    [InlineData("2", "outgoing")]
    [InlineData("3", "missed")]
    [InlineData("4", "voicemail")]
    [InlineData("5", "rejected")]
    [InlineData("6", "blocked")]
    public void InfoCallsPreservesTypedRecordsAndContactResolution(string callType, string expectedType)
    {
        const long timestamp = 1793269800000;
        const string number = "+15550101001";  // synthetic fixture only
        var result = FixtureCall("info", new JsonObject { ["kind"] = "calls", ["limit"] = 1 }, new JsonObject
        {
            ["queries"] = new JsonObject
            {
                ["content://call_log/calls"] = new JsonArray(new JsonObject { ["date"] = timestamp.ToString(CultureInfo.InvariantCulture), ["duration"] = "65", ["type"] = callType, ["number"] = number }),
                ["content://com.android.contacts/phone_lookup/*"] = new JsonArray(new JsonObject { ["display_name"] = "Fixture Contact" }),
            },
        });
        var data = Validated(result, "info");
        AssertJson(new JsonArray(new JsonObject
        {
            ["date"] = IsoSeconds(timestamp), ["type"] = expectedType, ["who"] = $"Fixture Contact ({number})",
            ["number"] = number, ["duration_s"] = 65,
        }), data["calls"]);
        Assert.Equal("calls", data["kind"]?.ToString());
        Assert.Null(data["location"]);
        AssertJson(new JsonArray(), data["contacts"]);
        AssertJson(new JsonArray(), data["events"]);
        Assert.Equal("date DESC LIMIT 1", result["query_calls"]![0]!["sort"]!.ToString());
        AssertReadOnly(result);
    }

    /// <summary>Reference limit range 1..200; expected rows and text derived from extras.calls (missed calls without
    /// number: "private number", no duration).</summary>
    [Theory]
    [InlineData(0, 1)]
    [InlineData(200, 200)]
    [InlineData(201, 200)]
    public void InfoCallsRetainsReferenceLimitRange(int requested, int expectedLimit)
    {
        var dates = Enumerable.Range(0, 200).Select(index => 1793269800000L - index * 60000L).ToList();
        var rows = new JsonArray(dates.Select(date => (JsonNode?)new JsonObject { ["date"] = date.ToString(CultureInfo.InvariantCulture), ["duration"] = "0", ["type"] = "3", ["number"] = "" }).ToArray());
        var kept = dates.Take(expectedLimit).ToList();
        var referenceRows = new JsonArray(kept.Select(date => (JsonNode?)new JsonObject
        {
            ["date"] = IsoSeconds(date), ["type"] = "missed", ["who"] = "private number", ["number"] = "", ["duration_s"] = 0,
        }).ToArray());
        var referenceText = "Recent calls:\n" + string.Join("\n", kept.Select(date => Local(date).ToString("dd'/'MM HH':'mm", CultureInfo.InvariantCulture) + " · missed · private number"));
        var result = FixtureCall("info", new JsonObject { ["kind"] = "calls", ["limit"] = requested }, new JsonObject { ["queries"] = new JsonObject { ["content://call_log/calls"] = rows } });
        var data = Validated(result, "info");
        AssertJson(referenceRows, data["calls"]);
        Assert.Equal(referenceText, data["text"]?.ToString());
        Assert.Equal($"date DESC LIMIT {expectedLimit}", result["query_calls"]![0]!["sort"]!.ToString());
        AssertReadOnly(result);
    }

    [Fact]
    public void InfoContactsReturnsNamesNumbersAndDeduplicates()
    {
        var result = FixtureCall("info", new JsonObject { ["kind"] = "contacts", ["query"] = "Fixture Contact" }, new JsonObject
        {
            ["queries"] = new JsonObject
            {
                ["content://com.android.contacts/data/phones/filter/*"] = new JsonArray(
                    new JsonObject { ["display_name"] = "Fixture Contact", ["data1"] = "+15550101001" },
                    new JsonObject { ["display_name"] = "Fixture Contact", ["data1"] = "+1 555 010 1001" },
                    new JsonObject { ["display_name"] = "Fixture Contact", ["data1"] = "+15550101002" }),
            },
        });
        var data = Validated(result, "info");
        AssertJson(new JsonArray(
            new JsonObject { ["name"] = "Fixture Contact", ["number"] = "+15550101001" },
            new JsonObject { ["name"] = "Fixture Contact", ["number"] = "+15550101002" }), data["contacts"]);
        Assert.Equal("contacts", data["kind"]?.ToString());
        Assert.Null(data["location"]);
        AssertJson(new JsonArray(), data["calls"]);
        AssertJson(new JsonArray(), data["events"]);
        AssertReadOnly(result);
    }

    [Fact]
    public void InfoCalendarPreservesRecurringInstancesNullsAndAllDay()
    {
        const long begin = 1793269800000, end = 1793273400000;
        var result = FixtureCall("info", new JsonObject { ["kind"] = "calendar", ["days"] = 3 }, new JsonObject
        {
            ["queries"] = new JsonObject
            {
                ["content://com.android.calendar/instances/when/*"] = new JsonArray(
                    new JsonObject { ["begin"] = begin.ToString(CultureInfo.InvariantCulture), ["end"] = end.ToString(CultureInfo.InvariantCulture), ["allDay"] = "0", ["eventLocation"] = "Fixture Room", ["title"] = "Recurring Fixture" },
                    new JsonObject { ["begin"] = (begin + 86400000).ToString(CultureInfo.InvariantCulture), ["end"] = "NULL", ["allDay"] = "1", ["eventLocation"] = "NULL", ["title"] = "Recurring Fixture" }),
            },
        });
        var data = Validated(result, "info");
        AssertJson(new JsonArray(
            new JsonObject { ["begin"] = IsoMinutes(begin), ["end"] = IsoMinutes(end), ["all_day"] = false, ["title"] = "Recurring Fixture", ["location"] = "Fixture Room" },
            new JsonObject { ["begin"] = IsoMinutes(begin + 86400000), ["end"] = null, ["all_day"] = true, ["title"] = "Recurring Fixture", ["location"] = "" }), data["events"]);
        Assert.Equal("calendar", data["kind"]?.ToString());
        Assert.Null(data["location"]);
        AssertJson(new JsonArray(), data["calls"]);
        AssertJson(new JsonArray(), data["contacts"]);
        var query = result["query_calls"]!.AsArray().Select(c => c!.AsObject()).First(c => c["uri"]!.ToString().Contains("/instances/when/", StringComparison.Ordinal));
        var parts = query["uri"]!.ToString().Split('/');
        var (queryBegin, queryEnd) = (long.Parse(parts[^2], CultureInfo.InvariantCulture), long.Parse(parts[^1], CultureInfo.InvariantCulture));
        Assert.Equal(3 * 86400000L, queryEnd - queryBegin);
        Assert.Equal("begin ASC", query["sort"]!.ToString());
        AssertReadOnly(result);
    }
}

/// <summary>Python json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True) of a JSON tree.</summary>
internal static class Canonical
{
    public static string Serialize(JsonNode? node)
    {
        var builder = new StringBuilder();
        Write(builder, node);
        return builder.ToString();
    }

    private static void Write(StringBuilder builder, JsonNode? node)
    {
        switch (node)
        {
            case null: builder.Append("null"); break;
            case JsonObject obj:
                builder.Append('{');
                var first = true;
                foreach (var (key, value) in obj.OrderBy(p => p.Key, StringComparer.Ordinal))
                {
                    if (!first) builder.Append(',');
                    first = false;
                    String(builder, key);
                    builder.Append(':');
                    Write(builder, value);
                }
                builder.Append('}');
                break;
            case JsonArray array:
                builder.Append('[');
                for (var i = 0; i < array.Count; i++) { if (i > 0) builder.Append(','); Write(builder, array[i]); }
                builder.Append(']');
                break;
            case JsonValue value:
                var element = System.Text.Json.JsonDocument.Parse(value.ToJsonString()).RootElement;
                switch (element.ValueKind)
                {
                    case System.Text.Json.JsonValueKind.String: String(builder, element.GetString()!); break;
                    case System.Text.Json.JsonValueKind.True: builder.Append("true"); break;
                    case System.Text.Json.JsonValueKind.False: builder.Append("false"); break;
                    case System.Text.Json.JsonValueKind.Null: builder.Append("null"); break;
                    case System.Text.Json.JsonValueKind.Number:
                        builder.Append(element.TryGetInt64(out var integer) && !element.GetRawText().Contains('.')
                            ? integer.ToString(CultureInfo.InvariantCulture)
                            : PythonFloat(element.GetDouble()));
                        break;
                    default: throw new InvalidOperationException("unexpected JSON value " + element.ValueKind);
                }
                break;
        }
    }

    private static string PythonFloat(double value)
    {
        var text = value.ToString("R", CultureInfo.InvariantCulture);
        return text.Contains('.') || text.Contains('E') ? text : text + ".0";
    }

    private static void String(StringBuilder builder, string text)
    {
        builder.Append('"');
        foreach (var c in text)
        {
            switch (c)
            {
                case '"': builder.Append("\\\""); break;
                case '\\': builder.Append("\\\\"); break;
                case '\n': builder.Append("\\n"); break;
                case '\r': builder.Append("\\r"); break;
                case '\t': builder.Append("\\t"); break;
                case '\b': builder.Append("\\b"); break;
                case '\f': builder.Append("\\f"); break;
                default:
                    if (c < 0x20 || c > 0x7e) builder.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                    else builder.Append(c);
                    break;
            }
        }
        builder.Append('"');
    }
}

/// <summary>JSON Schema (2020-12) validation for the subset the output schemas use: type, enum, properties, required,
/// additionalProperties, items, anyOf, local $ref (#/$defs/…); annotations (title, description, default) ignored. Any
/// other keyword fails the test, so a schema growing beyond this subset is noticed instead of silently accepted.</summary>
internal static class SchemaValidator
{
    private static readonly HashSet<string> Known = ["type", "enum", "properties", "required", "additionalProperties", "items", "anyOf", "$ref", "$defs", "title", "description", "default"];

    public static void Validate(JsonObject root, JsonObject schema, JsonNode? data, string path, List<string> errors)
    {
        foreach (var (keyword, _) in schema)
            if (!Known.Contains(keyword)) errors.Add($"{path}: unsupported schema keyword {keyword}");
        if (schema["$ref"] is JsonNode reference)
        {
            var target = reference.ToString();
            if (!target.StartsWith("#/$defs/", StringComparison.Ordinal) || root["$defs"]?[target["#/$defs/".Length..]] is not JsonObject definition)
                errors.Add($"{path}: unresolvable $ref {target}");
            else Validate(root, definition, data, path, errors);
        }
        if (schema["anyOf"] is JsonArray anyOf)
        {
            var matched = anyOf.Any(option =>
            {
                var local = new List<string>();
                Validate(root, option!.AsObject(), data, path, local);
                return local.Count == 0;
            });
            if (!matched) errors.Add($"{path}: matches no anyOf option ({data?.ToJsonString() ?? "null"})");
        }
        if (schema["type"] is JsonNode type)
        {
            var types = type is JsonArray list ? list.Select(t => t!.ToString()).ToList() : [type.ToString()];
            if (!types.Any(t => IsType(data, t))) errors.Add($"{path}: {data?.ToJsonString() ?? "null"} is not of type {string.Join("|", types)}");
        }
        if (schema["enum"] is JsonArray choices && !choices.Any(choice => JsonNode.DeepEquals(choice, data)))
            errors.Add($"{path}: {data?.ToJsonString() ?? "null"} is not one of {choices.ToJsonString()}");
        if (data is JsonObject obj)
        {
            foreach (var required in schema["required"]?.AsArray() ?? [])
                if (!obj.ContainsKey(required!.ToString())) errors.Add($"{path}: '{required}' is a required property");
            var properties = schema["properties"] as JsonObject;
            foreach (var (key, value) in obj)
            {
                if (properties?[key] is JsonObject property) Validate(root, property, value, path + "." + key, errors);
                else if (schema["additionalProperties"] is JsonValue additional && additional.TryGetValue<bool>(out var allowed) && !allowed)
                    errors.Add($"{path}: additional property '{key}' not allowed");
                else if (schema["additionalProperties"] is JsonObject additionalSchema) Validate(root, additionalSchema, value, path + "." + key, errors);
            }
        }
        if (data is JsonArray array && schema["items"] is JsonObject items)
            for (var i = 0; i < array.Count; i++) Validate(root, items, array[i], $"{path}[{i}]", errors);
    }

    private static bool IsType(JsonNode? data, string type)
    {
        if (data is null) return type == "null";
        if (data is JsonObject) return type == "object";
        if (data is JsonArray) return type == "array";
        var element = System.Text.Json.JsonDocument.Parse(data.ToJsonString()).RootElement;
        return type switch
        {
            "string" => element.ValueKind == System.Text.Json.JsonValueKind.String,
            "boolean" => element.ValueKind is System.Text.Json.JsonValueKind.True or System.Text.Json.JsonValueKind.False,
            "number" => element.ValueKind == System.Text.Json.JsonValueKind.Number,
            "integer" => element.ValueKind == System.Text.Json.JsonValueKind.Number && element.TryGetDouble(out var d) && d == Math.Floor(d),
            "null" => element.ValueKind == System.Text.Json.JsonValueKind.Null,
            _ => false,
        };
    }
}
