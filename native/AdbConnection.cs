using System.Buffers.Binary;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Globalization;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace NexusAndroid;

public partial class AdbClient
{
    private readonly object connectionGate = new();
    private double absentUntil;
    private const double AbsentSeconds = 120;
    private const int MaxWifiAttempts = 4;
    private const uint Cnxn = 0x4E584E43, Auth = 0x48545541, Stls = 0x534C5453;
    public bool Rebooted { get; private set; }
    private string proofSerial="",proofHardware="";
    public string? VerifiedSelectedHardwareIdentity{get{lock(connectionGate)return SelectedSerial==proofSerial&&proofHardware.Length>0?proofHardware:null;}}

    protected virtual string? ForcedDeviceSerial => Environment.GetEnvironmentVariable("NEXUS_ANDROID_SERIAL") is { Length: > 0 } value ? value : null;
    protected virtual double ConnectionClock => Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency;
    protected virtual void ConnectionWait(int milliseconds) => Task.Delay(milliseconds, NativeSafety.Cancellation.Value).GetAwaiter().GetResult();

    public static bool IsWirelessSerial(string serial) => serial.Contains(':') || serial.Contains("._adb-tls-connect.", StringComparison.OrdinalIgnoreCase);

    private static string SafeSerial(string serial)
    {
        if (serial.Length is < 1 or > 256 || !Regex.IsMatch(serial, @"\A[A-Za-z0-9._:%\[\]-]+\z"))
            throw new IOException("Invalid ADB device address; no connection attempted");
        return serial;
    }

    private static string StateText(JsonObject state, string key) => state[key] is JsonValue value && value.TryGetValue<string>(out var text) ? text.Trim() : "";
    private static string KnownIdentity(JsonObject state) => StateText(state, "serialno") is { Length: > 0 } identity ? identity : StateText(state, "usb");

    protected virtual JsonObject ReadConnectionState()
    {
        try
        {
            var state = JsonNode.Parse(File.ReadAllText(ConnectionStatePath)) as JsonObject;
            if (state is null || state.Count > 0 && KnownIdentity(state).Length == 0)
                throw new JsonException("Saved identity missing");
            return state;
        }
        catch (Exception error) when (error is FileNotFoundException or DirectoryNotFoundException) { return new JsonObject(); }
        catch (Exception error) when (error is IOException or JsonException or UnauthorizedAccessException)
        {
            throw new IOException("Saved Android identity is unreadable; no unknown device will be adopted", error);
        }
    }

    protected virtual void SaveConnectionState(JsonObject state)
    {
        var parent = Path.GetDirectoryName(ConnectionStatePath)!;
        Directory.CreateDirectory(parent);
        using var lease = new FileStream(ConnectionStatePath + ".lock", FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.ReadWrite);
        var until = ConnectionClock + 1.618;
        while (true)
        {
            NativeSafety.Cancellation.Value.ThrowIfCancellationRequested();
            try { lease.Lock(0, 1); break; }
            catch (IOException) { if (ConnectionClock >= until) throw new IOException("Android identity file busy"); ConnectionWait(50); }
        }
        string? temporary = null;
        try
        {
            var current = ReadConnectionState();
            var known = KnownIdentity(current);
            if (known.Length > 0 && known != KnownIdentity(state))
                throw new InvalidOperationException("Registered Android identity changed concurrently; no device selected");
            foreach (var item in state) current[item.Key] = item.Value?.DeepClone();
            temporary = ConnectionStatePath + "." + Guid.NewGuid().ToString("N") + ".tmp";
            File.WriteAllText(temporary, current.ToJsonString(), new UTF8Encoding(false));
            File.Move(temporary, ConnectionStatePath, true);
        }
        finally
        {
            if (temporary is not null && File.Exists(temporary)) File.Delete(temporary);
            lease.Unlock(0, 1);
        }
    }

    private string TargetedRead(string serial, string command, int timeout = 8000) => RunHost("-s " + SafeSerial(serial) + " shell " + command, timeout).Stdout;

