using System.Buffers.Binary;
using System.IO.Compression;
using System.Text;
using System.Text.Json.Nodes;

namespace NexusAndroid;

// Android's binary XML/resource table formats, read directly: no external runtime or APK execution.
public sealed class ApkSettingsIndex
{
    private readonly string language,country;
    private ApkSettingsIndex(string language,string country){this.language=language;this.country=country;}
    private readonly Dictionary<uint,(int Rank,byte Type,uint Data)> values=[];
    private readonly Dictionary<uint,string> names=[];
    private string[] global=[];
    private static ushort U16(byte[] b,int p)=>BinaryPrimitives.ReadUInt16LittleEndian(b.AsSpan(p,2));
    private static uint U32(byte[] b,int p)=>BinaryPrimitives.ReadUInt32LittleEndian(b.AsSpan(p,4));
    private static int I32(byte[] b,int p)=>checked((int)U32(b,p));
    private static int Len8(byte[] b,ref int p){var n=b[p++];return(n&128)!=0?((n&127)<<8)|b[p++]:n;}
    private static int Len16(byte[] b,ref int p){var n=U16(b,p);p+=2;if((n&0x8000)==0)return n;var more=U16(b,p);p+=2;return((n&0x7fff)<<16)|more;}
    private static string[] Pool(byte[] b,int start)
    {
        var header=U16(b,start+2);var count=I32(b,start+8);if(count>2000000)throw new IOException("APK string table exceeds bound");var utf8=(U32(b,start+16)&0x100)!=0;var strings=I32(b,start+20);var result=new string[count];
        for(var i=0;i<count;i++){var p=start+strings+I32(b,start+header+i*4);if(utf8){Len8(b,ref p);var size=Len8(b,ref p);result[i]=Encoding.UTF8.GetString(b,p,size);}else{var size=Len16(b,ref p);result[i]=Encoding.Unicode.GetString(b,p,size*2);}}return result;
    }
    private static void Chunks(byte[] data,int from,int until,Action<ushort,int,int,int> callback)
    {
        for(var p=from;p+8<=until;){var type=U16(data,p);var header=U16(data,p+2);var size=I32(data,p+4);if(size<8||p+size>until||header>size)throw new IOException("Invalid Android resource chunk");callback(type,p,header,size);p+=size;}
    }
    private void Resources(byte[] b)
    {
        Chunks(b,U16(b,2),b.Length,(type,p,header,size)=>
        {
            if(type==1&&global.Length==0)global=Pool(b,p);
            if(type!=0x200)return;var package=U32(b,p+8);var keyPool=Pool(b,p+I32(b,p+276));
            Chunks(b,p+header,p+size,(t,pos,h,s)=>
            {
                if(t!=0x201)return;var tid=b[pos+8];var flags=b[pos+9];var count=I32(b,pos+12);var entries=I32(b,pos+16);var config=pos+20;var lang=Encoding.ASCII.GetString(b,config+8,2).TrimEnd('\0');var region=Encoding.ASCII.GetString(b,config+10,2).TrimEnd('\0');var rank=lang==language?(region==country?3:2):lang.Length==0?1:0;if(rank==0)return;
                for(var i=0;i<count;i++)
                {
                    uint off;var index=i;var at=pos+h;
                    if((flags&1)!=0){index=U16(b,at+i*4);off=(uint)U16(b,at+i*4+2)*4;}
                    else if((flags&2)!=0){var v=U16(b,at+i*2);if(v==0xffff)continue;off=(uint)v*4;}
                    else{off=U32(b,at+i*4);if(off==uint.MaxValue)continue;}
                    var e=checked(pos+entries+(int)off);var esize=U16(b,e);var ef=U16(b,e+2);uint key;byte vt;uint vd;
                    if((ef&8)!=0){key=esize;vt=(byte)(ef>>8);vd=U32(b,e+4);}else{if((ef&1)!=0)continue;key=U32(b,e+4);vt=b[e+esize+3];vd=U32(b,e+esize+4);}
                    var id=(package<<24)|((uint)tid<<16)|(uint)index;if(key<keyPool.Length){names.TryAdd(id,keyPool[key]);}if(!values.TryGetValue(id,out var old)||rank>old.Rank)values[id]=(rank,vt,vd);
                }
            });
        });
    }
    private string Resolve(uint id,int depth=0){if(depth>8||!values.TryGetValue(id,out var value))return "";return value.Type==3&&value.Data<global.Length?global[value.Data]:value.Type==1?Resolve(value.Data,depth+1):"";}
    private sealed class Element(string tag){public readonly string Tag=tag;public Element? Parent;public readonly Dictionary<string,string> Attr=[];public readonly List<Element> Children=[];public string Get(string name)=>Attr.GetValueOrDefault(name,"").Trim();public IEnumerable<Element> All()=>new[]{this}.Concat(Children.SelectMany(c=>c.All()));}
    private static bool PreferenceRow(Element row)=>row.Get("title").Length>0&&!row.Tag.Contains("Category",StringComparison.OrdinalIgnoreCase)&&System.Text.RegularExpressions.Regex.IsMatch(row.Tag,@"(?i)preference|switch|checkbox|seekbar|list|radio|dropdown|slider|button")&&!System.Text.RegularExpressions.Regex.IsMatch(row.Tag,@"(?i)footer|description|caption|illustration|layoutpreference|divider|banner|tips?pref|summarypref|animation|lottie|preview|inset|blank|space|relativelink|contextualcard")&&(row.Get("fragment").Length>0||!System.Text.RegularExpressions.Regex.IsMatch(row.Get("key"),@"(?i)description|caption(?!ing)|footer|_desc$|_guide|_notice|_info_text|disclaimer"));
    private Element Xml(byte[] b)
    {
        string[] strings=[];uint[] resources=[];var androidAttrs=new Dictionary<uint,string>{[0x01010003]="name",[0x01010021]="targetPackage",[0x0101002f]="targetClass",[0x01010024]="value",[0x010101e1]="title",[0x010101e8]="key",[0x010101e9]="summary",[0x010102e3]="fragment",[0x01010001]="label",[0x01010010]="exported",[0x01010202]="targetActivity"};var root=new Element("root");var stack=new Stack<Element>();stack.Push(root);
        Chunks(b,U16(b,2),b.Length,(type,p,header,size)=>
        {
            if(type==1)strings=Pool(b,p);
            else if(type==0x180)resources=Enumerable.Range(0,(size-header)/4).Select(i=>U32(b,p+header+4*i)).ToArray();
            else if(type==0x102)
            {
                var ext=p+16;var name=U32(b,ext+4);var node=new Element(strings[name]);var count=U16(b,ext+12);var start=U16(b,ext+8);var attrSize=U16(b,ext+10);
                for(var n=0;n<count;n++){var a=ext+start+n*attrSize;var key=U32(b,a+4);var raw=U32(b,a+8);var vt=b[a+15];var vd=U32(b,a+16);var value=vt==1?Resolve(vd):vt==3?(raw<strings.Length?strings[raw]:vd<strings.Length?strings[vd]:""):vt==0x12?(vd!=0?"true":"false"):vd.ToString();var attr=key<resources.Length?androidAttrs.GetValueOrDefault(resources[key],strings[key]):strings[key];node.Attr[attr]=value;}
                node.Parent=stack.Peek();stack.Peek().Children.Add(node);stack.Push(node);
            }
            else if(type==0x103&&stack.Count>1)stack.Pop();
        });return root;
    }
    private static byte[] Bytes(ZipArchiveEntry entry){if(entry.Length>256L*1024*1024)throw new IOException("APK entry exceeds 256MB bound");using var input=entry.Open();using var buffer=new MemoryStream();input.CopyTo(buffer);return buffer.ToArray();}
    public static List<JsonObject> Build(string apk,string language="fr",string country="FR")
    {
        using var zip=ZipFile.OpenRead(apk);var resources=Bytes(zip.GetEntry("resources.arsc")??throw new IOException("Settings APK missing resource table"));var parser=new ApkSettingsIndex(language,country);parser.Resources(resources);var english=new ApkSettingsIndex("en","US");english.Resources(resources);var manifest=parser.Xml(Bytes(zip.GetEntry("AndroidManifest.xml")!));
        var activities=new Dictionary<string,(string Component,string Action,string Label)>();var byName=new Dictionary<string,(string Action,string Label)>();var package=manifest.All().First(e=>e.Tag=="manifest").Get("package");
        foreach(var activity in manifest.All().Where(e=>e.Tag is "activity" or "activity-alias").Where(a=>!(a.Get("exported")=="false"||a.Get("exported").Length==0&&!a.Children.Any(c=>c.Tag=="intent-filter"))))
        {
            var name=activity.Get("name");if(name.StartsWith('.')){name=package+name;}var component=package+"/"+name;var action=activity.All().FirstOrDefault(e=>e.Tag=="action"&&e.Get("name")!="android.intent.action.MAIN")?.Get("name")??"";
            var fragment=activity.All().FirstOrDefault(e=>e.Tag=="meta-data"&&e.Get("name")=="com.android.settings.FRAGMENT_CLASS")?.Get("value")??"";
            byName[name]=(action,activity.Get("label"));
            if(fragment.Length>0&&(!activities.TryGetValue(fragment,out var current)||action.Length>0&&current.Action.Length==0))activities[fragment]=(component,action,activity.Get("label"));
        }
        var xmls=new Dictionary<string,Element>();var englishXmls=new Dictionary<string,Element>();foreach(var entry in zip.Entries.Where(e=>e.FullName.StartsWith("res/xml/")&&e.FullName.EndsWith(".xml")))try{var bytes=Bytes(entry);var rootXml=parser.Xml(bytes);if(rootXml.Children.FirstOrDefault()?.Tag.Contains("Preference",StringComparison.Ordinal)!=true)continue;xmls[entry.FullName]=rootXml;englishXmls[entry.FullName]=english.Xml(bytes);}catch(Exception ex)when(ex is IOException or ArgumentException or IndexOutOfRangeException){/* Non-preference binary XML is outside the index. */}
        var fragmentXml=new Dictionary<string,string>();var fragments=new HashSet<string>(activities.Keys.Concat(xmls.Values.SelectMany(v=>v.All()).Select(e=>e.Get("fragment")).Where(f=>f.Length>0)));var constants=new Dictionary<string,List<(string Method,uint Resource)>>();foreach(var dex in zip.Entries.Where(e=>e.FullName.StartsWith("classes")&&e.FullName.EndsWith(".dex"))){var missing=new HashSet<string>(fragments.Except(constants.Keys));if(missing.Count==0)break;foreach(var pair in DexResources.Constants(Bytes(dex),missing))constants[pair.Key]=pair.Value;}
        foreach(var fragment in constants)
        {
            var candidates=fragment.Value.Select(v=>(v.Method,File:parser.Resolve(v.Resource))).Where(v=>xmls.ContainsKey(v.File)).ToList();var own=candidates.Where(v=>v.Method=="getPreferenceScreenResId").ToList();var indexed=candidates.Where(v=>v.Method=="<clinit>").Select(v=>v.File).ToHashSet();var best=own.FirstOrDefault(v=>indexed.Contains(v.File)).File??own.OrderByDescending(v=>xmls[v.File].All().Skip(2).Count(PreferenceRow)).FirstOrDefault().File??candidates.FirstOrDefault().File;if(best is not null)fragmentXml[fragment.Key]=best;
        }
        // Preference fragment references provide exact parent paths where available; all other preference XML remains searchable.
        var paths=new Dictionary<string,(string Path,string Component,string Action)>();var root=xmls.Keys.FirstOrDefault(x=>x.EndsWith("sec_top_level_settings.xml"))??xmls.Keys.FirstOrDefault(x=>x.EndsWith("top_level_settings.xml"));if(root is not null)paths[root]=("Paramètres","com.android.settings/.Settings","android.settings.SETTINGS");
        var parents=new Dictionary<string,(string Xml,string Title)>();var queue=new Queue<string>();
        void Expand(){while(queue.TryDequeue(out var parent))foreach(var (row,child) in xmls[parent].All().Skip(2).Where(PreferenceRow).Select(r=>(r,fragmentXml.GetValueOrDefault(r.Get("fragment"))??"")).Where(p=>p.Item2.Length>0&&!paths.ContainsKey(p.Item2))){var meta=activities.GetValueOrDefault(row.Get("fragment"));paths[child]=(paths[parent].Path+" > "+row.Get("title"),meta.Component??"",meta.Action??"");parents[child]=(parent,row.Get("title"));queue.Enqueue(child);}}
        if(root is not null){queue.Enqueue(root);}Expand();foreach(var (activity,xml) in activities.OrderBy(p=>p.Key,StringComparer.Ordinal).Select(a=>(a,fragmentXml.GetValueOrDefault(a.Key)??"")).Where(p=>p.Item2.Length>0&&!paths.ContainsKey(p.Item2))){var direct=activity.Value;paths[xml]=(direct.Label.Length>0?direct.Label:Path.GetFileNameWithoutExtension(xml),direct.Component,direct.Action);queue.Enqueue(xml);Expand();}
        var output=new List<JsonObject>();foreach(var pair in xmls)
        {
            var route=paths.GetValueOrDefault(pair.Key,(Path:"["+Path.GetFileNameWithoutExtension(pair.Key)+"]",Component:"",Action:""));
            var translated=englishXmls[pair.Key].All().ToList();var position=-1;foreach(var row in pair.Value.All())
            {
                position++;var title=row.Get("title");if(position<2||!PreferenceRow(row))continue;
                var component=route.Component;var action=route.Action;
                var intent=row.Children.FirstOrDefault(e=>e.Tag=="intent");var intentAction=intent?.Get("action")??"";var intentTarget=intent is not null&&intent.Get("targetPackage").Length>0&&intent.Get("targetClass").Length>0?intent.Get("targetPackage")+"/"+intent.Get("targetClass"):"";var targetAction="";
                if(intentTarget.Length==0&&fragmentXml.TryGetValue(row.Get("fragment"),out var child)&&paths.TryGetValue(child,out var childRoute)){if(childRoute.Action.Length>0)targetAction=childRoute.Action;else intentTarget=childRoute.Component;}
                var section="";for(var ancestor=row.Parent;ancestor is not null;ancestor=ancestor.Parent){if(ancestor.Tag.Contains("Category",StringComparison.OrdinalIgnoreCase)&&ancestor.Get("title").Length>0){section=ancestor.Get("title");break;}}var englishTitle=position<translated.Count?translated[position].Get("title"):"";
                var entry=new JsonObject{["title"]=title,["english"]=englishTitle==title?"":englishTitle,["path"]=route.Path+" > "+title,["section"]=section,["fragment"]=row.Get("fragment"),["intent_action"]=intentAction,["target"]=intentTarget,["target_action"]=targetAction,["keywords"]=row.Get("keywords"),["key"]=row.Get("key"),["summary"]=row.Get("summary"),["open"]=component,["component"]=component,["action"]=action,["xml"]=pair.Key,["orphan"]=!paths.ContainsKey(pair.Key),["kind"]=System.Text.RegularExpressions.Regex.IsMatch(row.Tag,@"(?i)switch|checkbox")?"switch":"",["tag"]=row.Tag};
                if(component.Length==0&&action.Length==0){var here=pair.Key;var taps=new List<string>();var seen=new HashSet<string>();while(parents.TryGetValue(here,out var parent)&&seen.Add(here)){taps.Insert(0,parent.Title);here=parent.Xml;if(paths.TryGetValue(here,out var via)&&(via.Component.Length>0||via.Action.Length>0)){entry["via"]=new JsonObject{["component"]=via.Component,["action"]=via.Action};entry["taps"]=new JsonArray(taps.Select(v=>(JsonNode?)JsonValue.Create(v)).ToArray());break;}}}
                output.Add(entry);
            }
        }
        if(output.Count==0)throw new IOException("No Settings preferences decoded from APK");foreach(var activity in byName.OrderBy(p=>p.Key,StringComparer.Ordinal).Where(p=>p.Value.Label.Length>0)){output.Add(new JsonObject{["title"]=activity.Value.Label,["path"]=activity.Value.Label,["source"]="activity",["component"]=package+"/"+activity.Key,["open"]=package+"/"+activity.Key,["action"]=activity.Value.Action,["orphan"]=false,["direct"]=true,["xml"]=""});}return output;
    }
}
