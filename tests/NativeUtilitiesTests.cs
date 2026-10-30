using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using NexusAndroid.Tests.Engine;

namespace NexusAndroid.Tests.Utilities;

/// <summary>Engine utilities on injected transports, never adb or a real phone (former test_native_utilities.py):
/// messages, drafts, notifications, apps, logs and intents, each call through FixtureDriver. Test images are built with
/// the engine's own PNG/JPEG encoders (ImageChain); EXIF orientation JPEGs get a hand-made APP1 Exif segment.</summary>
public sealed class NativeUtilitiesTests
{
    private const string ThreadUri = "content://mms-sms/conversations?simple=true";
    private const string CanonicalUri = "content://mms-sms/canonical-addresses";

    // ------------------------------------------------------------------ helpers

    private static JsonObject Call(string tool, JsonObject args, JsonObject fixture) => FixtureDriver.Call(tool, args, fixture);

    private static JsonObject Result(JsonObject result) => result["response"]!["result"]!.AsObject();

    private static IEnumerable<JsonObject> Blocks(JsonObject result) => Result(result)["content"]?.AsArray().OfType<JsonObject>() ?? [];

    private static string Text(JsonObject result) => string.Join("\n", Blocks(result).Select(block => block["text"]?.ToString() ?? ""));

    private static bool IsError(JsonObject result) => Result(result)["isError"]?.GetValue<bool>() == true;

    private static string Success(JsonObject result)
    {
        Assert.False(IsError(result), result.ToJsonString());
        return Text(result);
    }

    private static List<string> Commands(JsonObject result) => result["commands"]!.AsArray().Select(c => c!.ToString()).ToList();

    private static List<JsonObject> QueryCalls(JsonObject result) => result["query_calls"]!.AsArray().OfType<JsonObject>().ToList();

    private static JsonObject Json(string text) => JsonNode.Parse(text)!.AsObject();

    private static JsonObject Queries(JsonObject fixture) => fixture["queries"]!.AsObject();

    /// <summary>a before b, both present (Python body.index(a) &lt; body.index(b)).</summary>
    private static void Before(string body, string first, string second)
    {
        Assert.Contains(first, body);
        Assert.Contains(second, body);
        Assert.True(body.IndexOf(first, StringComparison.Ordinal) < body.IndexOf(second, StringComparison.Ordinal), $"'{first}' should come before '{second}'");
    }

    private static int Count(string text, string part)
    {
        var count = 0;
        for (var index = text.IndexOf(part, StringComparison.Ordinal); index >= 0; index = text.IndexOf(part, index + part.Length, StringComparison.Ordinal)) count++;
        return count;
    }

    private static JsonObject Conversations() => Json($$$"""
        {"shell": {"getprop gsm.sim.operator.iso-country": "fr\n"}, "queries": {
          "{{{CanonicalUri}}}": [{"_id": "1", "address": "+33612345678"}, {"_id": "2", "address": "+33699999999"}],
          "{{{ThreadUri}}}": [
            {"_id": "7", "date": "2001", "snippet": "new", "recipient_ids": "1", "message_count": "3", "read": "0"},
            {"_id": "8", "date": "1001", "snippet": "old", "recipient_ids": "2", "message_count": "1", "read": "1", "archived": "1"},
            {"_id": "9", "date": "3001", "snippet": "empty draft", "recipient_ids": "1", "message_count": "0"}
          ]}}
        """);

    // ------------------------------------------------------------------ messages

    [Fact]
    public void ConversationPageBeforeFiltersBeforeLimitAndKeepsCursor()
    {
        var result = Call("messages", Json("""{"limit": 1, "before": "2001"}"""), Conversations());
        var body = Success(result);
        Assert.Contains("[8]", body);
        Assert.DoesNotContain("[7]", body);
        Assert.DoesNotContain("[9]", body);
        Assert.Contains("hidden in the app", body);
        Assert.Contains("messages(before=", body);
        var query = QueryCalls(result).First(row => row["uri"]!.ToString() == ThreadUri);
        Assert.Equal("message_count>0 AND date<2001", query["where"]!.ToString());
    }