    private string DeviceIdentity(string serial)
    {
        NativeSafety.Cancellation.Value.ThrowIfCancellationRequested();
        try { return TargetedRead(serial, "getprop ro.serialno").Trim(); }
        catch (Exception error) when (error is IOException or InvalidOperationException or TimeoutException)
        {
            NativeSafety.Cancellation.Value.ThrowIfCancellationRequested();
            return "";
        }
    }

    public static string? WifiAddress(string output)
    {
        var matches = Regex.Matches(output, @"inet (\d+\.\d+\.\d+\.\d+)/\d+[^\r\n]*?\b(wlan0|ap_br_\w+|swlan\d+)\s*$", RegexOptions.Multiline);
        var found = matches.Cast<Match>().Where(m => ValidIpv4(m.Groups[1].Value)).ToList();
        return found.LastOrDefault(m => m.Groups[2].Value == "wlan0")?.Groups[1].Value ?? found.FirstOrDefault()?.Groups[1].Value;
    }

    public static int? AdbdPort(string output)
    {
        foreach (var line in output.Split('\n'))
        {
            var fields = line.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
            if (fields.Length <= 7 || fields[3] != "0A" || fields[7] != "2000") continue;
            var value = fields[1].Split(':').Last();
            if (int.TryParse(value, NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var port) && port is > 0 and <= 65535 && port != 5037) return port;
        }
        return null;
    }

    private void RememberUsb(DeviceInfo device, string identity, JsonObject before)
    {
        if (identity.Length == 0) throw new IOException("USB device did not report its hardware identity");
        var state = new JsonObject { ["serialno"] = identity, ["usb"] = device.Serial };
        try
        {
            var ip = WifiAddress(TargetedRead(device.Serial, "ip -4 addr"));
            var port = AdbdPort(TargetedRead(device.Serial, "cat /proc/net/tcp6 /proc/net/tcp"));
            if (ip is not null && port is not null) state["address"] = $"{ip}:{port}";
        }
        catch (Exception error) when (error is IOException or InvalidOperationException or TimeoutException)
        {
            NativeSafety.Cancellation.Value.ThrowIfCancellationRequested();
            // A missing Wi-Fi address never prevents recording the verified USB hardware identity.
        }
        if (state.Any(pair => !JsonNode.DeepEquals(pair.Value, before[pair.Key]))) SaveConnectionState(state);
    }

    private List<(DeviceInfo Device, string Identity)> Mine(IEnumerable<DeviceInfo> devices, string known)
    {
        var accepted = new List<(DeviceInfo, string)>();
        foreach (var device in devices.Where(d => d.State == "device" && !d.Serial.StartsWith("emulator-", StringComparison.Ordinal) && !(known.Length == 0 && IsWirelessSerial(d.Serial))))
        {
            var identity = DeviceIdentity(device.Serial);
            if (identity.Length > 0 && (known.Length == 0 || identity == known)) accepted.Add((device, identity));
        }
        if (known.Length == 0 && accepted.Count > 1)
            throw new IOException("Several USB devices and no phone registered yet; no arbitrary device selected");
        return accepted;
    }

    private bool TryWifi(string address, string known)
    {
        SafeSerial(address);
        try
        {
            var result = RunHost("connect " + address, 8000).Stdout;
            if (!result.Contains("connected to", StringComparison.OrdinalIgnoreCase)) return false;
            if (DeviceIdentity(address) == known) return true;
        }
        catch (Exception error) when (error is IOException or InvalidOperationException or TimeoutException)
        {
            NativeSafety.Cancellation.Value.ThrowIfCancellationRequested();
        }
        DisconnectQuietly(address);
        return false;
    }

    private void DisconnectQuietly(string serial)
    {
        try { RunHost("disconnect " + SafeSerial(serial), 5000); }
        catch (Exception error) when (error is IOException or InvalidOperationException or TimeoutException) { /* best-effort: the device may already be disconnected */ }
    }

    public void ConnectionLost(string? serial = null)
    {
        lock (connectionGate)
        {
            var stale = serial ?? SelectedSerial;
            if (serial is not null && SelectedSerial != serial) return;
            proofSerial="";proofHardware="";
            SelectedSerial = null;
            absentUntil = 0;
            if (stale is not null && IsWirelessSerial(stale)) DisconnectQuietly(stale);
        }
    }

