using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace NexusAndroid.Banc;

/// <summary>Fold bench: each scenario runs several times per tuning variant and is judged on the phone itself, not
/// on what the tool reports (a ✓ that did nothing and a ~ that did act are both counted).</summary>
internal static class Program
{
    private static NativeEngine engine = null!;
    private static readonly StringBuilder Report = new();
    private static readonly JsonArray Rows = [];

    private static int Main(string[] args)
    {
        if (args.Length == 2 && args[0] == "sizes")
            return Sizes(args[1]);
        var reps = args.Length > 0 && int.TryParse(args[0], out var r) ? r : 8;
        engine = new NativeEngine(new AdbClient(null));
        var phone = Text(engine.Execute("phone", new JsonObject()));
        Console.WriteLine(phone);
        Report.AppendLine(CultureInfo.InvariantCulture, $"# NexusAndroid bench — {DateTime.Now:yyyy-MM-dd HH:mm}, {reps} repetitions\n\n{phone}\n");
        try
        {
            // Optional second argument: the sections to run (settle,scroll,look,read), all by default.
            var only = args.Length > 1 ? args[1].Split(',') : ["settle", "scroll", "look", "read"];
            if (only.Contains("settle"))
                Settle(reps);
            if (only.Contains("scroll"))
                Scrolls(only.Length == 1 ? reps : Math.Max(3, reps / 2));
            if (only.Contains("look"))
                Looks(Math.Max(3, reps / 2));
            if (only.Contains("read"))
                Reads(Math.Max(3, reps / 2));
        }
        finally
        {
            Defaults();
            try
            {
                engine.Execute("act", Json("""{"steps": [{"do": "key", "keys": "home"}], "then": "none"}"""));
            }
            catch (Exception e) { Console.WriteLine("home: " + e.Message); }
        }
        var folder = Path.GetFullPath(Path.Join(FindRepo(), "benchmarks", "results"));
        Directory.CreateDirectory(folder);
        var stamp = DateTime.Now.ToString("yyyy-MM-dd-HHmm", CultureInfo.InvariantCulture);
        File.WriteAllText(Path.Join(folder, stamp + "-fold.md"), Report.ToString());
        File.WriteAllText(Path.Join(folder, stamp + "-fold.json"), Rows.ToJsonString(new System.Text.Json.JsonSerializerOptions { WriteIndented = true }));
        Console.WriteLine(Report.ToString());
        return 0;
    }

    /// <summary>Offline: size, time and legibility of each encoding of one screenshot (no phone needed). Legibility is
    /// what Windows OCR still reads compared with the original (words found / words of the original).</summary>
    private static int Sizes(string path)
    {
        var picture = ImageChain.Decode(File.ReadAllBytes(path));
        static HashSet<string> Words(PixelBuffer pixels) => NativeOcr.Read(ImageChain.EncodePng(pixels)).SelectMany(l => Regex.Split(l.Text.ToLowerInvariant(), @"\W+")).Where(w => w.Length >= 2).ToHashSet();
        var reference = Words(picture);
        string Read(PixelBuffer pixels)
        {
            var found = Words(pixels);
            return $"{100.0 * reference.Count(found.Contains) / Math.Max(1, reference.Count):0.0} % of {reference.Count} words";
        }
        Console.WriteLine($"{Path.GetFileName(path)}: {picture.Width}×{picture.Height}");
        foreach (var bits in new[] { 0, 2, 3, 4, 5 })
        {
            var copy = picture.Clone();
            var mask = (byte)(0xFF << bits);
            for (var i = 0; i < copy.Bgra.Length; i++)
                if ((i & 3) != 3)
                    copy.Bgra[i] &= mask;
            var clock = Stopwatch.StartNew();
            var webp = WebP.Lossless(copy.Bgra, copy.Width, copy.Height);
            Console.WriteLine($"WebP, {bits} low bits dropped: {webp.Length / 1024} KB, {clock.ElapsedMilliseconds} ms, OCR {Read(copy)}");
        }
        foreach (var q in new[] { 82, 72, 60 })
        {
            var clock = Stopwatch.StartNew();
            var jpeg = ImageChain.EncodeLossyJpeg(picture, q);
            Console.WriteLine($"JPEG quality {q}: {jpeg.Length / 1024} KB, {clock.ElapsedMilliseconds} ms, OCR {Read(ImageChain.Decode(jpeg))}");
        }
        return 0;
    }
    private static string FindRepo()
    {
        // Built outside the sources (Directory.Build.props): look up from the working folder (dotnet run from Banc).
        var dir = new DirectoryInfo(Directory.GetCurrentDirectory());
        while (dir is not null && !File.Exists(Path.Join(dir.FullName, "test.ps1")))
            dir = dir.Parent;
        return dir?.FullName ?? Directory.GetCurrentDirectory();
    }

