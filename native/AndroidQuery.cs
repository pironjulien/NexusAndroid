using System.Buffers.Binary;
using System.Net.Sockets;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Xml.Linq;

namespace NexusAndroid;

public sealed class NetworkReader(byte[] data)
{
    private int pos;
    public int Number() { if (pos + 4 > data.Length) throw new IOException("Truncated phone response"); var v = BinaryPrimitives.ReadInt32BigEndian(data.AsSpan(pos,4)); pos += 4; return v; }
    public byte[] Bytes(int n) { if (n < 0 || n > 64 * 1024 * 1024 || pos + n > data.Length) throw new IOException($"Invalid phone response size (offset={pos}, requested={n}, available={data.Length})"); var b = data.AsSpan(pos,n).ToArray(); pos += n; return b; }
    public string Text() { var n=Number(); return n == -1 ? "" : Encoding.UTF8.GetString(Bytes(n)); }
}

public sealed record NativeScreen(string State, string Xml, int Width, int Height, byte[]? Image, string Error = "");

public interface IAndroidQuery
{
    NativeScreen Screen(bool image);
    List<Dictionary<string,string>> Query(string uri,string projection="",string where="",string sort="");
}
public sealed class AndroidQuery(AdbClient adb) : IAndroidQuery
{
    private int port; private string token = ""; private string serial = "";
    private readonly byte[] dex = ReadDex();
    private static byte[] ReadDex() { using var stream=Assembly.GetExecutingAssembly().GetManifestResourceStream("nexus-query.dex")!; using var ms=new MemoryStream(); stream.CopyTo(ms); return ms.ToArray(); }
    private string Prefix => "nexus-query-" + Convert.ToHexString(SHA256.HashData(dex)).ToLowerInvariant()[..12];
    private static void Int(Stream stream,int value) { Span<byte> buf=stackalloc byte[4];BinaryPrimitives.WriteInt32BigEndian(buf,value);stream.Write(buf); }
    private static void Text(Stream stream,string value) { var buf=Encoding.UTF8.GetBytes(value);Int(stream,buf.Length);stream.Write(buf); }
    private byte[] Request(params string[][] queries)
    {
        using var client=new TcpClient(); client.ConnectAsync("127.0.0.1",port).Wait(TimeSpan.FromSeconds(3)); client.ReceiveTimeout=10000;client.SendTimeout=10000;
        using var stream=client.GetStream(); using var request=new MemoryStream(); request.Write("NXQ2"u8);Int(request,queries.Length);
        foreach(var query in queries) { Int(request,query.Length);foreach(var arg in query)Text(request,arg); }
        stream.Write(request.ToArray());using var response=new MemoryStream();stream.CopyTo(response);
        var reader=new NetworkReader(response.ToArray()); if (Encoding.ASCII.GetString(reader.Bytes(4))!="NXQ2" || reader.Text()!=token)throw new IOException("Wrong phone helper identity");
        return reader.Bytes((int)(response.Length - 8 - Encoding.UTF8.GetByteCount(token)));
    }
    private void Ensure()
    {
        // Execute already verified the selected hardware while holding PhoneLease.
        // The existing helper's random token proves its bound device on every RPC.
        if(port!=0 && serial==adb.SelectedSerial){try{Request();return;}catch(Exception e)when(e is IOException or SocketException){port=0;}}
        var device=adb.PickDevice(); if(device?.State!="device")throw new IOException("No authorized Android device connected");
        serial=device.Serial;
        var listing=adb.RunAdb("forward --list").Stdout;
        var forwards=Regex.Matches(listing,Regex.Escape(serial)+@" tcp:(\d+) localabstract:"+Prefix+@"-([0-9a-f]{32})").Reverse();
        foreach(var found in forwards){port=int.Parse(found.Groups[1].Value);token=found.Groups[2].Value;try{Request();return;}catch(IOException){/* stale forward: try the next one */}catch(SocketException){/* stale forward: try the next one */} }
        var local=Path.Join(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"NexusAndroid","tools",Prefix+".dex");Directory.CreateDirectory(Path.GetDirectoryName(local)!);if(!File.Exists(local))File.WriteAllBytes(local,dex);
        var remote="/data/local/tmp/"+Prefix+".dex";adb.RunAdb($"push \"{local}\" {remote}");
        token=Convert.ToHexString(RandomNumberGenerator.GetBytes(16)).ToLowerInvariant();var name=Prefix+"-"+token;
        adb.Shell("sh -c "+NativeSafety.Quote($"CLASSPATH={remote} setsid nohup app_process / NexusQuery {name} 600 {token} </dev/null >/dev/null 2>&1 &"));
        port=int.Parse(adb.RunAdb("forward tcp:0 localabstract:"+name).Stdout.Trim());
        var deadline=DateTime.UtcNow.AddSeconds(5);
        while(true){try{Request();break;}catch(Exception e)when(e is IOException or SocketException){if(DateTime.UtcNow>=deadline)throw;Thread.Sleep(80);}}
    }
    public NativeScreen Screen(bool image)
    {
        Ensure(); var r=new NetworkReader(Request(["screen","--image",image?"full":"none","--format","png","--quiet","0"]));
        if(r.Number()!=2)throw new IOException("Phone screen helper refused: "+r.Text());var s=new NetworkReader(r.Bytes(r.Number()));
        var state=s.Text();s.Number();var width=s.Number();var height=s.Number();var kind=s.Number();var error="";var root=new XElement("hierarchy");
        if(kind==0)
        {
            var count=s.Number();if(count<0||count>20000)throw new IOException("Invalid node count");
            string[] flags=["checkable","checked","clickable","enabled","focusable","focused","scrollable","long-clickable","password","selected","editable","showing-hint"];
            var ancestry=new Stack<(XElement Element,int Depth)>();ancestry.Push((root,-1));
            for(var i=0;i<count;i++)
            {
                var node=new XElement("node",new XAttribute("text",s.Text()),new XAttribute("resource-id",s.Text()),new XAttribute("class",s.Text()),new XAttribute("package",s.Text()),new XAttribute("content-desc",s.Text()));
                var bits=s.Number();var x1=s.Number();var y1=s.Number();var x2=s.Number();var y2=s.Number();var depth=s.Number();var order=s.Number();node.SetAttributeValue("bounds",$"[{x1},{y1}][{x2},{y2}]");node.SetAttributeValue("drawing-order",order);
                for(var f=0;f<flags.Length;f++){node.SetAttributeValue(flags[f],(bits&(1<<f))!=0?"true":"false");}while(ancestry.Peek().Depth>=depth){ancestry.Pop();}ancestry.Peek().Element.Add(node);ancestry.Push((node,depth));
            }
        }
        else error=s.Text();
        byte[]? picture=null;kind=s.Number();if(kind==0){s.Number();s.Number();picture=s.Bytes(s.Number());}else if(kind==1)error+=" "+s.Text();
        if(image)
        {
            picture??=adb.CaptureScreenPng();var decoded=ImageChain.Decode(picture);if(decoded.Width!=width||decoded.Height!=height)throw new IOException("Captured display geometry does not match the observed UI; no image returned");var after=Screen(false);if(after.Xml!=root.ToString()||after.Width!=width||after.Height!=height||after.State!=state)throw new IOException("Screen changed during screenshot; image not returned because redaction bounds could be stale");
        }
        return new(state,root.ToString(),width,height,picture,error);
    }
    public List<Dictionary<string,string>> Query(string uri,string projection="",string where="",string sort="")
    {
        Ensure();var args=new List<string>{"query","--uri",uri};if(projection!=""){args.AddRange(["--projection",projection]);}if(where!=""){args.AddRange(["--where",where]);}if(sort!="")args.AddRange(["--sort",sort]);
        var reader=new NetworkReader(Request(args.ToArray()));var status=reader.Number();if(status!=0)throw new IOException("Phone content provider: "+NativeSafety.Mask(reader.Text()));
        var width=reader.Number();if(width<0||width>512)throw new IOException("Invalid table width");var names=Enumerable.Range(0,width).Select(_=>reader.Text()).ToArray();var count=reader.Number();if(count<0||count>1000000)throw new IOException("Invalid table height");var rows=new List<Dictionary<string,string>>();
        for(var i=0;i<count;i++){var row=new Dictionary<string,string>();foreach(var name in names){row[name]=reader.Text();}rows.Add(row);}return rows;
    }
}