    public void NoteBoot(string? boot = null)
    {
        lock (connectionGate)
        {
            if (SelectedSerial is null) return;
            // An absent/malformed caller observation is not a boot identity and
            // must not trigger another device query or touch saved user state.
            if (boot is not null && !Guid.TryParseExact(boot.Trim(), "D", out _)) return;
            try
            {
                var state = ReadConnectionState();
                var known = KnownIdentity(state);
                if (known.Length == 0 || DeviceIdentity(SelectedSerial) != known) return;
                boot = (boot ?? TargetedRead(SelectedSerial, "cat /proc/sys/kernel/random/boot_id", 5000)).Trim();
                if (!Guid.TryParseExact(boot, "D", out _) || boot == StateText(state, "boot_id")) return;
                var rebooted = StateText(state, "boot_id").Length > 0;
                SaveConnectionState(new JsonObject { ["serialno"] = known, ["boot_id"] = boot });
                Rebooted = rebooted;
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or TimeoutException)
            {
                NativeSafety.Cancellation.Value.ThrowIfCancellationRequested();
            }
        }
    }

    public bool ConsumeReboot()
    {
        lock (connectionGate) { var rebooted = Rebooted; Rebooted = false; return rebooted; }
    }

    private const string DeviceProofMark="@@NEXUS-DEVICE@@";
    public virtual bool EnsureDevice()
    {
        lock(connectionGate)
        {
            if(SelectedSerial is null||ForcedDeviceSerial is {Length:>0} forced&&forced!=SelectedSerial)return PickDevice()?.State=="device";
            var serial=SelectedSerial;string proof;
            try{proof=TargetedRead(serial,"sh -c "+NativeSafety.Quote("getprop ro.serialno; echo "+DeviceProofMark+"; cat /proc/sys/kernel/random/boot_id"));}
            catch(Exception error)when(error is IOException or InvalidOperationException or TimeoutException)
            {
                NativeSafety.Cancellation.Value.ThrowIfCancellationRequested();ConnectionLost(serial);return PickDevice()?.State=="device";
            }
            var parts=proof.Split(DeviceProofMark,StringSplitOptions.None).Select(value=>value.Trim()).ToArray();
            if(parts.Length!=2){ConnectionLost(serial);throw new IOException("Incomplete fresh Android identity proof; no operation started");}
            AcceptStatusObservation(serial,parts[0],parts[1]);return true;
        }
    }

    public void AcceptStatusObservation(string serial,string hardwareIdentity,string boot)
    {
        lock(connectionGate)
        {
            if(SelectedSerial!=serial)throw new IOException("Selected Android transport changed during the status read; no stale status returned");
            var state=ReadConnectionState();var known=KnownIdentity(state);var forced=ForcedDeviceSerial==serial;
            if(hardwareIdentity.Length==0||!forced&&(known.Length==0||hardwareIdentity!=known))
            {
                ConnectionLost(serial);throw new IOException("Android hardware identity changed or became unreadable; no foreign status returned and no device adopted");
            }
            // A forced foreign device is explicit for this process only. It must
            // never change the remembered phone or its persistent boot identity.
            if(!Guid.TryParseExact(boot,"D",out _))throw new IOException("Android boot identity unreadable; status sample rejected");
            proofSerial=serial;proofHardware=hardwareIdentity;
            if(known.Length==0||hardwareIdentity!=known)return;
            var before=StateText(state,"boot_id");if(before==boot)return;
            SaveConnectionState(new JsonObject{["serialno"]=known,["boot_id"]=boot});Rebooted|=before.Length>0;
        }
    }

    private static bool ValidIpv4(string host) => Regex.IsMatch(host, @"\A\d{1,3}(?:\.\d{1,3}){3}\z") && IPAddress.TryParse(host, out var ip) && ip.AddressFamily == AddressFamily.InterNetwork;

    private static bool TryAddress(string value, out string host)
    {
        host = "";
        var parts = value.Split(':');
        if (parts.Length != 2 || !ValidIpv4(parts[0]) || !int.TryParse(parts[1], out var port) || port is < 1 or > 65535) return false;
        host = parts[0];
        return true;
    }

