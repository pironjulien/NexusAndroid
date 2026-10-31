using System.Diagnostics;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace NexusAndroid;

public interface IVideoRecorder:IDisposable
{
    bool HasExited{get;}int? ExitCode=>null;string Error=>"";bool WaitForExit(int milliseconds);Task WaitForExitAsync(CancellationToken cancellation);void Kill(bool children);
}
public sealed class AdbVideoRecorder:IVideoRecorder
{
    private readonly Process process;
    private readonly Task<string> error;
    private readonly Task<string> output;
    public AdbVideoRecorder(AdbClient adb,string remote,int seconds,int width,int height)
    {
        var shortSide=Math.Min(720,Math.Min(width,height));if(shortSide<=0)throw new IOException("Unknown screen geometry; no video started");var longSide=(int)Math.Round((double)Math.Max(width,height)*shortSide/Math.Min(width,height)/16)*16;shortSide=shortSide/16*16;var size=width<=height?$"{shortSide}x{longSide}":$"{longSide}x{shortSide}";var display=adb.CaptureDisplayId();
        var psi=new ProcessStartInfo(adb.AdbPath){UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true};foreach(var arg in new[]{"-s",adb.SelectedSerial!,"shell","screenrecord --size "+size+" --bit-rate 4000000 --time-limit "+seconds+(display is null?"":" --display-id "+display)+" "+remote}){psi.ArgumentList.Add(arg);}process=Process.Start(psi)??throw new IOException("Could not start screenrecord");output=process.StandardOutput.ReadToEndAsync();error=process.StandardError.ReadToEndAsync();
    }
    public bool HasExited=>process.HasExited;public int? ExitCode=>process.HasExited?process.ExitCode:null;public string Error=>string.Join('\n',new[]{error.IsCompletedSuccessfully?error.Result:"",output.IsCompletedSuccessfully?output.Result:""}.Where(value=>value.Length>0));public bool WaitForExit(int milliseconds)=>process.WaitForExit(milliseconds);public Task WaitForExitAsync(CancellationToken cancellation)=>process.WaitForExitAsync(cancellation);public void Kill(bool children)=>process.Kill(children);public void Dispose()=>process.Dispose();
}
public sealed partial class NativeEngine
{
    private IVideoRecorder? video;
    private string videoRemote="",videoTarget="";
    private volatile bool videoRefused;
    private CancellationTokenSource? videoMonitor;
    private Task? videoMonitoring;
    private string videoOwner="";
    private static HashSet<string> ShownMediaPackages(NativeScreen screen)
    {
        var elements=ScreenParser.ParseXml(screen.Xml);var width=Math.Max(1,elements.Select(e=>e.Right).DefaultIfEmpty().Max());var height=Math.Max(1,elements.Select(e=>e.Bottom).DefaultIfEmpty().Max());
        // Same visible-area rule as the reference: the small Wallet handle does
        // not count as an open payment app; a half-screen bank does.
        return elements.GroupBy(e=>e.PackageName).Where(group=>(double)(group.Max(e=>e.Right)-group.Min(e=>e.Left))*(group.Max(e=>e.Bottom)-group.Min(e=>e.Top))>=0.25*width*height).Select(group=>group.Key).Append(Foreground(screen)).Where(package=>package.Length>0).ToHashSet();
    }
    private void ObserveRecording(NativeScreen screen)
    {
        if(video is null||video.HasExited||videoRefused||!ShownMediaPackages(screen).Any(AndroidPolicy.IsProtectedPackage))return;
        videoRefused=true;
        try{StopRemoteVideo(videoRemote);adb.Shell("rm -f "+NativeSafety.Quote(videoRemote),3000);}
        catch(Exception e)when(e is IOException or InvalidOperationException or TimeoutException){presenceWarning+=" Protected recording was discarded; producer cleanup is unconfirmed: "+NativeSafety.Mask(e.Message);}
    }
    private JsonObject Capture(JsonObject args)
    {
        var action=Str(args,"action","screenshot");
        if(action=="stop")return StopVideo();
        NativeSafety.StopGuard();var screen=Read();WaitForOwner(screen);if(!Awake(screen)){Shell("input","keyevent","KEYCODE_WAKEUP");lastInput=DateTimeOffset.UtcNow;screen=Read();}screen=LiftTrustedLock(screen);if(Locked(screen)||!LockKnown(screen))throw new InvalidOperationException("Phone locked with a code or lock state unreadable; no capture saved. The agent never enters a code.");if(action=="screenshot")screen=Read(true);
        if(action=="screenshot")
        {
            var protectedShown=ShownMediaPackages(screen).Where(AndroidPolicy.IsProtectedPackage).Order(StringComparer.Ordinal).ToList();if(protectedShown.Count>0)NativeSafety.Require(args,"screenshot showing "+string.Join(", ",protectedShown));
            var elements=ScreenParser.ParseXml(screen.Xml);if(elements.Count==0&&!Bool(args,"unmasked"))throw new InvalidOperationException("UI unreadable: code masking unavailable; unmasked=true only on user's explicit agreement");
            if(screen.Image is null)throw new IOException("Screenshot unavailable: "+screen.Error);var folder=NativeSafety.PcPath(Str(args,"dest",Path.Join(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),"Downloads","Telephone")),Bool(args,"confirm"));Directory.CreateDirectory(folder);var name=CaptureName(Str(args,"name"),false);
            var boxes=elements.Where(Sensitive).Select(e=>(e.Left,e.Top,e.Right,e.Bottom)).ToList();try{var ocr=NativeOcr.Read(screen.Image);var context=NativeSafety.HasCodeKeyword(string.Join(' ',ocr.Select(l=>l.Text)));foreach(var line in ocr.Where(l=>NativeSafety.MaskCodes(NativeSafety.Mask(l.Text),context)!=l.Text))boxes.Add((line.Left,line.Top,line.Right,line.Bottom));}catch(Exception e)when(e is IOException or System.Runtime.InteropServices.COMException){if(!Bool(args,"unmasked"))throw new IOException("OCR unavailable: unmasked=true only on explicit agreement to save a capture without drawn-code masking",e);}
            var image=ImageChain.ProcessEncodedImage(screen.Image,maxEdge:16384,maxBytes:64*1024*1024,preferredLosslessFormat:"png",maxRawPixels:64*1024*1024,blackouts:boxes);var bytes=Path.GetExtension(name).ToLowerInvariant() is ".jpg" or ".jpeg"?ImageChain.EncodeLossyJpeg(ImageChain.Decode(image.Bytes),90):image.Bytes;var target=FreePath(NativeSafety.PcPath(Path.Join(folder,name),Bool(args,"confirm")));File.WriteAllBytes(target,bytes);return Text("Screenshot saved (codes masked): "+target,new JsonObject{["path"]=target});
        }
        if(action is not ("start" or "record"))throw new ArgumentException("Unknown capture action");if(video is not null)throw new InvalidOperationException("A video is already recording; stop it first");if(ShownMediaPackages(screen).Any(AndroidPolicy.IsProtectedPackage))throw new InvalidOperationException("Video of protected apps refused");
        var seconds=Math.Clamp(Num(args,"seconds",action=="record"?30:180),1,180);var dest=NativeSafety.PcPath(Str(args,"dest",Path.Join(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),"Downloads","Telephone")),Bool(args,"confirm"));Directory.CreateDirectory(dest);var filename=CaptureName(Str(args,"name"),true);videoTarget=FreePath(NativeSafety.PcPath(Path.Join(dest,filename),Bool(args,"confirm")));videoRemote="/data/local/tmp/nexus-record-"+Guid.NewGuid().ToString("N")+".mp4";videoOwner=NativeSafety.Session.Value??"stdio";
        if(adb.Shell("pidof screenrecord || true").Trim().Length>0)throw new InvalidOperationException("Another screenrecord is active; nothing started");screen=Read();if(Locked(screen)||!LockKnown(screen)||ShownMediaPackages(screen).Any(AndroidPolicy.IsProtectedPackage))throw new InvalidOperationException("Foreground changed to a locked or protected screen; no video started");
        video=recorder(videoRemote,seconds,screen);videoRefused=false;videoMonitor=new CancellationTokenSource();var cancellation=videoMonitor.Token;
        var running=video;var recordingRemote=videoRemote;videoMonitoring=Task.Run(async()=>{NativeSafety.Cancellation.Value=default;while(!cancellation.IsCancellationRequested&&!running.HasExited){try{var state=adb.Shell("dumpsys window",3000);if(running.HasExited)break;var foreground=Regex.Match(state,@"mCurrentFocus=[^\r\n]*?\s([\w.]+)/").Groups[1].Value;if(foreground.Length==0||AndroidPolicy.IsProtectedPackage(foreground))throw new InvalidOperationException("Recording foreground unsafe");}catch(Exception){/* Fail closed: any failure while watching the foreground refuses the recording. */videoRefused=true;try{StopRemoteVideo(recordingRemote);adb.Shell("rm -f "+NativeSafety.Quote(recordingRemote),3000);}catch(Exception){/* StopVideo retries the owned producer and refuses delivery. */}break;}try{await Task.Delay(400,cancellation);}catch(OperationCanceledException){break;}}},cancellation);
        if(video.WaitForExit(1000)&&video.ExitCode is not (0 or null)){videoRefused=true;var error=NativeSafety.Mask(video.Error);try{StopVideo();}catch(InvalidOperationException){/* best-effort stop; the screenrecord failure below is what gets reported */}throw new IOException("screenrecord failed before recording: "+error);}
        if(action=="record"){video.WaitForExit((seconds+10)*1000);return StopVideo();}return Text($"Video started for at most {seconds}s (security codes are not masked). It is discarded if a sensitive app appears; capture(action=stop) saves it.");
    }
    private static string CaptureName(string value,bool video)
    {
        var name=value.Trim();if(name.Length==0){name=DateTime.Now.ToString("yyyyMMdd-HHmmss")+(video?".mp4":".png");}if(Path.GetFileName(name)!=name||name.IndexOfAny(Path.GetInvalidFileNameChars())>=0||name.Trim('.').Length==0||name.EndsWith('.'))throw new ArgumentException("name must be a filename without folder or invalid Windows characters");var ext=Path.GetExtension(name).ToLowerInvariant();if(ext.Length==0)return name+(video?".mp4":".png");if(video?ext!=".mp4":ext is not (".png" or ".jpg" or ".jpeg"))throw new ArgumentException("Unsupported capture extension");return name;
    }
    private JsonObject StopVideo()
    {
        if(video is null)throw new InvalidOperationException("No video recording");
        if(videoOwner!=(NativeSafety.Session.Value??"stdio"))throw new InvalidOperationException("This recording belongs to another MCP session");
        videoMonitor?.Cancel();videoMonitoring?.Wait(5000);
        var previous=NativeSafety.Cancellation.Value;using var cleanup=new CancellationTokenSource(TimeSpan.FromSeconds(15));NativeSafety.Cancellation.Value=cleanup.Token;
        try
        {
            StopRemoteVideo(videoRemote);if(!video.WaitForExit(5000)){video.Kill(true);throw new IOException("Recording stop unconfirmed; not saved");}
            if(videoRefused)throw new InvalidOperationException("Video discarded because a protected app appeared or its foreground became unreadable");
            cleanup.CancelAfter(TimeSpan.FromSeconds(120));adb.RunAdbArgs(["pull",videoRemote,videoTarget],120000);return Text("Video saved: "+videoTarget,new JsonObject{["path"]=videoTarget});
        }
        finally{try{using var erase=new CancellationTokenSource(TimeSpan.FromSeconds(5));NativeSafety.Cancellation.Value=erase.Token;adb.Shell("rm -f "+NativeSafety.Quote(videoRemote),5000);}finally{video.Dispose();video=null;videoRemote="";videoOwner="";videoMonitor?.Dispose();videoMonitor=null;videoMonitoring=null;NativeSafety.Cancellation.Value=previous;}}
    }
    private void StopRemoteVideo(string remote)
    {
        // Match an actual screenrecord process and its unique argument, never a
        // shell whose command line mentions it (pkill -f can kill that shell).
        if(!Regex.IsMatch(remote,@"^/data/local/tmp/nexus-record-[a-f0-9]{32}\.mp4$"))throw new InvalidOperationException("Unknown recording identity; no process stopped");
        // Android may need up to the reference recorder's fifteen-second finish
        // window to flush the MP4 after SIGINT; a four-second cutoff destroyed it.
        var prior=NativeSafety.Cancellation.Value;using var cleanup=new CancellationTokenSource(TimeSpan.FromSeconds(20));NativeSafety.Cancellation.Value=cleanup.Token;
        try
        {
            List<string> Owned()=>adb.Shell("ps -A -o PID,ARGS",2000).Split('\n').Select(line=>Regex.Match(line,@"^\s*(\d+)\s+(.+)$")).Where(match=>match.Success&&match.Groups[2].Value.StartsWith("screenrecord ",StringComparison.Ordinal)&&match.Groups[2].Value.Split((char[]?)null,StringSplitOptions.RemoveEmptyEntries).Contains(remote,StringComparer.Ordinal)).Select(match=>match.Groups[1].Value).ToList();
            foreach(var pid in Owned())adb.Shell("kill -2 "+pid,3000);
            var until=DateTime.UtcNow.AddSeconds(15);
            while(Owned().Count>0)
            {
                if(DateTime.UtcNow>=until){foreach(var pid in Owned()){adb.Shell("kill -9 "+pid,2000);}if(Owned().Count>0)throw new IOException("Owned screenrecord process did not stop; capture is not delivered");throw new IOException("Recording required forced termination and is discarded");}
                Thread.Sleep(80);
            }
        }
        finally{NativeSafety.Cancellation.Value=prior;}
    }
    private void DiscardVideo(string session)
    {
        lock(gate){using var cleanup=new CancellationTokenSource(TimeSpan.FromSeconds(20));var previous=NativeSafety.Cancellation.Value;NativeSafety.Cancellation.Value=cleanup.Token;var priorSession=NativeSafety.Session.Value;NativeSafety.Session.Value=session;try{using var lease=new PhoneLease(phoneLockPath);if(video is not null&&videoOwner==session){videoRefused=true;try{StopVideo();}catch(InvalidOperationException){/* best-effort stop during session cleanup */}}}finally{NativeSafety.Session.Value=priorSession;NativeSafety.Cancellation.Value=previous;}}
    }
}