    [Fact]
    public void ContactUsesLatestOneToOneThreadAndNormalizesSimNumbers()
    {
        var fixture = Conversations();
        Queries(fixture)["content://com.android.contacts/data/phones/filter/Alice"] = JsonNode.Parse("""
            [{"display_name": "Alice", "data1": "06 12 34 56 78"}, {"display_name": "Alice", "data1": "+33 6 12 34 56 78"}]
            """);
        Queries(fixture)[ThreadUri]!.AsArray().Add(Json("""{"_id": "10", "date": "4001", "recipient_ids": "1 2", "message_count": "4"}"""));
        Queries(fixture)["content://sms"] = JsonNode.Parse("""[{"_id": "1", "thread_id": "7", "date": "2000", "type": "1", "read": "0", "body": "hello Alice"}]""");
        var body = Success(Call("messages", Json("""{"contact": "Alice", "images": 0}"""), fixture));
        Assert.Contains("Conversation [7]", body);
        Assert.Contains("hello Alice", body);
        Assert.DoesNotContain("Conversation [10]", body);
    }

    [Fact]
    public void ContactAmbiguityIsRefused()
    {
        var fixture = Conversations();
        Queries(fixture)["content://com.android.contacts/data/phones/filter/Al"] = JsonNode.Parse("""
            [{"display_name": "Alice", "data1": "0612345678"}, {"display_name": "Albert", "data1": "0699999999"}]
            """);
        var result = Call("messages", Json("""{"contact": "Al"}"""), fixture);
        Assert.True(IsError(result));
        Assert.Contains("Several contacts", Text(result));
    }

    [Theory]
    [InlineData("not a date")]
    [InlineData("2026-99-90")]
    [InlineData("10/12/2026")]
    public void MessageInvalidCursorReadsNoMessageTables(string value)
    {
        var result = Call("messages", new JsonObject { ["before"] = value }, new JsonObject());
        Assert.True(IsError(result));
        Assert.Contains("ISO date", Text(result));
        Assert.Empty(QueryCalls(result));
    }

    [Fact]
    public void ThreadMergeBoundaryAttachmentMentionsAndChronologicalOrder()
    {
        var fixture = Conversations();
        Queries(fixture)["content://sms"] = JsonNode.Parse("""
            [{"_id": "1", "thread_id": "7", "date": "1001", "body": "old sms", "type": "2", "read": "1"},
             {"_id": "2", "thread_id": "7", "date": "3001", "body": "new sms", "type": "1", "read": "0"},
             {"_id": "3", "thread_id": "7", "date": "5000", "body": "outside boundary", "type": "1"}]
            """);
        Queries(fixture)["content://mms"] = JsonNode.Parse("""[{"_id": "4", "thread_id": "7", "date": "2", "msg_box": "1", "read": "0"}]""");
        Queries(fixture)["content://mms/part"] = JsonNode.Parse("""
            [{"_id": "30", "mid": "4", "ct": "text/plain", "text": "middle mms"},
             {"_id": "31", "mid": "4", "ct": "image/png"}, {"_id": "32", "mid": "4", "ct": "video/mp4"}]
            """);
        var result = Call("messages", Json("""{"conversation": 7, "limit": 3, "before": "4001", "images": 0}"""), fixture);
        var body = Success(result);
        Before(body, "old sms", "middle mms");
        Before(body, "middle mms", "new sms");
        Assert.DoesNotContain("outside boundary", body);
        Assert.Contains("image not shown", body);
        Assert.Contains("video not shown", body);
        Assert.Contains("older: messages(conversation=7", body);
        Assert.Equal("thread_id=7 AND date<5", QueryCalls(result).First(r => r["uri"]!.ToString() == "content://mms")["where"]!.ToString());
        Assert.All(Blocks(result), block => Assert.NotEqual("image", block["type"]?.ToString()));
    }

    [Fact]
    public void ThreadOutputKeepsMessageBoundariesAndMasksBeforeBudget()
    {
        var fixture = Conversations();
        var longText = string.Concat(Enumerable.Repeat("long text ", 3000));
        Queries(fixture)["content://sms"] = new JsonArray(Enumerable.Range(0, 4).Select(index => (JsonNode?)new JsonObject
        {
            ["_id"] = index.ToString(), ["thread_id"] = "7", ["date"] = (10000 + index).ToString(), ["type"] = "1", ["body"] = $"message{index}: " + longText,
        }).ToArray());
        var body = Success(Call("messages", Json("""{"conversation": 7, "limit": 4, "images": 0}"""), fixture));
        Assert.True(body.Length <= 60000, "body length " + body.Length);
        Assert.Contains("message3:", body);
        Assert.Contains("older:", body);
        Assert.DoesNotContain("message0:", body);
    }

    private static JsonObject ImageFixture(byte[] data)
    {
        var fixture = Conversations();
        Queries(fixture)["content://mms"] = JsonNode.Parse("""
            [{"_id": "2", "thread_id": "7", "date": "2", "msg_box": "1"}, {"_id": "3", "thread_id": "7", "date": "3", "msg_box": "1"}]
            """);
        Queries(fixture)["content://mms/part"] = JsonNode.Parse("""
            [{"_id": "21", "mid": "2", "ct": "image/jpeg"}, {"_id": "31", "mid": "3", "ct": "image/jpeg"}]
            """);
        fixture["exec_out"] = new JsonObject { ["content read --uri content://mms/part/"] = Convert.ToBase64String(data) };
        return fixture;
    }