    public static IReadOnlyList<string> MdnsAddresses(string output) => Regex.Matches(output, @"_adb-tls-connect\._tcp\.?\s+(\d+\.\d+\.\d+\.\d+:\d+)").Select(m => m.Groups[1].Value).Where(a => TryAddress(a, out _)).Distinct(StringComparer.Ordinal).ToList();

    public static byte[] AdbHello()
    {
        var payload = "host::\0"u8.ToArray();
        var message = new byte[24 + payload.Length];
        uint[] fields = [Cnxn, 0x01000001, 256 * 1024, (uint)payload.Length, (uint)payload.Sum(b => b), Cnxn ^ uint.MaxValue];
        for (var i = 0; i < fields.Length; i++) BinaryPrimitives.WriteUInt32LittleEndian(message.AsSpan(i * 4, 4), fields[i]);
        payload.CopyTo(message, 24);
        return message;
    }

    public static bool SpeaksAdb(ReadOnlySpan<byte> response)
    {
        if (response.Length < 24) return false;
        var command = BinaryPrimitives.ReadUInt32LittleEndian(response[..4]);
        return command is Cnxn or Auth or Stls && BinaryPrimitives.ReadUInt32LittleEndian(response.Slice(20, 4)) == (command ^ uint.MaxValue);
    }

    public static bool SameSubnet(IPAddress local, IPAddress remote, int prefix = 24)
    {
        if (local.AddressFamily != AddressFamily.InterNetwork || remote.AddressFamily != AddressFamily.InterNetwork || prefix is < 0 or > 32) return false;
        var mask = prefix == 0 ? 0u : uint.MaxValue << (32 - prefix);
        return (BinaryPrimitives.ReadUInt32BigEndian(local.GetAddressBytes()) & mask) == (BinaryPrimitives.ReadUInt32BigEndian(remote.GetAddressBytes()) & mask);
    }

    protected virtual bool SameWifiSubnet(string host)
    {
        if (!ValidIpv4(host)) return false;
        try
        {
            using var socket = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
            socket.Connect(new IPEndPoint(IPAddress.Parse(host), 9)); // Route selection only; no packet is sent.
            return socket.LocalEndPoint is IPEndPoint local && SameSubnet(local.Address, IPAddress.Parse(host));
        }
        catch (SocketException) { return false; }
    }

    protected virtual IReadOnlyList<int> FindAdbPorts(string host)
    {
        if (!ValidIpv4(host)) return [];
        var found = new ConcurrentBag<int>();
        var cancellation = NativeSafety.Cancellation.Value;
        Parallel.ForEachAsync(Enumerable.Range(30000, 20000), new ParallelOptions { MaxDegreeOfParallelism = 800, CancellationToken = cancellation }, async (port, token) =>
        {
            using var socket = new TcpClient(AddressFamily.InterNetwork);
            try
            {
                using (var connect = CancellationTokenSource.CreateLinkedTokenSource(token))
                {
                    connect.CancelAfter(400);
                    await socket.ConnectAsync(IPAddress.Parse(host), port, connect.Token);
                }
                using var handshake = CancellationTokenSource.CreateLinkedTokenSource(token);
                handshake.CancelAfter(1000);
                using var stream = socket.GetStream();
                await stream.WriteAsync(AdbHello(), handshake.Token);
                var header = new byte[24];
                await stream.ReadExactlyAsync(header, handshake.Token);
                if (SpeaksAdb(header)) found.Add(port);
            }
            catch (Exception error) when (error is IOException or SocketException or OperationCanceledException)
            {
                token.ThrowIfCancellationRequested();
            }
        }).GetAwaiter().GetResult();
        return found.Order().ToArray();
    }