    // The product defaults, captured before any variant changes them.
    private static readonly (int NoChange, int Max, int Poll, bool Two, bool Steady, int Moves, int Hold, int Image, string Format) Product =
        (Tuning.SettleNoChangeMs, Tuning.SettleMaxMs, Tuning.SettlePollMs, Tuning.SettleTwoReads, Tuning.SteadyScroll, Tuning.SteadyMoves, Tuning.SteadyHoldMs, Tuning.LookImageMaxBytes, Tuning.LookLosslessFormat);
    private static void Defaults()
    {
        (Tuning.SettleNoChangeMs, Tuning.SettleMaxMs, Tuning.SettlePollMs, Tuning.SettleTwoReads, Tuning.SteadyScroll, Tuning.SteadyMoves, Tuning.SteadyHoldMs, Tuning.LookImageMaxBytes, Tuning.LookLosslessFormat) = Product;
    }

    private static JsonObject Json(string text) => JsonNode.Parse(text)!.AsObject();
    private static string Text(JsonObject result) => result["content"] is JsonArray blocks ? string.Join("\n", blocks.Select(b => b?["text"]?.ToString() ?? "")) : result["text"]?.ToString() ?? result.ToJsonString();
    private static int ImageBytes(JsonObject result) => result["content"] is JsonArray blocks ? blocks.Where(b => b?["type"]?.ToString() == "image").Sum(b => b!["data"]!.ToString().Length * 3 / 4) : 0;

    private static (JsonObject Result, double Ms, string? Error) Timed(string tool, JsonObject args)
    {
        var clock = Stopwatch.StartNew();
        try
        {
            var result = engine.Execute(tool, args);
            return (result, clock.Elapsed.TotalMilliseconds, null);
        }
        catch (Exception e) { return (new JsonObject(), clock.Elapsed.TotalMilliseconds, e.Message); }
    }

    private static string Look(string? query = null) => Text(engine.Execute("look", query is null ? new JsonObject() : new JsonObject { ["query"] = query }));

    private static double Median(IEnumerable<double> values)
    {
        var v = values.OrderBy(x => x).ToList();
        return v.Count == 0 ? double.NaN : v.Count % 2 == 1 ? v[v.Count / 2] : (v[v.Count / 2 - 1] + v[v.Count / 2]) / 2;
    }

    private static void Row(string scenario, string variant, JsonObject values)
    {
        values["scenario"] = scenario;
        values["variant"] = variant;
        Rows.Add(values);
    }

    // ------------------------------------------------------------------ settle after a gesture

    private static readonly (string Name, Action Set)[] SettleVariants =
    [
        ("product defaults", Defaults),
        ("two reads 600/1500 ms, poll 60 (0.23.12-0.23.17)", () => { Tuning.SettleNoChangeMs = 600; Tuning.SettleMaxMs = 1500; Tuning.SettlePollMs = 60; Tuning.SettleTwoReads = true; }),
        ("fast 300/1000 ms, poll 30, two reads", () => { Tuning.SettleNoChangeMs = 300; Tuning.SettleMaxMs = 1000; Tuning.SettlePollMs = 30; Tuning.SettleTwoReads = true; }),
        ("first change 600/1500 ms, poll 60", () => { Tuning.SettleNoChangeMs = 600; Tuning.SettleMaxMs = 1500; Tuning.SettlePollMs = 60; Tuning.SettleTwoReads = false; }),
        ("first change 300/1000 ms, poll 30", () => { Tuning.SettleNoChangeMs = 300; Tuning.SettleMaxMs = 1000; Tuning.SettlePollMs = 30; Tuning.SettleTwoReads = false; }),
        ("patient 900/2000 ms, poll 60, two reads", () => { Tuning.SettleNoChangeMs = 900; Tuning.SettleMaxMs = 2000; Tuning.SettlePollMs = 60; Tuning.SettleTwoReads = true; }),
    ];

