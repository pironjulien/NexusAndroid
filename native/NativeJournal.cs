using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace NexusAndroid;

public static class NativeJournal
{
    public static readonly AsyncLocal<string?> Agent=new();
    private static readonly object Gate=new();
    public static string? Identify(string? client){var name=(client??"").ToLowerInvariant();return name.Contains("codex")?"codex":name.Contains("antigravity")||name.Contains("jetski")||name.Contains("gemini")?"antigravity":name.Contains("claude")?"claude":null;}
    public static void Write(string action,string? package,string outcome,double milliseconds,Exception? error=null)
    {
        if(Agent.Value is not ("codex" or "claude" or "antigravity")||Environment.GetEnvironmentVariable("NEXUS_JOURNAL") is "0" or "off" or "false" or "no")return;
        try
        {
            var now=DateTimeOffset.UtcNow;var message=error?.Message.ToLowerInvariant()??"";var cause=error is null?null:message.Contains("accord")?"accord required":message.Contains("confirmation")?"confirmation required":message.Contains("locked")?"phone locked":message.Contains("cancel")?"cancelled":message.Contains("using the phone")?"user busy":message.Contains("password")?"password field":message.Contains("protected")?"protected app":message.Contains("timeout")||message.Contains("timed out")?"timeout":"error";
            var row=new JsonObject{["v"]=1,["ts"]=now.ToString("yyyy-MM-ddTHH:mm:ss.fffZ"),["agent"]=Agent.Value,["outil"]=Program.Version,["appel"]=Guid.NewGuid().ToString("N"),["app"]=package is not null&&Regex.IsMatch(package,@"^[A-Za-z][\w]*(\.[A-Za-z_][\w]*)+$")?package:null,["cadre"]=null,["action"]=action,["technique"]="native:adb",["issue"]=outcome,["ms"]=(int)Math.Round(milliseconds),["premier_plan"]=false,["erreur"]=cause};var folder=Path.Combine(Environment.GetEnvironmentVariable("NEXUS_JOURNAL_HOME")??Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),".nexus","journal"),"android",Agent.Value!);lock(Gate){Directory.CreateDirectory(folder);File.AppendAllText(Path.Combine(folder,now.ToString("yyyy-MM")+".jsonl"),row.ToJsonString()+"\n");}
        }
        catch(IOException){/* Logging never repeats or blocks a phone gesture. */}
    }
}