    private static PixelBuffer Filled(int width, int height, byte r, byte g, byte b)
    {
        var picture = new PixelBuffer(width, height);
        picture.FillRect(0, 0, width, height, r, g, b);
        return picture;
    }

    [Theory]
    [InlineData(2, 0, 0)]
    [InlineData(2, 1, 1)]
    [InlineData(2, 2, 2)]
    [InlineData(1, 2, 1)]
    public void MessageImagesReadOnlySelectedRecentParts(int limit, int count, int expected)
    {
        var png = ImageChain.EncodePng(Filled(12, 6, 255, 0, 0));
        var result = Call("messages", new JsonObject { ["conversation"] = 7, ["images"] = count, ["limit"] = limit }, ImageFixture(png));
        Success(result);
        var reads = Commands(result).Where(command => command.StartsWith("content read", StringComparison.Ordinal)).ToList();
        var images = Blocks(result).Where(block => block["type"]?.ToString() == "image").ToList();
        Assert.Equal(expected, reads.Count);
        Assert.Equal(expected, images.Count);
        if (expected > 0)
        {
            Assert.EndsWith("/31", reads[0]);
            Assert.Contains("[image 1]", Text(result));
        }
        if (expected > 1)
        {
            Assert.EndsWith("/21", reads[1]);
            Assert.Contains("[image 2]", Text(result));
        }
        foreach (var block in images)
        {
            var decoded = ImageChain.Decode(Convert.FromBase64String(block["data"]!.ToString()));
            Assert.True(decoded.Width > 0 && decoded.Height > 0);
        }
    }

    [Fact]
    public void UnreadableMessageImageDoesNotDiscardThread()
    {
        var result = Call("messages", Json("""{"conversation": 7, "images": 1}"""), ImageFixture(Encoding.ASCII.GetBytes("not an image")));
        var body = Success(result);
        Assert.Contains("[unreadable image]", body);
        Assert.DoesNotContain(Blocks(result), block => block["type"]?.ToString() == "image");
    }

    /// <summary>JPEG with an APP1 Exif segment holding only the Orientation tag (0x0112, SHORT), inserted after SOI
    /// (and after the JFIF APP0 segment when present).</summary>
    private static byte[] WithExifOrientation(byte[] jpeg, int orientation)
    {
        Assert.True(jpeg[0] == 0xFF && jpeg[1] == 0xD8, "not a JPEG");
        var exif = new List<byte>();
        exif.AddRange("Exif\0\0"u8.ToArray());
        exif.AddRange([(byte)'I', (byte)'I', 0x2A, 0x00, 0x08, 0x00, 0x00, 0x00]);  // little-endian TIFF, IFD0 at 8
        exif.AddRange([0x01, 0x00]);                                                 // one entry
        exif.AddRange([0x12, 0x01, 0x03, 0x00, 0x01, 0x00, 0x00, 0x00, (byte)orientation, 0x00, 0x00, 0x00]);
        exif.AddRange([0x00, 0x00, 0x00, 0x00]);                                     // no next IFD
        var length = exif.Count + 2;
        var segment = new List<byte> { 0xFF, 0xE1, (byte)(length >> 8), (byte)(length & 0xFF) };
        segment.AddRange(exif);
        var position = 2;
        if (jpeg[2] == 0xFF && jpeg[3] == 0xE0) position = 4 + (jpeg[4] << 8 | jpeg[5]);
        return [.. jpeg[..position], .. segment, .. jpeg[position..]];
    }

    /// <summary>Source pixel shown at (x, y) of the upright image (ImageOps.exif_transpose), for a width × height source.</summary>
    private static (int X, int Y) Upright(int orientation, int x, int y, int width, int height) => orientation switch
    {
        2 => (width - 1 - x, y),                  // mirrored horizontally
        3 => (width - 1 - x, height - 1 - y),     // rotated 180°
        4 => (x, height - 1 - y),                 // mirrored vertically
        5 => (y, x),                              // transposed
        6 => (y, height - 1 - x),                 // rotated 90° clockwise
        7 => (width - 1 - y, height - 1 - x),     // transversed
        8 => (width - 1 - y, x),                  // rotated 90° counter-clockwise
        _ => (x, y),
    };