    private static bool TabActive(string tab)
    {
        var line = Look(tab).Split('\n').FirstOrDefault(l => l.Contains($"desc:\"{tab}\"", StringComparison.Ordinal));
        return line is not null && !line.Contains("click", StringComparison.Ordinal);
    }

    private static void Settle(int reps)
    {
        engine.Execute("launch", Json("""{"app": "com.sec.android.app.clockpackage", "then": "none"}"""));
        Report.AppendLine("## Gesture settle (Clock tabs, then the overflow menu)\n");
        Report.AppendLine("| Variant | Taps | ✓ reported | Really done | ✓ but not done | done but ~ | Median ms | Menu ✓ / done | Menu median ms |");
        Report.AppendLine("|---|---|---|---|---|---|---|---|---|");
        foreach (var (name, set) in SettleVariants)
        {
            set();
            int verified = 0, done = 0, falseOk = 0, missed = 0;
            var times = new List<double>();
            for (var i = 0; i < reps; i++)
            {
                var tab = i % 2 == 0 ? "Chronomètre" : "Minuteur";
                var (result, ms, error) = Timed("act", new JsonObject { ["steps"] = new JsonArray(new JsonObject { ["do"] = "tap", ["target"] = "desc:" + tab }), ["then"] = "none" });
                var ok = error is null && result["structuredContent"]?["verified"]?.GetValue<bool>() == true;
                Thread.Sleep(1200);
                var real = TabActive(tab);
                verified += ok ? 1 : 0;
                done += real ? 1 : 0;
                falseOk += ok && !real ? 1 : 0;
                missed += !ok && real ? 1 : 0;
                times.Add(ms);
                if (error is not null)
                    Console.WriteLine($"{name} tap: {error}");
            }
            int menuOk = 0, menuDone = 0;
            var menuTimes = new List<double>();
            for (var i = 0; i < Math.Max(2, reps / 2); i++)
            {
                var before = Look();
                var (result, ms, error) = Timed("act", Json("""{"steps": [{"do": "tap", "target": "desc:Options supplémentaires"}], "then": "none"}"""));
                var ok = error is null && result["structuredContent"]?["verified"]?.GetValue<bool>() == true;
                Thread.Sleep(1000);
                var after = Look();
                var real = after.Split('\n').Length != before.Split('\n').Length;
                menuOk += ok ? 1 : 0;
                menuDone += real ? 1 : 0;
                menuTimes.Add(ms);
                engine.Execute("act", Json("""{"steps": [{"do": "key", "keys": "back"}], "then": "none"}"""));
                Thread.Sleep(600);
            }
            Report.AppendLine(CultureInfo.InvariantCulture, $"| {name} | {reps} | {verified} | {done} | {falseOk} | {missed} | {Median(times):0} | {menuOk} / {menuDone} | {Median(menuTimes):0} |");
            Row("settle", name, new JsonObject { ["taps"] = reps, ["verified"] = verified, ["done"] = done, ["false_ok"] = falseOk, ["missed"] = missed, ["median_ms"] = Median(times), ["menu_verified"] = menuOk, ["menu_done"] = menuDone, ["menu_median_ms"] = Median(menuTimes) });
            Console.WriteLine($"settle {name}: {verified}/{reps} ✓, {done} done, median {Median(times):0} ms");
        }
        Defaults();
        engine.Execute("act", Json("""{"steps": [{"do": "key", "keys": "home"}], "then": "none"}"""));
        Report.AppendLine();
    }

    // ------------------------------------------------------------------ scroll precision

