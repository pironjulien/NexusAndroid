using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace NexusAndroid;

public sealed partial class NativeEngine
{
    private readonly AdbClient adb;
    private readonly IAndroidQuery query;
    private readonly Func<IPhoneControl> control;
    private readonly Func<string,int,NativeScreen,IVideoRecorder> recorder;
    private readonly object gate = new();
    private string verifiedHardwareForOperation="";
    private sealed class SessionState{public List<UiElement> Last=[];public NativeScreen? Screen;public DateTime Seen;public double Scale=1,ScaleY=1;public ImageChainResult? Image;public int ImageWidth,ImageHeight,NextId=1;public bool CoordinatesValid=true;}
    private readonly Dictionary<string,SessionState> sessions=[];
    private SessionState Current{get{var key=NativeSafety.Session.Value??"stdio";if(!sessions.TryGetValue(key,out var state))sessions[key]=state=new();return state;}}
    private List<UiElement> last{get=>Current.Last;set=>Current.Last=value;}
    private NativeScreen? lastScreen{get=>Current.Screen;set=>Current.Screen=value;}
    private DateTime lastSeen{get=>Current.Seen;set=>Current.Seen=value;}
    private double imageScale{get=>Current.Scale;set=>Current.Scale=value;}
    private readonly string stateRoot;
    private static readonly HashSet<string> Gestures=["tap","long_press","double_tap","type","clear","key","swipe","scroll","wait","open_url","zoom","drag"];
    public NativeEngine(AdbClient client,IAndroidQuery? queryClient=null,string? localState=null,Func<IPhoneControl>? controller=null,Func<string,int,NativeScreen,IVideoRecorder>? videoRecorder=null){adb=client;query=queryClient??new AndroidQuery(client);control=controller??(()=>new ScrcpyChannel(adb));recorder=videoRecorder??((remote,seconds,screen)=>new AdbVideoRecorder(adb,remote,seconds,screen.Width,screen.Height));stateRoot=localState??Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"NexusAndroid","state");phoneLockPath=localState is null?Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),".nexus","android.lock"):Path.Combine(localState,"android.lock");awakePath=localState is null?Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),".nexus","nexus-android","awake.json"):Path.Combine(localState,"awake.json");Directory.CreateDirectory(stateRoot);}
    private static string Str(JsonObject a,string key,string fallback="")=>a[key]?.ToString()??fallback;
    private static bool Bool(JsonObject a,string key)=>a[key]?.GetValue<bool>()==true;
    private static int Num(JsonObject a,string key,int fallback)=>a[key] is null?fallback:a[key]!.GetValue<int>();
    private static JsonObject Text(string text,JsonObject? structured=null,bool codes=false)=>new(){["content"]=new JsonArray(new JsonObject{["type"]="text",["text"]=NativeSafety.Mask(text,codes:codes)}),["structuredContent"]=NativeSafety.MaskJson(structured)};
    private static string Hash(string value)=>Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value)));
    private string Shell(params string[] args)
    {
        if(args.Length>0&&(args[0] is "input" or "wm" or "svc"||args[0]=="am"&&args.Skip(1).Any(a=>a is "start" or "force-stop" or "broadcast")||args[0]=="settings"&&args.Length>1&&args[1]=="put"||args[0]=="cmd"&&args.Any(a=>a is "execute-app-function" or "set-location-enabled" or "set_dnd" or "--set")||args[0]=="pm"&&args.Any(a=>a is "clear" or "uninstall")))NativeSafety.StopGuard();
        return adb.Shell(string.Join(' ',args.Select(NativeSafety.Quote)));
    }
    public JsonObject Execute(string name,JsonObject args)
    {
        NativeSchemas.Validate(name=="phone_map"?"map":name,args);
        if(name=="capture"&&Str(args,"action")=="record")
        {
            var start=args.DeepClone().AsObject();start["action"]="start";start["seconds"]=Num(args,"seconds",30);Execute("capture",start);var recording=video!;
            try{recording.WaitForExitAsync(NativeSafety.Cancellation.Value).GetAwaiter().GetResult();return Execute("capture",new JsonObject{["action"]="stop"});}
            catch(OperationCanceledException){DiscardVideo(NativeSafety.Session.Value??"stdio");throw;}
        }
        var deadline=DateTime.UtcNow.AddSeconds(30);while(!Monitor.TryEnter(gate,80)){NativeSafety.Cancellation.Value.ThrowIfCancellationRequested();if(DateTime.UtcNow>=deadline)throw new IOException("Phone busy with another agent, no action sent");}
        try
        {
            using var lease=new PhoneLease(phoneLockPath);
            if(name is not ("checkup" or "phone")&&!adb.EnsureDevice())throw new IOException("No authorized Android device connected; use checkup for connection details");
            verifiedHardwareForOperation=adb.VerifiedSelectedHardwareIdentity??"";
            if(name is not ("checkup" or "phone"))RestoreExpiredAwake();
            var began=System.Diagnostics.Stopwatch.StartNew();try{var result=name switch
            {
                "phone"=>Phone(),"checkup"=>Checkup(args),"look"=>Look(args),"act"=>Act(args),"launch"=>Launch(args),
                "messages"=>Messages(args),"draft"=>Draft(args),"notifications"=>Notifications(args),"files"=>Files(args),
                "capture"=>Capture(args),"logs"=>Logs(args),"clipboard"=>Clipboard(args),"info"=>Info(args),"settings"=>Settings(args),
                "functions"=>Functions(args),"web"=>Web(args),"apps"=>Apps(args),"phone_map" or "map"=>Map(args),
                _=>throw new ArgumentException("Unknown tool: "+name)
            };
            NativeJournal.Write(name,lastScreen is null?null:Foreground(lastScreen),result["structuredContent"]?["verified"]?.ToJsonString()=="false"?"~":"✓",began.Elapsed.TotalMilliseconds);return result;}
            catch(Exception e){NativeJournal.Write(name,lastScreen is null?null:Foreground(lastScreen),"✗",began.Elapsed.TotalMilliseconds,e);throw;}
        }
        finally{verifiedHardwareForOperation="";Monitor.Exit(gate);}
    }
    private NativeScreen Read(bool image=false)
    {
        var screen=query.Screen(image||last.Any(e=>e.IsOcr));ObserveRecording(screen);if(lastScreen is not null&&(lastScreen.Width!=screen.Width||lastScreen.Height!=screen.Height))Current.CoordinatesValid=false;return screen;
    }
    public void CloseSession(string session)
    {
        DiscardVideo(session);lock(gate)sessions.Remove(session);NativeSafety.ForgetSession(session);
    }
    public void Shutdown()
    {
        string? owner;lock(gate)owner=video is null?null:videoOwner;if(owner is not null)DiscardVideo(owner);
        lock(gate){using var cleanup=new CancellationTokenSource(TimeSpan.FromSeconds(10));var previous=NativeSafety.Cancellation.Value;NativeSafety.Cancellation.Value=cleanup.Token;try{using var lease=new PhoneLease(phoneLockPath);RestoreExpiredAwake(forceOwn:true);}finally{NativeSafety.Cancellation.Value=previous;awakeTimer?.Dispose();}}
    }
    private List<UiElement> Elements(NativeScreen screen)
    {
        var elements=ScreenParser.ParseXml(screen.Xml);if(elements.Any(e=>e.Text.Length>0||e.ContentDesc.Length>0)||screen.Image is null)return elements;
        try
        {
            var lines=NativeOcr.Read(screen.Image);var codeContext=NativeSafety.HasCodeKeyword(string.Join(' ',lines.Select(l=>l.Text)));
            foreach(var line in lines){var masked=NativeSafety.MaskCodes(NativeSafety.Mask(line.Text),codeContext);elements.Add(new("e"+(elements.Count+1),masked,"","OCR",Foreground(screen),line.Left,line.Top,line.Right,line.Bottom,(line.Left+line.Right)/2,(line.Top+line.Bottom)/2,true,false,true,false,false,false,false,false,line.Text,"ocr:"+(elements.Count+1),"",true));}
        }
        catch(Exception e)when(e is IOException or System.Runtime.InteropServices.COMException){presenceWarning+=" Windows OCR unavailable: "+e.Message;}
        return elements;
    }
    private static string Foreground(NativeScreen screen)=>Regex.Match(screen.State,@"(?:mCurrentFocus|mFocusedApp)=[^\r\n]*?\s([\w.]+)/").Groups[1].Value;
    private static bool Locked(NativeScreen screen)=>Regex.IsMatch(screen.State,@"(?:isKeyguardShowing|mShowingLockscreen|KeyguardShowing|mIsShowing|isStatusBarKeyguard)=true");
    private static bool LockKnown(NativeScreen screen)=>Regex.IsMatch(screen.State,@"(?:isKeyguardShowing|mShowingLockscreen|KeyguardShowing|mIsShowing|isStatusBarKeyguard)=(true|false)");
    private static bool Sensitive(UiElement e)=>e.IsPassword||NativeSafety.Mask(e.RawText)!=e.RawText||NativeSafety.Mask(e.RawDescription)!=e.RawDescription||e.Text.Contains("[masked",StringComparison.OrdinalIgnoreCase)||e.ContentDesc.Contains("[masked",StringComparison.OrdinalIgnoreCase);
    private JsonObject Look(JsonObject args)
    {
        if(args["region"] is JsonArray region)
        {
            var full=Current.Image??throw new InvalidOperationException("No full image previously sent in this session");if(region.Count!=4)throw new ArgumentException("region needs four coordinates");var p=region.Select(n=>(int)Math.Round(n!.GetValue<double>())).ToArray();var x1=Math.Clamp(p[0],0,full.Width);var y1=Math.Clamp(p[1],0,full.Height);var x2=Math.Clamp(p[2],0,full.Width);var y2=Math.Clamp(p[3],0,full.Height);if(x2<=x1||y2<=y1)throw new ArgumentException("Empty region");var decoded=ImageChain.Decode(full.Bytes);var cropped=new PixelBuffer(x2-x1,y2-y1);for(var y=y1;y<y2;y++)Buffer.BlockCopy(decoded.Bgra,(y*decoded.Width+x1)*4,cropped.Bgra,(y-y1)*cropped.Width*4,cropped.Width*4);var enlarged=ImageChain.ResizeBilinear(cropped,cropped.Width*2,cropped.Height*2);var result=ImageChain.ProcessChain(enlarged,maxBytes:500*1024);var regionOutput=Text($"Zoom of last full image [{x1},{y1},{x2},{y2}]. Action coordinates remain those of that full image.");regionOutput["content"]!.AsArray().Add(new JsonObject{["type"]="image",["mimeType"]=result.MimeType,["data"]=Convert.ToBase64String(result.Bytes)});return regionOutput;
        }
        var show=Str(args,"show","auto");if(show=="none")return Text("No screen view requested; previous references remain bound to their observed contents.");var image=show is "both" or "image" || args["region"] is not null;var previous=last.ToList();
        var screen=Read();if(Locked(screen))screen=LiftTrustedLock(screen);ExtendAwake(screen);if(image)screen=Read(true);var elements=Elements(screen);
        if(show=="auto" && !elements.Any(e=>e.Text.Length>0||e.ContentDesc.Length>0)){image=true;screen=Read(true);elements=Elements(screen);}
        var assigned=new List<UiElement>();var used=new HashSet<string>();foreach(var element in elements){var candidates=previous.Where(p=>p.ResourceId==element.ResourceId&&p.ClassName==element.ClassName&&p.PackageName==element.PackageName&&p.Left==element.Left&&p.Top==element.Top&&!used.Contains(p.Id)).ToList();var id=candidates.Count==1?candidates[0].Id:"e"+Current.NextId++;used.Add(id);assigned.Add(element with{Id=id});}elements=assigned;
        var fresh=DateTime.UtcNow;last=elements;lastScreen=screen;lastSeen=fresh;if(Current.Image is not null&&(Current.ImageWidth!=screen.Width||Current.ImageHeight!=screen.Height))Current.CoordinatesValid=false;
        var filtered=Str(args,"query") is {Length:>0} q?elements.Where(e=>(e.Text+" "+e.ContentDesc).Contains(q,StringComparison.OrdinalIgnoreCase)).ToList():elements;
        var tree=ScreenParser.FormatCompactTree(filtered.Take(300),imageScale,Current.ScaleY);if(show=="changes"){var differences=new List<string>();foreach(var element in filtered){var old=previous.FirstOrDefault(e=>e.Id==element.Id);if(old is null)differences.Add("+ "+ScreenParser.FormatCompactTree([element],imageScale,Current.ScaleY).TrimEnd());else if(old!=element)differences.Add("~ "+ScreenParser.FormatCompactTree([element],imageScale,Current.ScaleY).TrimEnd());}foreach(var old in previous.Where(p=>elements.All(e=>e.Id!=p.Id)))differences.Add("- ["+old.Id[1..]+"] removed");tree=differences.Count>0?string.Join('\n',differences):"No visible element changed";}
        var output=Text($"{Foreground(screen)} · {screen.Width}×{screen.Height}"+(Locked(screen)?" · locked":"")+"\n"+NativeSafety.Mask(screen.Error)+"\n"+(show=="image" ? "" : tree),codes:false);
        if(image && screen.Image is not null)
        {
            if(elements.Count==0 && !Bool(args,"confirm")){output["content"]!.AsArray().Add(new JsonObject{["type"]="text",["text"]="Image not sent: UI tree unavailable, code masking cannot be guaranteed. confirm=true only if the user accepts this exact unmasked image."});return output;}
            var blackouts=elements.Where(Sensitive).Select(e=>(e.Left,e.Top,e.Right,e.Bottom)).ToList();
            try{var lines=NativeOcr.Read(screen.Image);var context=NativeSafety.HasCodeKeyword(string.Join(' ',lines.Select(l=>l.Text)));foreach(var line in lines)if(NativeSafety.MaskCodes(NativeSafety.Mask(line.Text),context)!=line.Text)blackouts.Add((line.Left,line.Top,line.Right,line.Bottom));}
            catch(Exception e)when(e is IOException or System.Runtime.InteropServices.COMException){output["content"]!.AsArray().Add(new JsonObject{["type"]="text",["text"]="Windows OCR unavailable; codes drawn outside the UI tree may remain visible: "+NativeSafety.Mask(e.Message)});}
            var marks=Str(args,"marks","auto");var badges=show=="both"&&marks!="none"?elements.Where(e=>marks=="all" || (e.Clickable&&string.IsNullOrWhiteSpace(e.Text))).Select(e=>(int.Parse(e.Id[1..]),e.CenterX,e.CenterY)).ToList():null;
            var result=ImageChain.ProcessEncodedImage(screen.Image,maxBytes:500*1024,blackouts:blackouts,badges:badges);
            Current.Image=result;
            Current.ImageWidth=screen.Width;Current.ImageHeight=screen.Height;Current.CoordinatesValid=true;
            imageScale=(double)result.Width/screen.Width;
            Current.ScaleY=(double)result.Height/screen.Height;if(show!="image")output["content"]![0]!["text"]=NativeSafety.Mask($"{Foreground(screen)} · {screen.Width}×{screen.Height}"+(Locked(screen)?" · locked":"")+"\n"+NativeSafety.Mask(screen.Error)+"\n"+ScreenParser.FormatCompactTree(filtered.Take(300),imageScale,Current.ScaleY),codes:false);
            output["content"]!.AsArray().Add(new JsonObject{["type"]="image",["mimeType"]=result.MimeType,["data"]=Convert.ToBase64String(result.Bytes)});
        }
        return output;
    }
    private (int X,int Y,UiElement? Element) Resolve(JsonNode? target,List<UiElement> fresh)
    {
        if(target is null)throw new ArgumentException("Missing target: use [N], visible text, id:resource-id or x,y");var text=target.ToString();
        UiElement? element=null;
        if(Regex.IsMatch(text,@"^\[?\d+\]?$"))
        {
            if(DateTime.UtcNow-lastSeen>TimeSpan.FromSeconds(60))throw new InvalidOperationException("Stale [N] reference: look again");
            element=last.SingleOrDefault(e=>e.Id=="e"+text.Trim('[',']'))??throw new ArgumentException("Unknown [N] reference");
            var matches=fresh.Where(e=>e.ResourceId==element.ResourceId&&e.RawText==element.RawText&&e.ClassName==element.ClassName&&e.PackageName==element.PackageName&&e.Left==element.Left&&e.Top==element.Top&&e.Right==element.Right&&e.Bottom==element.Bottom&&e.ZPath==element.ZPath).ToList();
            if(matches.Count!=1)throw new InvalidOperationException("Target changed since look: nothing sent; look again");element=matches[0];
        }
        else if(Regex.Match(text,@"^\s*@?\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$") is {Success:true} point)
        {
            if(!Current.CoordinatesValid)throw new InvalidOperationException("Screen dimensions changed since the image: request a fresh full image before coordinate input");var x=(int)(double.Parse(point.Groups[1].Value,CultureInfo.InvariantCulture)/imageScale);var y=(int)(double.Parse(point.Groups[2].Value,CultureInfo.InvariantCulture)/Current.ScaleY);if(lastScreen is null||x<0||y<0||x>=lastScreen.Width||y>=lastScreen.Height)throw new ArgumentException("Coordinates outside the last observed image");
            var at=fresh.Where(e=>x>=e.Left&&x<e.Right&&y>=e.Top&&y<e.Bottom).OrderByDescending(e=>e.ZPath,StringComparer.Ordinal).ThenBy(e=>(long)(e.Right-e.Left)*(e.Bottom-e.Top)).FirstOrDefault();if(at is {Enabled:false})throw new InvalidOperationException("Target disabled");return(x,y,at);
        }
        else
        {
            var normalized=NativeSafety.Comparable(text);var matches=fresh.Where(e=>text.StartsWith("id:")?e.ResourceId==text[3..]:text.StartsWith("desc:")?e.ContentDesc==text[5..]:NativeSafety.Comparable(e.Text)==normalized||NativeSafety.Comparable(e.ContentDesc)==normalized).ToList();
            if(matches.Count!=1)throw new ArgumentException(matches.Count==0?"Target not found":"Ambiguous target: use id:resource-id or [N]");element=matches[0];
        }
        if(!element.Enabled)throw new InvalidOperationException("Target disabled: nothing sent");if(element.Right<=element.Left||element.Bottom<=element.Top)throw new InvalidOperationException("Target outside visible screen: no input sent");var front=fresh.Where(e=>element.CenterX>=e.Left&&element.CenterX<e.Right&&element.CenterY>=e.Top&&element.CenterY<e.Bottom).OrderByDescending(e=>e.ZPath,StringComparer.Ordinal).FirstOrDefault();if(front is not null&&front.ZPath.Length>0&&element.ZPath.Length>0&&!front.ZPath.StartsWith(element.ZPath,StringComparison.Ordinal)&&!element.ZPath.StartsWith(front.ZPath,StringComparison.Ordinal))throw new InvalidOperationException("Target is covered by another element: nothing clicked");return(element.CenterX,element.CenterY,element);
    }
    private static JsonObject TargetIdentity(UiElement e)=>new(){["resource_id"]=e.ResourceId,["class"]=e.ClassName,["package"]=e.PackageName,["path"]=e.ZPath,["bounds"]=new JsonArray(e.Left,e.Top,e.Right,e.Bottom),["checked"]=e.Checked,["selected"]=e.Selected,["text"]=Sensitive(e)?"[masked]#"+NativeSafety.Seal(e.RawText+"\0"+e.RawDescription):e.Text,["description"]=Sensitive(e)?"[masked]":e.ContentDesc};
    private static UiElement? AtPoint(List<UiElement> elements,int x,int y)=>elements.Where(e=>x>=e.Left&&x<e.Right&&y>=e.Top&&y<e.Bottom).OrderByDescending(e=>e.ZPath,StringComparer.Ordinal).ThenBy(e=>(long)(e.Right-e.Left)*(e.Bottom-e.Top)).FirstOrDefault();
    private static UiElement? FollowTarget(UiElement before,List<UiElement> after)
    {
        var candidates=after.Where(e=>e.ResourceId==before.ResourceId&&e.PackageName==before.PackageName&&e.ClassName==before.ClassName).ToList();var exact=candidates.Where(e=>e.Left==before.Left&&e.Top==before.Top&&e.Right==before.Right&&e.Bottom==before.Bottom&&e.ZPath==before.ZPath).ToList();return exact.Count==1?exact[0]:before.ResourceId.Length>0&&candidates.Count==1?candidates[0]:null;
    }
    private string? Guard(NativeScreen screen,List<UiElement> elements,JsonObject step,UiElement? target,string gesture,bool authorize=true,UiElement? destination=null)
    {
        NativeSafety.StopGuard();if(Locked(screen) && gesture is not ("back" or "home" or "recent"))throw new InvalidOperationException("Phone locked: no input sent. A required code is never entered by this tool.");
        var pkg=Foreground(screen);if(pkg.Length==0)throw new InvalidOperationException("Foreground app unknown: no input sent");
        var label=(target?.RawText??"")+" "+(target?.ContentDesc??"")+" "+(destination?.RawText??"")+" "+(destination?.ContentDesc??"");var judgedPackages=new HashSet<string>{pkg,target?.PackageName??"",destination?.PackageName??""};var context=string.Join(' ',elements.Where(e=>judgedPackages.Contains(e.PackageName)).Select(e=>e.RawText+" "+e.RawDescription));
        var safeKey=gesture is "back" or "home" or "recent";
        if(!LockKnown(screen)&&!safeKey)throw new InvalidOperationException("Lock state unreadable; no input sent");
        if((AndroidPolicy.IsProtectedPackage(pkg)||target is not null&&AndroidPolicy.IsProtectedPackage(target.PackageName)||destination is not null&&AndroidPolicy.IsProtectedPackage(destination.PackageName))&&!safeKey)
        {
            var irreversible=NativeSafety.Risky(label)||NativeSafety.ContextRisk(label,context);var slider=new[]{target,destination}.Any(e=>e is not null&&Regex.IsMatch(e.ClassName+" "+e.ResourceId+" "+e.RawDescription,@"(?i)slider|seekbar|glisser|slide to"));
            if(irreversible&&(gesture!="scroll"||slider))throw new InvalidOperationException("Irreversible, money and send gestures in protected apps are refused");
            if(gesture!="scroll"&&new[]{target,destination}.Any(e=>e is {Clickable:true,Editable:false}&&(e.RawText+e.RawDescription).Trim().Length==0))throw new InvalidOperationException("Unnamed icon in a protected app is always refused; it may initiate payment or transfer");
            if(gesture!="scroll"&&!Bool(step,"confirm"))throw new InvalidOperationException("Protected app: reading and scrolling only without confirm=true");
        }
        if(target?.IsPassword==true && gesture is "type" or "clear")throw new InvalidOperationException("Password field: typing refused");
        var trigger=gesture is "type" or "clear"?"":label;var unnamedSend=target is not null && !target.Editable && (Regex.IsMatch(target.ResourceId,@"(?i)send|compose_message_send|send_message_button|send_button")||target.Clickable&&label.Trim().Length==0&&Regex.IsMatch(pkg,@"(?i)messag|messenger|chat|mail|sms|dialer|telecom|incallui|whatsapp|telegram|discord|instagram|twitter|facebook|slack|teams"));
        var risky=NativeSafety.Risky(trigger)||NativeSafety.ContextRisk(trigger,context)||unnamedSend||gesture=="enter"||gesture=="type"&&Str(step,"text").Contains('\n');
        if(risky&&gesture=="double_tap")throw new InvalidOperationException("No double tap on an outward or irreversible trigger");
        if(risky&&target?.IsOcr==true)throw new InvalidOperationException("Outward or irreversible action on a drawn surface cannot be bound to an exact readable agreement; no input sent");
        if(target is null && !safeKey && !(elements.Count>0&&gesture is "scroll" or "zoom")&&gesture is not ("tab" or "left" or "right" or "up" or "down" or "escape"))throw new InvalidOperationException("No readable element under target: no blind input sent");
        if(risky&&authorize)
        {
            var exact=new JsonObject{["tool"]="android",["gesture"]=gesture,["package"]=pkg,["step"]=step.DeepClone(),["target"]=target is null?null:TargetIdentity(target),["destination"]=destination is null?null:TargetIdentity(destination),["screen"]=new JsonArray(elements.Where(e=>!e.IsOcr&&e.PackageName!="com.android.systemui"&&!Regex.IsMatch(e.PackageName,@"(?i)inputmethod|honeyboard|keyboard|swiftkey")).Select(e=>(JsonNode?)new JsonObject{["package"]=e.PackageName,["id"]=e.ResourceId,["class"]=e.ClassName,["text"]=Sensitive(e)?"[masked]#"+NativeSafety.Seal(e.RawText+"\0"+e.RawDescription):e.Text,["description"]=Sensitive(e)?"[masked]":e.ContentDesc,["checked"]=e.Checked,["selected"]=e.Selected}).ToArray())};exact["step"]!.AsObject().Remove("accord");exact["step"]!.AsObject().Remove("confirm");
            var sealedAction=exact.ToJsonString();
            NativeSafety.Require(step,sealedAction,NativeSafety.Outward(trigger)||unnamedSend||gesture=="enter"||gesture=="type"||NativeSafety.ContextRisk(trigger,context)&&Regex.IsMatch(NativeSafety.Normalize(context),"paiement|payer|payment|virement|transfer|iban|checkout|carte bancaire|credit card"));return sealedAction;
        }
        return null;
    }
    private JsonObject Act(JsonObject args)
    {
        NativeSafety.StopGuard();var steps=args["steps"]?.DeepClone().AsArray();var routine=Str(args,"routine");
        if(routine.Length>0){if(steps is not null)throw new ArgumentException("Use either steps or routine");var saved=JsonNode.Parse(File.ReadAllText(RoutinePath(routine)))!.AsObject();var expected=Str(saved,"package");if(expected.Length>0&&Foreground(Read())!=expected)throw new InvalidOperationException("Routine was recorded in "+expected+"; open that app before replaying");steps=saved["steps"]!.DeepClone().AsArray();}
        if(steps is null||steps.Count==0)throw new ArgumentException("steps required");
        var templates=steps.DeepClone().AsArray();var parameters=args["params"]?.AsObject();foreach(var step in steps.OfType<JsonObject>())if(step["text"] is not null){var text=Str(step,"text");var substitutions=Regex.Matches(text,@"\{\{(\w+)\}\}");if(substitutions.Count>0){if(Str(step,"do")=="type"&&Regex.Replace(text,@"\{\{\w+\}\}","").Trim().Length>0)throw new ArgumentException("Parameterized typed text must contain only parameters");foreach(Match parameter in substitutions){if(parameters?[parameter.Groups[1].Value] is null)throw new ArgumentException("Missing routine parameter: "+parameter.Groups[1].Value);text=text.Replace(parameter.Value,parameters[parameter.Groups[1].Value]!.ToString());}step["text"]=text;}}
        foreach(var node in steps){if(node is not JsonObject s||!Gestures.Contains(Str(s,"do")))throw new ArgumentException("Unknown gesture or missing do; no input sent");}
        var reports=new List<string>();var savedSteps=new JsonArray();var allVerified=true;var routinePackage=Foreground(Read());var stepIndex=-1;
        foreach(var node in steps)
        {
            stepIndex++;var stepBegan=System.Diagnostics.Stopwatch.StartNew();var step=node!.AsObject();var action=Str(step,"do");var screen=Read();if(action is not ("wait" or "open_url")){WaitForOwner(screen);screen=LiftTrustedLock(screen);if(!Awake(screen)&&!Locked(screen)){Shell("input","keyevent","KEYCODE_WAKEUP");lastInput=DateTimeOffset.UtcNow;screen=Read();}}var elements=Elements(screen);
            try
            {
                if(action=="wait")
                {
                    var deadline=DateTime.UtcNow.AddSeconds(Math.Clamp(step["timeout"]?.GetValue<double>()??5,0,30));var wanted=NativeSafety.Comparable(Str(step,"text"));
                    while(Elements(screen).Any(e=>NativeSafety.Comparable(e.Text+" "+e.ContentDesc).Contains(wanted,StringComparison.Ordinal))==Bool(step,"gone")){NativeSafety.StopGuard();if(DateTime.UtcNow>=deadline)throw new TimeoutException("Expected screen condition not observed");Thread.Sleep(80);screen=Read();}reports.Add("✓ wait condition observed");savedSteps.Add(templates[stepIndex]!.DeepClone());continue;
                }
                if(action=="open_url"){Launch(new JsonObject{["action"]="android.intent.action.VIEW",["data"]=Str(step,"text"),["confirm"]=step["confirm"]?.DeepClone(),["accord"]=step["accord"]?.DeepClone(),["then"]="none"});var template=templates[stepIndex]!.DeepClone().AsObject();template.Remove("confirm");template.Remove("accord");savedSteps.Add(template);reports.Add("✓ intent arrival inspected");continue;}
                (int X,int Y,UiElement? Element) aim=default;
                UiElement? endElement=null;
                var key=Str(step,"keys").ToLowerInvariant();var keyCodes=action=="key"?Keys(key):[];var guardKey=action=="key"?!SafeKeys(keyCodes)?"enter":NavigationGesture(keyCodes):key;WaitForOwner(screen);screen=LiftTrustedLock(Read());elements=Elements(screen);
                if(action=="key")aim.Element=elements.FirstOrDefault(e=>e.Focused);
                else if((action is "type" or "clear")&&step["target"] is null)aim.Element=elements.SingleOrDefault(e=>e.Focused&&e.Editable)??throw new InvalidOperationException("Input focus changed; no input sent");
                else if(action is "scroll" or "zoom"&&step["target"] is null)aim=(screen.Width/2,screen.Height/2,AtPoint(elements,screen.Width/2,screen.Height/2));
                else aim=Resolve(step["target"],elements);
                if(action is "type" or "clear"&&aim.Element?.Editable!=true)throw new InvalidOperationException("Target is not an editable field");
                if(action=="key")GuardKeys(screen,elements,keyCodes);
                (int X,int Y,UiElement? Element) end=default;if(action is "swipe" or "drag"){end=Resolve(step["to"],elements);endElement=end.Element;}
                if(action!="scroll")Guard(screen,elements,step,aim.Element,action=="key"?guardKey:action,authorize:action!="type",destination:endElement);
                switch(action)
                {
                    case "tap":Shell("input","tap",aim.X.ToString(),aim.Y.ToString());break;
                    case "double_tap":
                        Shell("input","tap",aim.X.ToString(),aim.Y.ToString());var beganSecond=System.Diagnostics.Stopwatch.StartNew();var secondScreen=Read();var secondElements=Elements(secondScreen);if(beganSecond.Elapsed.TotalMilliseconds>250)throw new IOException("First tap sent; screen recheck exceeded 250 ms, so no second tap sent");var again=AtPoint(secondElements,aim.X,aim.Y);var unapproved=step.DeepClone().AsObject();unapproved.Remove("accord");Guard(secondScreen,secondElements,unapproved,again,"double_tap");Shell("input","tap",aim.X.ToString(),aim.Y.ToString());break;
                    case "long_press":Shell("input","swipe",aim.X.ToString(),aim.Y.ToString(),aim.X.ToString(),aim.Y.ToString(),"650");break;
                    case "type":
                        if(step["target"] is not null&&!aim.Element!.Focused){Shell("input","tap",aim.X.ToString(),aim.Y.ToString());var focused=Read();var now=Elements(focused);var field=now.SingleOrDefault(e=>e.Focused&&e.Editable);if(field is null||field.IsPassword||field.ResourceId!=aim.Element.ResourceId||field.PackageName!=aim.Element.PackageName)throw new IOException("Field focus changed after tap; nothing typed");screen=focused;elements=now;aim.Element=field;}
                        Guard(screen,elements,step,aim.Element,"type");
                        PasteText(Str(step,"text"));break;
                    case "clear":
                        if(step["target"] is not null&&!aim.Element!.Focused){Shell("input","tap",aim.X.ToString(),aim.Y.ToString());var clearedFocus=Read();var fields=Elements(clearedFocus);var targetField=fields.SingleOrDefault(e=>e.Focused&&e.Editable);if(targetField is null||targetField.ResourceId!=aim.Element.ResourceId||targetField.PackageName!=aim.Element.PackageName)throw new IOException("Target field did not receive focus; nothing cleared");Guard(clearedFocus,fields,step,targetField,"clear");}
                        Shell("input","keycombination","113","29");Shell("input","keyevent","67");break;
                    case "key":
                        if(keyCodes.Count==1)Shell("input","keyevent",keyCodes[0]);else Shell(new[]{"input","keycombination"}.Concat(keyCodes).ToArray());break;
                    case "swipe":case "drag":
                        Shell("input",action=="drag"?"draganddrop":"swipe",aim.X.ToString(),aim.Y.ToString(),end.X.ToString(),end.Y.ToString(),action=="drag"?"1600":"400");break;
                    case "scroll":
                        var direction=Str(step,"direction","down");var dx=direction=="left"?screen.Width/3:direction=="right"?-screen.Width/3:0;var dy=direction=="down"?-screen.Height/3:direction=="up"?screen.Height/3:0;
                        if(direction is not ("up" or "down" or "left" or "right"))throw new ArgumentException("scroll requires up/down/left/right; use zoom for in/out");var amount=Math.Clamp(Num(step,"amount",1),1,10);if(step["accord"] is not null&&amount>1)throw new InvalidOperationException("An accord authorizes one scroll only; amount must be 1");
                        for(var n=0;n<amount;n++){if(n>0){screen=Read();elements=Elements(screen);}var from=(X:aim.X-dx/2,Y:aim.Y-dy/2);var to=(X:aim.X+dx/2,Y:aim.Y+dy/2);if(from.X<0||from.Y<0||to.X<0||to.Y<0||from.X>=screen.Width||to.X>=screen.Width||from.Y>=screen.Height||to.Y>=screen.Height)throw new ArgumentException("Scroll path outside screen");Guard(screen,elements,step,AtPoint(elements,from.X,from.Y),"scroll",destination:AtPoint(elements,to.X,to.Y));Shell("input","swipe",from.X.ToString(),from.Y.ToString(),to.X.ToString(),to.Y.ToString(),"350");}break;
                    case "zoom":
                        var zoomDirection=Str(step,"direction","in");if(zoomDirection is not ("in" or "out"))throw new ArgumentException("zoom requires in/out");var geometry=ScrcpyChannel.ZoomGeometry(aim.X,aim.Y,screen.Width,screen.Height,zoomDirection=="in",Num(step,"amount",1));foreach(var point in new[]{geometry.First,geometry.Second})foreach(var under in elements.Where(e=>point.X>=e.Left&&point.X<e.Right&&point.Y>=e.Top&&point.Y<e.Bottom)){var label=under.RawText+" "+under.RawDescription;if(NativeSafety.Risky(label)||NativeSafety.ContextRisk(label,string.Join(' ',elements.Select(e=>e.RawText)))||(AndroidPolicy.IsProtectedPackage(Foreground(screen))||AndroidPolicy.IsProtectedPackage(under.PackageName))&&under.Clickable)throw new InvalidOperationException("A zoom finger would land on an action button; aim at the content away from buttons");}
                        using(var channel=control())channel.Zoom(aim.X,aim.Y,screen.Width,screen.Height,zoomDirection=="in",Num(step,"amount",1));break;
                }
                lastInput=DateTimeOffset.UtcNow;KeepScreenAwake(screen);var after=Read();var afterElements=Elements(after);var same=aim.Element is null?null:FollowTarget(aim.Element,afterElements);
                var verified=Foreground(after)!=Foreground(screen)||(same is not null&&aim.Element is not null&&(same.Checked!=aim.Element.Checked||same.RawText!=aim.Element.RawText||same.Focused!=aim.Element.Focused));
                if(action is "type" or "clear")verified=same is {Focused:true,Editable:true}&&same.RawText!=aim.Element!.RawText&&(action=="clear"?same.RawText.Length==0:same.RawText.Contains(Str(step,"text"),StringComparison.Ordinal));
                if(action=="scroll")verified=Hash(string.Join('|',afterElements.Where(e=>e.PackageName==Foreground(after)).Select(e=>$"{e.ResourceId}|{e.RawText}|{e.Top}|{e.Bottom}")))!=Hash(string.Join('|',elements.Where(e=>e.PackageName==Foreground(screen)).Select(e=>$"{e.ResourceId}|{e.RawText}|{e.Top}|{e.Bottom}")));
                reports.Add((verified?"✓ ":"~ ")+action+(verified?" effect read back":" sent, effect unverified; do not repeat"));NativeJournal.Write(action,Foreground(after),verified?"✓":"~",stepBegan.Elapsed.TotalMilliseconds);
                if(!verified){allVerified=false;break;}
                var saved=templates[stepIndex]!.DeepClone().AsObject();saved.Remove("confirm");saved.Remove("accord");if(step["target"] is not null&&aim.Element is not null)saved["target"]=aim.Element.ResourceId.Length>0?"id:"+aim.Element.ResourceId:aim.Element.RawText;if(step["to"] is not null&&endElement is not null)saved["to"]=endElement.ResourceId.Length>0?"id:"+endElement.ResourceId:endElement.RawText;
                if(saved["target"]?.ToString() is {Length:0})throw new InvalidOperationException("No stable target to remember");savedSteps.Add(saved);
            }
            catch(Exception ex){reports.Add("✗ "+action+": "+ex.Message);allVerified=false;break;}
        }
        if(Str(args,"remember") is {Length:>0} remember && allVerified){foreach(var saved in savedSteps.OfType<JsonObject>()){saved.Remove("confirm");saved.Remove("accord");}File.WriteAllText(RoutinePath(remember),new JsonObject{["package"]=routinePackage,["saved"]=DateTimeOffset.Now.ToString("O"),["steps"]=savedSteps}.ToJsonString());reports.Add("Routine saved using stable targets; no authorizations stored");}
        if(presenceWarning.Length>0){reports.Add("⚠ "+presenceWarning);presenceWarning="";}var view=Look(new JsonObject{["show"]=Str(args,"then","auto")});view["content"]!.AsArray().Insert(0,new JsonObject{["type"]="text",["text"]=NativeSafety.Mask(string.Join('\n',reports))});view["structuredContent"]=new JsonObject{["verified"]=allVerified,["reports"]=new JsonArray(reports.Select(v=>(JsonNode?)JsonValue.Create(NativeSafety.Mask(v))).ToArray())};return view;
    }
    private string RoutinePath(string name){if(!Regex.IsMatch(name,@"^[\w-]{1,80}$"))throw new ArgumentException("Invalid routine name");return Path.Combine(stateRoot,"routine-"+name+".json");}
    private static readonly HashSet<string> NavigationKeyCodes=["3","4","19","20","21","22","24","25","61","82","111","187","224"];
    private void PasteText(string text)
    {
        using var clip=control();var old=clip.Get();try{NativeSafety.StopGuard();clip.Set(text,true);Thread.Sleep(300);}catch(IOException e){throw new IOException("Paste interrupted; effect unknown, observe before any new decision. "+e.Message,e);}finally{try{clip.Set(old,false);}catch(IOException){presenceWarning+=" The clipboard could not be restored after typing; do not repeat the paste.";}}
    }
    private static readonly HashSet<string> SafeKeyCodes=[..NavigationKeyCodes,"67","112","84"];
    private static bool SafeKeys(List<string> codes)=>codes.SequenceEqual(new[]{"113","29"})||codes.Count==1&&SafeKeyCodes.Contains(codes[0]);
    private static string NavigationGesture(List<string> codes)=>codes.Count==1?codes[0] switch{"3"=>"home","4"=>"back","187"=>"recent","19"=>"up","20"=>"down","21"=>"left","22"=>"right","61"=>"tab","111"=>"escape",_=>"tab"}:"tab";
    private static void GuardKeys(NativeScreen screen,List<UiElement> elements,List<string> codes)
    {
        if(codes.All(NavigationKeyCodes.Contains))return;
        if(elements.Count==0)throw new InvalidOperationException("Active field unreadable: only navigation keys permitted; no key sent");
        if(elements.Any(e=>e.Focused&&e.IsPassword))throw new InvalidOperationException("Password field: only navigation keys permitted; no key sent");
        if(!SafeKeys(codes)&&(AndroidPolicy.IsProtectedPackage(Foreground(screen))||elements.Any(e=>e.Focused&&AndroidPolicy.IsProtectedPackage(e.PackageName))))throw new InvalidOperationException("Validation keys in protected apps are always refused, including with confirm and accord");
    }
    private static List<string> Keys(string keys)
    {
        var aliases=new Dictionary<string,string>{["back"]="4",["retour"]="4",["home"]="3",["accueil"]="3",["recent"]="187",["recents"]="187",["app_switch"]="187",["enter"]="66",["entrée"]="66",["return"]="66",["delete"]="67",["backspace"]="67",["del"]="112",["forward_del"]="112",["tab"]="61",["left"]="21",["dpad_left"]="21",["right"]="22",["dpad_right"]="22",["up"]="19",["dpad_up"]="19",["down"]="20",["dpad_down"]="20",["escape"]="111",["esc"]="111",["space"]="62",["ctrl"]="113",["ctrl_left"]="113",["control"]="113",["shift"]="59",["alt"]="57",["a"]="29",["volume_up"]="24",["volume_down"]="25",["wakeup"]="224",["menu"]="82",["search"]="84",["pageup"]="92",["pagedown"]="93",["move_home"]="122",["move_end"]="123"};
        var parts=keys.Split('+',StringSplitOptions.TrimEntries|StringSplitOptions.RemoveEmptyEntries);if(parts.Length==0)throw new ArgumentException("keys required");var codes=new List<string>();foreach(var part in parts){var bare=part.StartsWith("keycode_")?part[8..]:part;if(aliases.TryGetValue(bare,out var code))codes.Add(code);else if(Regex.IsMatch(part,@"^keycode_[a-z0-9_]+$"))codes.Add(part.ToUpperInvariant());else throw new ArgumentException("Unknown key: "+part);}if(codes.Count>1&&codes.Take(codes.Count-1).Any(c=>c is not ("113" or "59" or "57")))throw new ArgumentException("Only one non-modifier key per step; put successive keys in separate steps");return codes;
    }
}