    private static (int R, int G, int B) Pixel(PixelBuffer picture, int x, int y)
    {
        var offset = (y * picture.Width + x) * 4;
        return (picture.Bgra[offset + 2], picture.Bgra[offset + 1], picture.Bgra[offset]);
    }

    [Theory]
    [InlineData(2)]
    [InlineData(3)]
    [InlineData(4)]
    [InlineData(5)]
    [InlineData(6)]
    [InlineData(7)]
    [InlineData(8)]
    public void MessagePhotoAppliesExifOrientation(int orientation)
    {
        var picture = Filled(64, 32, 255, 0, 0);
        picture.FillRect(32, 0, 64, 16, 0, 0, 255);
        picture.FillRect(0, 16, 32, 32, 0, 128, 0);
        picture.FillRect(32, 16, 64, 32, 255, 255, 0);
        var plain = ImageChain.EncodeLossyJpeg(picture, 95);
        var raw = ImageChain.Decode(plain);  // no EXIF yet: the stored pixels, JPEG artifacts included
        Assert.Equal((64, 32), (raw.Width, raw.Height));
        var source = WithExifOrientation(plain, orientation);
        var transposed = orientation >= 5;
        var (expectedWidth, expectedHeight) = transposed ? (raw.Height, raw.Width) : (raw.Width, raw.Height);
        var result = Call("messages", Json("""{"conversation": 7, "images": 1, "limit": 1}"""), ImageFixture(source));
        Success(result);
        var block = Blocks(result).First(b => b["type"]?.ToString() == "image");
        var actual = ImageChain.Decode(Convert.FromBase64String(block["data"]!.ToString()));
        Assert.Equal((expectedWidth, expectedHeight), (actual.Width, actual.Height));
        foreach (var (x, y) in new[] { (8, 8), (actual.Width - 8, 8), (8, actual.Height - 8), (actual.Width - 8, actual.Height - 8) })
        {
            var (sx, sy) = Upright(orientation, x, y, raw.Width, raw.Height);
            var expected = Pixel(raw, sx, sy);
            var found = Pixel(actual, x, y);
            var difference = Math.Max(Math.Abs(found.R - expected.R), Math.Max(Math.Abs(found.G - expected.G), Math.Abs(found.B - expected.B)));
            Assert.True(difference < 5, $"orientation {orientation} at ({x},{y}): {found} vs {expected}");
        }
    }

    private static string Base32(byte[] data)
    {
        const string alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
        var output = new StringBuilder();
        for (var offset = 0; offset < data.Length; offset += 5)
        {
            var chunk = new byte[5];
            var count = Math.Min(5, data.Length - offset);
            Array.Copy(data, offset, chunk, 0, count);
            var bits = 0UL;
            foreach (var value in chunk) bits = bits << 8 | value;
            var characters = (count * 8 + 4) / 5;
            for (var i = 0; i < 8; i++) output.Append(i < characters ? alphabet[(int)(bits >> (35 - i * 5) & 31)] : '=');
        }
        return output.ToString();
    }

    [Fact]
    public void RcsGroupResolvesRealMembersWithoutOwner()
    {
        var fixture = Conversations();
        var group = Base32(Encoding.ASCII.GetBytes("\u001f+33612345678\u001fgroup")) + "@rcs.google.com";
        Queries(fixture)[CanonicalUri] = new JsonArray(new JsonObject { ["_id"] = "1", ["address"] = group });
        Queries(fixture)[ThreadUri] = JsonNode.Parse("""[{"_id": "7", "recipient_ids": "1", "date": "2000", "message_count": "2"}]""");
        Queries(fixture)["content://mms"] = JsonNode.Parse("""[{"_id": "3", "date": "2", "thread_id": "7", "msg_box": "1"}]""");
        Queries(fixture)["content://mms/addr"] = JsonNode.Parse("""
            [{"msg_id": "3", "type": "137", "address": "+33699999999"}, {"msg_id": "3", "type": "151", "address": "+33612345678"}]
            """);
        Queries(fixture)["content://mms/part"] = JsonNode.Parse("""[{"_id": "30", "mid": "3", "ct": "text/plain", "text": "group hello"}]""");
        var body = Success(Call("messages", Json("""{"conversation": 7, "images": 0}"""), fixture));
        Assert.Contains("RCS group", body);
        Assert.Contains("+33699999999", body);
        Assert.DoesNotContain("+33612345678", body);
    }

    // ------------------------------------------------------------------ drafts

