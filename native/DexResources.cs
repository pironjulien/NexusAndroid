using System.Buffers.Binary;
using System.Text;

namespace NexusAndroid;

public static class DexResources
{
    private static uint U32(byte[] d,int p)=>BinaryPrimitives.ReadUInt32LittleEndian(d.AsSpan(p,4));
    private static ushort U16(byte[] d,int p)=>BinaryPrimitives.ReadUInt16LittleEndian(d.AsSpan(p,2));
    private static int Leb(byte[] d,ref int p){var value=0;for(var shift=0;shift<35;shift+=7){var n=d[p++];value|=(n&127)<<shift;if(n<128)return value;}throw new IOException("Invalid DEX LEB128");}
    private static int[] Widths()
    {
        var sizes=Enumerable.Repeat(1,256).ToArray();int[] two=[0x02,0x05,0x08,0x13,0x15,0x16,0x19,0x1a,0x1c,0x1f,0x20,0x22,0x23,0x29,0xfe,0xff];int[] three=[0x03,0x06,0x09,0x14,0x17,0x1b,0x24,0x25,0x26,0x2a,0x2b,0x2c,0xfc,0xfd];foreach(var op in two)sizes[op]=2;foreach(var op in three)sizes[op]=3;foreach(var op in Enumerable.Range(0x2d,0x6e-0x2d).Concat(Enumerable.Range(0x90,0xb0-0x90)).Concat(Enumerable.Range(0xd0,0xe3-0xd0)))sizes[op]=2;foreach(var op in Enumerable.Range(0x6e,0x74-0x6e).Concat(Enumerable.Range(0x74,0x79-0x74)))sizes[op]=3;sizes[0x18]=5;sizes[0xfa]=sizes[0xfb]=4;return sizes;
    }
    public static Dictionary<string,List<(string Method,uint Resource)>> Constants(byte[] d,HashSet<string> requested)
    {
        if(d.Length<0x70||Encoding.ASCII.GetString(d,0,4)!="dex\n")throw new IOException("Invalid Settings DEX");var stringOff=(int)U32(d,0x3c);var typeOff=(int)U32(d,0x44);var methodOff=(int)U32(d,0x5c);var classCount=(int)U32(d,0x60);var classOff=(int)U32(d,0x64);if(classCount<0||classCount>200000||classOff+32L*classCount>d.Length)throw new IOException("DEX class table out of range");var strings=new Dictionary<int,string>();var widths=Widths();var found=new Dictionary<string,List<(string,uint)>>();
        string String(int id){if(strings.TryGetValue(id,out var cached))return cached;var p=(int)U32(d,stringOff+4*id);Leb(d,ref p);var end=Array.IndexOf(d,(byte)0,p,Math.Min(65536,d.Length-p));if(end<0)throw new IOException("Unterminated DEX string");return strings[id]=Encoding.UTF8.GetString(d,p,end-p);}
        string Type(int id){var name=String((int)U32(d,typeOff+4*id));return name.StartsWith('L')?name[1..^1].Replace('/','.'):name;}
        IEnumerable<uint> Code(int code)
        {
            var size=(int)U32(d,code+12);if(size<0||code+16L+size*2>d.Length)throw new IOException("DEX code out of range");var p=0;
            while(p<size)
            {
                var instruction=U16(d,code+16+2*p);var op=instruction&255;
                if(op==0x14&&p+2<size){var value=(uint)(U16(d,code+16+2*(p+1))|U16(d,code+16+2*(p+2))<<16);if(value>>24==0x7f)yield return value;}
                else if(op==0x15&&p+1<size){var value=(uint)U16(d,code+16+2*(p+1))<<16;if(value>>24==0x7f)yield return value;}
                var width=widths[op];if(op==0){width=instruction switch{0x100=>U16(d,code+16+2*(p+1))*2+4,0x200=>U16(d,code+16+2*(p+1))*4+2,0x300=>(int)((U16(d,code+16+2*(p+1))*(long)(U16(d,code+16+2*(p+2))|U16(d,code+16+2*(p+3))<<16)+1)/2+4),_=>1};}if(width<1||p+width>size)throw new IOException("Malformed DEX instruction");p+=width;
            }
        }
        for(var c=0;c<classCount;c++)
        {
            var item=classOff+32*c;var name=Type((int)U32(d,item));if(!requested.Contains(name))continue;var p=(int)U32(d,item+24);if(p==0)continue;var sf=Leb(d,ref p);var inf=Leb(d,ref p);var dm=Leb(d,ref p);var vm=Leb(d,ref p);for(var f=0;f<2*(sf+inf);f++)Leb(d,ref p);var constants=new List<(string,uint)>();foreach(var count in new[]{dm,vm}){var method=0;for(var m=0;m<count;m++){method+=Leb(d,ref p);Leb(d,ref p);var code=Leb(d,ref p);if(code!=0){var methodName=String((int)U32(d,methodOff+8*method+4));foreach(var value in Code(code))constants.Add((methodName,value));}}}found[name]=constants;
        }
        return found;
    }
}
