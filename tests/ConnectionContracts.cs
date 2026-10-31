using NexusAndroid;
using System.Buffers.Binary;
using System.Net;
using System.Text.Json.Nodes;

namespace NexusAndroid.Tests.Connection;

// Contracts run in declaration order, once per test run (ContractSuite); each one is a test case.
internal static class ConnectionContracts
{
    public static void Run(Action<string, Action> Test)
    {
void Assert(bool condition, string message) { if (!condition) throw new Exception(message); }
void Denied(Action run, string part)
{
    try { run(); }
    catch (Exception error) when (error.Message.Contains(part, StringComparison.OrdinalIgnoreCase)) { return; }
    throw new Exception("Expected refusal: " + part);
}
FakeAdb New() => new();
void Registered(FakeAdb adb) => adb.State = new JsonObject { ["serialno"] = "MY-HARDWARE", ["usb"] = "MY-USB", ["address"] = "192.0.2.67:33915", ["custom"] = "preserved" };
DeviceInfo Usb(string serial, string state = "device") => new(serial, state, "Fixture", true);
DeviceInfo Wifi(string serial, string state = "device") => new(serial, state, "Fixture", false);

Test("Device parser recognizes mDNS transport and unauthorized USB", () =>
{
    var adb = New();
    adb.Devices = [Wifi("adb-MY_123._adb-tls-connect._tcp"), Usb("MY-USB", "unauthorized")];
    var devices = adb.GetDevices();
    Assert(devices.Count == 2 && !devices[0].IsUsb && devices[1].State == "unauthorized", "transport parser drift");
});
Test("A lone unknown Wi-Fi device is never adopted", () =>
{
    var adb = New(); adb.Devices = [Wifi("192.0.2.8:40000")]; adb.Ids["192.0.2.8:40000"] = "OTHER";
    Denied(() => adb.PickDevice(), "No phone registered");
    Assert(adb.SelectedSerial is null && adb.Saves == 0 && !adb.Commands.Any(c => c.Contains("ro.serialno")), "unknown Wi-Fi inspected/adopted");
});
Test("Emulators excluded from implicit first registration", () =>
{
    var adb = New(); adb.Devices = [Usb("emulator-5554")]; adb.Ids["emulator-5554"] = "EMULATOR";
    Denied(() => adb.PickDevice(), "No phone registered"); Assert(adb.Saves == 0, "emulator registered");
});
Test("One verified USB phone establishes identity and Wi-Fi metadata", () =>
{
    var adb = New(); adb.Devices = [Usb("MY-USB")]; adb.Ids["MY-USB"] = "MY-HARDWARE";
    adb.ShellOutputs["ip -4 addr"] = "    inet 192.0.2.67/24 brd 192.0.2.255 scope global wlan0\n";
    adb.ShellOutputs["cat /proc/net/tcp6 /proc/net/tcp"] = " 0: 0000000000000000:9C40 00000000:0000 0A 0 0 0 2000 0 0\n";
    Assert(adb.PickDevice()?.Serial == "MY-USB", "USB not chosen");
    Assert(adb.State["serialno"]?.ToString() == "MY-HARDWARE" && adb.State["address"]?.ToString() == "192.0.2.67:40000", "metadata missing");
});
Test("Several unknown USB phones are refused without arbitrary adoption", () =>
{
    var adb = New(); adb.Devices = [Usb("ONE"), Usb("TWO")]; adb.Ids["ONE"] = "ID-ONE"; adb.Ids["TWO"] = "ID-TWO";
    Denied(() => adb.PickDevice(), "Several USB"); Assert(adb.Saves == 0 && adb.SelectedSerial is null, "arbitrary USB adopted");
});
Test("A USB device without a hardware identity cannot become registered", () =>
{
    var adb = New(); adb.Devices = [Usb("ONE")]; Denied(() => adb.PickDevice(), "No phone registered"); Assert(adb.Saves == 0, "unreadable identity adopted");
});
Test("Registered Wi-Fi phone beats unrelated USB", () =>
{
    var adb = New(); Registered(adb); adb.Devices = [Usb("FOREIGN"), Wifi("192.0.2.67:33915")];
    adb.Ids["FOREIGN"] = "FOREIGN-HARDWARE"; adb.Ids["192.0.2.67:33915"] = "MY-HARDWARE";
    Assert(adb.PickDevice()?.Serial == "192.0.2.67:33915", "unrelated USB won");
    Assert(adb.State["usb"]?.ToString() == "MY-USB" && adb.State["serialno"]?.ToString() == "MY-HARDWARE", "registered identity replaced");
});
Test("Same hardware over USB and Wi-Fi prefers USB", () =>
{
    var adb = New(); Registered(adb); adb.Devices = [Wifi("192.0.2.67:33915"), Usb("MY-USB")];
    adb.Ids["MY-USB"] = adb.Ids["192.0.2.67:33915"] = "MY-HARDWARE";
    Assert(adb.PickDevice()?.Serial == "MY-USB", "USB not preferred for same phone");
});
Test("Current selection cannot override a mismatching hardware identity", () =>
{
    var adb = New(); Registered(adb); adb.SelectedSerial = "FOREIGN"; adb.Devices = [Usb("FOREIGN")]; adb.Ids["FOREIGN"] = "OTHER";
    Denied(() => adb.PickDevice(), "Registered phone not found"); Assert(adb.SelectedSerial is null && adb.Saves == 0, "selection bypassed identity");
});
Test("Legacy USB identity is verified and upgraded without losing fields", () =>
{
    var adb = New(); adb.State = new JsonObject { ["usb"] = "OLD-ID", ["custom"] = "preserved" }; adb.Devices = [Usb("OLD-ID")]; adb.Ids["OLD-ID"] = "OLD-ID";
    adb.PickDevice(); Assert(adb.State["serialno"]?.ToString() == "OLD-ID" && adb.State["custom"]?.ToString() == "preserved", "legacy state damaged");
});
Test("Explicit emulator selection is allowed and never changes registration", () =>
{
    var adb = New(); Registered(adb); var original = adb.State.ToJsonString(); adb.Forced = "emulator-5554"; adb.Devices = [Usb("emulator-5554")];
    Assert(adb.PickDevice()?.Serial == "emulator-5554", "explicit choice refused"); Assert(adb.State.ToJsonString() == original && adb.Saves == 0, "forced choice persisted");
});
Test("Explicit unreachable device never falls back to another phone", () =>
{
    var adb = New(); adb.Forced = "missing"; adb.Devices = [Usb("OTHER")]; adb.Ids["OTHER"] = "OTHER";
    Denied(() => adb.PickDevice(), "NEXUS_ANDROID_SERIAL"); Assert(adb.SelectedSerial is null && adb.Saves == 0, "forced fallback");
});
Test("Explicit Wi-Fi endpoint reconnects only to that endpoint", () =>
{
    var adb = New(); Registered(adb); adb.Forced = "192.0.2.3:43210"; adb.Answers.Add(adb.Forced);
    Assert(adb.PickDevice()?.Serial == adb.Forced, "forced endpoint not connected");
    Assert(adb.Connects.SequenceEqual(new[] { adb.Forced }) && adb.Saves == 0, "extra discovery or registration");
});
Test("Malformed explicit serial cannot inject host arguments", () =>
{
    var adb = New(); adb.Forced = "x -s another"; Denied(() => adb.PickDevice(), "Invalid ADB"); Assert(adb.Commands.Count == 0, "malformed selector used");
});
Test("Known old endpoint is tried before mDNS and verified before use", () =>
{
    var adb = New(); Registered(adb); adb.Answers.Add("192.0.2.67:33915"); adb.Ids["192.0.2.67:33915"] = "MY-HARDWARE";
    Assert(adb.PickDevice()?.Serial == "192.0.2.67:33915", "saved endpoint failed"); Assert(!adb.Commands.Contains("mdns services"), "unnecessary mDNS");
});
Test("Wrong device at remembered address is disconnected and not persisted", () =>
{
    var adb = New(); Registered(adb); var original = adb.State.ToJsonString(); adb.Answers.Add("192.0.2.67:33915"); adb.Ids["192.0.2.67:33915"] = "FOREIGN";
    Denied(() => adb.PickDevice(), "Registered phone not found"); Assert(adb.Commands.Contains("disconnect 192.0.2.67:33915") && adb.State.ToJsonString() == original, "foreign endpoint retained");
});
Test("Changed identity at same endpoint is re-read instead of cached", () =>
{
    var adb = New(); Registered(adb); adb.Devices = [Wifi("192.0.2.67:33915")]; adb.Ids["192.0.2.67:33915"] = "MY-HARDWARE"; adb.PickDevice();
    adb.Ids["192.0.2.67:33915"] = "FOREIGN"; Denied(() => adb.PickDevice(), "Registered phone not found"); Assert(adb.SelectedSerial is null, "stale identity cache trusted");
});
Test("mDNS reconnect requires exact hardware identity and preserves user fields", () =>
{
    var adb = New(); Registered(adb); adb.Mdns = "adb-fixture _adb-tls-connect._tcp. 192.0.2.67:40001\n";
    adb.Answers.Add("192.0.2.67:40001"); adb.Ids["192.0.2.67:40001"] = "MY-HARDWARE";
    Assert(adb.PickDevice()?.Serial == "192.0.2.67:40001", "mDNS reconnect failed");
    Assert(adb.State["address"]?.ToString() == "192.0.2.67:40001" && adb.State["custom"]?.ToString() == "preserved", "new endpoint not saved cleanly");
});
Test("Stale saved address failure does not prevent later mDNS candidate", () =>
{
    var adb = New(); Registered(adb); adb.ThrowConnect.Add("192.0.2.67:33915"); adb.Mdns = "x _adb-tls-connect._tcp 192.0.2.67:40001\n";
    adb.Answers.Add("192.0.2.67:40001"); adb.Ids["192.0.2.67:40001"] = "MY-HARDWARE";
    Assert(adb.PickDevice()?.Serial == "192.0.2.67:40001" && adb.Commands.Contains("disconnect 192.0.2.67:33915"), "stale port blocked reconnect");
});
Test("Discovery scans only remembered IP on the same subnet", () =>
{
    var adb = New(); Registered(adb); adb.Ports = [40000, 40001]; adb.Answers.Add("192.0.2.67:40001"); adb.Ids["192.0.2.67:40001"] = "MY-HARDWARE";
    Assert(adb.PickDevice()?.Serial == "192.0.2.67:40001", "scan candidate failed"); Assert(adb.Scans.SequenceEqual(new[] { "192.0.2.67" }), "wrong host scanned");
});
Test("Different subnet never invokes port scan", () =>
{
    var adb = New(); Registered(adb); adb.LocalSubnet = false; Denied(() => adb.PickDevice(), "Registered phone not found"); Assert(adb.Scans.Count == 0, "foreign subnet scanned");
});
Test("Timer-style scan=false avoids scan and does not cache incomplete absence", () =>
{
    var adb = New(); Registered(adb); Denied(() => adb.ConnectRemembered(false), "Registered phone not found"); Assert(adb.Scans.Count == 0, "timer scanned");
    adb.Commands.Clear(); Denied(() => adb.PickDevice(), "Registered phone not found"); Assert(adb.Connects.Count == 1 && adb.Scans.Count == 1, "partial search suppressed later full search");
});
Test("Full absent search is cached for 120 seconds but USB remains immediately detectable", () =>
{
    var adb = New(); Registered(adb); Denied(() => adb.PickDevice(), "Registered phone not found"); var scans = adb.Scans.Count;
    adb.Commands.Clear(); Denied(() => adb.PickDevice(), "Registered phone not found"); Assert(adb.Connects.Count == 0 && adb.Scans.Count == scans, "absence searched again");
    adb.Devices = [Usb("MY-USB")]; adb.Ids["MY-USB"] = "MY-HARDWARE"; Assert(adb.PickDevice()?.Serial == "MY-USB", "USB hidden by Wi-Fi absence cache");
});
Test("Absence cache expires and permits a new Wi-Fi search", () =>
{
    var adb = New(); Registered(adb); Denied(() => adb.PickDevice(), "Registered phone not found"); adb.Now += 121; adb.Commands.Clear();
    Denied(() => adb.PickDevice(), "Registered phone not found"); Assert(adb.Connects.Count == 1 && adb.Scans.Count == 2, "absence cache never expired");
});
Test("Scan results bounded to four verified ADB candidate ports", () =>
{
    var adb = New(); Registered(adb); adb.Ports = [1234, 29999, 30000, 30001, 30002, 30003, 30004, 50000];
    Denied(() => adb.PickDevice(), "Registered phone not found"); Assert(adb.Connects.Count == 5 && adb.Connects.Skip(1).SequenceEqual(new[] { "192.0.2.67:30000", "192.0.2.67:30001", "192.0.2.67:30002", "192.0.2.67:30003" }), "scan attempt bound/range drift");
});
Test("Offline link is reconnected before Wi-Fi search", () =>
{
    var adb = New(); Registered(adb); adb.Devices = [Usb("MY-USB", "offline")]; adb.Ids["MY-USB"] = "MY-HARDWARE"; adb.RestoreOffline = true;
    Assert(adb.PickDevice()?.Serial == "MY-USB", "offline not recovered"); Assert(adb.Commands.Contains("reconnect offline") && !adb.Commands.Contains("mdns services"), "offline reconnect ordering");
});
Test("Connection loss clears selected Wi-Fi once without retrying input", () =>
{
    var adb = New(); Registered(adb); adb.SelectedSerial = "192.0.2.67:33915"; adb.ConnectionLost();
    Assert(adb.SelectedSerial is null && adb.Commands.SequenceEqual(new[] { "disconnect 192.0.2.67:33915" }), "lost connection not invalidated cleanly");
    Assert(adb.Saves == 0, "lost connection modified identity");
});
Test("Late failure of a previous transport cannot clear a newly selected one", () =>
{
    var adb = New(); adb.SelectedSerial = "MY-USB"; adb.ConnectionLost("192.0.2.67:33915"); Assert(adb.SelectedSerial == "MY-USB" && adb.Commands.Count == 0, "new selection invalidated by old callback");
});
Test("Host discovery never changes selection while issuing untargeted commands", () =>
{
    var adb = New(); Registered(adb); adb.SelectedSerial = "stale"; adb.Answers.Add("192.0.2.67:33915"); adb.Ids["192.0.2.67:33915"] = "MY-HARDWARE"; adb.PickDevice();
    Assert(adb.SelectionDuringHost.All(s => s == "stale" || s == "192.0.2.67:33915"), "temporary selection clearing");
    Assert(adb.Commands.Contains("connect 192.0.2.67:33915") && !adb.Commands.Any(c => c.StartsWith("-s stale connect")), "host command targeted accidentally");
});
Test("Unauthorized/no-permissions states report refusal rather than selecting them", () =>
{
    foreach (var state in new[] { "unauthorized", "no permissions" })
    {
        var adb = New(); adb.Devices = [Usb("MY-USB", state)]; Denied(() => adb.PickDevice(), state == "unauthorized" ? "not authorized" : "permissions"); Assert(adb.SelectedSerial is null && adb.Saves == 0, "unusable device selected");
    }
});
Test("Malformed remembered IP is not used for connect or scanning", () =>
{
    var adb = New(); Registered(adb); adb.State["address"] = "192.0.2.67:40000 -s OTHER";
    Denied(() => adb.PickDevice(), "invalid"); Assert(adb.Connects.Count == 0 && adb.Scans.Count == 0, "malformed endpoint used");
});
Test("mDNS parser filters pairing services, duplicates and invalid addresses", () =>
{
    var parsed = AdbClient.MdnsAddresses("x _adb-tls-pairing._tcp 192.0.2.1:1234\nx _adb-tls-connect._tcp 192.0.2.2:40000\ny _adb-tls-connect._tcp. 192.0.2.2:40000\nz _adb-tls-connect._tcp 999.0.0.1:40000\nx _adb-tls-connect._tcp 192.0.2.1:99999\n");
    Assert(parsed.SequenceEqual(new[] { "192.0.2.2:40000" }), "mDNS unsafe/duplicate address");
});
Test("Wi-Fi interface parsing prefers wlan0 over tethering interfaces", () =>
{
    Assert(AdbClient.WifiAddress("inet 192.0.2.8/24 scope global ap_br_swlan0\ninet 192.0.2.67/24 scope global wlan0\n") == "192.0.2.67", "wrong interface chosen");
    Assert(AdbClient.WifiAddress("inet 192.0.2.8/24 scope global ap_br_swlan0\n") == "192.0.2.8", "tethering missing");
});
Test("ADB listening port parser requires listen state and shell UID", () =>
{
    var dump = "0: 0000:13AD 0000:0000 0A 0 0 0 2000\n1: 0000:9C40 0000:0000 01 0 0 0 2000\n2: 0000:9C41 0000:0000 0A 0 0 0 1000\n3: 0000:9C42 0000:0000 0A 0 0 0 2000\n";
    Assert(AdbClient.AdbdPort(dump) == 40002, "wrong service mistaken for adbd");
});
Test("ADB handshake requires supported command and coherent magic", () =>
{
    var hello = AdbClient.AdbHello(); Assert(hello.Length == 31 && hello.Skip(24).SequenceEqual("host::\0"u8.ToArray()), "CNXN payload drift");
    Assert(BinaryPrimitives.ReadUInt32LittleEndian(hello.AsSpan(12, 4)) == 7 && BinaryPrimitives.ReadUInt32LittleEndian(hello.AsSpan(16, 4)) == "host::\0"u8.ToArray().Sum(b => b), "length/checksum drift");
    foreach (uint command in new uint[] { 0x4E584E43, 0x48545541, 0x534C5453 })
    {
        var header = new byte[24]; BinaryPrimitives.WriteUInt32LittleEndian(header, command); BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(20), command ^ uint.MaxValue); Assert(AdbClient.SpeaksAdb(header), "valid ADB reply refused"); header[23] ^= 1; Assert(!AdbClient.SpeaksAdb(header), "wrong magic accepted");
    }
    Assert(!AdbClient.SpeaksAdb("HTTP/1.1 200 OK\r\nnot adb!!"u8) && !AdbClient.SpeaksAdb(new byte[23]), "non-ADB service accepted");
});
Test("Subnet comparison uses IPv4 /24 and rejects other networks and families", () =>
{
    Assert(AdbClient.SameSubnet(IPAddress.Parse("192.0.2.1"), IPAddress.Parse("192.0.2.254")), "same subnet refused");
    Assert(!AdbClient.SameSubnet(IPAddress.Parse("192.0.2.1"), IPAddress.Parse("192.0.3.1")), "other subnet accepted");
    Assert(!AdbClient.SameSubnet(IPAddress.Loopback, IPAddress.IPv6Loopback), "mixed address families");
});
Test("Boot identity survives daemon restart and is reported once", () =>
{
    var adb = New(); Registered(adb); adb.State["boot_id"] = "00000000-0000-0000-0000-000000000001"; adb.Devices = [Usb("MY-USB")]; adb.Ids["MY-USB"] = "MY-HARDWARE";
    adb.ShellOutputs["cat /proc/sys/kernel/random/boot_id"] = "00000000-0000-0000-0000-000000000002\n"; adb.PickDevice();
    Assert(adb.ConsumeReboot() && !adb.ConsumeReboot(), "reboot not single-report");
    var restarted = New(); restarted.State = adb.State.DeepClone().AsObject(); restarted.Devices = adb.Devices; restarted.Ids["MY-USB"] = "MY-HARDWARE"; restarted.ShellOutputs["cat /proc/sys/kernel/random/boot_id"] = adb.ShellOutputs["cat /proc/sys/kernel/random/boot_id"]; restarted.PickDevice();
    Assert(!restarted.ConsumeReboot(), "same boot reported after daemon restart");
});
Test("First boot and invalid boot are never reported as a reboot", () =>
{
    var adb = New(); Registered(adb); adb.SelectedSerial = "MY-USB"; adb.Ids["MY-USB"] = "MY-HARDWARE"; adb.NoteBoot("00000000-0000-0000-0000-000000000001");
    Assert(!adb.ConsumeReboot(), "first boot mistaken for reboot"); var before = adb.State.ToJsonString(); var commandsBefore=adb.Commands.Count; adb.NoteBoot("not a boot"); Assert(adb.State.ToJsonString() == before && adb.Commands.Count==commandsBefore, "invalid boot queried or persisted");
});
Test("Forced foreign phone cannot contaminate stored boot state", () =>
{
    var adb = New(); Registered(adb); var before = adb.State.ToJsonString(); adb.SelectedSerial = "FOREIGN"; adb.Ids["FOREIGN"] = "OTHER"; adb.NoteBoot("00000000-0000-0000-0000-000000000002"); Assert(adb.State.ToJsonString() == before, "foreign boot persisted");
});
Test("Real state writer preserves fields and fails closed on damaged state", () =>
{
    var folder = Path.Join(TestPaths.Root, "connection-tests", "state-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(folder);
    var path = Path.Join(folder, "wifi.json");
    try
    {
        File.WriteAllText(path, "{\"serialno\":\"MY-HARDWARE\",\"usb\":\"MY-USB\",\"custom\":\"preserved\"}");
        var adb = new FakeAdb(path) { PhysicalState = true, Devices = [Usb("MY-USB")] }; adb.Ids["MY-USB"] = "MY-HARDWARE";
        adb.ShellOutputs["ip -4 addr"] = "inet 192.0.2.67/24 scope global wlan0\n"; adb.ShellOutputs["cat /proc/net/tcp6 /proc/net/tcp"] = "0: 0000:9C40 0000:0000 0A 0 0 0 2000\n"; adb.PickDevice();
        var saved = JsonNode.Parse(File.ReadAllText(path))!; Assert(saved["custom"]?.ToString() == "preserved" && saved["address"]?.ToString() == "192.0.2.67:40000", "state merge damaged fields");
        File.WriteAllText(path, "{broken"); Denied(() => adb.PickDevice(), "unreadable"); Assert(File.ReadAllText(path) == "{broken", "corrupt state overwritten");
    }
    finally
    {
        foreach (var file in Directory.EnumerateFiles(folder)) File.Delete(file);
        Directory.Delete(folder);
    }
});

Test("Fresh status identity and boot are accepted locally without another transport query",()=>{var adb=New();Registered(adb);adb.SelectedSerial="MY-USB";adb.State["boot_id"]="00000000-0000-0000-0000-000000000001";adb.AcceptStatusObservation("MY-USB","MY-HARDWARE","00000000-0000-0000-0000-000000000002");Assert(adb.Commands.Count==0&&adb.Saves==1&&adb.State["custom"]?.ToString()=="preserved"&&adb.ConsumeReboot()&&!adb.ConsumeReboot(),"status proof queried transport or lost persistent reboot");});
Test("Fresh status rejects a foreign hardware identity without adopting or overwriting it",()=>{var adb=New();Registered(adb);adb.SelectedSerial="MY-USB";var before=adb.State.ToJsonString();Denied(()=>adb.AcceptStatusObservation("MY-USB","FOREIGN","00000000-0000-0000-0000-000000000002"),"identity changed");Assert(adb.SelectedSerial is null&&adb.State.ToJsonString()==before&&adb.Saves==0,"foreign status contaminated selection/state");});
Test("Late status of an old transport cannot invalidate a newly selected transport",()=>{var adb=New();Registered(adb);adb.SelectedSerial="NEW-USB";Denied(()=>adb.AcceptStatusObservation("OLD-USB","MY-HARDWARE","00000000-0000-0000-0000-000000000002"),"transport changed");Assert(adb.SelectedSerial=="NEW-USB"&&adb.Commands.Count==0&&adb.Saves==0,"old status invalidated fresh selection");});
Test("Explicit foreign status stays process local and malformed boot is rejected",()=>{var adb=New();Registered(adb);adb.Forced="FOREIGN";adb.SelectedSerial="FOREIGN";var before=adb.State.ToJsonString();adb.AcceptStatusObservation("FOREIGN","FOREIGN-HARDWARE","00000000-0000-0000-0000-000000000002");Assert(adb.State.ToJsonString()==before&&adb.Saves==0,"forced phone changed remembered boot");adb.Forced=null;adb.SelectedSerial="MY-USB";Denied(()=>adb.AcceptStatusObservation("MY-USB","MY-HARDWARE","invalid"),"boot identity");Assert(adb.State.ToJsonString()==before&&adb.Saves==0,"invalid boot persisted");});
Test("Warm operation verifies current hardware and boot in one fresh batch",()=>{var adb=New();Registered(adb);adb.SelectedSerial="MY-USB";adb.Ids["MY-USB"]="MY-HARDWARE";adb.State["boot_id"]="00000000-0000-0000-0000-000000000001";Assert(adb.EnsureDevice()&&adb.Commands.Count==1&&adb.Commands[0].Contains("@@NEXUS-DEVICE@@")&&adb.Saves==0,"operation repeated discovery or cached its proof");});
Test("Warm operation detects replacement hardware at the same transport",()=>{var adb=New();Registered(adb);adb.SelectedSerial="MY-USB";adb.Ids["MY-USB"]="FOREIGN";var before=adb.State.ToJsonString();Denied(()=>adb.EnsureDevice(),"identity changed");Assert(adb.SelectedSerial is null&&adb.State.ToJsonString()==before&&adb.Commands.Count==1,"foreign hardware used cached proof or became adopted");});
Test("Lost warm proof reconnects before the operation without replaying input",()=>{var adb=New();Registered(adb);adb.SelectedSerial="MY-USB";adb.ThrowProof=true;adb.Devices=[Usb("MY-USB")];adb.Ids["MY-USB"]="MY-HARDWARE";Assert(adb.EnsureDevice()&&adb.SelectedSerial=="MY-USB"&&adb.Commands.Contains("devices -l")&&!adb.Commands.Any(command=>command.Contains("input")),"lost proof did not rediscover safely");});
    }
}