    private static JsonObject DraftFixture(string package = "com.example.sms", string body = "draft text")
    {
        var fixture = Conversations();
        fixture["shell"]!["get-role-holders"] = package;
        fixture["shell"]!["am start"] = "Status: ok";
        fixture["state"] = $"mCurrentFocus=Window{{token u0 {package}/.Main}}\nmWakefulness=Awake\nisKeyguardShowing=false";
        fixture["xml"] = $"<hierarchy><node text='{body}' package='{package}' resource-id='compose' class='android.widget.EditText' bounds='[0,0][200,100]' editable='true' enabled='true'/><node text='Send' package='{package}' resource-id='send_button' class='android.widget.Button' bounds='[200,0][300,100]' clickable='true' enabled='true'/></hierarchy>";
        return fixture;
    }

    [Fact]
    public void DraftConversationUsesRoleSmsAndReturnsRealAgreementWithoutSend()
    {
        var result = Call("draft", Json("""{"to": "[7]", "text": "draft text"}"""), DraftFixture());
        var body = Success(result);
        var starts = Commands(result).Where(command => command.Contains("'am' 'start'", StringComparison.Ordinal)).ToList();
        Assert.Single(starts);
        Assert.Contains("'smsto:+33612345678'", starts[0]);
        Assert.Contains("'-p' 'com.example.sms'", starts[0]);
        Assert.Contains("exact text read back", body);
        Assert.Matches(new Regex("accord=[a-f0-9]{48}"), body);
        Assert.DoesNotContain(Commands(result), command => command.Contains("'input' 'tap'", StringComparison.Ordinal));
    }

    [Fact]
    public void GroupDraftRefusedBeforeLaunch()
    {
        var fixture = DraftFixture();
        Queries(fixture)[ThreadUri]![0]!["recipient_ids"] = "1 2";
        var result = Call("draft", Json("""{"to": "[7]", "text": "draft text"}"""), fixture);
        Assert.True(IsError(result));
        Assert.Contains("Group conversation", Text(result));
        Assert.DoesNotContain(Commands(result), command => command.Contains("'am' 'start'", StringComparison.Ordinal));
    }

    [Theory]
    [InlineData("fr", "0612345678", "33612345678")]
    [InlineData("be", "0470123456", "32470123456")]
    [InlineData("it", "0612345678", "390612345678")]
    [InlineData("hu", "0612345678", "3612345678")]
    [InlineData("", "+447123456789", "447123456789")]
    [InlineData("", "0033612345678", "33612345678")]
    public void WhatsappUsesSimMetadataNotGuessedCountry(string country, string number, string expected)
    {
        var fixture = DraftFixture("com.whatsapp");
        fixture["shell"]!["getprop gsm.sim.operator.iso-country"] = country;
        var result = Call("draft", new JsonObject { ["to"] = number, ["text"] = "draft text", ["app"] = "whatsapp" }, fixture);
        Success(result);
        Assert.Contains(Commands(result), command => command.Contains($"https://wa.me/{expected}?text=draft%20text", StringComparison.Ordinal));
    }

    [Fact]
    public void WhatsappUnknownSimNationalNumberRefusedBeforeLaunch()
    {
        var fixture = DraftFixture("com.whatsapp");
        fixture["shell"]!["getprop gsm.sim.operator.iso-country"] = "";
        var result = Call("draft", Json("""{"to": "0612345678", "text": "draft text", "app": "whatsapp"}"""), fixture);
        Assert.True(IsError(result));
        Assert.Contains("SIM card country unknown", Text(result));
        Assert.DoesNotContain(Commands(result), command => command.Contains("'am' 'start'", StringComparison.Ordinal));
    }

    [Theory]
    [InlineData("wrong package")]
    [InlineData("wrong text")]
    [InlineData("locked")]
    public void DraftNeverConfirmsUnreadOrWrongAppText(string mode)
    {
        var fixture = DraftFixture(body: mode == "wrong text" ? "wrong" : "draft text");
        if (mode == "wrong package") fixture["state"] = fixture["state"]!.ToString().Replace("com.example.sms", "com.wrong.app");
        else if (mode == "locked") fixture["state"] = fixture["state"]!.ToString().Replace("isKeyguardShowing=false", "isKeyguardShowing=true");
        var body = Success(Call("draft", Json("""{"to": "36179", "text": "draft text"}"""), fixture));
        Assert.Contains("Nothing sent", body);
        Assert.DoesNotContain("accord=", body);
        Assert.DoesNotContain("exact text read back", body);
    }

    // ------------------------------------------------------------------ notifications

