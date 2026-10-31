using NexusAndroid;
using System.Net;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace NexusAndroid.Tests.Advanced;

// Contracts run in declaration order, once per test run (ContractSuite); each one is a test case.
internal static class AdvancedContracts
{
    public static void Run(Action<string, Action> Test)
    {
void Check(bool condition,string why){if(!condition)throw new Exception(why);}
string Text(JsonObject value)=>string.Join('\n',value["content"]!.AsArray().OfType<JsonObject>().Select(c=>c["text"]?.ToString()??""));
bool Refused(Action action,string word){try{action();return false;}catch(Exception e)when(NativeSafety.Recoverable(e)){/* assertion helper: observes whatever the engine throws */return e.Message.Contains(word,StringComparison.OrdinalIgnoreCase);}}
object? Private(string name,params object[] arguments)=>typeof(NativeEngine).GetMethod(name,BindingFlags.NonPublic|BindingFlags.Static)!.Invoke(null,arguments);
JsonObject Args(string action)=>new(){["action"]=action};
var phone=new FixturePhone();var adb=new FixtureAdb(phone);
var state=Path.Join(TestPaths.Root, "advanced-fixtures",Guid.NewGuid().ToString("N"));
var engine=new NativeEngine(adb,phone,state,()=>throw new Exception("No input control allowed in advanced fixtures"));
string DeviceRoot()=>Path.Join(state,"map",Hash(adb.Identity)[..12].ToLowerInvariant());
void SeedIndex(JsonArray? rows=null){Directory.CreateDirectory(DeviceRoot());File.WriteAllText(Path.Join(DeviceRoot(),"settings-"+Hash("native-index-5|fr-FRversionCode=7lastUpdateTime=fixture|/fixture/settings.apk|12345:fixture|fixture/build")[..16]+".json"),(rows??new JsonArray(new JsonObject{["title"]="Notifications",["path"]="Paramètres > Notifications",["key"]="notification_badging",["component"]="com.android.settings/.Settings",["orphan"]=false})).ToJsonString());}
void SetFunction(string schema,string method,bool enabled=true){adb.Catalog=new JsonObject{["com.example.app"]=new JsonArray(new JsonObject{["AppFunctionStaticMetadata-x"]=new JsonObject{["schemaName"]=new JsonArray(schema),["functionId"]=new JsonArray("com.example.Service#"+method),["enabledByDefault"]=new JsonArray(enabled)}})}.ToJsonString();adb.Commands.Clear();}
JsonObject RunFunction(bool confirm=false)=>new(){["action"]="run",["app"]="com.example.app",["function"]="readName",["params"]=new JsonObject{["recipient"]="fixture recipient",["body"]="fixture body"},["confirm"]=confirm};

Test("risk matches independent schema/method verbs and destructive/outward triggers",()=>
{
    foreach(var (schema,method,expected) in new[]{("readName","sendEmail","outward"),("getStatus","Actions_deleteAll","risky"),("findCallRecords","PhoneFunctions_findCallRecords","read"),("message","SmsFunctions_send_message","outward"),("acceptCall","CallAppFunctions_acceptCall","outward"),("openAlbum","GalleryFunctions_openAlbum","read"),("setAlarm","AlarmActions_createAlarm","write"),("findUnknown","Mystery_blink","risky"),("readNotes","NotesFunctions_resetAll","risky"),("getStatus","dialPhone","outward"),("createNote","NotesFunctions_straightenNote","write")})
        Check((string)Private("FunctionRisk",schema,method)! == expected,schema+"/"+method+" misclassified");
});
Test("read schema wrapping sendEmail requires bound agreement and cannot execute",()=>{SetFunction("readName","sendEmail");var output=Refused(()=>engine.Execute("functions",RunFunction()),"agreement");Check(output&&!adb.Commands.Any(c=>c.Contains("execute-app-function")),"unconfirmed send ran");});
Test("outward agreement is bound to params and consumed once",()=>
{
    SetFunction("readName","SmsFunctions_send_message");var args=RunFunction(true);string token="";
    try{engine.Execute("functions",args);}catch(InvalidOperationException e){token=Regex.Match(e.Message,@"accord=([a-f0-9]{48})").Groups[1].Value;}
    Check(token.Length==48,"proposal missing");var changed=args.DeepClone().AsObject();changed["params"]!["body"]="changed fixture body";changed["accord"]=token;Check(Refused(()=>engine.Execute("functions",changed),"agreement"),"changed body accepted");Check(!adb.Commands.Any(c=>c.Contains("execute-app-function")),"changed body sent");
    // A rejected changed action consumes its token; acquire a fresh proposal for the original payload.
    try{engine.Execute("functions",args);}catch(InvalidOperationException e){token=Regex.Match(e.Message,@"accord=([a-f0-9]{48})").Groups[1].Value;}
    args["accord"]=token;engine.Execute("functions",args);Check(adb.Commands.Count(c=>c.Contains("execute-app-function"))==1,"agreed exact payload absent");Check(Refused(()=>engine.Execute("functions",args),"agreement"),"agreement replay accepted");Check(adb.Commands.Count(c=>c.Contains("execute-app-function"))==1,"agreement replay executed");
});
Test("ordinary read with Call noun and reversible changes still work",()=>{SetFunction("findCallRecords","PhoneFunctions_findCallRecords");var args=RunFunction();args["function"]="findCallRecords";engine.Execute("functions",args);Check(adb.Commands.Any(c=>c.Contains("execute-app-function")),"read blocked");SetFunction("createNote","NotesFunctions_createNote");args["function"]="createNote";engine.Execute("functions",args);Check(adb.Commands.Any(c=>c.Contains("execute-app-function")),"reversible creation blocked");});
Test("disabled function false survives JSON bool formatting",()=>{SetFunction("readName","readName",false);Check(Refused(()=>engine.Execute("functions",RunFunction()),"disabled"),"disabled ran");Check(!adb.Commands.Any(c=>c.Contains("execute-app-function")),"disabled executed");});
Test("multiline code masking keeps later ordinary years",()=>{var cleaned=(string)Private("CleanAdvancedText","Votre code :\n\n482913\nsoit 1889 en tout",false)!;Check(!cleaned.Contains("482913")&&cleaned.Contains("1889"),"code context or year lost");});
Test("protected host bank/payment/tax/identity and checkout rules with ordinary browsing allowed",()=>
{
    foreach(var address in new[]{"https://mabanque.example/","https://checkout.stripe.com/","https://franceconnect.gouv.fr/","https://impots.gouv.fr/","https://www.amazon.fr/ap/signin","https://www.amazon.com/gp/buy/x","https://www.amazon.fr/checkout",""})Check((bool)Private("ProtectedSite",address)!,"protected address accepted");
    foreach(var address in new[]{"https://example.org/article","https://www.amazon.fr/product/book","about:blank"})Check(!(bool)Private("ProtectedSite",address)!,"ordinary address blocked");
});
Test("Web refuses a protected catalog address before WebSocket/evaluation",()=>
{
    using var page=new DevToolsFixture("https://paypal.com/account","https://paypal.com/account","fixture must stay private");adb.Port=page.Port;Check(Refused(()=>engine.Execute("web",new()),"before DevTools"),"protected catalog read");Check(page.Evaluations==0,"protected script executed");Check(adb.Commands.Count(c=>c.StartsWith("forward tcp:0"))==adb.Commands.Count(c=>c.StartsWith("forward --remove")),"forward leaked");adb.Port=0;
});
Test("Web rechecks actual navigation and returns no protected content",()=>
{
    using var page=new DevToolsFixture("https://example.org/article","https://franceconnect.gouv.fr/","fixture protected destination");adb.Port=page.Port;Check(Refused(()=>engine.Execute("web",new()),"nothing returned"),"protected redirect returned");Check(page.Evaluations==1,"read fixture did not exercise CDP");adb.Port=0;
});
Test("Web query runs after masking and cannot reveal masked-line membership",()=>
{
    using var page=new DevToolsFixture("https://example.org/article","https://example.org/article","Contact fixtureperson@example.org\nVotre code :\n482913\nPublic value 1889");adb.Port=page.Port;
    foreach(var secret in new[]{"fixtureperson","482913"})Check(Text(engine.Execute("web",new JsonObject{["query"]=secret})).Length==0,"masked query inferred secret line");
    Check(Text(engine.Execute("web",new JsonObject{["query"]="1889"})).Contains("1889"),"public query broken");
    var all=Text(engine.Execute("web",new()));Check(!all.Contains("fixtureperson")&&!all.Contains("482913")&&all.Contains("https://example.org/next")&&!all.Contains("javascript:"),"read output did not mask/filter links");adb.Port=0;
});
Test("Web unidentified actual URL and evaluation exception are errors",()=>{using var page=new DevToolsFixture("https://example.org/article","","fixture absent URL");adb.Port=page.Port;Check(Refused(()=>engine.Execute("web",new()),"nothing returned"),"missing actual URL returned");page.Exception=true;Check(Refused(()=>engine.Execute("web",new()),"refused"),"CDP exception hidden");adb.Port=0;});

Test("map note refuses protected foreground and hidden protected overlay without writing",()=>
{
    var original=phone.Xml;phone.Package="com.revolut.revolut";Check(Refused(()=>engine.Execute("map",new JsonObject{["action"]="note",["path"]="Fixture"}),"nothing noted"),"protected note accepted");phone.Package="com.example.app";
    phone.Xml=original.Replace("</hierarchy>","<node package='com.x8bit.bitwarden' text='' bounds='[0,0][1,1]'/></hierarchy>");Check(Refused(()=>engine.Execute("map",new JsonObject{["action"]="note",["path"]="Fixture"}),"overlay"),"non-informative overlay ignored");phone.Xml=original;Check(!File.Exists(Path.Join(DeviceRoot(),"screens.json")),"unsafe screen persisted");
});
Test("map note refuses lock unknown/locked and disagreement between activity and tree",()=>
{
    phone.Lock="true";Check(Refused(()=>engine.Execute("map",new JsonObject{["action"]="note",["path"]="Fixture"}),"Locked"),"locked recorded");phone.Lock="unknown";Check(Refused(()=>engine.Execute("map",new JsonObject{["action"]="note",["path"]="Fixture"}),"unknown"),"unknown lock recorded");phone.Lock="false";
    adb.Activity="topResumedActivity=ActivityRecord{x u0 com.other.app/.Main t1}";Check(Refused(()=>engine.Execute("map",new JsonObject{["action"]="note",["path"]="Fixture"}),"Foreground changed"),"mismatched activity recorded");adb.Activity="";
});
Test("map note refuses changed screen between XML and final read",()=>{phone.ChangeOnSecondRead=true;phone.Reads=0;Check(Refused(()=>engine.Execute("map",new JsonObject{["action"]="note",["path"]="Fixture"}),"Screen changed"),"changed screen saved");phone.ChangeOnSecondRead=false;});
Test("map note stores real state, excludes passwords, and append deduplicates",()=>
{
    engine.Execute("map",new JsonObject{["action"]="note",["path"]=" Settings  >  Fixture "});engine.Execute("map",new JsonObject{["action"]="note",["path"]="Settings > Fixture",["append"]=true});
    var screens=JsonNode.Parse(File.ReadAllText(Path.Join(DeviceRoot(),"screens.json")))!.AsArray();var items=screens[0]!["items"]!.AsArray();Check(items.Count==1&&items[0]!["state"]?.ToString()=="on","state missing or duplicate");Check(!screens.ToJsonString().Contains("SyntheticPassword"),"password persisted");
});
Test("map device namespace is stable across transport and separate for different device",()=>
{
    var first=DeviceRoot();adb.Serial="192.0.2.44:5555";engine.Execute("map",new JsonObject{["action"]="note",["path"]="Transport"});Check(DeviceRoot()==first,"transport split identity");adb.Identity="fixture-device-two";engine.Execute("map",new JsonObject{["action"]="note",["path"]="Other device"});var second=JsonNode.Parse(File.ReadAllText(Path.Join(DeviceRoot(),"screens.json")))!.AsArray();Check(second.Count==1&&second[0]!["path"]?.ToString()=="Other device","devices mixed");adb.Identity="fixture-device-one";adb.Serial="fixture-usb";
});
Test("map find searches noted item labels and current settings keys without mutation",()=>
{
    SeedIndex();adb.System="notification_badging=1\nindependent_setting=enabled\n";var found=Text(engine.Execute("map",new JsonObject{["query"]="notifications"}));Check(found.Contains("Notifications")&&found.Contains("now: system/notification_badging=1")&&!found.Contains("{\""),"APK or live state missing, or raw JSON instead of readable lines");
    Check(Text(engine.Execute("map",new JsonObject{["query"]="Bluetooth"})).Contains("[on]"),"noted item state not searched");Check(Text(engine.Execute("map",new JsonObject{["query"]="independent setting"})).Contains("independent_setting"),"raw setting key lost");
});
Test("map open cannot launch a protected target saved before the migration",()=>
{
    var path=Path.Join(DeviceRoot(),"screens.json");var rows=JsonNode.Parse(File.ReadAllText(path))!.AsArray();rows.Add(new JsonObject{["path"]="Legacy",["component"]="com.revolut.revolut/.Main",["items"]=new JsonArray()});File.WriteAllText(path,rows.ToJsonString());adb.Commands.Clear();Check(Refused(()=>engine.Execute("map",new JsonObject{["action"]="open",["target"]="com.revolut.revolut/.Main"}),"Protected"),"legacy protected launched");Check(!adb.Commands.Any(c=>c.StartsWith("'am' 'start'")),"protected launch sent");
});
Test("map open uses guarded launch component/highlight and respects STOP",()=>
{
    adb.Commands.Clear();engine.Execute("map",new JsonObject{["action"]="open",["target"]="com.android.settings/.Settings",["key"]="notification_badging"});var start=adb.Commands.Single(c=>c.StartsWith("'am' 'start'"));Check(start.Contains("'-n' 'com.android.settings/.Settings'")&&start.Contains("':settings:fragment_args_key' 'notification_badging'"),"component/highlight lost");Check(adb.Commands.Any(c=>c.Contains("resolve-activity"))&&adb.Commands.Any(c=>c.Contains("lastUserActivityTime")),"launch guard/presence bypassed");
    using var cancel=new CancellationTokenSource();cancel.Cancel();NativeSafety.Cancellation.Value=cancel.Token;adb.Commands.Clear();try{engine.Execute("map",new JsonObject{["action"]="open",["target"]="com.android.settings/.Settings"});throw new Exception("cancel ignored");}catch(OperationCanceledException){/* expected: the cancelled call must stop */}finally{NativeSafety.Cancellation.Value=default;}Check(!adb.Commands.Any(c=>c.StartsWith("'am' 'start'")),"STOP sent launch");
});
Test("state masking covers IPv4 ports, bracketed/scoped IPv6, MAC and identity keys",()=>
{
    foreach(var value in new[]{"192.0.2.1:443","http://192.0.2.1:8080/x","gw:192.0.2.1","[2001:db8::7]:443","fe80::1%wlan0","::1","AA:BB:CC:DD:EE:FF"})Check(((string)Private("MaskStateValue","ordinary_setting",value)!).StartsWith("[masked]#"),"address leaked: "+value);
    foreach(var key in new[]{"advertising_id","phone_number","bluetooth_name","owner_info","line1","account","gaia","android_id"})Check(((string)Private("MaskStateValue",key,"SyntheticIdentity")!).StartsWith("[masked]#"),"identity key not masked: "+key);
    Check((string)Private("MaskStateValue","screen_brightness","128")! == "128","ordinary numeric state overmasked");
});
Test("snapshots retain permission grants and diff removals across values/apps/roles/devices",()=>
{
    adb.System="screen_brightness=128\nremoved_setting=on\nidentity=192.0.2.1:443";adb.Packages="package:com.example.app versionCode:1 installer=com.android.vending uid:10001\npackage:com.removed.app versionCode:2 installer=com.android.vending";adb.PermissionDump="  Package [com.example.app] (x):\n    android.permission.CAMERA: granted=true\n";adb.Bluetooth="    AA:BB:CC:DD:EE:FF [bonded] Fixture headset";
    engine.Execute("map",Args("snapshot"));var folder=Path.Join(DeviceRoot(),"snapshots");var before=JsonNode.Parse(File.ReadAllText(Directory.GetFiles(folder,"*.json").Single()))!.AsObject();Check(before["permissions"]?["CAMERA"]?[0]?.ToString()=="com.example.app","permission missing");Check(!before.ToJsonString().Contains("192.0.2.1")&&!before.ToJsonString().Contains("AA:BB"),"snapshot leaked network identity");
    adb.System="screen_brightness=130";adb.Packages="package:com.example.app versionCode:3 installer=com.android.vending";adb.PermissionDump="";adb.Bluetooth="";var diff=Text(engine.Execute("map",Args("diff")));Check(diff.Contains("removed_setting")&&diff.Contains("[removed]")&&diff.Contains("com.removed.app")&&diff.Contains("withdrawn from")&&diff.Contains("bluetooth removed"),"diff removals missing");
});
Test("snapshot since selects the requested boundary and earliest fallback, retention is bounded",()=>
{
    var folder=Path.Join(DeviceRoot(),"snapshots");foreach(var file in Directory.GetFiles(folder,"*.json"))File.Delete(file);
    var now=DateTimeOffset.Now;for(var i=0;i<32;i++)File.WriteAllText(Path.Join(folder,$"fixture-{i:D2}.json"),new JsonObject{["at"]=now.AddHours(-32+i).ToString("O"),["settings"]=new JsonObject{["system"]=new JsonObject{["screen_brightness"]=i.ToString()}}}.ToJsonString());
    var result=Text(engine.Execute("map",new JsonObject{["action"]="diff",["since"]="2h"}));Check(result.Contains("screen_brightness: 30 → 130"),"wrong since boundary");Check(Directory.GetFiles(folder,"*.json").Length==30,"retention unbounded");
    Check((DateTimeOffset)Private("MapCutoff","13:05",new DateTimeOffset(2026,10,29,14,0,0,TimeSpan.FromHours(1)))! == new DateTimeOffset(2026,10,29,13,5,0,TimeSpan.FromHours(1)),"time-of-day since broken");
});
    }
    private static string Hash(string value)=>Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value)));
}


