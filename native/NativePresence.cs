using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace NexusAndroid;

public sealed class PhoneLease:IDisposable
{
    private readonly FileStream file;
    public PhoneLease(string path)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);file=new FileStream(path,FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.ReadWrite);var until=DateTime.UtcNow.AddSeconds(30);
        while(true){NativeSafety.Cancellation.Value.ThrowIfCancellationRequested();try{file.Lock(0,1);break;}catch(IOException){if(DateTime.UtcNow>=until){file.Dispose();throw new IOException("Phone busy with another process; no action sent");}Thread.Sleep(80);}}
    }
    public void Dispose(){try{file.Unlock(0,1);}finally{file.Dispose();}}
}

public sealed partial class NativeEngine
{
    private DateTimeOffset lastInput=DateTimeOffset.MinValue;
    private readonly string phoneLockPath;
    private readonly string awakePath;
    private Timer? awakeTimer;
    private string presenceWarning="";
    private JsonObject ReadAwakeState(){try{return File.Exists(awakePath)?JsonNode.Parse(File.ReadAllText(awakePath))?.AsObject()??new():new();}catch(Exception e)when(e is IOException or System.Text.Json.JsonException){presenceWarning+=" Saved screen-timeout state unreadable; no unknown value restored.";return new();}}
    private void WriteAwakeState(JsonObject state){Directory.CreateDirectory(Path.GetDirectoryName(awakePath)!);var temporary=awakePath+"."+Guid.NewGuid().ToString("N")+".tmp";try{File.WriteAllText(temporary,state.ToJsonString());File.Move(temporary,awakePath,true);}finally{if(File.Exists(temporary))File.Delete(temporary);}}
    private string OperationHardwareIdentity()=>verifiedHardwareForOperation.Length>0?verifiedHardwareForOperation:Shell("getprop","ro.serialno").Trim();
    private bool AwakeMatches(JsonObject state)=>Str(state,"phone") is {Length:>0} phone?phone==OperationHardwareIdentity():Str(state,"serial")==adb.SelectedSerial;
    private static bool Awake(NativeScreen screen)=>Regex.IsMatch(screen.State,@"(?:mWakefulness=Awake|mInteractive=true|mScreenOn=true|mAwake=true)");
    private double? Idle()
    {
        var output=Shell("sh","-c","dumpsys power | grep lastUserActivityTime= || true");var match=Regex.Match(output,@"lastUserActivityTime=\d+ \((\d+) ms ago\)");return match.Success?double.Parse(match.Groups[1].Value,System.Globalization.CultureInfo.InvariantCulture)/1000:null;
    }
    private void WaitForOwner(NativeScreen screen)
    {
        if(!Awake(screen))return;var until=DateTime.UtcNow.AddSeconds(10);
        while(true)
        {
            var asked=DateTimeOffset.UtcNow;var idle=Idle();if(idle is null){presenceWarning="Phone does not report last touch; user-presence check unavailable on this device.";return;}
            var own=lastInput;var state=ReadAwakeState();if(AwakeMatches(state)&&state["last"]?.GetValue<double>() is double lastSeconds)own=DateTimeOffset.FromUnixTimeMilliseconds((long)(lastSeconds*1000))>own?DateTimeOffset.FromUnixTimeMilliseconds((long)(lastSeconds*1000)):own;
            if(idle>=3||asked.AddSeconds(-idle.Value)<=own.AddSeconds(1))return;
            if(DateTime.UtcNow>=until)throw new InvalidOperationException("The user is using the phone right now; no action sent");NativeSafety.StopGuard();Thread.Sleep(160);
        }
    }
    private NativeScreen LiftTrustedLock(NativeScreen screen)
    {
        if(!Locked(screen))return screen;
        var trusted=Regex.IsMatch(Shell("dumpsys","trust"),@"\(current\):[^\r\n]*\bdeviceLocked=0\b");if(!trusted)return screen;NativeSafety.StopGuard();Shell("input","keyevent","KEYCODE_WAKEUP");Shell("wm","dismiss-keyguard");lastInput=DateTimeOffset.UtcNow;
        var until=DateTime.UtcNow.AddSeconds(2);do{screen=Read();if(!Locked(screen)){KeepScreenAwake(screen);return screen;}Thread.Sleep(80);}while(DateTime.UtcNow<until);return screen;
    }
    private void KeepScreenAwake(NativeScreen screen)
    {
        if(Environment.GetEnvironmentVariable("NEXUS_ANDROID_KEEP_AWAKE")=="0"||Locked(screen))return;
        try
        {
            var currentText=Shell("settings","get","system","screen_off_timeout").Trim();if(!int.TryParse(currentText,out var current)){presenceWarning+=" Screen timeout unavailable; it was not changed.";return;}
            var state=ReadAwakeState();if(state["active"]?.GetValue<bool>()==true&&!AwakeMatches(state)){presenceWarning+=" Another phone owns the saved screen timeout; it was not replaced.";return;}var original=current is 300000 or 1800000?state["original"]?.GetValue<int>()??current:current;var now=DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()/1000.0;
            state["active"]=true;state["original"]=original;state["last"]=now;state["poked"]=now;state["pid"]=Environment.ProcessId;state["serial"]=adb.SelectedSerial;state["phone"]=OperationHardwareIdentity();WriteAwakeState(state);if(current!=300000)Shell("settings","put","system","screen_off_timeout","300000");
            awakeTimer??=new Timer(_=>RestoreAwake(),null,TimeSpan.FromSeconds(120),Timeout.InfiniteTimeSpan);awakeTimer.Change(TimeSpan.FromSeconds(120),Timeout.InfiniteTimeSpan);
        }
        catch(Exception e)when(e is IOException or InvalidOperationException){presenceWarning+=" Could not keep screen awake: "+NativeSafety.Mask(e.Message);}
    }
    private void RestoreAwake()
    {
        if(!Monitor.TryEnter(gate))return;
        try
        {
            using var lease=new PhoneLease(phoneLockPath);RestoreExpiredAwake();
        }
        catch(Exception){/* Timer callback boundary: never crash the host. Timer is read on the next call; transport failure must not fabricate a restored state. */}
        finally{Monitor.Exit(gate);}
    }
    // Caller holds both the in-process and shared phone leases. A restarted daemon
    // must complete an expired keep-awake period rather than leaving it indefinitely.
    private void RestoreExpiredAwake(bool forceOwn=false)
    {
        var state=ReadAwakeState();if(state["active"]?.GetValue<bool>()!=true)return;if(forceOwn&&state["pid"]?.GetValue<int>()!=Environment.ProcessId)return;var elapsed=DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()/1000.0-(state["last"]?.GetValue<double>()??0);if(elapsed<120&&!forceOwn){awakeTimer??=new Timer(_=>RestoreAwake(),null,Timeout.InfiniteTimeSpan,Timeout.InfiniteTimeSpan);awakeTimer.Change(TimeSpan.FromSeconds(120-elapsed),Timeout.InfiniteTimeSpan);return;}
        if(!AwakeMatches(state))return;var current=Shell("settings","get","system","screen_off_timeout").Trim();if(current is "300000" or "1800000"){Shell("settings","put","system","screen_off_timeout",state["original"]!.ToString());}state["active"]=false;WriteAwakeState(state);
    }
    private void ExtendAwake(NativeScreen screen)
    {
        if(Environment.GetEnvironmentVariable("NEXUS_ANDROID_KEEP_AWAKE")=="0"||!Awake(screen)||Locked(screen))return;var state=ReadAwakeState();if(state["active"]?.GetValue<bool>()!=true||!AwakeMatches(state))return;var now=DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()/1000.0;if(now-(state["poked"]?.GetValue<double>()??0)>60){Shell("input","keyevent","KEYCODE_WAKEUP");lastInput=DateTimeOffset.UtcNow;state["poked"]=now;}state["last"]=now;WriteAwakeState(state);awakeTimer??=new Timer(_=>RestoreAwake(),null,Timeout.InfiniteTimeSpan,Timeout.InfiniteTimeSpan);awakeTimer.Change(TimeSpan.FromSeconds(120),Timeout.InfiniteTimeSpan);
    }
}