    private static string Notice(string package, long when, string title, string body, bool ongoing = false) =>
        $"\n    NotificationRecord(pkg={package} id=1 flags={(ongoing ? "ONGOING_EVENT" : "0")} when={when})\n" +
        $"      extras={{\n        android.title=String ({title})\n        android.text=String (short)\n" +
        $"        android.bigText=String ({body})\n      }}\n";

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void NotificationsOnlyActiveSortedMultilinePlusArchive(bool all)
    {
        var dump = "  Notification List:" + Notice("com.old", 1000000000000, "old title", "old body");
        dump += Notice("com.new", 2000000000000, "new title", "first line\nsecond line");
        dump += Notice("com.service", 2000000000001, "service", "running", true);
        dump += "\n  mArchive=Archive\n    StatusBarNotification(pkg=com.dismissed id=1)\n    StatusBarNotification(pkg=com.dismissed id=2)";
        dump += Notice("com.stale", 2000000000002, "stale title", "stale body");
        var result = Call("notifications", new JsonObject { ["all"] = all }, new JsonObject { ["shell"] = new JsonObject { ["dumpsys notification"] = dump } });
        var body = Success(result);
        Before(body, "com.new", "com.old");
        Assert.Contains("first line second line", body);
        Assert.DoesNotContain("com.stale", body);
        Assert.Contains("Recently dismissed", body);
        Assert.Contains("com.dismissed ×2", body);
        Assert.Equal(all, body.Contains("com.service", StringComparison.Ordinal));
    }

    [Fact]
    public void NotificationTitleProtectsMultilineNumericAndAlphanumericCodes()
    {
        var dump = "  Notification List:" + Notice("com.example", 2000000000000, "Verification", "first line\n482913\nA7K9P2");
        var body = Success(Call("notifications", new JsonObject(), new JsonObject { ["shell"] = new JsonObject { ["dumpsys notification"] = dump } }));
        Assert.DoesNotContain("482913", body);
        Assert.DoesNotContain("A7K9P2", body);
        Assert.Contains("first line", body);
    }

    // ------------------------------------------------------------------ apps

    [Fact]
    public void RestoreResolvesPackagesHiddenForUser()
    {
        var fixture = Json("""{"shell": {"pm list packages -u": "package:com.example.hidden\n", "install-existing": "Package com.example.hidden installed for user: 0"}}""");
        var result = Call("apps", Json("""{"action": "restore", "app": "com.example.hidden"}"""), fixture);
        Assert.Contains("installed", Success(result));
        Assert.Contains(Commands(result), command => command.Contains("'install-existing'", StringComparison.Ordinal));
    }

    [Theory]
    [InlineData("com.android.adbd")]
    [InlineData("com.samsung.android.providers.context")]
    [InlineData("android")]
    public void ExactCorePackagesCannotBeStoppedOrRemoved(string package)
    {
        var result = Call("apps", new JsonObject { ["action"] = "stop", ["app"] = package }, new JsonObject { ["shell"] = new JsonObject { ["pm list packages"] = $"package:{package}\n" } });
        Assert.True(IsError(result));
        Assert.Contains("Core system", Text(result));
        Assert.DoesNotContain(Commands(result), command => command.Contains("'force-stop'", StringComparison.Ordinal));
    }

    [Fact]
    public void SystemMembershipUsesWholePackageNotSubstring()
    {
        var fixture = Json("""{"shell": {"pm list packages -s": "package:com.example.app.extra\n", "pm list packages": "package:com.example.app\n", "pm uninstall": "Success"}}""");
        var result = Call("apps", Json("""{"action": "uninstall", "app": "com.example.app", "confirm": true}"""), fixture);
        Success(result);
        var command = Commands(result).First(c => c.Contains("'uninstall'", StringComparison.Ordinal));
        Assert.DoesNotContain("'--user'", command);
    }

    // ------------------------------------------------------------------ logs

    private static string LogLine(string message, int pid = 321, string tag = "AndroidRuntime", string time = "13:00:00.000") =>
        $"2026-10-29 {time} {pid} 321 E {tag}: {message}\n";

    private static JsonObject Logcat(string raw) => new() { ["shell"] = new JsonObject { ["logcat"] = raw } };

    [Fact]
    public void DeadProtectedCrashHidesDetailsKeepsExceptionAndOwnFrames()
    {
        const string package = "com.revolut.revolut";
        var raw = LogLine("FATAL EXCEPTION: main") + LogLine($"Process: {package}, PID: 321");
        raw += LogLine("java.lang.IllegalStateException: SyntheticAccountBalance");
        raw += LogLine($"    at {package}.Main.fail(Main.java:42)");
        raw += LogLine("    at android.app.Activity.run(Activity.java:90)");
        var body = Success(Call("logs", new JsonObject(), Logcat(raw)));
        Assert.Contains("IllegalStateException", body);
        Assert.Contains("Main.fail", body);
        Assert.DoesNotContain("SyntheticAccountBalance", body);
        Assert.DoesNotContain("Activity.run", body);
    }

