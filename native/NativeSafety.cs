using System.Collections.Concurrent;
using System.Globalization;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace NexusAndroid;

public static class NativeSafety
{
    public static readonly AsyncLocal<string?> Session = new();
    public static readonly AsyncLocal<CancellationToken> Cancellation = new();
    private static readonly ConcurrentDictionary<string, (string Session, string Action, DateTimeOffset Until)> Accords = new();
    private static readonly byte[] SealKey=RandomNumberGenerator.GetBytes(32);
    public static void ForgetSession(string session){foreach(var item in Accords.Where(p=>p.Value.Session==session))Accords.TryRemove(item.Key,out _);}
    public static string Seal(string text)=>Convert.ToHexString(HMACSHA256.HashData(SealKey,Encoding.UTF8.GetBytes(text)))[..20];
    private static readonly JsonObject Policy = JsonNode.Parse(new StreamReader(Assembly.GetExecutingAssembly().GetManifestResourceStream("garde-fous.json")!).ReadToEnd())!.AsObject();
    public static string Comparable(string value) => Regex.Replace(string.Concat(value.Normalize(NormalizationForm.FormD).Where(c => CharUnicodeInfo.GetUnicodeCategory(c) is not (UnicodeCategory.NonSpacingMark or UnicodeCategory.Format))).ToLowerInvariant(), @"[^\p{L}\p{N}]+", " ").Trim();
    public static string Normalize(string value)
    {
        var upperMap=new Dictionary<char,char>{['А']='A',['В']='B',['Е']='E',['К']='K',['М']='M',['Н']='H',['О']='O',['Р']='P',['С']='C',['Т']='T',['У']='Y',['Х']='X',['І']='I',['Ј']='J',['Ѕ']='S',['Α']='A',['Β']='B',['Ε']='E',['Ζ']='Z',['Η']='H',['Ι']='I',['Κ']='K',['Μ']='M',['Ν']='N',['Ο']='O',['Ρ']='P',['Τ']='T',['Υ']='Y',['Χ']='X'};
        var lowerMap=new Dictionary<char,char>{['а']='a',['в']='b',['е']='e',['к']='k',['м']='m',['н']='h',['о']='o',['р']='p',['с']='c',['т']='t',['у']='y',['х']='x',['і']='i',['ј']='j',['ѕ']='s',['ԁ']='d',['ԛ']='q',['ԝ']='w',['һ']='h',['ӏ']='l',['α']='a',['β']='b',['ε']='e',['η']='n',['ι']='i',['κ']='k',['ν']='v',['ο']='o',['ρ']='p',['τ']='t',['υ']='u',['χ']='x',['ω']='w'};
        var visible=string.Concat(value.Normalize(NormalizationForm.FormKD).Replace("€"," eur ").Replace("$"," usd ").Replace("£"," gbp ").Replace("¥"," jpy ").Where(c=>CharUnicodeInfo.GetUnicodeCategory(c) is not (UnicodeCategory.NonSpacingMark or UnicodeCategory.SpacingCombiningMark or UnicodeCategory.EnclosingMark or UnicodeCategory.Format)).Select(c=>upperMap.GetValueOrDefault(c,c))).ToLowerInvariant();
        var mapped=string.Concat(visible.Select(c=>lowerMap.GetValueOrDefault(c,c)));return Regex.Replace(mapped,@"[^a-z0-9]+"," ").Trim();
    }
    private static bool Matches(string section, string key, string text) => Policy[section]?[key]?.AsArray().Any(p => Regex.IsMatch(Normalize(text), @"\b(?:" + p!.GetValue<string>() + @")\b", RegexOptions.IgnoreCase, TimeSpan.FromMilliseconds(100))) == true;
    public static bool Risky(string label) => Matches("confirmation", "motifs", label);
    public static bool Outward(string label) => Risky(label) && !Matches("accord", "motifs_locaux", label);
    public static bool ContextRisk(string label,string context) => Policy["confirmation"]?["contextes"]?.AsArray().OfType<JsonObject>().Any(c=>Regex.IsMatch(Normalize(context),@"\b(?:"+c["titre"]+@")\b")&&Regex.IsMatch(Normalize(label),@"\b(?:"+c["verbes"]+@")\b"))==true;
    public static void Require(JsonObject args, string exactAction, bool outward = false)
    {
        if (!outward && args["confirm"]?.GetValue<bool>() != true)
            throw new InvalidOperationException($"Confirmation required for this exact action: {Mask(exactAction)}. Set confirm=true only on the user's explicit order.");
        if (!outward) return;
        var supplied = args["accord"]?.GetValue<string>();
        if (args["confirm"]?.GetValue<bool>() == true && supplied is not null && Accords.TryGetValue(supplied,out var known) && known.Session==(Session.Value??"stdio") && Accords.TryRemove(supplied,out var saved) && saved.Until >= DateTimeOffset.UtcNow && saved.Action == exactAction) return;
        foreach (var stale in Accords.Where(x => x.Value.Until < DateTimeOffset.UtcNow)) Accords.TryRemove(stale.Key, out _);
        var token = Convert.ToHexString(RandomNumberGenerator.GetBytes(24)).ToLowerInvariant();
        Accords[token] = (Session.Value??"stdio", exactAction, DateTimeOffset.UtcNow.AddMinutes(10));
        throw new InvalidOperationException($"Exact action requiring the user's agreement: {Mask(exactAction)}\nSingle-use accord={token}. Show this exact summary to the user, then retry only after their explicit yes. Any changed action invalidates it.");
    }
    public static JsonNode? MaskJson(JsonNode? node)
    {
        if(node is JsonObject obj){var result=new JsonObject();foreach(var pair in obj)result[pair.Key]=Regex.IsMatch(pair.Key,@"(?i)^(?:(?:access|refresh|id|auth|session|csrf|api|client)?[_-]?(?:token|secret|password|passwd|pwd|cookie|authorization|api[_-]?key|session[_-]?id|sid))$")?JsonValue.Create("[masked]"):MaskJson(pair.Value);return result;}
        if(node is JsonArray array)return new JsonArray(array.Select(MaskJson).ToArray());
        if(node is JsonValue value&&value.TryGetValue<string>(out var text))return JsonValue.Create(Mask(text));return node?.DeepClone();
    }
    public static void StopGuard()
    {
        Cancellation.Value.ThrowIfCancellationRequested();
        var stop = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".nexus", "stop");
        if (File.Exists(stop) && DateTime.UtcNow - File.GetLastWriteTimeUtc(stop) < TimeSpan.FromSeconds(120))
            throw new InvalidOperationException("Arrêt d'urgence demandé par Julien : plus aucune action pendant 120 secondes ; ne pas contourner.");
    }
    private static readonly Regex CodeWords=new(@"code|\b(?:codigo|codice|clave\b|contrasena|kennwort|passwort|bestatig|token\b|otp\b|pin\b|passcode|mot de passe|password|2fa\b|log[- ]?in\b|login|sign[- ]?in\b|connexion|securit|verif|valid|confirm|authentif|authentic|identifiant|usage unique|one[- ]?time)",RegexOptions.IgnoreCase);
    private static string CodePlain(string text)=>string.Concat(text.Select(c=>char.IsSurrogate(c)?c.ToString():c is '’' or '‘' or 'ʼ'?"'":new string(c.ToString().Normalize(NormalizationForm.FormKD).Where(x=>CharUnicodeInfo.GetUnicodeCategory(x)!=UnicodeCategory.NonSpacingMark).ToArray()) is {Length:1} plain?plain:c.ToString()));
    public static bool HasCodeKeyword(params string[] values)=>values.Any(t=>CodeWords.IsMatch(CodePlain(t)));
    private static bool OwnAccord(string token)=>Accords.TryGetValue(token,out var accord)&&accord.Session==(Session.Value??"stdio")&&accord.Until>=DateTimeOffset.UtcNow;
    public static string MaskCodes(string text,bool context=false)
    {
        var protectedTokens=new Dictionary<string,string>();text=Regex.Replace(text,@"\b[a-f0-9]{48}\b",m=>{if(!OwnAccord(m.Value))return m.Value;var placeholder=char.ConvertFromUtf32(0xF0000+protectedTokens.Count);protectedTokens[placeholder]=m.Value;return placeholder;});
        var words=CodeWords.Matches(CodePlain(text));if(!context&&words.Count==0){foreach(var token in protectedTokens)text=text.Replace(token.Key,token.Value,StringComparison.Ordinal);return text;}
        text=Regex.Replace(text,@"(?<![\w-])(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{4,10}(?![\w-])",m=>context||words.Any(k=>m.Index-60<=k.Index+k.Length&&k.Index<=m.Index+m.Length+60)?"[masked code]":m.Value);
        text=Regex.Replace(text,@"(?<![\d,./$€£+-])(?<!\d[ -])(?!\d{1,2}-\d{1,2}-\d{2,4}(?!\d)|\d{4}-\d{1,2}-\d{1,2}(?!\d))(?:[A-Z]-)?\d(?:[ -]?\d){3,8}(?![ -]?\d|[.,/]\d|\s?(?:€|\$|£|eur\b|EUR\b|%))","[masked code]");foreach(var token in protectedTokens)text=text.Replace(token.Key,token.Value,StringComparison.Ordinal);return text;
    }
    public static string Mask(string text, bool personal = false,bool codes=true)
    {
        text = Regex.Replace(text, @"(?i)\b(bearer\s+|(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|secret|authorization)\s*[=:]\s*)[^\s,;\r\n]+", "$1[masked]");
        text=Regex.Replace(text,"(?i)\\b((?:access|refresh|id|auth|session|csrf|api|client)?[_-]?(?:token|secret|password|passwd|pwd|cookie|authorization|api[_-]?key|session[_-]?id|sid))([\\\"']?\\s*[:=]\\s*[\\\"']?)([^\\s\\\"',;&}\\]]{4,})","$1$2[masked]");
        text = Regex.Replace(text, @"(?:sk-|sk_(?:live|test)_|rk_(?:live|test)_|gh[pousr]_|github_pat_|glpat-|AKIA|ASIA|xox[abposr]-|AIza|hf_|npm_|pypi-|SG\.|shpat_|ya29\.)[A-Za-z0-9._\-]{12,}|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*", "[masked]");
        text=Regex.Replace(text,@"[A-Za-z0-9+/_=.~-]{20,}",m=>
        {
            var word=m.Value.TrimEnd('.');if(OwnAccord(word))return m.Value;var entropy=-word.GroupBy(c=>c).Sum(g=>{var p=(double)g.Count()/word.Length;return p*Math.Log2(p);});
            var hex=word.Length>=32&&Regex.IsMatch(word,@"\A[0-9a-fA-F]+\z")&&entropy>=3;
            var b64=word.Length>=32&&Regex.IsMatch(word,@"\A[A-Za-z0-9+/_=-]+\z")&&word.Any(char.IsLower)&&word.Any(char.IsUpper)&&word.Any(char.IsDigit)&&word.Count(c=>c is '-' or '_')*10<=word.Length&&entropy>=3.5;
            return hex||b64?"[masked]":m.Value;
        });
        text = Regex.Replace(text, @"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b", "[masked]");
        text = Regex.Replace(text, @"\b(?:\d[ -]?){13,19}\b", m=>
        {
            var digits=string.Concat(m.Value.Where(char.IsDigit));var total=0;for(var i=0;i<digits.Length;i++){var n=digits[digits.Length-1-i]-'0';if((i&1)==1)n*=2;total+=n>9?n-9:n;}return digits.Length is >=15 and <=19&&digits[0] is >= '2' and <= '6'&&digits.Distinct().Count()>1&&total%10==0?"[masked card]":m.Value;
        });
        if(codes)text=MaskCodes(text);
        if (personal)
        {
            text = Regex.Replace(text, @"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", "[email masked]");
            text = Regex.Replace(text, @"(?<!\w)(?:\+\d{1,3}[ .-]?)?(?:\d[ .-]?){9,12}(?!\w)", "[number masked]");
        }
        return text;
    }
    public static string Quote(string arg) => "'" + arg.Replace("'", "'\\''") + "'";
    public static string Package(string pkg)
    {
        if (!Regex.IsMatch(pkg, @"^[A-Za-z][\w]*(?:\.[A-Za-z][\w]*)+$")) throw new ArgumentException("Invalid Android package name");
        return pkg;
    }
    public static string PcPath(string path, bool confirm)
    {
        if(path=="~"||path.StartsWith("~/")||path.StartsWith("~\\"))path=Environment.GetFolderPath(Environment.SpecialFolder.UserProfile)+path[1..];var real = Path.GetFullPath(path);
        // Resolve every existing link before testing boundaries (also covers links in a parent directory).
        var current = Path.GetPathRoot(real)!;var parts=Path.GetRelativePath(current,real).Split(Path.DirectorySeparatorChar, StringSplitOptions.RemoveEmptyEntries);
        foreach (var part in parts)
        {
            current = Path.Combine(current, part);
            FileSystemInfo info = Directory.Exists(current) ? new DirectoryInfo(current) : new FileInfo(current);
            if (info.Exists && info.LinkTarget != null) current = info.ResolveLinkTarget(true)!.FullName;
        }
        real = current;
        var lower = real.ToLowerInvariant();
        if (Regex.IsMatch(lower, @"(^|[\\/])(?:nexussecure|administratif|\.ssh|\.gnupg|\.aws|\.azure|\.nexus|\.codex|\.claude|\.gemini|\.env(?:\.[^\\/]+)?)([\\/]|$)") || lower.Contains("\\start menu\\programs\\startup")) throw new InvalidOperationException("Sensitive path refused (secrets, identity, agent configuration or Windows startup)");
        var roots = new[] { Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), Environment.GetEnvironmentVariable("OneDrive") }.Where(p => !string.IsNullOrEmpty(p));
        if (!confirm && !roots.Any(r => new[] { "Downloads", "Documents", "Desktop", "Pictures", "Videos" }.Any(d => real.StartsWith(Path.Combine(r!, d) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) || real.Equals(Path.Combine(r!, d), StringComparison.OrdinalIgnoreCase)))) throw new InvalidOperationException("Path outside usual folders: confirm=true required");
        return real;
    }
    public static string PhonePath(string? path)
    {
        var value = (path ?? "/sdcard/Download").Replace('\\', '/').Replace("/storage/emulated/0", "/sdcard");
        if (!value.StartsWith('/')) value = "/sdcard/" + value;
        var clean = new List<string>();
        foreach (var part in value.Split('/', StringSplitOptions.RemoveEmptyEntries)) { if (part == "..") { if (clean.Count > 0) clean.RemoveAt(clean.Count - 1); } else if (part != ".") clean.Add(part); }
        var result = "/" + string.Join('/', clean);
        if (result != "/sdcard" && !result.StartsWith("/sdcard/", StringComparison.Ordinal)) throw new InvalidOperationException("Only shared storage /sdcard is accessible");
        return result;
    }
}
