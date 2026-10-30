using System.Text.Json.Nodes;

namespace NexusAndroid.Tests.Connection;

/// <summary>One phone selection (ConnectRemembered) against a scripted adb (former ConnectionContracts --fixture-json,
/// now in process). Fixture keys: state, forced, mdns, same_subnet, restore_offline, devices [{serial, state}],
/// identities, shell, answers, ports, scan. Result: {selected, transport} or {error: exception type}, plus state,
/// commands and scans.</summary>
internal static class ConnectionDriver
{
    public static JsonObject Select(JsonObject fixture)
    {
        var adb = new FakeAdb { State = fixture["state"]?.DeepClone().AsObject() ?? new(), Forced = fixture["forced"]?.ToString(), Mdns = fixture["mdns"]?.ToString() ?? "", LocalSubnet = fixture["same_subnet"]?.GetValue<bool>() ?? true, RestoreOffline = fixture["restore_offline"]?.GetValue<bool>() == true };
        foreach (var device in fixture["devices"]?.AsArray().OfType<JsonObject>() ?? []) adb.Devices.Add(new(device["serial"]!.ToString(), device["state"]?.ToString() ?? "device", "Fixture", !AdbClient.IsWirelessSerial(device["serial"]!.ToString())));
        foreach (var pair in fixture["identities"]?.AsObject() ?? new()) adb.Ids[pair.Key] = pair.Value!.ToString();
        foreach (var pair in fixture["shell"]?.AsObject() ?? new()) adb.ShellOutputs[pair.Key] = pair.Value!.ToString();
        foreach (var answer in fixture["answers"]?.AsArray() ?? []) adb.Answers.Add(answer!.ToString());
        adb.Ports = fixture["ports"]?.AsArray().Select(p => p!.GetValue<int>()).ToArray() ?? [];
        var result = new JsonObject();
        try { var selected = adb.ConnectRemembered(fixture["scan"]?.GetValue<bool>() ?? true); result["selected"] = selected?.Serial; result["transport"] = selected is null ? null : selected.IsUsb ? "usb" : "wifi"; }
        catch (Exception error) { result["error"] = error.GetType().Name; }
        result["state"] = adb.State.DeepClone();
        result["commands"] = new JsonArray(adb.Commands.Select(c => (JsonNode?)JsonValue.Create(c)).ToArray());
        result["scans"] = new JsonArray(adb.Scans.Select(c => (JsonNode?)JsonValue.Create(c)).ToArray());
        return result;
    }
}