    [Fact]
    public void RecentProtectedDeadPidNeverLeaksFollowingLines()
    {
        var raw = LogLine("Process: com.revolut.revolut, PID: 321");
        raw += LogLine("SyntheticAccountBalance") + LogLine("ordinary app", pid: 999);
        var body = Success(Call("logs", Json("""{"kind": "recent"}"""), Logcat(raw)));
        Assert.Contains("ordinary app", body);
        Assert.DoesNotContain("SyntheticAccountBalance", body);
    }

    [Theory]
    [InlineData("2h", "2026-10-29 11:30:00.000")]
    [InlineData("13:05", "2026-10-29 13:05:00.000")]
    [InlineData("10-28 14:03", "2026-10-28 14:03:00.000")]
    public void LogsSinceUsesPhoneClockAndSinceNotLineCount(string since, string stamp)
    {
        var raw = LogLine("old", time: "11:00:00.000") + LogLine("new", time: "13:20:00.000");
        var result = Call("logs", new JsonObject { ["kind"] = "recent", ["since"] = since }, new JsonObject { ["shell"] = new JsonObject { ["date +%Y"] = "2026-10-29 13:30:00", ["logcat"] = raw } });
        var body = Success(result);
        Assert.Contains(Commands(result), command => command.Contains($"'-t' '{stamp}'", StringComparison.Ordinal));
        if (since != "10-28 14:03")
        {
            Assert.DoesNotContain("old", body);
            Assert.Contains("new", body);
        }
    }

    [Fact]
    public void LogsInvalidSinceNeverReadsLogBuffer()
    {
        var result = Call("logs", Json("""{"since": "yesterday maybe"}"""), new JsonObject());
        Assert.True(IsError(result));
        Assert.DoesNotContain(Commands(result), command => command.Contains("'logcat'", StringComparison.Ordinal));
    }

    [Fact]
    public void LogsRecentAppFilterUsesStartProcPidNotJustPackageText()
    {
        var raw = LogLine("Start proc 123:com.example.app/u0a100", pid: 1, tag: "ActivityManager");
        raw += LogLine("actual selected log", pid: 123) + LogLine("other app", pid: 999);
        var result = Call("logs", Json("""{"kind": "recent", "app": "com.example.app"}"""), new JsonObject { ["shell"] = new JsonObject { ["pm list packages"] = "package:com.example.app\n", ["logcat"] = raw } });
        var body = Success(result);
        Assert.Contains("actual selected log", body);
        Assert.DoesNotContain("other app", body);
    }

    [Fact]
    public void AnrsMergeDropboxAndEventsWithoutRevealingProtectedReason()
    {
        const string drop = "2026-10-29 13:00:00 data_app_anr (text, 123 bytes)\nProcess: com.revolut.revolut\nPID: 321\nSubject: SyntheticAccountBalance\n";
        var anrEvent = LogLine("[0,321,com.revolut.revolut,1,SyntheticAccountBalance]", tag: "am_anr", time: "13:00:20.000");
        var body = Success(Call("logs", Json("""{"kind": "anr"}"""), new JsonObject { ["shell"] = new JsonObject { ["dumpsys dropbox"] = drop, ["logcat"] = anrEvent } }));
        Assert.Equal(1, Count(body, "com.revolut.revolut"));
        Assert.DoesNotContain("SyntheticAccountBalance", body);
        Assert.Contains("reason not shown", body);
    }

    [Fact]
    public void RepeatedCrashesGroupWithoutLosingCount()
    {
        var raw = "";
        foreach (var time in new[] { "13:00:00.000", "13:05:00.000" })
        {
            raw += LogLine("FATAL EXCEPTION: main", time: time) + LogLine("Process: com.example.app, PID: 321", time: time);
            raw += LogLine("java.lang.IllegalStateException: broken", time: time);
        }
        var body = Success(Call("logs", new JsonObject(), Logcat(raw)));
        Assert.Contains("Repeated 2 time(s)", body);
        Assert.Equal(1, Count(body, "IllegalStateException"));
    }

    [Fact]
    public void CrashFilterAcceptsUninstalledFullPackage()
    {
        var raw = LogLine("FATAL EXCEPTION: main") + LogLine("Process: org.gone.app, PID: 321");
        raw += LogLine("java.lang.IllegalStateException: broken");
        var result = Call("logs", Json("""{"app": "org.gone.app"}"""), Logcat(raw));
        Assert.Contains("org.gone.app", Success(result));
        Assert.DoesNotContain(Commands(result), command => command.Contains("'pm' 'list'", StringComparison.Ordinal));
    }

