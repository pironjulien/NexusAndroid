using System.Buffers.Binary;
using System.Diagnostics;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;

namespace NexusAndroid;

public interface IPhoneControl:IDisposable
{
    string Get();void Set(string text,bool paste);void Zoom(int x,int y,int width,int height,bool inward,int amount);
}
public sealed class ScrcpyChannel : IPhoneControl
{
    private readonly AdbClient adb; private readonly Process server; private readonly TcpClient client; private readonly NetworkStream stream; private readonly string port;private long sequence=1;
    public ScrcpyChannel(AdbClient adb)
    {
        this.adb=adb;NativeSafety.StopGuard();var jar=Path.Join(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),".nexus","nexus-android","tools","scrcpy-server-v5.0.1");
        if(!File.Exists(jar)||Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(jar)))!="764EB6F79811D5211FE9DF341120882BA9994C7A61B897D7BF3FB662E53BC536")throw new IOException("Verified scrcpy-server v5.0.1 missing; run native install.ps1 to install the pinned dependency");
        adb.PickDevice();adb.RunAdb($"push \"{jar}\" /data/local/tmp/nexus-scrcpy.jar");var scid=RandomNumberGenerator.GetInt32(1,int.MaxValue).ToString("x8");port=adb.RunAdb("forward tcp:0 localabstract:scrcpy_"+scid).Stdout.Trim();
        var psi=new ProcessStartInfo(adb.AdbPath){UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true};
        foreach(var arg in new[]{"-s",adb.SelectedSerial!,"shell",$"CLASSPATH=/data/local/tmp/nexus-scrcpy.jar app_process / com.genymobile.scrcpy.Server 5.0.1 scid={scid} log_level=warn tunnel_forward=true video=false audio=false control=true send_device_meta=false send_dummy_byte=true clipboard_autosync=false cleanup=false"})psi.ArgumentList.Add(arg);
        server=Process.Start(psi)!;client=new TcpClient();var deadline=DateTime.UtcNow.AddSeconds(8);
        while(true)
        {
            try{client.Connect("127.0.0.1",int.Parse(port));client.ReceiveTimeout=3000;client.SendTimeout=5000;stream=client.GetStream();if(stream.ReadByte()!=0)throw new IOException("Wrong scrcpy handshake");break;}
            catch(Exception e)when(e is SocketException or IOException){client.Dispose();client=new TcpClient();if(server.HasExited||DateTime.UtcNow>deadline){server.Kill(true);throw new IOException("scrcpy channel unavailable before input",e);}Thread.Sleep(80);}
        }
    }
    private byte[] Read(int n){if(n<0||n>256*1024)throw new IOException("Invalid scrcpy reply size");var b=new byte[n];stream.ReadExactly(b);return b;}
    public string Get()
    {
        stream.Write([8,0]);client.ReceiveTimeout=1500;
        int type;
        // scrcpy sends no packet for a null/non-text clipboard. Only absence of the FIRST
        // byte has this protocol meaning; a timeout in a started packet is a transport error.
        try{type=stream.ReadByte();}
        catch(IOException e)when(e.InnerException is SocketException {SocketErrorCode:SocketError.TimedOut}){if(server.HasExited)throw new IOException("Clipboard server stopped before replying",e);return "";}
        finally{client.ReceiveTimeout=5000;}
        if(type!=0)throw new IOException("Wrong or closed clipboard reply");var n=BinaryPrimitives.ReadInt32BigEndian(Read(4));return Encoding.UTF8.GetString(Read(n));
    }
    public void Set(string text,bool paste)
    {
        var data=Encoding.UTF8.GetBytes(text);if(data.Length>256*1024-14)throw new ArgumentException("Clipboard text exceeds 256KB");var message=new byte[14+data.Length];message[0]=9;var seq=sequence++;BinaryPrimitives.WriteInt64BigEndian(message.AsSpan(1,8),seq);message[9]=paste?(byte)1:(byte)0;BinaryPrimitives.WriteInt32BigEndian(message.AsSpan(10,4),data.Length);data.CopyTo(message,14);stream.Write(message);
        while(true){var type=stream.ReadByte();if(type==1){if(BinaryPrimitives.ReadInt64BigEndian(Read(8))==seq)return;}else if(type==0)Read(BinaryPrimitives.ReadInt32BigEndian(Read(4)));else throw new IOException("Clipboard acknowledgement missing; effect unknown, never replay input");}
    }
    private void Touch(byte action,long pointer,int x,int y,int w,int h)
    {
        var b=new byte[32];b[0]=2;b[1]=action;BinaryPrimitives.WriteInt64BigEndian(b.AsSpan(2,8),pointer);BinaryPrimitives.WriteInt32BigEndian(b.AsSpan(10,4),x);BinaryPrimitives.WriteInt32BigEndian(b.AsSpan(14,4),y);BinaryPrimitives.WriteUInt16BigEndian(b.AsSpan(18,2),(ushort)w);BinaryPrimitives.WriteUInt16BigEndian(b.AsSpan(20,2),(ushort)h);BinaryPrimitives.WriteUInt16BigEndian(b.AsSpan(22,2),action==1?(ushort)0:ushort.MaxValue);stream.Write(b);
    }
    public static ((int X,int Y) First,(int X,int Y) Second,int From,int To,bool Horizontal) ZoomGeometry(int x,int y,int w,int h,bool inward,int amount)
    {
        if(x<w*.06||x>w*.94||y<h*.1||y>h*.9)throw new ArgumentException("Zoom center must be inside content");var roomV=Math.Min(y-h*.1,h*.9-y);var roomH=Math.Min(x-w*.06,w*.94-x);var horizontal=roomH>=roomV*.5;var room=(int)(horizontal?roomH:roomV);var far=Math.Min(room*2,Math.Min(w,h)*(2+Math.Clamp(amount,1,3))/8);var near=Math.Max(40,far/5);if(far<=near)throw new ArgumentException("Not enough content room for zoom");var from=inward?near:far;var to=inward?far:near;return(horizontal?(x-from/2,y):(x,y-from/2),horizontal?(x+from/2,y):(x,y+from/2),from,to,horizontal);
    }
    public void Zoom(int x,int y,int w,int h,bool inward,int amount)
    {
        var geometry=ZoomGeometry(x,y,w,h,inward,amount);var from=geometry.From;var to=geometry.To;var horizontal=geometry.Horizontal;
        var gap=from;var first=false;var second=false;
        try{Touch(0,1,geometry.First.X,geometry.First.Y,w,h);first=true;Touch(0,2,geometry.Second.X,geometry.Second.Y,w,h);second=true;for(var i=1;i<=50;i++){NativeSafety.StopGuard();gap=from+(to-from)*i/50;Touch(2,1,horizontal?x-gap/2:x,horizontal?y:y-gap/2,w,h);Touch(2,2,horizontal?x+gap/2:x,horizontal?y:y+gap/2,w,h);Thread.Sleep(8);}}
        finally{try{if(second)Touch(1,2,horizontal?x+gap/2:x,horizontal?y:y+gap/2,w,h);}finally{if(first)Touch(1,1,horizontal?x-gap/2:x,horizontal?y:y-gap/2,w,h);}}
    }
    public void Dispose(){stream.Dispose();client.Dispose();if(!server.WaitForExit(1000)){server.Kill(true);}server.Dispose();try{adb.RunAdb("forward --remove tcp:"+port);}catch(IOException){/* best-effort: forward already gone */}catch(InvalidOperationException){/* best-effort: device already gone */} }
}