sealed class FakeAdb(string? statePath = null) : AdbClient("never-run-real-adb.exe", statePath ?? Path.Join(TestPaths.Root, "connection-tests", "unused-fixture-state.json"))
{
    public List<DeviceInfo> Devices = [];
    public readonly Dictionary<string, string> Ids = [], ShellOutputs = [];
    public readonly List<string> Commands = [], Scans = [];
    public readonly List<string?> SelectionDuringHost = [];
    public readonly HashSet<string> Answers = [], ThrowConnect = [];
    public JsonObject State = new();
    public string? Forced;
    public string Mdns = "";
    public bool LocalSubnet = true, RestoreOffline, PhysicalState, ThrowProof;
    public double Now = 100;
    public int Saves;
    public IReadOnlyList<int> Ports = [];
    public List<string> Connects => Commands.Where(c => c.StartsWith("connect ", StringComparison.Ordinal)).Select(c => c[8..]).ToList();
    protected override string? ForcedDeviceSerial => Forced;
    protected override double ConnectionClock => Now;
    protected override void ConnectionWait(int milliseconds) => Now += milliseconds / 1000.0;
    protected override JsonObject ReadConnectionState() => PhysicalState ? base.ReadConnectionState() : State.DeepClone().AsObject();
    protected override void SaveConnectionState(JsonObject update)
    {
        if (PhysicalState) { base.SaveConnectionState(update); return; }
        foreach (var pair in update) State[pair.Key] = pair.Value?.DeepClone();
        Saves++;
    }
    protected override bool SameWifiSubnet(string host) => LocalSubnet;
    protected override IReadOnlyList<int> FindAdbPorts(string host) { Scans.Add(host); return Ports; }
    public override (string Stdout, string Stderr) RunAdb(string arguments, int timeoutMs = 15000) => throw new Exception("Connection discovery used device-targeted RunAdb");
    public override (string Stdout, string Stderr) RunHost(string arguments, int timeoutMs = 15000)
    {
        Commands.Add(arguments); SelectionDuringHost.Add(SelectedSerial);
        if (arguments == "devices -l") return ("List of devices attached\n" + string.Join('\n', Devices.Select(d => $"{d.Serial}\t{d.State} model:Fixture_Phone")) + "\n", "");
        if (arguments == "mdns services") return (Mdns, "");
        if (arguments == "reconnect offline") { if (RestoreOffline) {Devices = Devices.Select(d => d.State == "offline" ? d with { State = "device" } : d).ToList();} return ("reconnecting", ""); }
        if (arguments.StartsWith("connect ", StringComparison.Ordinal))
        {
            var address = arguments[8..]; if (ThrowConnect.Contains(address)) throw new TimeoutException("fixture stale port");
            if (Answers.Contains(address)) { if (Devices.All(d => d.Serial != address)) {Devices.Add(new(address, "device", "Fixture", false));} return ("connected to " + address, ""); }
            return ("failed to connect", "");
        }
        if (arguments.StartsWith("disconnect ", StringComparison.Ordinal)) { Devices.RemoveAll(d => d.Serial == arguments[11..]); return ("disconnected", ""); }
        if (arguments.StartsWith("-s ", StringComparison.Ordinal))
        {
            var parts = arguments.Split(' ', 4); var serial = parts[1]; var command = parts.Length == 4 ? parts[3] : "";
            if(command.Contains("@@NEXUS-DEVICE@@")){if(ThrowProof){ThrowProof=false;throw new TimeoutException("fixture selected transport lost");}return(Ids.GetValueOrDefault(serial,"")+"@@NEXUS-DEVICE@@"+ShellOutputs.GetValueOrDefault("cat /proc/sys/kernel/random/boot_id","00000000-0000-0000-0000-000000000001"),"");}return (command == "getprop ro.serialno" ? Ids.GetValueOrDefault(serial, "") : ShellOutputs.GetValueOrDefault(command, ""), "");
        }
        throw new Exception("Unexpected host command: " + arguments);
    }
}