    [Fact]
    public void ProtectedNativeKeepsSignalAndAppBacktraceOnly()
    {
        var raw = LogLine("*** *** *** ***", tag: "DEBUG");
        raw += LogLine("pid: 123, tid: 123, name: Main >>> com.revolut.revolut <<<", tag: "DEBUG");
        raw += LogLine("signal 6 (SIGABRT), code -1 (SI_QUEUE), fault addr --------", tag: "DEBUG");
        raw += LogLine("Abort message: SyntheticAccountBalance", tag: "DEBUG");
        raw += LogLine("    #00 pc 000000000001aabb /apex/com.android.runtime/lib64/bionic/libc.so", tag: "DEBUG");
        raw += LogLine("    #01 pc 000000000002aabb /data/app/com.revolut.revolut/lib/arm64/libapp.so (CrashHere+4)", tag: "DEBUG");
        var body = Success(Call("logs", new JsonObject(), Logcat(raw)));
        Assert.Contains("signal 6 (SIGABRT)", body);
        Assert.Contains("libapp.so", body);
        Assert.Contains("pid 123", body);
        Assert.DoesNotContain("libc.so", body);
        Assert.DoesNotContain("SyntheticAccountBalance", body);
    }

    [Fact]
    public void DeepApplicationFrameSurvivesFrameworkNoise()
    {
        var raw = LogLine("FATAL EXCEPTION: main") + LogLine("Process: com.example.app, PID: 321");
        raw += LogLine("java.lang.IllegalStateException: broken");
        raw += string.Concat(Enumerable.Range(0, 20).Select(index => LogLine($"    at android.app.Activity.method{index}(Activity.java:90)")));
        raw += LogLine("    at com.example.app.Main.crashed(Main.kt:42)");
        var body = Success(Call("logs", new JsonObject(), Logcat(raw)));
        Assert.Contains("Main.crashed(Main.kt:42)", body);
        Assert.Equal(1, Count(body, "Activity.method"));
    }

    // ------------------------------------------------------------------ launch

    public static TheoryData<string> RiskyIntents => new()
    {
        """{"action": "android.intent.action.CALL_PRIVILEGED", "data": "tel:123"}""",
        """{"action": "android.intent.action.RESPOND_VIA_MESSAGE", "data": "smsto:123"}""",
        """{"action": "org.vendor.intent.action.WIPE_DATA"}""",
        """{"action": "android.intent.action.UNINSTALL_PACKAGE", "data": "package:com.example.app"}""",
        """{"data": "https://checkout.stripe.com/pay/test"}""",
        """{"data": "https://www.paypal.com/checkoutnow/session"}""",
        """{"data": "upi://pay?pa=somewhere"}""",
        """{"action": "android.intent.action.VIEW", "extras": {"auto_send": false}}""",
    };

    [Theory, MemberData(nameof(RiskyIntents))]
    public void RiskyIntentsRequireApprovalBeforeResolutionOrLaunch(string arguments)
    {
        var result = Call("launch", Json(arguments), new JsonObject());
        Assert.True(IsError(result));
        Assert.DoesNotContain(Commands(result), command => command.Contains("'am' 'start'", StringComparison.Ordinal) || command.Contains("'resolve-activity'", StringComparison.Ordinal));
        Assert.True(Text(result).Contains("Confirmation required", StringComparison.Ordinal) || Text(result).Contains("accord=", StringComparison.Ordinal), Text(result));
    }

    [Fact]
    public void PlainSmsComposeIsReversibleAndNeedsNoSendAgreement()
    {
        var fixture = DraftFixture();
        fixture["shell"]!["resolve-activity"] = "com.example.sms/.Main";
        fixture["shell"]!["query-activities"] = "com.example.sms/.Main";
        var result = Call("launch", Json("""{"action": "android.intent.action.SENDTO", "data": "smsto:123", "then": "none"}"""), fixture);
        Assert.Contains("foreground read back", Success(result));
        Assert.Contains(Commands(result), command => command.Contains("'am' 'start'", StringComparison.Ordinal));
    }

    [Fact]
    public void ProtectedCandidateBlocksIntentBeforeLaunch()
    {
        var fixture = Json("""{"shell": {"resolve-activity": "com.example.app/.Main", "query-activities": "com.example.app/.Main\ncom.revolut.revolut/.Main"}}""");
        var result = Call("launch", Json("""{"data": "https://ordinary.example/path"}"""), fixture);
        Assert.True(IsError(result));
        Assert.Contains("protected", Text(result));
        Assert.DoesNotContain(Commands(result), command => command.Contains("'am' 'start'", StringComparison.Ordinal));
    }
}