    private static readonly (string Name, Action Set)[] ScrollVariants =
    [
        ("input swipe 350 ms (before 0.23.15)", () => { Tuning.SteadyScroll = false; }),
        ("product defaults", Defaults),
        ("steady, 4 moves, hold 120 ms (0.23.15-0.23.17)", () => { Tuning.SteadyScroll = true; Tuning.SteadyMoves = 4; Tuning.SteadyHoldMs = 120; }),
        ("steady, 2 moves, hold 60 ms", () => { Tuning.SteadyScroll = true; Tuning.SteadyMoves = 2; Tuning.SteadyHoldMs = 60; }),
        ("steady, 4 moves, hold 40 ms", () => { Tuning.SteadyScroll = true; Tuning.SteadyMoves = 4; Tuning.SteadyHoldMs = 40; }),
        ("steady, 8 moves, hold 120 ms", () => { Tuning.SteadyScroll = true; Tuning.SteadyMoves = 8; Tuning.SteadyHoldMs = 120; }),
    ];

    private static Dictionary<string, int> Titles(string look)
    {
        var titles = new Dictionary<string, int>();
        foreach (Match m in Regex.Matches(look, "TextView \"([^\"]+)\" @\\d+,(\\d+) #android:id/title"))
            titles.TryAdd(m.Groups[1].Value, int.Parse(m.Groups[2].Value, CultureInfo.InvariantCulture));
        return titles;
    }

    private static double? Moved(Dictionary<string, int> before, Dictionary<string, int> after)
    {
        var deltas = before.Keys.Intersect(after.Keys).Select(k => (double)(before[k] - after[k])).ToList();
        return deltas.Count == 0 ? null : Median(deltas);
    }

    private static void Scrolls(int reps)
    {
        engine.Execute("launch", Json("""{"app": "com.android.settings", "then": "none"}"""));
        var height = int.Parse(Regex.Match(Look("zzz-none"), @"×(\d+)").Groups[1].Value, CultureInfo.InvariantCulture);
        var expected = height / 3;
        Report.AppendLine(CultureInfo.InvariantCulture, $"## Scroll precision (Settings, one notch = {expected} px of finger travel)\n");
        Report.AppendLine("| Variant | Notches | ✓ reported | Median moved px | Spread px (max-min) | Back to start (median px off) | Median ms |");
        Report.AppendLine("|---|---|---|---|---|---|---|");
        foreach (var (name, set) in ScrollVariants)
        {
            set();
            engine.Execute("act", Json("""{"steps": [{"do": "scroll", "direction": "up", "amount": 4}], "then": "none"}"""));
            Thread.Sleep(800);
            var moved = new List<double>();
            var off = new List<double>();
            var times = new List<double>();
            var verified = 0;
            for (var i = 0; i < reps; i++)
            {
                var start = Titles(Look());
                var (down, ms, error) = Timed("act", Json("""{"steps": [{"do": "scroll", "direction": "down"}], "then": "none"}"""));
                Thread.Sleep(1200);
                var mid = Titles(Look());
                Timed("act", Json("""{"steps": [{"do": "scroll", "direction": "up"}], "then": "none"}"""));
                Thread.Sleep(1200);
                var end = Titles(Look());
                if (error is not null)
                    Console.WriteLine($"{name} scroll: {error}");
                verified += down["structuredContent"]?["verified"]?.GetValue<bool>() == true ? 1 : 0;
                times.Add(ms);
                if (Moved(start, mid) is double m)
                    moved.Add(m);
                if (Moved(start, end) is double back)
                    off.Add(Math.Abs(back));
            }
            var spread = moved.Count > 0 ? moved.Max() - moved.Min() : double.NaN;
            Console.WriteLine($"{name} moved: {string.Join(", ", moved.Select(m => m.ToString("0", CultureInfo.InvariantCulture)))}");
            Report.AppendLine(CultureInfo.InvariantCulture, $"| {name} | {reps} | {verified} | {Median(moved):0} | {spread:0} | {Median(off):0} | {Median(times):0} |");
            Row("scroll", name, new JsonObject { ["notches"] = reps, ["verified"] = verified, ["median_moved_px"] = Median(moved), ["spread_px"] = spread, ["back_off_px"] = Median(off), ["median_ms"] = Median(times), ["expected_px"] = expected });
            Console.WriteLine($"scroll {name}: moved {Median(moved):0} px, spread {spread:0}, {Median(times):0} ms");
        }
        Defaults();
        engine.Execute("act", Json("""{"steps": [{"do": "key", "keys": "home"}], "then": "none"}"""));
        Report.AppendLine();
    }

