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
    private readonly Func<string, int, NativeScreen, IVideoRecorder> recorder;
    private readonly object gate = new();
    private string verifiedHardwareForOperation = "";
    private sealed class SessionState
    {
        public List<UiElement> Last = [];
        public NativeScreen? Screen;
        public DateTime Seen;
        public double Scale = 1, ScaleY = 1;
        public ImageChainResult? Image;
        public int ImageWidth, ImageHeight, NextId = 1;
        public bool CoordinatesValid = true;
        public (string Key, byte[] Thumbnail)? Sent;
    }

    private readonly Dictionary<string, SessionState> sessions = [];
    private SessionState Current
    {
        get
        {
            var key = NativeSafety.Session.Value ?? "stdio";
            if (!sessions.TryGetValue(key, out var state))
            {
                sessions[key] = state = new();
            }

            return state;
        }
    }
    private List<UiElement> last { get => Current.Last; set => Current.Last = value; }
    private NativeScreen? lastScreen { get => Current.Screen; set => Current.Screen = value; }
    private DateTime lastSeen { get => Current.Seen; set => Current.Seen = value; }
    private double imageScale { get => Current.Scale; set => Current.Scale = value; }
    private readonly string stateRoot;
    private static readonly HashSet<string> Gestures = ["tap", "long_press", "double_tap", "type", "clear", "key", "swipe", "scroll", "wait", "open_url", "zoom", "drag"];
    public NativeEngine(AdbClient client, IAndroidQuery? queryClient = null, string? localState = null, Func<IPhoneControl>? controller = null, Func<string, int, NativeScreen, IVideoRecorder>? videoRecorder = null)
    {
        adb = client;
        query = queryClient ?? new AndroidQuery(client);
        control = controller ?? (() => new PhoneControl(adb, query));
        recorder = videoRecorder ?? ((remote, seconds, screen) => new AdbVideoRecorder(adb, remote, seconds, screen.Width, screen.Height));
        stateRoot = localState ?? Path.Join(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "NexusAndroid", "state");
        phoneLockPath = localState is null ? Path.Join(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".nexus", "android.lock") : Path.Join(localState, "android.lock");
        awakePath = localState is null ? Path.Join(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".nexus", "nexus-android", "awake.json") : Path.Join(localState, "awake.json");
        Directory.CreateDirectory(stateRoot);
    }
    private static string Str(JsonObject a, string key, string fallback = "") => a[key]?.ToString() ?? fallback;
    private static bool Bool(JsonObject a, string key) => a[key]?.GetValue<bool>() == true;
    private static int Num(JsonObject a, string key, int fallback) => a[key] is null ? fallback : a[key]!.GetValue<int>();
    // Claude Code shows structuredContent alone when a tool returns it: the text report rides along in it as "text",
    // otherwise checkup and the other reports reached Claude as their bare fields.
    private static JsonObject Text(string text, JsonObject? structured = null, bool codes = false)
    {
        var masked = NativeSafety.Mask(text, codes: codes);
        var data = NativeSafety.MaskJson(structured);
        if (data is JsonObject fields && !fields.ContainsKey("text"))
            fields["text"] = masked;
        return new()
        {
            ["content"] = new JsonArray(new JsonObject { ["type"] = "text", ["text"] = masked }),
            ["structuredContent"] = data
        };
    }
    private static string Hash(string value) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value)));
    private static bool Mutating(string[] args) => args.Length > 0 && args[0] switch
    {
        "input" or "wm" or "svc" => true,
        "am" => args.Skip(1).Any(a => a is "start" or "force-stop" or "broadcast"),
        "settings" => args.Length > 1 && args[1] == "put",
        "cmd" => args.Any(a => a is "execute-app-function" or "set-location-enabled" or "set_dnd" or "--set"),
        "pm" => args.Any(a => a is "clear" or "uninstall"),
        _ => false,
    };
    private string Shell(params string[] args)
    {
        if (Mutating(args))
            NativeSafety.StopGuard();
        return adb.Shell(string.Join(' ', args.Select(NativeSafety.Quote)));
    }
    /// <summary>One tool call, and its one line in the common Nexus journal whatever ends it: a result, a guard-rail
    /// refusal, or a failure before any gesture (invalid arguments, phone busy with another agent, no device).</summary>
    public JsonObject Execute(string name, JsonObject args)
    {
        var began = System.Diagnostics.Stopwatch.StartNew();
        try
        {
            var result = Run(name, args);
            NativeJournal.Write(name, lastScreen is null ? null : Foreground(lastScreen), result["structuredContent"]?["verified"]?.ToJsonString() == "false" ? "~" : "✓", began.Elapsed.TotalMilliseconds);
            return result;
        }
        catch (Exception e)
        {
            NativeJournal.Write(name, lastScreen is null ? null : Foreground(lastScreen), "✗", began.Elapsed.TotalMilliseconds, e);
            throw;
        }
    }
    private JsonObject Run(string name, JsonObject args)
    {
        NativeSchemas.Validate(name == "phone_map" ? "map" : name, args);
        if (name == "capture" && Str(args, "action") == "record")
        {
            var start = args.DeepClone().AsObject();
            start["action"] = "start";
            start["seconds"] = Num(args, "seconds", 30);
            Run("capture", start);
            var recording = video!;
            try
            {
                recording.WaitForExitAsync(NativeSafety.Cancellation.Value).GetAwaiter().GetResult();
                return Run("capture", new JsonObject { ["action"] = "stop" });
            }
            catch (OperationCanceledException)
            {
                DiscardVideo(NativeSafety.Session.Value ?? "stdio");
                throw;
            }
        }
        var deadline = DateTime.UtcNow.AddSeconds(30);
        while (!Monitor.TryEnter(gate, 80))
        {
            NativeSafety.Cancellation.Value.ThrowIfCancellationRequested();
            if (DateTime.UtcNow >= deadline)
                throw new IOException("Phone busy with another agent, no action sent");
        }
        try
        {
            using var lease = new PhoneLease(phoneLockPath);
            if (name is not ("checkup" or "phone") && !adb.EnsureDevice())
                throw new IOException("No authorized Android device connected; use checkup for connection details");
            verifiedHardwareForOperation = adb.VerifiedSelectedHardwareIdentity ?? "";
            if (name is not ("checkup" or "phone"))
                RestoreExpiredAwake();
            return name switch
            {
                "phone" => Phone(),
                "checkup" => Checkup(args),
                "look" => Look(args),
                "act" => Act(args),
                "launch" => Launch(args),
                "messages" => Messages(args),
                "draft" => Draft(args),
                "notifications" => Notifications(args),
                "files" => Files(args),
                "capture" => Capture(args),
                "logs" => Logs(args),
                "clipboard" => Clipboard(args),
                "info" => Info(args),
                "settings" => Settings(args),
                "functions" => Functions(args),
                "web" => Web(args),
                "apps" => Apps(args),
                "phone_map" or "map" => Map(args),
                _ => throw new ArgumentException("Unknown tool: " + name)
            };
        }
        finally
        {
            verifiedHardwareForOperation = "";
            Monitor.Exit(gate);
        }
    }
    private NativeScreen Read(bool image = false)
    {
        var screen = query.Screen(image || last.Any(e => e.IsOcr));
        ObserveRecording(screen);
        if (lastScreen is not null && (lastScreen.Width != screen.Width || lastScreen.Height != screen.Height))
        {
            Current.CoordinatesValid = false;
        }
        return screen;
    }
    public void CloseSession(string session)
    {
        DiscardVideo(session);
        lock (gate)
            sessions.Remove(session);
        NativeSafety.ForgetSession(session);
    }
    public void Shutdown()
    {
        string? owner;
        lock (gate)
            owner = video is null ? null : videoOwner;
        if (owner is not null)
            DiscardVideo(owner);
        lock (gate)
        {
            using var cleanup = new CancellationTokenSource(TimeSpan.FromSeconds(10));
            var previous = NativeSafety.Cancellation.Value;
            NativeSafety.Cancellation.Value = cleanup.Token;
            using (awakeTimer)
            {
                try
                {
                    using var lease = new PhoneLease(phoneLockPath);
                    RestoreExpiredAwake(forceOwn: true);
                }
                finally { NativeSafety.Cancellation.Value = previous; }
            }
        }
    }
    private List<UiElement> Elements(NativeScreen screen)
    {
        var elements = ScreenParser.ParseXml(screen.Xml);
        if (elements.Any(e => e.Text.Length > 0 || e.ContentDesc.Length > 0) || screen.Image is null)
            return elements;
        try
        {
            var lines = NativeOcr.Read(screen.Image);
            var codeContext = NativeSafety.HasCodeKeyword(string.Join(' ', lines.Select(l => l.Text)));
            foreach (var line in lines)
            {
                var masked = NativeSafety.MaskCodes(NativeSafety.Mask(line.Text), codeContext);
                elements.Add(new("e" + (elements.Count + 1), masked, "", "OCR", Foreground(screen), line.Left, line.Top, line.Right, line.Bottom, (line.Left + line.Right) / 2, (line.Top + line.Bottom) / 2, true, false, true, false, false, false, false, false, line.Text, "ocr:" + (elements.Count + 1), "", true));
            }
        }
        catch (Exception e) when (e is IOException or System.Runtime.InteropServices.COMException) { presenceWarning += " Windows OCR unavailable: " + e.Message; }
        return elements;
    }
    private static string Foreground(NativeScreen screen) => Regex.Match(screen.State, @"(?:mCurrentFocus|mFocusedApp)=[^\r\n]*?\s([\w.]+)/").Groups[1].Value;
    private static bool Locked(NativeScreen screen) => Regex.IsMatch(screen.State, @"(?:isKeyguardShowing|mShowingLockscreen|KeyguardShowing|mIsShowing|isStatusBarKeyguard)=true");
    private static bool LockKnown(NativeScreen screen) => Regex.IsMatch(screen.State, @"(?:isKeyguardShowing|mShowingLockscreen|KeyguardShowing|mIsShowing|isStatusBarKeyguard)=(true|false)");
    private static bool Sensitive(UiElement e) =>
        e.IsPassword
        || NativeSafety.Mask(e.RawText) != e.RawText
        || NativeSafety.Mask(e.RawDescription) != e.RawDescription
        || e.Text.Contains("[masked", StringComparison.OrdinalIgnoreCase)
        || e.ContentDesc.Contains("[masked", StringComparison.OrdinalIgnoreCase);
    private JsonObject Look(JsonObject args)
    {
        if (args["region"] is JsonArray region)
        {
            var full = Current.Image ?? throw new InvalidOperationException("No full image previously sent in this session");
            if (region.Count != 4)
                throw new ArgumentException("region needs four coordinates");
            var p = region.Select(n => (int)Math.Round(n!.GetValue<double>())).ToArray();
            var x1 = Math.Clamp(p[0], 0, full.Width);
            var y1 = Math.Clamp(p[1], 0, full.Height);
            var x2 = Math.Clamp(p[2], 0, full.Width);
            var y2 = Math.Clamp(p[3], 0, full.Height);
            if (x2 <= x1 || y2 <= y1)
                throw new ArgumentException("Empty region");
            var decoded = full.Pixels ?? ImageChain.Decode(full.Bytes);
            var cropped = new PixelBuffer(x2 - x1, y2 - y1);
            for (var y = y1; y < y2; y++)
            {
                Buffer.BlockCopy(decoded.Bgra, (y * decoded.Width + x1) * 4, cropped.Bgra, (y - y1) * cropped.Width * 4, cropped.Width * 4);
            }
            var enlarged = ImageChain.ResizeBilinear(cropped, cropped.Width * 2, cropped.Height * 2);
            var result = ImageChain.ProcessChain(enlarged, maxBytes: ImageChain.DefaultMaxInlineBytes);
            Current.Sent = null;/* the zoom is now the last image sent: the next full view goes out again */
            var regionOutput = Text($"Zoom of last full image [{x1},{y1},{x2},{y2}]. Action coordinates remain those of that full image.");
            regionOutput["content"]!.AsArray().Add((JsonNode?)new JsonObject { ["type"] = "image", ["mimeType"] = result.MimeType, ["data"] = Convert.ToBase64String(result.Bytes) });
            return regionOutput;
        }
        var show = Str(args, "show", "auto");
        if (show == "none")
            return Text("No screen view requested; previous references remain bound to their observed contents.");
        var image = show is "both" or "image" || args["region"] is not null;
        var previous = last.ToList();
        // One read when the phone is open: the image comes with the tree; a lock lifted first needs the image read again.
        var screen = Read(image);
        if (Locked(screen))
        {
            screen = LiftTrustedLock(screen);
            if (image)
                screen = Read(true);
        }
        ExtendAwake(screen);
        var elements = Elements(screen);
        if (show == "auto" && !elements.Any(e => e.Text.Length > 0 || e.ContentDesc.Length > 0))
        {
            image = true;
            screen = Read(true);
            elements = Elements(screen);
        }
        var assigned = new List<UiElement>();
        var used = new HashSet<string>();
        foreach (var element in elements)
        {
            var candidates = previous.Where(p => p.ResourceId == element.ResourceId && p.ClassName == element.ClassName && p.PackageName == element.PackageName && p.Left == element.Left && p.Top == element.Top && !used.Contains(p.Id)).ToList();
            var id = candidates.Count == 1 ? candidates[0].Id : "e" + Current.NextId++;
            used.Add(id);
            assigned.Add(element with
            {
                Id = id
            });
        }
        elements = assigned;
        var fresh = DateTime.UtcNow;
        last = elements;
        lastScreen = screen;
        lastSeen = fresh;
        if (Current.Image is not null && (Current.ImageWidth != screen.Width || Current.ImageHeight != screen.Height))
            Current.CoordinatesValid = false;
        var filtered = Str(args, "query") is { Length: > 0 } q ? elements.Where(e => (e.Text + " " + e.ContentDesc).Contains(q, StringComparison.OrdinalIgnoreCase)).ToList() : elements;
        var tree = ScreenParser.FormatCompactTree(filtered.Take(300), imageScale, Current.ScaleY);
        if (show == "changes")
        {
            var differences = new List<string>();
            foreach (var element in filtered)
            {
                var old = previous.FirstOrDefault(e => e.Id == element.Id);
                if (old is null)
                    differences.Add("+ " + ScreenParser.FormatCompactTree([element], imageScale, Current.ScaleY).TrimEnd());
                else if (old != element)
                    differences.Add("~ " + ScreenParser.FormatCompactTree([element], imageScale, Current.ScaleY).TrimEnd());
            }

            foreach (var old in previous.Where(p => elements.All(e => e.Id != p.Id)))
            {
                differences.Add("- [" + old.Id[1..] + "] removed");
            }

            tree = differences.Count > 0 ? string.Join('\n', differences) : "No visible element changed";
        }
        var hints = LookHints(screen, elements);
        var output = Text($"{Foreground(screen)} · {screen.Width}×{screen.Height}" + (Locked(screen) ? " · locked" : "") + hints + "\n" + NativeSafety.Mask(screen.Error) + "\n" + (show == "image" ? "" : tree), codes: false);
        if (image && screen.Image is not null)
        {
            if (elements.Count == 0 && !Bool(args, "confirm"))
            {
                output["content"]!.AsArray().Add((JsonNode?)new JsonObject { ["type"] = "text", ["text"] = "Image not sent: UI tree unavailable, code masking cannot be guaranteed. confirm=true only if the user accepts this exact unmasked image." });
                return output;
            }
            var blackouts = elements.Where(Sensitive).Select(e => (e.Left, e.Top, e.Right, e.Bottom)).ToList();
            var seen = new List<string>();
            try
            {
                var lines = NativeOcr.Read(screen.Image);
                seen.AddRange(lines.Select(l => $"{NativeSafety.MaskCodes(NativeSafety.Mask(l.Text), false)}|{l.Left},{l.Top},{l.Right},{l.Bottom}"));
                var context = NativeSafety.HasCodeKeyword(string.Join(' ', lines.Select(l => l.Text)));
                foreach (var line in lines.Where(l => NativeSafety.MaskCodes(NativeSafety.Mask(l.Text), context) != l.Text))
                    blackouts.Add((line.Left, line.Top, line.Right, line.Bottom));
            }
            catch (Exception e) when (e is IOException or System.Runtime.InteropServices.COMException) { output["content"]!.AsArray().Add((JsonNode?)new JsonObject { ["type"] = "text", ["text"] = "Windows OCR unavailable; codes drawn outside the UI tree may remain visible: " + NativeSafety.Mask(e.Message) }); }
            var marks = Str(args, "marks", "auto");
            var badges = show == "both" && marks != "none" ? elements.Where(e => marks == "all" || (e.Clickable && string.IsNullOrWhiteSpace(e.Text))).Select(e => (int.Parse(e.Id[1..], CultureInfo.InvariantCulture), e.CenterX, e.CenterY)).ToList() : null;
            var result = ImageChain.ProcessEncodedImage(screen.Image, maxBytes: Tuning.LookImageMaxBytes, preferredLosslessFormat: Tuning.LookLosslessFormat, blackouts: blackouts, badges: badges);
            // Same session, same marks, same geometry and same full screen content (system bar included, text read by OCR
            // included), and the same colour print: the image the agent already holds is not sent again.
            var key = string.IsNullOrEmpty(screen.Error) && elements.Count > 0 ? string.Join('#', ContentPrint(elements, seen, Foreground(screen)), screen.Width, screen.Height, result.Width, result.Height, string.Join(',', badges?.Select(b => b.Item1) ?? [])) : null;
            var unchanged = key is not null && Current.Image is not null && Current.Sent is { } sent && sent.Key == key && ImageChain.SameThumbnail(sent.Thumbnail, result.Thumbnail);
            if (!unchanged)
            {
                Current.Image = result;
                Current.Sent = key is not null && result.Thumbnail is not null ? (key, result.Thumbnail) : null;
                imageScale = (double)result.Width / screen.Width;
                Current.ScaleY = (double)result.Height / screen.Height;
            }
            Current.ImageWidth = screen.Width;
            Current.ImageHeight = screen.Height;
            Current.CoordinatesValid = true;
            var header = $"{Foreground(screen)} · {screen.Width}×{screen.Height}" + (Locked(screen) ? " · locked" : "") + hints + (unchanged ? " · image unchanged since the last one sent (not sent again)" : "");
            output["content"]![0]!["text"] = NativeSafety.Mask(header + "\n" + NativeSafety.Mask(screen.Error) + "\n" + (show == "image" ? "" : ScreenParser.FormatCompactTree(filtered.Take(300), imageScale, Current.ScaleY)), codes: false);
            if (!unchanged)
                output["content"]!.AsArray().Add((JsonNode?)new JsonObject { ["type"] = "image", ["mimeType"] = result.MimeType, ["data"] = Convert.ToBase64String(result.Bytes) });
        }
        return output;
    }
    /// <summary>Short notes for the AI after the screen header: a dialog window in front (what lies behind it is not in the
    /// tree and cannot be reached), and the app's own AppFunctions, a structured path that needs no screen.</summary>
    private string LookHints(NativeScreen screen, List<UiElement> elements)
    {
        var notes = "";
        var own = elements.Where(e => !e.IsOcr && e.PackageName == Foreground(screen) && e.Right > e.Left && e.Bottom > e.Top).ToList();
        if (own.Count > 0 && screen.Width > 0 && screen.Height > 0)
        {
            // A full-screen app has a root view the size of the screen; a dialog window's largest view is the dialog.
            var largest = own.Max(e => (long)(e.Right - e.Left) * (e.Bottom - e.Top));
            if (largest < (long)screen.Width * screen.Height * 6 / 10)
                notes += " · dialog in front: what lies behind is not reachable (back closes it)";
        }
        var count = AppFunctionCount(Foreground(screen));
        if (count > 0)
            notes += $" · {count} app functions: functions(app=\"{Foreground(screen)}\") works without the screen";
        return notes;
    }
    private (string Serial, Dictionary<string, int> Counts, DateTime Until) functionApps = ("", [], DateTime.MinValue);
    /// <summary>Enabled AppFunctions per package, read once every 10 minutes (one shell), never failing a look.</summary>
    private int AppFunctionCount(string package)
    {
        if (package.Length == 0 || AndroidPolicy.IsProtectedPackage(package))
            return 0;
        var serial = adb.SelectedSerial ?? "";
        if (functionApps.Serial != serial || functionApps.Until < DateTime.UtcNow)
        {
            var counts = new Dictionary<string, int>();
            try
            {
                if (JsonNode.Parse(Shell("cmd", "app_function", "list-app-functions")) is JsonObject raw)
                {
                    foreach (var pair in raw.Where(p => p.Value is JsonArray))
                    {
                        var enabled = pair.Value!.AsArray().OfType<JsonObject>().SelectMany(e => e.Where(p => p.Key.StartsWith("AppFunctionStaticMetadata", StringComparison.Ordinal)).Select(p => p.Value).OfType<JsonObject>()).Count(item => !(item["enabledByDefault"] is JsonArray v && v.Count > 0 && string.Equals(v[0]?.ToString(), "false", StringComparison.OrdinalIgnoreCase)));
                        if (enabled > 0)
                            counts[pair.Key] = enabled;
                    }
                }
            }
            catch (Exception e) when (e is IOException or System.Text.Json.JsonException or InvalidOperationException or TimeoutException) {/* no hint rather than a failed look */}
            functionApps = (serial, counts, DateTime.UtcNow.AddMinutes(10));
        }
        return functionApps.Counts.GetValueOrDefault(package);
    }

    /// <summary>Full print of a screen for "image unchanged": every element, system bar included (a tile or a notification
    /// that changes state), exact bounds and states, masked text only, plus the text read on the image by OCR.</summary>
    internal static string ContentPrint(IEnumerable<UiElement> elements, IEnumerable<string> seen, string package)
    {
        var rows = elements
            .Select(e => $"{e.PackageName}|{e.ClassName}|{e.Text}|{e.ContentDesc}|{e.ResourceId}"
                + $"|{e.Left},{e.Top},{e.Right},{e.Bottom}"
                + $"|{e.Checked},{e.Selected},{e.Focused},{e.Enabled},{e.ShowingHint}|{e.IsOcr}")
            .Order(StringComparer.Ordinal);
        return Hash(string.Join('\n', new[] { package }.Concat(rows).Concat(seen.Order(StringComparer.Ordinal))));
    }
    private (int X, int Y, UiElement? Element) Resolve(JsonNode? target, List<UiElement> fresh)
    {
        if (target is null)
            throw new ArgumentException("Missing target: use [N], visible text, id:resource-id or x,y");
        var text = target.ToString();
        UiElement? element = null;
        if (Regex.IsMatch(text, @"^\[?\d+\]?$"))
        {
            if (DateTime.UtcNow - lastSeen > TimeSpan.FromSeconds(60))
                throw new InvalidOperationException("Stale [N] reference: look again");
            element = last.SingleOrDefault(e => e.Id == "e" + text.Trim('[', ']')) ?? throw new ArgumentException("Unknown [N] reference");
            var matches = fresh.Where(e => e.ResourceId == element.ResourceId && e.RawText == element.RawText && e.ClassName == element.ClassName && e.PackageName == element.PackageName && e.Left == element.Left && e.Top == element.Top && e.Right == element.Right && e.Bottom == element.Bottom && e.ZPath == element.ZPath).ToList();
            if (matches.Count != 1)
                throw new InvalidOperationException("Target changed since look: nothing sent; look again");
            element = matches[0];
        }
        else if (Regex.Match(text, @"^\s*@?\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$") is { Success: true } point)
        {
            if (!Current.CoordinatesValid)
                throw new InvalidOperationException("Screen dimensions changed since the image: request a fresh full image before coordinate input");
            var x = (int)(double.Parse(point.Groups[1].Value, CultureInfo.InvariantCulture) / imageScale);
            var y = (int)(double.Parse(point.Groups[2].Value, CultureInfo.InvariantCulture) / Current.ScaleY);
            if (lastScreen is null || x < 0 || y < 0 || x >= lastScreen.Width || y >= lastScreen.Height)
                throw new ArgumentException("Coordinates outside the last observed image");
            var at = fresh.Where(e => x >= e.Left && x < e.Right && y >= e.Top && y < e.Bottom).OrderByDescending(e => e.ZPath, StringComparer.Ordinal).ThenBy(e => (long)(e.Right - e.Left) * (e.Bottom - e.Top)).FirstOrDefault();
            if (at is { Enabled: false })
                throw new InvalidOperationException("Target disabled");
            return (x, y, at);
        }
        else
        {
            var normalized = NativeSafety.Comparable(text);
            var matches = fresh.Where(e => text.StartsWith("id:", StringComparison.Ordinal) ? e.ResourceId == text[3..] : text.StartsWith("desc:", StringComparison.Ordinal) ? e.ContentDesc == text[5..] || e.RawDescription == text[5..] : NativeSafety.Comparable(e.Text) == normalized || NativeSafety.Comparable(e.ContentDesc) == normalized).ToList();
            if (matches.Count != 1)
                throw new ArgumentException(matches.Count == 0 ? "Target not found" : "Ambiguous target: use id:resource-id or [N]");
            element = matches[0];
        }
        if (!element.Enabled)
            throw new InvalidOperationException("Target disabled: nothing sent");
        if (element.Right <= element.Left || element.Bottom <= element.Top)
            throw new InvalidOperationException("Target outside visible screen: no input sent");
        var front = fresh.Where(e => element.CenterX >= e.Left && element.CenterX < e.Right && element.CenterY >= e.Top && element.CenterY < e.Bottom).OrderByDescending(e => e.ZPath, StringComparer.Ordinal).FirstOrDefault();
        if (front is not null && front.ZPath.Length > 0 && element.ZPath.Length > 0 && !front.ZPath.StartsWith(element.ZPath, StringComparison.Ordinal) && !element.ZPath.StartsWith(front.ZPath, StringComparison.Ordinal))
            throw new InvalidOperationException("Target is covered by another element: nothing clicked");
        return (element.CenterX, element.CenterY, element);
    }
    private static JsonObject TargetIdentity(UiElement e) => new()
    {
        ["resource_id"] = e.ResourceId,
        ["class"] = e.ClassName,
        ["package"] = e.PackageName,
        ["path"] = e.ZPath,
        ["bounds"] = new JsonArray(e.Left, e.Top, e.Right, e.Bottom),
        ["checked"] = e.Checked,
        ["selected"] = e.Selected,
        ["text"] = Sensitive(e) ? "[masked]#" + NativeSafety.Seal(e.RawText + "\0" + e.RawDescription) : e.Text,
        ["description"] = Sensitive(e) ? "[masked]" : e.ContentDesc,
    };
    private static UiElement? AtPoint(List<UiElement> elements, int x, int y) => elements.Where(e => x >= e.Left && x < e.Right && y >= e.Top && y < e.Bottom).OrderByDescending(e => e.ZPath, StringComparer.Ordinal).ThenBy(e => (long)(e.Right - e.Left) * (e.Bottom - e.Top)).FirstOrDefault();
    private static UiElement? FollowTarget(UiElement before, List<UiElement> after)
    {
        var candidates = after.Where(e => e.ResourceId == before.ResourceId && e.PackageName == before.PackageName && e.ClassName == before.ClassName).ToList();
        var exact = candidates.Where(e => e.Left == before.Left && e.Top == before.Top && e.Right == before.Right && e.Bottom == before.Bottom && e.ZPath == before.ZPath).ToList();
        return exact.Count == 1 ? exact[0] : before.ResourceId.Length > 0 && candidates.Count == 1 ? candidates[0] : null;
    }
    private static string? Guard(NativeScreen screen, List<UiElement> elements, JsonObject step, UiElement? target, string gesture, bool authorize = true, UiElement? destination = null)
    {
        NativeSafety.StopGuard();
        if (Locked(screen) && gesture is not ("back" or "home" or "recent"))
            throw new InvalidOperationException("Phone locked: no input sent. A required code is never entered by this tool.");
        var pkg = Foreground(screen);
        if (pkg.Length == 0)
            throw new InvalidOperationException("Foreground app unknown: no input sent");
        var label = (target?.RawText ?? "") + " " + (target?.RawDescription ?? "") + " " + (destination?.RawText ?? "") + " " + (destination?.RawDescription ?? "");
        var judgedPackages = new HashSet<string> { pkg, target?.PackageName ?? "", destination?.PackageName ?? "" };
        var context = string.Join(' ', elements.Where(e => judgedPackages.Contains(e.PackageName)).Select(e => e.RawText + " " + e.RawDescription));
        var safeKey = gesture is "back" or "home" or "recent";
        if (!LockKnown(screen) && !safeKey)
            throw new InvalidOperationException("Lock state unreadable; no input sent");
        if (!safeKey && new[] { pkg, target?.PackageName, destination?.PackageName }.Any(AndroidPolicy.IsProtectedPackage))
        {
            var irreversible = NativeSafety.Risky(label) || NativeSafety.ContextRisk(label, context);
            var slider = new[] { target, destination }.Any(e => e is not null && Regex.IsMatch(e.ClassName + " " + e.ResourceId + " " + e.RawDescription, @"(?i)slider|seekbar|glisser|slide to"));
            if (irreversible && (gesture != "scroll" || slider))
                throw new NativeSafety.GuardRefusal("Irreversible, money and send gestures in protected apps are refused");
            if (gesture != "scroll" && new[] { target, destination }.Any(e => e is { Clickable: true, Editable: false } && (e.RawText + e.RawDescription).Trim().Length == 0))
                throw new NativeSafety.GuardRefusal("Unnamed icon in a protected app is always refused; it may initiate payment or transfer");
            if (gesture != "scroll" && !Bool(step, "confirm"))
                throw new InvalidOperationException("Protected app: reading and scrolling only without confirm=true");
        }
        if (target?.IsPassword == true && gesture is "type" or "clear")
            throw new NativeSafety.GuardRefusal("Password field: typing refused");
        // Compose and RecyclerView rows put their words in child or overlapping views: an empty clickable container is
        // judged on the short labels drawn inside it (menu entry, button, row title), long snippets are content, not action.
        if (target is not null && label.Trim().Length == 0)
            label = string.Join(' ', elements.Where(e => !ReferenceEquals(e, target) && !e.Editable && !e.IsOcr && e.PackageName == target.PackageName && e.Left >= target.Left && e.Top >= target.Top && e.Right <= target.Right && e.Bottom <= target.Bottom).Select(e => (e.RawText + " " + e.RawDescription).Trim()).Where(t => t.Length is > 0 and <= 60).Distinct());
        var trigger = gesture is "type" or "clear" ? "" : label;
        var unnamedSend = gesture is not ("scroll" or "zoom") && target is not null && !target.Editable && (Regex.IsMatch(target.ResourceId, @"(?i)send|compose_message_send|send_message_button|send_button") || target.Clickable && label.Trim().Length == 0 && Regex.IsMatch(pkg, @"(?i)messag|messenger|chat|mail|sms|dialer|telecom|incallui|whatsapp|telegram|discord|instagram|twitter|facebook|slack|teams"));
        var risky = NativeSafety.Risky(trigger) || NativeSafety.ContextRisk(trigger, context) || unnamedSend || gesture == "enter" || gesture == "type" && Str(step, "text").Contains('\n');
        if (risky && gesture == "double_tap")
            throw new InvalidOperationException("No double tap on an outward or irreversible trigger");
        if (risky && target?.IsOcr == true)
            throw new InvalidOperationException("Outward or irreversible action on a drawn surface cannot be bound to an exact readable agreement; no input sent");
        if (target is null && !safeKey && !(elements.Count > 0 && gesture is "scroll" or "zoom") && gesture is not ("tab" or "left" or "right" or "up" or "down" or "escape"))
            throw new InvalidOperationException("No readable element under target: no blind input sent");
        if (risky && authorize)
        {
            var exact = new JsonObject { ["tool"] = "android", ["gesture"] = gesture, ["package"] = pkg, ["step"] = step.DeepClone(), ["target"] = target is null ? null : TargetIdentity(target), ["destination"] = destination is null ? null : TargetIdentity(destination), ["screen"] = new JsonArray(elements.Where(e => !e.IsOcr && e.PackageName != "com.android.systemui" && !Regex.IsMatch(e.PackageName, @"(?i)inputmethod|honeyboard|keyboard|swiftkey")).Select(e => (JsonNode?)new JsonObject { ["package"] = e.PackageName, ["id"] = e.ResourceId, ["class"] = e.ClassName, ["text"] = Sensitive(e) ? "[masked]#" + NativeSafety.Seal(e.RawText + "\0" + e.RawDescription) : e.Text, ["description"] = Sensitive(e) ? "[masked]" : e.ContentDesc, ["checked"] = e.Checked, ["selected"] = e.Selected }).ToArray()) };
            exact["step"]!.AsObject().Remove("accord");
            exact["step"]!.AsObject().Remove("confirm");
            var sealedAction = exact.ToJsonString();
            NativeSafety.Require(step, sealedAction, NativeSafety.Outward(trigger) || unnamedSend || gesture == "enter" || gesture == "type" || NativeSafety.ContextRisk(trigger, context) && Regex.IsMatch(NativeSafety.Normalize(context), "paiement|payer|payment|virement|transfer|iban|checkout|carte bancaire|credit card"));
            return sealedAction;
        }
        return null;
    }
    // act runs each step as: observe → aim → guard → send → settle → verify → remember. One observation per step: the
    // guard always judges the very screen the input is aimed on (type and clear re-guard after focusing their field).
    private sealed class ActRun
    {
        public readonly List<string> Reports = [];
        public readonly JsonArray SavedSteps = [];
        public bool AllVerified = true;
        public bool UnstableTarget;
    }
    private sealed class Aim
    {
        public int X, Y;
        public UiElement? Element;
        public int EndX, EndY;
        public UiElement? EndElement;
    }
    private JsonObject Act(JsonObject args)
    {
        NativeSafety.StopGuard();
        var (steps, templates) = ActSteps(args);
        var run = new ActRun();
        var remember = Str(args, "remember");
        var routinePackage = remember.Length > 0 ? Foreground(Read()) : "";
        var toastMark = ToastMark();
        for (var index = 0; index < steps.Count; index++)
        {
            var step = steps[index]!.AsObject();
            var action = Str(step, "do");
            var began = System.Diagnostics.Stopwatch.StartNew();
            try
            {
                if (!ActStep(step, action, templates[index]!.AsObject(), run, began))
                    break;
            }
            catch (Exception ex) when (NativeSafety.Recoverable(ex))
            {
                // Step boundary: any failure is reported as ✗ and stops the batch, never crashes the host.
                run.Reports.Add("✗ " + action + ": " + ex.Message);
                NativeJournal.Write(action, lastScreen is null ? null : Foreground(lastScreen), "✗", began.Elapsed.TotalMilliseconds, ex);
                run.AllVerified = false;
                break;
            }
        }
        if (remember.Length > 0 && run.AllVerified)
            SaveRoutine(remember, routinePackage, run);
        if (presenceWarning.Length > 0)
        {
            run.Reports.Add("⚠ " + presenceWarning);
            presenceWarning = "";
        }
        var view = Look(new JsonObject { ["show"] = Str(args, "then", "auto") });
        var toasts = ToastsSince(toastMark);
        run.Reports.AddRange(toasts.Select(t => $"Toast {t.Package}: {t.Text}"));
        view["content"]!.AsArray().Insert(0, new JsonObject { ["type"] = "text", ["text"] = NativeSafety.Mask(string.Join('\n', run.Reports)) });
        view["structuredContent"] = new JsonObject
        {
            ["verified"] = run.AllVerified,
            ["reports"] = new JsonArray(run.Reports.Select(v => (JsonNode?)JsonValue.Create(NativeSafety.Mask(v))).ToArray()),
            ["toasts"] = new JsonArray(toasts.Select(t => (JsonNode?)new JsonObject { ["package"] = t.Package, ["text"] = t.Text }).ToArray()),
        };
        return view;
    }
    /// <summary>The steps to run (given, or a saved routine) with their parameters filled in, and the untouched templates
    /// a new routine is recorded from. Nothing is sent when a step is unknown or a parameter missing.</summary>
    private (JsonArray Steps, JsonArray Templates) ActSteps(JsonObject args)
    {
        var steps = args["steps"]?.DeepClone().AsArray();
        var routine = Str(args, "routine");
        if (routine.Length > 0)
        {
            if (steps is not null)
                throw new ArgumentException("Use either steps or routine");
            var saved = JsonNode.Parse(File.ReadAllText(RoutinePath(routine)))!.AsObject();
            var expected = Str(saved, "package");
            if (expected.Length > 0 && Foreground(Read()) != expected)
                throw new InvalidOperationException("Routine was recorded in " + expected + "; open that app before replaying");
            steps = saved["steps"]!.DeepClone().AsArray();
        }
        if (steps is null || steps.Count == 0)
            throw new ArgumentException("steps required");
        var templates = steps.DeepClone().AsArray();
        var parameters = args["params"]?.AsObject();
        foreach (var step in steps.OfType<JsonObject>().Where(s => s["text"] is not null))
        {
            var text = Str(step, "text");
            var substitutions = Regex.Matches(text, @"\{\{(\w+)\}\}");
            if (substitutions.Count == 0)
                continue;
            if (Str(step, "do") == "type" && Regex.Replace(text, @"\{\{\w+\}\}", "").Trim().Length > 0)
                throw new ArgumentException("Parameterized typed text must contain only parameters");
            foreach (Match parameter in substitutions)
            {
                if (parameters?[parameter.Groups[1].Value] is null)
                    throw new ArgumentException("Missing routine parameter: " + parameter.Groups[1].Value);
                text = text.Replace(parameter.Value, parameters[parameter.Groups[1].Value]!.ToString());
            }
            step["text"] = text;
        }
        foreach (var node in steps)
        {
            if (node is not JsonObject s || !Gestures.Contains(Str(s, "do")))
                throw new ArgumentException("Unknown gesture or missing do; no input sent");
        }
        return (steps, templates);
    }
    /// <summary>Runs one step; false stops the batch (effect not read back).</summary>
    private bool ActStep(JsonObject step, string action, JsonObject template, ActRun run, System.Diagnostics.Stopwatch began)
    {
        var screen = Observe(action);
        if (action == "wait")
        {
            WaitFor(step, screen);
            run.Reports.Add("✓ wait condition observed");
            run.SavedSteps.Add(template.DeepClone());
            return true;
        }
        if (action == "open_url")
        {
            Launch(new JsonObject { ["action"] = "android.intent.action.VIEW", ["data"] = Str(step, "text"), ["confirm"] = step["confirm"]?.DeepClone(), ["accord"] = step["accord"]?.DeepClone(), ["then"] = "none" });
            var saved = template.DeepClone().AsObject();
            saved.Remove("confirm");
            saved.Remove("accord");
            run.SavedSteps.Add((JsonNode?)saved);
            run.Reports.Add("✓ intent arrival inspected");
            return true;
        }
        var elements = Elements(screen);
        var keyCodes = action == "key" ? Keys(Str(step, "keys").ToLowerInvariant()) : [];
        var aim = AimStep(step, action, screen, elements, keyCodes);
        if (action == "key")
            GuardKeys(screen, elements, keyCodes);
        var guardGesture = action != "key" ? action : !SafeKeys(keyCodes) ? "enter" : NavigationGesture(keyCodes);
        if (action != "scroll")
            Guard(screen, elements, step, aim.Element, guardGesture, authorize: action != "type", destination: aim.EndElement);
        (screen, elements) = SendStep(step, action, aim, screen, elements, keyCodes);
        lastInput = DateTimeOffset.UtcNow;
        KeepScreenAwake(screen);
        var after = SettleAfter(screen);
        var verified = Verified(step, action, aim, screen, elements, after, Elements(after));
        run.Reports.Add((verified ? "✓ " : "~ ") + action + (verified ? " effect read back" : " sent, effect unverified; do not repeat"));
        NativeJournal.Write(action, Foreground(after), verified ? "✓" : "~", began.Elapsed.TotalMilliseconds);
        if (!verified)
        {
            run.AllVerified = false;
            return false;
        }
        // The gesture already went through: only a requested routine needs stable targets, and an unstable one
        // cancels the recording, never the reported success (a false ✗ invites a double tap).
        var record = template.DeepClone().AsObject();
        record.Remove("confirm");
        record.Remove("accord");
        if (step["target"] is not null && aim.Element is not null)
            record["target"] = StableTarget(aim.Element);
        if (step["to"] is not null && aim.EndElement is not null)
            record["to"] = StableTarget(aim.EndElement);
        if (record["target"]?.ToString() is { Length: 0 } || record["to"]?.ToString() is { Length: 0 })
            run.UnstableTarget = true;
        run.SavedSteps.Add((JsonNode?)record);
        return true;
    }
    /// <summary>The screen a step is judged on: owner not holding the phone, trusted lock lifted, screen awake.</summary>
    private NativeScreen Observe(string action)
    {
        var screen = Read();
        if (action is "wait" or "open_url")
            return screen;
        WaitForOwner(screen);
        screen = LiftTrustedLock(screen);
        if (!Awake(screen) && !Locked(screen))
        {
            Shell("input", "keyevent", "KEYCODE_WAKEUP");
            lastInput = DateTimeOffset.UtcNow;
            screen = Read();
        }
        return screen;
    }
    private void WaitFor(JsonObject step, NativeScreen screen)
    {
        var deadline = DateTime.UtcNow.AddSeconds(Math.Clamp(step["timeout"]?.GetValue<double>() ?? 5, 0, 30));
        var wanted = NativeSafety.Comparable(Str(step, "text"));
        var gone = Bool(step, "gone");
        while (Elements(screen).Any(e => NativeSafety.Comparable(e.Text + " " + e.ContentDesc).Contains(wanted, StringComparison.Ordinal)) == gone)
        {
            NativeSafety.StopGuard();
            if (DateTime.UtcNow >= deadline)
                throw new TimeoutException("Expected screen condition not observed");
            Thread.Sleep(80);
            screen = Read();
        }
    }
    private Aim AimStep(JsonObject step, string action, NativeScreen screen, List<UiElement> elements, List<string> keyCodes)
    {
        var aim = new Aim();
        if (action == "key")
        {
            aim.Element = elements.FirstOrDefault(e => e.Focused);
        }
        else if (action is "type" or "clear" && step["target"] is null)
        {
            aim.Element = elements.SingleOrDefault(e => e.Focused && e.Editable) ?? throw new InvalidOperationException("Input focus changed; no input sent");
        }
        else if (action is "scroll" or "zoom" && step["target"] is null)
        {
            aim.X = screen.Width / 2;
            aim.Y = screen.Height / 2;
            aim.Element = AtPoint(elements, aim.X, aim.Y);
        }
        else
        {
            (aim.X, aim.Y, aim.Element) = Resolve(step["target"], elements);
        }
        if (action is "type" or "clear" && aim.Element?.Editable != true)
            throw new InvalidOperationException("Target is not an editable field");
        if (action is "swipe" or "drag")
            (aim.EndX, aim.EndY, aim.EndElement) = Resolve(step["to"], elements);
        return aim;
    }
    /// <summary>Sends the gesture. Returns the screen the step ends on before settling (a focused field for type/clear).</summary>
    private (NativeScreen Screen, List<UiElement> Elements) SendStep(JsonObject step, string action, Aim aim, NativeScreen screen, List<UiElement> elements, List<string> keyCodes)
    {
        string X() => aim.X.ToString(CultureInfo.InvariantCulture);
        string Y() => aim.Y.ToString(CultureInfo.InvariantCulture);
        switch (action)
        {
            case "tap":
                Shell("input", "tap", X(), Y());
                break;
            case "double_tap":
                {
                    Shell("input", "tap", X(), Y());
                    var clock = System.Diagnostics.Stopwatch.StartNew();
                    var second = Read();
                    var secondElements = Elements(second);
                    if (clock.Elapsed.TotalMilliseconds > 250)
                        throw new IOException("First tap sent; screen recheck exceeded 250 ms, so no second tap sent");
                    var unapproved = step.DeepClone().AsObject();
                    unapproved.Remove("accord");
                    Guard(second, secondElements, unapproved, AtPoint(secondElements, aim.X, aim.Y), "double_tap");
                    Shell("input", "tap", X(), Y());
                    break;
                }
            case "long_press":
                Shell("input", "swipe", X(), Y(), X(), Y(), "650");
                break;
            case "type":
                if (step["target"] is not null && !aim.Element!.Focused)
                {
                    Shell("input", "tap", X(), Y());
                    var focused = Read();
                    var now = Elements(focused);
                    var field = now.SingleOrDefault(e => e.Focused && e.Editable);
                    if (field is null || field.IsPassword || field.ResourceId != aim.Element.ResourceId || field.PackageName != aim.Element.PackageName)
                        throw new IOException("Field focus changed after tap; nothing typed");
                    screen = focused;
                    elements = now;
                    aim.Element = field;
                }
                Guard(screen, elements, step, aim.Element, "type");
                PasteText(Str(step, "text"));
                break;
            case "clear":
                if (step["target"] is not null && !aim.Element!.Focused)
                {
                    Shell("input", "tap", X(), Y());
                    var focused = Read();
                    var fields = Elements(focused);
                    var field = fields.SingleOrDefault(e => e.Focused && e.Editable);
                    if (field is null || field.ResourceId != aim.Element.ResourceId || field.PackageName != aim.Element.PackageName)
                        throw new IOException("Target field did not receive focus; nothing cleared");
                    Guard(focused, fields, step, field, "clear");
                }
                Shell("input", "keycombination", "113", "29");
                Shell("input", "keyevent", "67");
                break;
            case "key":
                if (keyCodes.Count == 1)
                    Shell("input", "keyevent", keyCodes[0]);
                else
                    Shell(["input", "keycombination", .. keyCodes]);
                break;
            case "swipe":
            case "drag":
                Shell("input", action == "drag" ? "draganddrop" : "swipe", X(), Y(), aim.EndX.ToString(CultureInfo.InvariantCulture), aim.EndY.ToString(CultureInfo.InvariantCulture), action == "drag" ? "1600" : "400");
                break;
            case "scroll":
                (screen, elements) = Scroll(step, aim, screen, elements);
                break;
            case "zoom":
                Zoom(step, aim, screen, elements);
                break;
        }
        return (screen, elements);
    }
    private (NativeScreen Screen, List<UiElement> Elements) Scroll(JsonObject step, Aim aim, NativeScreen screen, List<UiElement> elements)
    {
        var direction = Str(step, "direction", "down");
        if (direction is not ("up" or "down" or "left" or "right"))
            throw new ArgumentException("scroll requires up/down/left/right; use zoom for in/out");
        var dx = direction == "left" ? screen.Width / 3 : direction == "right" ? -screen.Width / 3 : 0;
        var dy = direction == "down" ? -screen.Height / 3 : direction == "up" ? screen.Height / 3 : 0;
        var amount = Math.Clamp(Num(step, "amount", 1), 1, 10);
        if (step["accord"] is not null && amount > 1)
            throw new InvalidOperationException("An accord authorizes one scroll only; amount must be 1");
        var original = (screen, elements);
        for (var n = 0; n < amount; n++)
        {
            if (n > 0)
            {
                screen = Read();
                elements = Elements(screen);
            }
            var from = (X: aim.X - dx / 2, Y: aim.Y - dy / 2);
            var to = (X: aim.X + dx / 2, Y: aim.Y + dy / 2);
            if (from.X < 0 || from.Y < 0 || to.X < 0 || to.Y < 0 || from.X >= screen.Width || to.X >= screen.Width || from.Y >= screen.Height || to.Y >= screen.Height)
                throw new ArgumentException("Scroll path outside screen");
            Guard(screen, elements, step, AtPoint(elements, from.X, from.Y), "scroll", destination: AtPoint(elements, to.X, to.Y));
            if (Tuning.SteadyScroll)
                SteadySwipe(from.X, from.Y, to.X, to.Y);
            else
                Shell("input", "swipe", from.X.ToString(CultureInfo.InvariantCulture), from.Y.ToString(CultureInfo.InvariantCulture), to.X.ToString(CultureInfo.InvariantCulture), to.Y.ToString(CultureInfo.InvariantCulture), "350");
        }
        // Verification compares with the screen before the first swipe, whatever the amount.
        return amount > 1 ? original : (screen, elements);
    }
    /// <summary>A scroll that stops where the finger stops: down, four even moves, a 120 ms hold, then up, in one shell.
    /// `input swipe` lifts while still moving, so lists fling on (600 px moved a Settings list ~1,070 px on the Fold;
    /// this moves it ~577, the touch slop aside). Falls back to `input swipe` where `input motionevent` is missing.</summary>
    private void SteadySwipe(int x1, int y1, int x2, int y2)
    {
        string P(int x, int y) => x.ToString(CultureInfo.InvariantCulture) + " " + y.ToString(CultureInfo.InvariantCulture);
        var steps = Tuning.SteadyMoves;
        var moves = Enumerable.Range(1, steps).Select(i => "input motionevent MOVE " + P(x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps));
        var script = "input motionevent DOWN " + P(x1, y1) + "; " + string.Join("; ", moves) + "; sleep " + (Tuning.SteadyHoldMs / 1000.0).ToString("0.###", CultureInfo.InvariantCulture) + "; input motionevent UP " + P(x2, y2);
        string output;
        try
        {
            output = Shell("sh", "-c", script);
        }
        catch (Exception e) when (e is IOException or TimeoutException or InvalidOperationException)
        {
            // Never leave a finger down on the screen.
            try
            {
                Shell("input", "motionevent", "UP", x2.ToString(CultureInfo.InvariantCulture), y2.ToString(CultureInfo.InvariantCulture));
            }
            catch (Exception up) when (up is IOException or TimeoutException or InvalidOperationException) {/* reported with the original failure */}
            throw;
        }
        if (Regex.IsMatch(output, @"(?i)error|unknown|usage"))
            Shell("input", "swipe", x1.ToString(CultureInfo.InvariantCulture), y1.ToString(CultureInfo.InvariantCulture), x2.ToString(CultureInfo.InvariantCulture), y2.ToString(CultureInfo.InvariantCulture), "350");
    }
    private void Zoom(JsonObject step, Aim aim, NativeScreen screen, List<UiElement> elements)
    {
        var direction = Str(step, "direction", "in");
        if (direction is not ("in" or "out"))
            throw new ArgumentException("zoom requires in/out");
        var geometry = ScrcpyChannel.ZoomGeometry(aim.X, aim.Y, screen.Width, screen.Height, direction == "in", Num(step, "amount", 1));
        var context = string.Join(' ', elements.Select(e => e.RawText));
        var protectedApp = AndroidPolicy.IsProtectedPackage(Foreground(screen));
        foreach (var point in new[] { geometry.First, geometry.Second })
        {
            foreach (var under in elements.Where(e => point.X >= e.Left && point.X < e.Right && point.Y >= e.Top && point.Y < e.Bottom))
            {
                var label = under.RawText + " " + under.RawDescription;
                if (NativeSafety.Risky(label) || NativeSafety.ContextRisk(label, context) || (protectedApp || AndroidPolicy.IsProtectedPackage(under.PackageName)) && under.Clickable)
                    throw new InvalidOperationException("A zoom finger would land on an action button; aim at the content away from buttons");
            }
        }
        using var channel = control();
        channel.Zoom(aim.X, aim.Y, screen.Width, screen.Height, direction == "in", Num(step, "amount", 1));
    }
    private static bool Verified(JsonObject step, string action, Aim aim, NativeScreen screen, List<UiElement> elements, NativeScreen after, List<UiElement> afterElements)
    {
        var same = aim.Element is null ? null : FollowTarget(aim.Element, afterElements);
        if (action is "type" or "clear")
        {
            return same is { Focused: true, Editable: true } && same.RawText != aim.Element!.RawText && (action == "clear" ? same.RawText.Length == 0 : same.RawText.Contains(Str(step, "text"), StringComparison.Ordinal));
        }
        if (action == "scroll")
        {
            string Content(NativeScreen view, List<UiElement> list) => Hash(string.Join('|', list.Where(e => e.PackageName == Foreground(view)).Select(e => $"{e.ResourceId}|{e.RawText}|{e.Top}|{e.Bottom}")));
            return Content(after, afterElements) != Content(screen, elements);
        }
        if (Foreground(after) != Foreground(screen))
            return true;
        if (same is not null && (same.Checked != aim.Element!.Checked || same.RawText != aim.Element.RawText || same.Focused != aim.Element.Focused))
            return true;
        // A menu, dialog or new page opening in the same app is an effect too: the app's own elements changed.
        string AppView(NativeScreen view, List<UiElement> list) => string.Join('|', list.Where(e => e.PackageName == Foreground(view)).Select(e => $"{e.ResourceId}|{e.ClassName}|{e.RawText}|{e.RawDescription}|{e.Left},{e.Top},{e.Right},{e.Bottom}|{e.Checked}|{e.Selected}"));
        return action is "tap" or "long_press" or "double_tap" or "key" or "drag" && AppView(after, afterElements) != AppView(screen, elements);
    }
    private void SaveRoutine(string name, string package, ActRun run)
    {
        if (run.UnstableTarget)
        {
            run.Reports.Add("Routine not saved: a target has no id, text or description to find it again");
            return;
        }
        foreach (var saved in run.SavedSteps.OfType<JsonObject>())
        {
            saved.Remove("confirm");
            saved.Remove("accord");
        }
        File.WriteAllText(RoutinePath(name), new JsonObject { ["package"] = package, ["saved"] = DateTimeOffset.Now.ToString("O"), ["steps"] = run.SavedSteps }.ToJsonString());
        run.Reports.Add("Routine saved using stable targets; no authorizations stored");
    }
    private static string StableTarget(UiElement element) => element.ResourceId.Length > 0 ? "id:" + element.ResourceId : element.RawText.Length > 0 ? element.RawText : element.RawDescription.Length > 0 ? "desc:" + element.RawDescription : "";
    // After a gesture the app answers within a few frames (dialog, new screen, typed text): read until the screen has
    // changed and two reads agree, instead of judging the read taken right after the input. A screen that never
    // changes is returned after 600 ms (effect unverified); one that never settles (video, clock) after 1.5 s.

    private NativeScreen SettleAfter(NativeScreen before)
    {
        var baseline = before.Xml;
        var clock = System.Diagnostics.Stopwatch.StartNew();
        var current = Read();
        string? changed = null;
        while (true)
        {
            if (current.Xml != baseline)
            {
                if (!Tuning.SettleTwoReads || current.Xml == changed)
                    return current;
                changed = current.Xml;
            }
            else if (clock.ElapsedMilliseconds >= Tuning.SettleNoChangeMs)
                return current;
            if (clock.ElapsedMilliseconds >= Tuning.SettleMaxMs)
                return current;
            NativeSafety.StopGuard();
            Thread.Sleep(Tuning.SettlePollMs);
            current = Read();
        }
    }
    // Toasts shown during the gestures ("Copied", "Saved", "Network error"), read by the phone helper's accessibility link.
    // Never decisive: an unavailable helper only leaves them out.
    private long ToastMark()
    {
        try
        {
            return query.Toasts(0).Select(t => long.TryParse(t.GetValueOrDefault("seq"), out var n) ? n : 0).DefaultIfEmpty(0).Max();
        }
        catch (Exception e) when (e is IOException or System.Net.Sockets.SocketException or AggregateException or NotSupportedException) { return -1; }
    }
    private List<(string Package, string Text)> ToastsSince(long mark)
    {
        if (mark < 0)
            return [];
        List<Dictionary<string, string>> rows;
        try
        {
            rows = query.Toasts(mark);
        }
        catch (Exception e) when (e is IOException or System.Net.Sockets.SocketException or AggregateException or NotSupportedException) { return []; }
        return rows.Select(r => (Package: r.GetValueOrDefault("package") ?? "", Raw: r.GetValueOrDefault("text") ?? "")).Where(r => !AndroidPolicy.IsProtectedPackage(r.Package) && r.Raw.Trim().Length > 0)
            .Select(r =>
            {
                var text = NativeSafety.Mask(NativeSafety.MaskCodes(r.Raw, NativeSafety.HasCodeKeyword(r.Raw))).Replace('\n', ' ').Replace('\r', ' ');
                return (r.Package.Length > 0 ? r.Package : "?", text.Length > 200 ? text[..200] : text);
            }).Distinct().ToList();
    }
    private string RoutinePath(string name)
    {
        if (!Regex.IsMatch(name, @"^[\w-]{1,80}$"))
            throw new ArgumentException("Invalid routine name");
        return Path.Join(stateRoot, "routine-" + name + ".json");
    }
    private static readonly HashSet<string> NavigationKeyCodes = ["3", "4", "19", "20", "21", "22", "24", "25", "61", "82", "111", "187", "224"];
    private void PasteText(string text)
    {
        using var clip = control();
        var old = clip.Get();
        try
        {
            NativeSafety.StopGuard();
            clip.Set(text, true);
            Thread.Sleep(300);
        }
        catch (IOException e) { throw new IOException("Paste interrupted; effect unknown, observe before any new decision. " + e.Message, e); }
        finally
        {
            try
            {
                clip.Set(old, false);
            }
            catch (IOException) { presenceWarning += " The clipboard could not be restored after typing; do not repeat the paste."; }
        }
    }
    private static readonly HashSet<string> SafeKeyCodes = [.. NavigationKeyCodes, "67", "112", "84"];
    private static readonly string[] SelectAllKeys = ["113", "29"];
    private static bool SafeKeys(List<string> codes) => codes.SequenceEqual(SelectAllKeys) || codes.Count == 1 && SafeKeyCodes.Contains(codes[0]);
    private static string NavigationGesture(List<string> codes) => codes.Count == 1 ? codes[0] switch { "3" => "home", "4" => "back", "187" => "recent", "19" => "up", "20" => "down", "21" => "left", "22" => "right", "61" => "tab", "111" => "escape", _ => "tab" } : "tab";
    private static void GuardKeys(NativeScreen screen, List<UiElement> elements, List<string> codes)
    {
        if (codes.All(NavigationKeyCodes.Contains))
            return;
        if (elements.Count == 0)
            throw new InvalidOperationException("Active field unreadable: only navigation keys permitted; no key sent");
        if (elements.Any(e => e.Focused && e.IsPassword))
            throw new NativeSafety.GuardRefusal("Password field: only navigation keys permitted; no key sent");
        if (!SafeKeys(codes) && (AndroidPolicy.IsProtectedPackage(Foreground(screen)) || elements.Any(e => e.Focused && AndroidPolicy.IsProtectedPackage(e.PackageName))))
            throw new NativeSafety.GuardRefusal("Validation keys in protected apps are always refused, including with confirm and accord");
    }
    private static List<string> Keys(string keys)
    {
        var aliases = new Dictionary<string, string> { ["back"] = "4", ["retour"] = "4", ["home"] = "3", ["accueil"] = "3", ["recent"] = "187", ["recents"] = "187", ["app_switch"] = "187", ["enter"] = "66", ["entrée"] = "66", ["return"] = "66", ["delete"] = "67", ["backspace"] = "67", ["del"] = "112", ["forward_del"] = "112", ["tab"] = "61", ["left"] = "21", ["dpad_left"] = "21", ["right"] = "22", ["dpad_right"] = "22", ["up"] = "19", ["dpad_up"] = "19", ["down"] = "20", ["dpad_down"] = "20", ["escape"] = "111", ["esc"] = "111", ["space"] = "62", ["ctrl"] = "113", ["ctrl_left"] = "113", ["control"] = "113", ["shift"] = "59", ["alt"] = "57", ["a"] = "29", ["volume_up"] = "24", ["volume_down"] = "25", ["wakeup"] = "224", ["menu"] = "82", ["search"] = "84", ["pageup"] = "92", ["pagedown"] = "93", ["move_home"] = "122", ["move_end"] = "123" };
        var parts = keys.Split('+', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length == 0)
            throw new ArgumentException("keys required");
        var codes = new List<string>();
        foreach (var part in parts)
        {
            var bare = part.StartsWith("keycode_", StringComparison.Ordinal) ? part[8..] : part;
            if (aliases.TryGetValue(bare, out var code))
                codes.Add(code);
            else if (Regex.IsMatch(part, @"^keycode_[a-z0-9_]+$"))
                codes.Add(part.ToUpperInvariant());
            else
                throw new ArgumentException("Unknown key: " + part);
        }
        if (codes.Count > 1 && codes.Take(codes.Count - 1).Any(c => c is not ("113" or "59" or "57")))
            throw new ArgumentException("Only one non-modifier key per step; put successive keys in separate steps");
        return codes;
    }
}
