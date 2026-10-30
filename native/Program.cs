using System;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;

namespace NexusAndroid;

public static class Program
{
    public const string Version = "0.20.3";

    public static async Task<int> Main(string[] args)
    {
        if (args.Length == 0 || args.Contains("--help") || args.Contains("-h"))
        {
            PrintHelp();
            return 0;
        }

        if (args.Contains("--version") || args.Contains("-v"))
        {
            Console.WriteLine($"NexusAndroid v{Version} (Native C# .NET 10)");
            return 0;
        }

        if (args.Contains("--selftest"))
        {
            return RunSelftest();
        }

        if (args.Contains("status"))
        {
            return RunStatus();
        }

        if (args.Contains("--http", StringComparer.OrdinalIgnoreCase))
        {
            int idx = Array.IndexOf(args, "--http");
            int port = 16180;
            if (idx + 1 < args.Length && int.TryParse(args[idx + 1], out int p))
                port = p;
            var server = new McpServer();
            using var cts = new CancellationTokenSource();
            Console.CancelKeyPress += (_, e) => { e.Cancel = true; cts.Cancel(); };
            await server.RunHttpAsync(port, cts.Token);
            return 0;
        }

        if (args.Contains("--mcp"))
        {
            var server = new McpServer();
            server.RunStdio();
            return 0;
        }

        PrintHelp();
        return 1;
    }

    private static void PrintHelp()
    {
        Console.WriteLine($"NexusAndroid v{Version} - Unified Standalone C# .NET 10 Engine");
        Console.WriteLine("Usage:");
        Console.WriteLine("  NexusAndroid.exe --http [port] Start HTTP MCP daemon (default 16180)");
        Console.WriteLine("  NexusAndroid.exe --mcp         Start stdio MCP server (18 tools)");
        Console.WriteLine("  NexusAndroid.exe --selftest    Run native unit test suite");
        Console.WriteLine("  NexusAndroid.exe status        Display connected Android devices");
        Console.WriteLine("  NexusAndroid.exe --version     Print version info");
    }

    private static int RunStatus()
    {
        var adb = new AdbClient();
        Console.WriteLine($"ADB path: {adb.AdbPath}");
        var devices = adb.GetDevices();
        Console.WriteLine($"Found {devices.Count} device(s):");
        foreach (var d in devices)
        {
            Console.WriteLine($"  - {d.Serial} [{d.State}] Model: {d.Model} (USB: {d.IsUsb})");
        }
        return 0;
    }