    private List<(DeviceInfo Device, string Identity)> SearchWifi(JsonObject remembered, string known, bool scan)
    {
        if (ConnectionClock < absentUntil) return [];
        var address = StateText(remembered, "address");
        if (address.Length > 0 && !TryAddress(address, out _)) throw new IOException("Saved Android Wi-Fi address is invalid; no network scan attempted");
        if (address.Length > 0 && TryWifi(address, known)) return Mine(GetDevices(), known);
        string mdns;
        try { mdns = RunHost("mdns services", 10000).Stdout; }
        catch (Exception error) when (error is IOException or InvalidOperationException or TimeoutException) { NativeSafety.Cancellation.Value.ThrowIfCancellationRequested(); mdns = ""; }
        foreach (var candidate in MdnsAddresses(mdns).Where(a => a != address))
            if (TryWifi(candidate, known)) return Mine(GetDevices(), known);
        if (!scan) return [];
        if (TryAddress(address, out var host) && SameWifiSubnet(host))
            foreach (var port in FindAdbPorts(host).Where(p => p is >= 30000 and < 50000 && $"{host}:{p}" != address).Distinct().Order().Take(MaxWifiAttempts))
                if (TryWifi($"{host}:{port}", known)) return Mine(GetDevices(), known);
        absentUntil = ConnectionClock + AbsentSeconds;
        return [];
    }

    public DeviceInfo? ConnectRemembered(bool scan = true)
    {
        lock (connectionGate)
        {
            NativeSafety.Cancellation.Value.ThrowIfCancellationRequested();
            if (ForcedDeviceSerial is { Length: > 0 } forced)
            {
                SafeSerial(forced);
                var devices = GetDevices();
                if (IsWirelessSerial(forced) && devices.All(d => d.State != "device" || d.Serial != forced))
                {
                    RunHost("connect " + forced, 8000);
                    devices = GetDevices();
                }
                var explicitDevice = devices.SingleOrDefault(d => d.Serial == forced && d.State == "device");
                if (explicitDevice is null) { SelectedSerial = null; throw new IOException("Device set by NEXUS_ANDROID_SERIAL is unreachable"); }
                SelectedSerial = explicitDevice.Serial;
                return explicitDevice;
            }
            var remembered = ReadConnectionState();
            var known = KnownIdentity(remembered);
            var devicesNow = GetDevices();
            var mine = Mine(devicesNow, known);
            if (mine.Count == 0 && devicesNow.Any(d => d.State == "offline"))
            {
                RunHost("reconnect offline", 10000);
                for (var attempt = 0; attempt < 10 && mine.Count == 0; attempt++) { ConnectionWait(500); mine = Mine(GetDevices(), known); }
            }
            if (mine.Count == 0 && known.Length > 0) mine = SearchWifi(remembered, known, scan);
            if (mine.Count == 0)
            {
                SelectedSerial = null;
                if (devicesNow.Any(d => d.State == "unauthorized")) throw new IOException("Phone not authorized: accept Allow USB debugging on its screen");
                if (devicesNow.Any(d => d.State == "no permissions")) throw new IOException("USB permissions block ADB; no phone selected");
                throw new IOException(known.Length == 0 ? "No phone registered yet: one authorized USB phone is required; unknown Wi-Fi devices and emulators are ignored" : "Registered phone not found; unrelated devices ignored. Connect its USB cable or enable its paired wireless debugging" + (ConnectionClock < absentUntil ? $". Next Wi-Fi search in {Math.Ceiling(absentUntil - ConnectionClock)} s; USB is checked immediately" : ""));
            }
            var chosen = mine.OrderBy(m => IsWirelessSerial(m.Device.Serial)).ThenBy(m => m.Device.Serial, StringComparer.Ordinal).First();
            if (!IsWirelessSerial(chosen.Device.Serial))
            {
                try { RememberUsb(chosen.Device, chosen.Identity, remembered); }
                catch (Exception error) when (error is IOException or UnauthorizedAccessException) { NativeSafety.Cancellation.Value.ThrowIfCancellationRequested(); }
            }
            else if (TryAddress(chosen.Device.Serial, out _) && chosen.Device.Serial != StateText(remembered, "address"))
            {
                SaveConnectionState(new JsonObject { ["serialno"] = chosen.Identity, ["address"] = chosen.Device.Serial });
            }
            SelectedSerial = chosen.Device.Serial;
            absentUntil = 0;
            NoteBoot();
            proofSerial=chosen.Device.Serial;proofHardware=chosen.Identity;
            return chosen.Device;
        }
    }
}