    // ------------------------------------------------------------------ look and image size

    private static void Looks(int reps)
    {
        engine.Execute("launch", Json("""{"app": "com.android.settings", "then": "none"}"""));
        Report.AppendLine("## look (Settings home)\n");
        Report.AppendLine("| Variant | Runs | Median ms | Image KB | Text chars |");
        Report.AppendLine("|---|---|---|---|---|");
        void Measure(string name, JsonObject args, Action? set = null)
        {
            set?.Invoke();
            var times = new List<double>();
            var bytes = new List<double>();
            var chars = new List<double>();
            for (var i = 0; i < reps; i++)
            {
                // A different screen each time, so "image unchanged" never skips the image.
                engine.Execute("act", Json($$"""{"steps": [{"do": "scroll", "direction": "{{(i % 2 == 0 ? "down" : "up")}}"}], "then": "none"}"""));
                var (result, ms, error) = Timed("look", args.DeepClone().AsObject());
                if (error is not null)
                {
                    Console.WriteLine($"{name}: {error}");
                    continue;
                }
                times.Add(ms);
                bytes.Add(ImageBytes(result) / 1024.0);
                chars.Add(Text(result).Length);
            }
            Report.AppendLine(CultureInfo.InvariantCulture, $"| {name} | {reps} | {Median(times):0} | {Median(bytes):0} | {Median(chars):0} |");
            Row("look", name, new JsonObject { ["runs"] = reps, ["median_ms"] = Median(times), ["image_kb"] = Median(bytes), ["text_chars"] = Median(chars) });
            Console.WriteLine($"look {name}: {Median(times):0} ms, {Median(bytes):0} KB");
            Defaults();
        }
        Measure("text only", new JsonObject());
        Measure("image, product (lossless WebP)", new JsonObject { ["show"] = "both" });
        Measure("image, lossless PNG (until 0.23.19)", new JsonObject { ["show"] = "both" }, () => Tuning.LookLosslessFormat = "png");
        engine.Execute("act", Json("""{"steps": [{"do": "key", "keys": "home"}], "then": "none"}"""));
        Report.AppendLine();
    }

    // ------------------------------------------------------------------ reading (no gesture)

    private static void Reads(int reps)
    {
        Report.AppendLine("## Reading tools (no gesture)\n");
        Report.AppendLine("| Call | Runs | Median ms | Min ms | Max ms |");
        Report.AppendLine("|---|---|---|---|---|");
        foreach (var (name, tool, args) in new[]
        {
            ("messages list (15)", "messages", Json("""{"limit": 15}""")),
            ("RCS group thread (30, no image)", "messages", Json("""{"conversation": 23, "limit": 30, "images": 0}""")),
            ("notifications", "notifications", new JsonObject()),
            ("apps info", "apps", Json("""{"action": "info", "app": "com.google.android.apps.messaging"}""")),
            ("checkup", "checkup", new JsonObject()),
            ("calendar 7 days", "info", Json("""{"kind": "calendar"}""")),
        })
        {
            var times = new List<double>();
            for (var i = 0; i < reps; i++)
            {
                var (_, ms, error) = Timed(tool, args.DeepClone().AsObject());
                if (error is null)
                    times.Add(ms);
                else
                    Console.WriteLine($"{name}: {error}");
            }
            Report.AppendLine(CultureInfo.InvariantCulture, $"| {name} | {times.Count} | {Median(times):0} | {times.DefaultIfEmpty(double.NaN).Min():0} | {times.DefaultIfEmpty(double.NaN).Max():0} |");
            Row("read", name, new JsonObject { ["runs"] = times.Count, ["median_ms"] = Median(times) });
        }
        Report.AppendLine();
    }
}
