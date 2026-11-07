using System.Text.Json.Nodes;

namespace NexusAndroid.Tests.Engine;

/// <summary>An image identical to the last one sent to the same session is not sent again (look, and the view act
/// returns): same marks, same geometry, same screen content and the same colour print. Another session, a changed screen
/// or a zoom in between sends it again.</summary>
public sealed class UnchangedImageTests
{
    private static JsonObject Obj(string json) => JsonNode.Parse(json)!.AsObject();
    private static int Images(JsonObject result) => result["content"]!.AsArray().OfType<JsonObject>().Count(c => c["type"]?.ToString() == "image");
    private static string Text(JsonObject result) => string.Join('\n', result["content"]!.AsArray().OfType<JsonObject>().Select(c => c["text"]?.ToString() ?? ""));

    [Fact]
    public void IdenticalImageIsNotSentTwiceToTheSameSession()
    {
        var phone = new FakePhone { Picture = ImageChain.EncodePng(PixelBuffer.CreateSyntheticScreen(1080, 1920)) };
        var engine = new NativeEngine(new FakeAdb(phone), phone, Path.Join(TestPaths.Transfers, "unchanged-" + Guid.NewGuid().ToString("N")), () => new FakeControl(phone));
        var prior = NativeSafety.Session.Value;
        try
        {
            NativeSafety.Session.Value = "unchanged-a";
            var both = Obj("{\"show\":\"both\"}");
            Assert.Equal(1, Images(engine.Execute("look", both)));
            var again = engine.Execute("look", both);
            Assert.Equal(0, Images(again));
            Assert.Contains("image unchanged since the last one sent (not sent again)", Text(again));
            Assert.Contains("[1]", Text(again)); // the element list still comes, with the scale of the image already held

            var act = engine.Execute("act", Obj("{\"steps\":[{\"do\":\"wait\",\"text\":\"Open\",\"timeout\":0}],\"then\":\"both\"}"));
            Assert.Equal(0, Images(act));
            Assert.Contains("not sent again", Text(act));

            NativeSafety.Session.Value = "unchanged-b"; // another agent never received it
            Assert.Equal(1, Images(engine.Execute("look", both)));

            NativeSafety.Session.Value = "unchanged-a";
            phone.Label = "Changed"; // tree content changed
            Assert.Equal(1, Images(engine.Execute("look", both)));
            var drawn = PixelBuffer.CreateSyntheticScreen(1080, 1920);
            drawn.FillRect(0, 600, 1080, 1500, 255, 255, 255);
            phone.Picture = ImageChain.EncodePng(drawn); // pixels only (a map, a video): the tree did not change
            Assert.Equal(1, Images(engine.Execute("look", both)));
            Assert.Equal(0, Images(engine.Execute("look", both)));
            Assert.Equal(0, Images(engine.Execute("look", Obj("{\"show\":\"image\"}")))); // no [N] drawn either way: same image
            Assert.Equal(1, Images(engine.Execute("look", Obj("{\"show\":\"both\",\"marks\":\"all\"}")))); // [N] drawn: another image

            Assert.Equal(1, Images(engine.Execute("look", Obj("{\"region\":[0,0,100,100]}"))));
            Assert.Equal(1, Images(engine.Execute("look", Obj("{\"show\":\"image\"}")))); // the zoom was the last one sent
        }
        finally { NativeSafety.Session.Value = prior; }
    }

    [Theory]
    [InlineData(null, 120, 300000)]
    [InlineData("", 120, 300000)]
    [InlineData("abc", 120, 300000)]
    [InlineData("-5", 120, 300000)]
    [InlineData("45", 45, 300000)]
    [InlineData("600", 600, 660000)]
    public void AwakeIdleDelayFollowsItsVariable(string? value, double idle, int raised)
    {
        Assert.Equal(idle, NativeEngine.AwakeIdleSeconds(value));
        Assert.Equal(raised, NativeEngine.RaisedTimeoutMs(NativeEngine.AwakeIdleSeconds(value)));
    }

    [Fact]
    public void ThumbnailToleratesEncodingNoiseOnly()
    {
        var a = ImageChain.Thumbnail(PixelBuffer.CreateSyntheticScreen(1080, 1920));
        var noisy = (byte[])a.Clone();
        for (var i = 0; i < noisy.Length; i += 7)
            noisy[i] = (byte)Math.Min(255, noisy[i] + ImageChain.ThumbnailSlack);
        Assert.True(ImageChain.SameThumbnail(a, noisy));
        noisy[0] = (byte)(a[0] > 127 ? 0 : 255);
        Assert.False(ImageChain.SameThumbnail(a, noisy));
    }
}
