using System.Text.Json.Nodes;

namespace NexusAndroid.Tests.Connection;

/// <summary>Phone selection (ConnectRemembered) on scripted adb transports, against the Python reference selection
/// algorithm (former native_connection/test_reference_connection.py). The reference (nexus_android.adb.Adb.connect,
/// same scripted transport) was run once on 30/10/2026; its result per scenario is frozen below: error or selected
/// device and transport, wifi.json state written, Wi-Fi port scans.</summary>
public sealed class ConnectionReferenceTests
{
    private const string Known = """{"serialno": "MY-HARDWARE", "usb": "MY-USB", "address": "192.0.2.67:33915", "custom": "preserved"}""";

    /// <summary>The connection invariants (ConnectionContractTests) keep their full count: none silently dropped.</summary>
    [Fact]
    public void NativeConnectionInvariantsAreAllPresent()
    {
        var names = new ContractSuite(ConnectionContracts.Run).Names();
        Assert.Equal(47, names.Count);
    }

    /// <summary>fixture (JSON, ConnectionDriver keys); expected selection ("error", or "serial|usb", "serial|wifi");
    /// expected state (JSON); expected scans (comma-separated hosts).</summary>
    public static TheoryData<string, string, string, string> Scenarios => new()
    {
        // 0. A foreign Wi-Fi device without a registered phone: never adopted.
        { """{"devices": [{"serial": "192.0.2.8:40000"}], "identities": {"192.0.2.8:40000": "OTHER"}}""", "error", "{}", "" },
        // 1. An emulator alone is not the user's phone.
        { """{"devices": [{"serial": "emulator-5554"}], "identities": {"emulator-5554": "EMULATOR"}}""", "error", "{}", "" },
        // 2. First USB phone: registered.
        { """{"devices": [{"serial": "MY-USB"}], "identities": {"MY-USB": "MY-HARDWARE"}}""", "MY-USB|usb", """{"serialno": "MY-HARDWARE", "usb": "MY-USB"}""", "" },
        // 3. Several USB devices and nothing registered: refused.
        { """{"devices": [{"serial": "ONE"}, {"serial": "TWO"}], "identities": {"ONE": "ONE-ID", "TWO": "TWO-ID"}}""", "error", "{}", "" },
        // 4. USB device without readable identity: refused.
        { """{"devices": [{"serial": "MY-USB"}], "identities": {}}""", "error", "{}", "" },
        // 5. Registered phone over Wi-Fi next to a foreign USB device.
        { $$$"""{"state": {{{Known}}}, "devices": [{"serial": "FOREIGN"}, {"serial": "192.0.2.67:33915"}], "identities": {"FOREIGN": "OTHER", "192.0.2.67:33915": "MY-HARDWARE"}}""", "192.0.2.67:33915|wifi", Known, "" },
        // 6. Same phone on Wi-Fi and USB: USB preferred.
        { $$$"""{"state": {{{Known}}}, "devices": [{"serial": "192.0.2.67:33915"}, {"serial": "MY-USB"}], "identities": {"MY-USB": "MY-HARDWARE", "192.0.2.67:33915": "MY-HARDWARE"}}""", "MY-USB|usb", Known, "" },
        // 7. Old state without serialno: completed, other keys preserved.
        { """{"state": {"usb": "OLD-ID", "custom": "preserved"}, "devices": [{"serial": "OLD-ID"}], "identities": {"OLD-ID": "OLD-ID"}}""", "OLD-ID|usb", """{"usb": "OLD-ID", "custom": "preserved", "serialno": "OLD-ID"}""", "" },
        // 8. NEXUS_ANDROID_SERIAL taken as is.
        { $$$"""{"state": {{{Known}}}, "forced": "emulator-5554", "devices": [{"serial": "emulator-5554"}]}""", "emulator-5554|usb", Known, "" },
        // 9. Forced device missing: refused, never another device.
        { $$$"""{"state": {{{Known}}}, "forced": "missing", "devices": [{"serial": "MY-USB"}], "identities": {"MY-USB": "MY-HARDWARE"}}""", "error", Known, "" },
        // 10. Forced Wi-Fi address connected.
        { $$$"""{"state": {{{Known}}}, "forced": "192.0.2.3:40000", "answers": ["192.0.2.3:40000"]}""", "192.0.2.3:40000|wifi", Known, "" },
        // 11. Remembered address answers with the registered phone.
        { $$$"""{"state": {{{Known}}}, "answers": ["192.0.2.67:33915"], "identities": {"192.0.2.67:33915": "MY-HARDWARE"}}""", "192.0.2.67:33915|wifi", Known, "" },
        // 12. Remembered address answers with another phone: refused after a port scan.
        { $$$"""{"state": {{{Known}}}, "answers": ["192.0.2.67:33915"], "identities": {"192.0.2.67:33915": "FOREIGN"}}""", "error", Known, "192.0.2.67" },
        // 13. mDNS finds the new port: address updated.
        { $$$"""{"state": {{{Known}}}, "mdns": "x _adb-tls-connect._tcp. 192.0.2.67:40001\n", "answers": ["192.0.2.67:40001"], "identities": {"192.0.2.67:40001": "MY-HARDWARE"}}""", "192.0.2.67:40001|wifi", """{"serialno": "MY-HARDWARE", "usb": "MY-USB", "address": "192.0.2.67:40001", "custom": "preserved"}""", "" },
        // 14. Port scan finds the new port: address updated.
        { $$$"""{"state": {{{Known}}}, "ports": [40000, 40001], "answers": ["192.0.2.67:40001"], "identities": {"192.0.2.67:40001": "MY-HARDWARE"}}""", "192.0.2.67:40001|wifi", """{"serialno": "MY-HARDWARE", "usb": "MY-USB", "address": "192.0.2.67:40001", "custom": "preserved"}""", "192.0.2.67" },
        // 15. Another subnet: no scan.
        { $$$"""{"state": {{{Known}}}, "ports": [40000], "same_subnet": false}""", "error", Known, "" },
        // 16. scan=False: no scan.
        { $$$"""{"state": {{{Known}}}, "ports": [40000], "scan": false}""", "error", Known, "" },
        // 17. Offline USB restored by `adb reconnect offline`.
        { $$$"""{"state": {{{Known}}}, "devices": [{"serial": "MY-USB", "state": "offline"}], "restore_offline": true, "identities": {"MY-USB": "MY-HARDWARE"}}""", "MY-USB|usb", Known, "" },
        // 18. Unauthorized USB: refused after a Wi-Fi scan.
        { $$$"""{"state": {{{Known}}}, "devices": [{"serial": "MY-USB", "state": "unauthorized"}]}""", "error", Known, "192.0.2.67" },
        // 19. USB without permissions: refused after a Wi-Fi scan.
        { $$$"""{"state": {{{Known}}}, "devices": [{"serial": "MY-USB", "state": "no permissions"}]}""", "error", Known, "192.0.2.67" },
        // 20. mDNS-named wireless serial of the registered phone.
        { $$$"""{"state": {{{Known}}}, "devices": [{"serial": "adb-MY_123._adb-tls-connect._tcp"}], "identities": {"adb-MY_123._adb-tls-connect._tcp": "MY-HARDWARE"}}""", "adb-MY_123._adb-tls-connect._tcp|wifi", Known, "" },
    };

    [Theory, MemberData(nameof(Scenarios))]
    public void NativeSelectionMatchesReference(string fixture, string expected, string state, string scans)
    {
        var actual = ConnectionDriver.Select(JsonNode.Parse(fixture)!.AsObject());
        var context = actual.ToJsonString();
        if (expected == "error")
        {
            Assert.True(actual["error"] is not null, "expected a refusal: " + context);
        }
        else
        {
            Assert.True(actual["error"] is null, "unexpected refusal: " + context);
            var parts = expected.Split('|');
            Assert.Equal(parts[0], actual["selected"]?.ToString());
            Assert.Equal(parts[1], actual["transport"]?.ToString());
        }
        Assert.True(JsonNode.DeepEquals(JsonNode.Parse(state), actual["state"]), $"state: expected {state}, actual {actual["state"]?.ToJsonString()}");
        var expectedScans = scans.Length == 0 ? [] : scans.Split(',');
        Assert.Equal(expectedScans, actual["scans"]!.AsArray().Select(s => s!.ToString()).ToArray());
    }
}