sealed class FixturePhone:IAndroidQuery
{
    public string Package="com.example.app",Lock="false";public int Reads;public bool ChangeOnSecondRead;
    public string Xml="<hierarchy><node package='com.example.app' class='android.widget.TextView' text='Bluetooth' bounds='[0,100][200,160]'/><node package='com.example.app' class='android.widget.Switch' text='' checked='true' clickable='true' bounds='[220,100][280,160]'/><node package='com.example.app' class='android.widget.EditText' text='SyntheticPassword' password='true' bounds='[0,200][300,250]'/></hierarchy>";
    public NativeScreen Screen(bool image){Reads++;return new("mCurrentFocus=Window{x u0 "+Package+"/.Main}\nmWakefulness=Awake\nisKeyguardShowing="+Lock,ChangeOnSecondRead&&Reads>=2?Xml.Replace("Bluetooth","Changed"):Xml,1080,1920,null);}
    public List<Dictionary<string,string>> Query(string uri,string projection="",string where="",string sort="")=>[];
}
sealed class FixtureAdb(FixturePhone phone):AdbClient("offline-fixture-never-started.exe")
{
    public readonly List<string> Commands=[];public string Identity="fixture-device-one",Serial="fixture-usb",Catalog="{}",System="",Packages="package:com.example.app",PermissionDump="",Bluetooth="",Activity="";public int Port;
    public override List<DeviceInfo> GetDevices()=>[new(Serial,"device","Fixture",!Serial.Contains(':'))];
    public override bool EnsureDevice()=>PickDevice()?.State=="device";
    public override DeviceInfo? PickDevice(){SelectedSerial=Serial;return GetDevices()[0];}
    public override string Shell(string command,int timeoutMs=15000)
    {
        Commands.Add(command);if(command.Contains("ro.serialno"))return Identity;if(command.Contains("persist.sys.locale"))return "fr-FR";if(command.Contains("'dumpsys' 'package' 'com.android.settings'"))return "versionCode=7\nlastUpdateTime=fixture";
        if(command.Contains("'pm' 'path' 'com.android.settings'"))return "package:/fixture/settings.apk";if(command.Contains("'stat' '-c'"))return "12345:fixture";if(command.Contains("ro.build.fingerprint"))return "fixture/build";
        if(command.Contains("list-app-functions"))return Catalog;if(command.Contains("execute-app-function"))return "answer";
        if(command.Contains("'/proc/net/unix'"))return Port>0?"0 0 0 @chrome_devtools_remote\n":"";
        if(command.Contains("'dumpsys' 'activity' 'activities'"))return Activity.Length>0?Activity:"topResumedActivity=ActivityRecord{x u0 "+phone.Package+"/.Main t1}";
        if(command.Contains("'settings' 'list' 'system'"))return System;
        if(command.Contains("'pm' 'list' 'packages'"))return command.Contains("'-d'")||command.Contains("'-3'")?"":Packages+"\npackage:com.android.settings";
        if(command.Contains("'dumpsys' 'package' 'packages'"))return PermissionDump;if(command.Contains("'dumpsys' 'bluetooth_manager'"))return Bluetooth;
        if(command.Contains("resolve-activity")||command.Contains("query-activities"))return "com.android.settings/.Settings";
        if(command.StartsWith("'am' 'start'")){phone.Package="com.android.settings";return "Status: ok";}
        if(command.Contains("lastUserActivityTime"))return "lastUserActivityTime=12345 (5000 ms ago)";
        return "";
    }
    public override (string Stdout,string Stderr) RunAdb(string arguments,int timeoutMs=15000){Commands.Add(arguments);return arguments.StartsWith("forward tcp:0")?(Port.ToString(),""):("","");}
}
sealed class DevToolsFixture:IDisposable
{
    private readonly HttpListener listener=new();private readonly CancellationTokenSource stop=new();private readonly Task server;public int Port{get;}public int Evaluations;public bool Exception;
    public DevToolsFixture(string catalogUrl,string actualUrl,string text)
    {
        using var ephemeral=new TcpListener(IPAddress.Loopback,0);ephemeral.Start();Port=((IPEndPoint)ephemeral.LocalEndpoint).Port;ephemeral.Stop();listener.Prefixes.Add($"http://127.0.0.1:{Port}/");listener.Start();
        server=Task.Run(async()=>{while(!stop.IsCancellationRequested){HttpListenerContext context;try{context=await listener.GetContextAsync().WaitAsync(stop.Token);}catch(Exception e)when(e is OperationCanceledException or HttpListenerException or ObjectDisposedException){break;}
            if(!context.Request.IsWebSocketRequest){var bytes=Encoding.UTF8.GetBytes(new JsonArray(new JsonObject{["type"]="page",["title"]="Fixture article",["url"]=catalogUrl,["webSocketDebuggerUrl"]=$"ws://127.0.0.1:{Port}/devtools/page/fixture"}).ToJsonString());context.Response.ContentType="application/json";await context.Response.OutputStream.WriteAsync(bytes);context.Response.Close();continue;}
            using var socket=(await context.AcceptWebSocketAsync(null)).WebSocket;var input=new byte[65536];await socket.ReceiveAsync(input,stop.Token);Interlocked.Increment(ref Evaluations);
            var value=new JsonObject{["title"]="Fixture article",["url"]=actualUrl,["text"]=text,["links"]=new JsonArray(new JsonArray("Next","https://example.org/next"),new JsonArray("Unsafe","javascript:alert(1)"))};
            var result=Exception?new JsonObject{["id"]=1,["result"]=new JsonObject{["exceptionDetails"]=new JsonObject{["text"]="refused"}}}:new JsonObject{["id"]=1,["result"]=new JsonObject{["result"]=new JsonObject{["value"]=value}}};
            await socket.SendAsync(Encoding.UTF8.GetBytes(result.ToJsonString()),WebSocketMessageType.Text,true,stop.Token);
            // Like Chrome, the page keeps the connection until the reader ends it. Aborting right after the reply (the
            // former `using` disposal) reset the TCP connection, and Windows then discards a reply the engine had not
            // read yet: on a slow runner the engine saw "connection closed" instead of the page's answer (CI, 30/10).
            try{while((await socket.ReceiveAsync(input,stop.Token)).MessageType!=WebSocketMessageType.Close){}}catch(Exception e)when(e is WebSocketException or OperationCanceledException or HttpListenerException){/* reader closed or fixture stopping */}
        }});
    }
    public void Dispose(){stop.Cancel();listener.Stop();try{server.GetAwaiter().GetResult();}catch(OperationCanceledException){/* fixture stopped */}listener.Close();stop.Dispose();}
}