    private static int RunSelftest()
    {
        int passed = 0;
        int failed = 0;

        void Assert(string name, bool condition)
        {
            if (condition)
            {
                passed++;
                Console.WriteLine($"[PASS] {name}");
            }
            else
            {
                failed++;
                Console.WriteLine($"[FAIL] {name}");
            }
        }

        Console.WriteLine("Running NexusAndroid native self-tests...");

        // 1. ImageChain tests (Anti-crash 400 & budget < 200 KB)
        try
        {
            // Synthetic 1080x1920 image
            int w = 1080;
            int h = 1920;
            byte[] rawBgra = new byte[w * h * 4];
            for (int i = 0; i < rawBgra.Length; i += 4)
            {
                rawBgra[i] = (byte)(i % 255);     // B
                rawBgra[i + 1] = (byte)((i / 4) % 255); // G
                rawBgra[i + 2] = 128;             // R
                rawBgra[i + 3] = 255;             // A
            }

            var pix = new PixelBuffer(w, h, rawBgra);
            var result = ImageChain.ProcessChain(pix, maxBytes: 200 * 1024);

            Assert("ImageChain compresses within 200 KB budget", result.Bytes.Length <= 200 * 1024);
            Assert("ImageChain outputs image bytes", result.Bytes.Length > 100);
            Assert("ImageChain respects maximum dimensions", result.Width <= 2000 && result.Height <= 2000);
        }
        catch (Exception ex)
        {
            Assert($"ImageChain test failed: {ex.Message}", false);
        }

        // 2. ScreenParser tests
        try
        {
            string sampleXml = @"<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation='0'>
  <node index='0' text='' resource-id='' class='android.widget.FrameLayout' package='com.example.app' content-desc='' clickable='false' enabled='true' focused='false' scrollable='false' bounds='[0,0][1080,1920]'>
    <node index='0' text='OK' resource-id='com.example.app:id/btn_ok' class='android.widget.Button' package='com.example.app' content-desc='Accept button' clickable='true' enabled='true' focused='false' scrollable='false' bounds='[100,200][300,280]' />
    <node index='1' text='secret_pass' resource-id='com.example.app:id/user_password' class='android.widget.EditText' package='com.example.app' content-desc='' clickable='true' enabled='true' focused='true' scrollable='false' password='true' bounds='[100,300][980,400]' />
  </node>
</hierarchy>";

            var elements = ScreenParser.ParseXml(sampleXml);
            Assert("ScreenParser extracts actionable elements", elements.Count == 2);

            var btn = elements.FirstOrDefault(e => e.Text == "OK");
            Assert("ScreenParser parsed button bounds and center", btn != null && btn.CenterX == 200 && btn.CenterY == 240);

            var pwd = elements.FirstOrDefault(e => e.IsPassword);
            Assert("ScreenParser masked sensitive password field", pwd != null && pwd.Text == "[MASKED]");

            string compact = ScreenParser.FormatCompactTree(elements);
            Assert("ScreenParser compact tree contains numbered targets and center coordinates", compact.Contains("[1]") && compact.Contains("@200,240") && compact.Contains("\"OK\""));
        }
        catch (Exception ex)
        {
            Assert($"ScreenParser test failed: {ex.Message}", false);
        }

        // 3. AndroidPolicy tests
        try
        {
            Assert("Protected banking package detected", AndroidPolicy.IsProtectedPackage("fr.creditagricole.androidapp"));
            Assert("Protected password manager detected", AndroidPolicy.IsProtectedPackage("com.x8bit.bitwarden"));
            Assert("Non-protected package permitted", !AndroidPolicy.IsProtectedPackage("com.android.calculator2"));

            var request=new JsonObject{["confirm"]=true};string accord="";
            try{NativeSafety.Require(request,"send to synthetic recipient",true);}catch(InvalidOperationException e){accord=System.Text.RegularExpressions.Regex.Match(e.Message,@"accord=([a-f0-9]{48})").Groups[1].Value;}
            request["accord"]=accord;NativeSafety.Require(request,"send to synthetic recipient",true);
            Assert("Exact accord accepts approved action",accord.Length==48);
            var replayDenied=false;try{NativeSafety.Require(request,"send to synthetic recipient",true);}catch(InvalidOperationException){replayDenied=true;}
            Assert("Exact accord rejects replay",replayDenied);
            var forgedDenied=false;try{NativeSafety.Require(new JsonObject{["confirm"]=true,["accord"]="ACCORD:forged:9999999999:send:recipient"},"send to synthetic recipient",true);}catch(InvalidOperationException){forgedDenied=true;}
            Assert("Exact accord rejects fabricated token",forgedDenied);
        }
        catch (Exception ex)
        {
            Assert($"AndroidPolicy test failed: {ex.Message}", false);
        }

        // 4. MCP Server tool list
        try
        {
            var server = new McpServer();
            var initReq = new JsonObject
            {
                ["jsonrpc"] = "2.0",
                ["id"] = 1,
                ["method"] = "initialize"
            };
            var initResp = server.HandleRequest(initReq);
            Assert("MCP server initialize returns 2.0 result", initResp?["result"]?["serverInfo"]?["name"]?.GetValue<string>() == "nexus-android");

            var listReq = new JsonObject
            {
                ["jsonrpc"] = "2.0",
                ["id"] = 2,
                ["method"] = "tools/list"
            };
            var listResp = server.HandleRequest(listReq);
            var tools = listResp?["result"]?["tools"] as JsonArray;
            Assert("MCP server tools/list contains exactly 18 tools", tools != null && tools.Count == 18);
        }
        catch (Exception ex)
        {
            Assert($"MCP server test failed: {ex.Message}", false);
        }

        Console.WriteLine($"Self-tests finished: {passed} passed, {failed} failed.");
        return failed == 0 ? 0 : 1;
    }
}
