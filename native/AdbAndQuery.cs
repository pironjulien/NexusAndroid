using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace NexusAndroid;

public sealed record DeviceInfo(string Serial, string State, string Model, bool IsUsb);

public partial class AdbClient
{
    private static readonly Regex DeviceLineRegex = new(@"^([a-zA-Z0-9_\.\:\-]+)\s+(device|offline|unauthorized|no permissions)\b([^\r\n]*)", RegexOptions.Multiline | RegexOptions.Compiled);

    private string? adbPath;
    /// <summary>adb in use: the one given, NEXUS_ANDROID_ADB, else the first found (PATH, SDK, winget, the copy `setup` or
    /// the server downloaded). Looked up again while none is found: a copy downloaded after start is used at once.</summary>
    public string AdbPath => adbPath ?? (Environment.GetEnvironmentVariable("NEXUS_ANDROID_ADB") is { Length: > 0 } set ? adbPath = set
        : FindAdb() is { } found ? adbPath = found : "adb.exe");
    public string? SelectedSerial { get; set; }
    public readonly string ConnectionStatePath;

    public AdbClient(string? customAdbPath = null,string? connectionStatePath=null)
    {
        adbPath = customAdbPath;
        ConnectionStatePath=connectionStatePath??Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),".nexus","nexus-android","wifi.json");
    }

    public static string? FindAdb()
    {
        // 1. PATH
        string pathEnv = Environment.GetEnvironmentVariable("PATH") ?? "";
        foreach (var p in pathEnv.Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries))
        {
            try
            {
                string cand = Path.Combine(p.Trim(), OperatingSystem.IsWindows() ? "adb.exe" : "adb");
                if (File.Exists(cand)) return cand;
            }
            catch { }
        }

        // 2. Standard Android SDK locations
        string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string[] candidates =
        {
            Path.Combine(localAppData, "Android", "Sdk", "platform-tools", "adb.exe"),
            @"C:\Android\platform-tools\adb.exe",
            @"C:\platform-tools\adb.exe",
            @"D:\Android\Sdk\platform-tools\adb.exe",
        };

        foreach (var c in candidates)
        {
            if (File.Exists(c)) return c;
        }

        var winget = Path.Combine(localAppData, "Microsoft", "WinGet", "Packages");
        if (Directory.Exists(winget))
            foreach (var dir in Directory.EnumerateDirectories(winget, "Google.PlatformTools*"))
            {
                var cand = Path.Combine(dir, "platform-tools", "adb.exe");
                if (File.Exists(cand)) return cand;
            }
        // 3. Google's Platform Tools downloaded by `setup` (~\.nexus\nexus-android\tools\platform-tools-<version>)
        return Tools.Downloaded();
    }

    public virtual List<DeviceInfo> GetDevices()
    {
        var (stdout, _) = RunHost("devices -l", timeoutMs: 5000);
        var list = new List<DeviceInfo>();

        foreach (Match m in DeviceLineRegex.Matches(stdout))
        {
            string serial = m.Groups[1].Value;
            string state = m.Groups[2].Value;
            var modelMatch=Regex.Match(m.Groups[3].Value,@"(?:^|\s)model:(\S+)");string model=modelMatch.Success?modelMatch.Groups[1].Value.Replace('_',' '):"Android";
            bool isUsb = !IsWirelessSerial(serial);
            list.Add(new DeviceInfo(serial, state, model, isUsb));
        }

        return list;
    }

    public virtual DeviceInfo? PickDevice()=>ConnectRemembered();

    public virtual (string Stdout, string Stderr) RunAdb(string arguments, int timeoutMs = 15000)
    {
        string fullArgs = arguments;
        if (!string.IsNullOrEmpty(SelectedSerial) && !arguments.StartsWith("devices") && !arguments.StartsWith("-s"))
        {
            fullArgs = $"-s {SelectedSerial} {arguments}";
        }

        var selected=SelectedSerial;try{return RunProcess(fullArgs,timeoutMs);}catch(TimeoutException){ConnectionLost(selected);throw;}catch(InvalidOperationException e)when(Regex.IsMatch(e.Message,@"(?im)(?:device .*not found|device offline|no devices|protocol fault|connection reset|transport.*closed)")){ConnectionLost(selected);throw;}
    }
    public virtual (string Stdout,string Stderr) RunHost(string arguments,int timeoutMs=15000)=>RunProcess(arguments,timeoutMs);
    public virtual (string Stdout,string Stderr) RunAdbArgs(IReadOnlyList<string> arguments,int timeoutMs=15000)
    {
        var psi=new ProcessStartInfo(AdbPath);if(SelectedSerial is {Length:>0} serial){psi.ArgumentList.Add("-s");psi.ArgumentList.Add(serial);}foreach(var argument in arguments)psi.ArgumentList.Add(argument);
        var selected=SelectedSerial;try{return RunProcess(psi,timeoutMs);}catch(TimeoutException){ConnectionLost(selected);throw;}catch(InvalidOperationException e)when(Regex.IsMatch(e.Message,@"(?im)(?:device .*not found|device offline|no devices|protocol fault|connection reset|transport.*closed)")){ConnectionLost(selected);throw;}
    }
    private (string Stdout,string Stderr) RunProcess(string fullArgs,int timeoutMs)
        =>RunProcess(new ProcessStartInfo(AdbPath,fullArgs),timeoutMs);
    private (string Stdout,string Stderr) RunProcess(ProcessStartInfo psi,int timeoutMs)
    {
        psi.RedirectStandardOutput=true;psi.RedirectStandardError=true;psi.UseShellExecute=false;psi.CreateNoWindow=true;psi.StandardOutputEncoding=Encoding.UTF8;psi.StandardErrorEncoding=Encoding.UTF8;

        using var proc = Process.Start(psi) ?? throw new InvalidOperationException("Failed to start ADB process");
        var stdoutTask = proc.StandardOutput.ReadToEndAsync();
        var stderrTask = proc.StandardError.ReadToEndAsync();

        using var timeout=new CancellationTokenSource(timeoutMs);using var cancellation=CancellationTokenSource.CreateLinkedTokenSource(timeout.Token,NativeSafety.Cancellation.Value);
        try { proc.WaitForExitAsync(cancellation.Token).GetAwaiter().GetResult(); }
        catch(OperationCanceledException)
        {
            try { proc.Kill(); } catch { }
            if(NativeSafety.Cancellation.Value.IsCancellationRequested)throw new IOException("ADB call cancelled during transport; input outcome may be unknown, observe before any new decision");
            throw new TimeoutException($"ADB command timed out after {timeoutMs}ms; input outcome may be unknown");
        }

        var output = stdoutTask.GetAwaiter().GetResult();
        var error = stderrTask.GetAwaiter().GetResult();
        if (proc.ExitCode != 0)
            throw new InvalidOperationException($"ADB failed (exit {proc.ExitCode}): {NativeSafety.Mask(error.Length > 0 ? error : output)}");
        return (output, error);
    }

    public byte[] CaptureScreenPng(int timeoutMs = 10000)
    {
        var physical=CaptureDisplayId();return ExecOut("screencap -p"+(physical is not null?" -d "+physical:""),timeoutMs);
    }
    public string? CaptureDisplayId(){var displays=Shell("dumpsys SurfaceFlinger --display-id");if(System.Text.RegularExpressions.Regex.Matches(displays,@"(?m)^Display \d+").Count<=1)return null;return ParseLogicalDisplay(Shell("dumpsys display"))??throw new IOException("Multiple displays exist but the active logical display cannot be identified; no arbitrary display captured");}
    public static string? ParseLogicalDisplay(string dump)
    {
        var match=System.Text.RegularExpressions.Regex.Match(dump,@"mDisplayId=0\s*\n\s*mPrimaryDisplayDevice=[^\n]*\(local:(\d+)\)");if(!match.Success)match=System.Text.RegularExpressions.Regex.Match(dump,"mDisplayId=0\\s*\\n\\s*mBaseDisplayInfo=DisplayInfo\\{[^\\r\\n]*?uniqueId \\\"local:(\\d+)\\\"");return match.Success?match.Groups[1].Value:null;
    }

    public virtual byte[] ExecOut(string command,int timeoutMs=15000)
    {
        string fullArgs = "exec-out " + command;
        if (!string.IsNullOrEmpty(SelectedSerial))
        {
            fullArgs = $"-s {SelectedSerial} {fullArgs}";
        }

        var psi = new ProcessStartInfo(AdbPath, fullArgs)
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };

        using var proc = Process.Start(psi) ?? throw new InvalidOperationException("Failed to start ADB screencap");
        using var ms = new MemoryStream();
        var output=proc.StandardOutput.BaseStream.CopyToAsync(ms);var errors=proc.StandardError.ReadToEndAsync();

        if (!proc.WaitForExit(timeoutMs))
        {
            try { proc.Kill(); } catch { }
            ConnectionLost(SelectedSerial);
            throw new TimeoutException($"ADB screencap timed out after {timeoutMs}ms");
        }

        output.GetAwaiter().GetResult();var error=errors.GetAwaiter().GetResult();if(proc.ExitCode!=0)throw new IOException("ADB binary read failed: "+NativeSafety.Mask(error));
        return ms.ToArray();
    }

    public virtual string Shell(string command, int timeoutMs = 15000)
    {
        var (stdout, _) = RunAdb($"shell {command}", timeoutMs);
        return stdout;
    }

    public string DumpUiHierarchy(int timeoutMs = 10000)
    {
        // Try dump to /sdcard/window_dump.xml
        Shell("uiautomator dump /sdcard/window_dump.xml", timeoutMs);
        string dump = Shell("cat /sdcard/window_dump.xml", timeoutMs);
        return dump;
    }
}
