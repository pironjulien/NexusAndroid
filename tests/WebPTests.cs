using System.Runtime.InteropServices.WindowsRuntime;
using Windows.Graphics.Imaging;
using Windows.Storage.Streams;

namespace NexusAndroid.Tests.Images;

/// <summary>The lossless WebP sent to the AI (NexusShared/images.md) decodes to the exact pixels encoded, checked with
/// Windows' own WebP decoder (Windows 11 ships it; skipped where the codec is missing).</summary>
public sealed class WebPTests
{
    private static byte[]? DecodeWithWindows(byte[] webp, out int width, out int height)
    {
        width = height = 0;
        try
        {
            using var stream = new InMemoryRandomAccessStream();
            stream.WriteAsync(webp.AsBuffer()).AsTask().GetAwaiter().GetResult();
            stream.Seek(0);
            var decoder = BitmapDecoder.CreateAsync(BitmapDecoder.WebpDecoderId, stream).AsTask().GetAwaiter().GetResult();
            width = (int)decoder.PixelWidth;
            height = (int)decoder.PixelHeight;
            var data = decoder.GetPixelDataAsync(BitmapPixelFormat.Bgra8, BitmapAlphaMode.Straight, new BitmapTransform(), ExifOrientationMode.IgnoreExifOrientation, ColorManagementMode.DoNotColorManage).AsTask().GetAwaiter().GetResult();
            return data.DetachPixelData();
        }
        catch (Exception e) when (e is System.Runtime.InteropServices.COMException or NotSupportedException or ArgumentException)
        {
            return null;
        }
    }

    /// <summary>The image a tool sent, decoded: WebP through the tests' own VP8L decoder (runs on CI servers too),
    /// anything else through the engine's own decoder.</summary>
    internal static PixelBuffer DecodeSent(byte[] bytes)
    {
        if (bytes.Length < 12 || System.Text.Encoding.ASCII.GetString(bytes, 8, 4) != "WEBP") return ImageChain.Decode(bytes);
        var pixels = WebPDecoder.Decode(bytes, out var width, out var height);
        return new PixelBuffer(width, height, pixels);
    }

    private static PixelBuffer Sample(int width, int height, int seed)
    {
        var random = new Random(seed);
        var picture = PixelBuffer.CreateSyntheticScreen(width, height);
        var bgra = picture.Bgra;
        // Noise and a gradient band so every predictor, LZ77 match and Huffman table is exercised.
        for (var y = height / 2; y < height / 2 + 40 && y < height; y++)
            for (var x = 0; x < width; x++)
            {
                var i = (y * width + x) * 4;
                bgra[i] = (byte)random.Next(256); bgra[i + 1] = (byte)(x * 255 / width); bgra[i + 2] = (byte)(y & 0xFF); bgra[i + 3] = 255;
            }
        return picture;
    }

    [Theory]
    [InlineData(1, 1, 1)]
    [InlineData(37, 23, 2)]
    [InlineData(1248, 1972, 3)]
    public void LosslessWebPDecodesToTheExactPixels(int width, int height, int seed)
    {
        var picture = Sample(width, height, seed);
        var webp = WebP.Lossless(picture.Bgra, width, height);
        Assert.Equal("RIFF", System.Text.Encoding.ASCII.GetString(webp, 0, 4));
        Assert.Equal("WEBP", System.Text.Encoding.ASCII.GetString(webp, 8, 4));
        // Our own decoder everywhere (CI included), then Windows' decoder as an independent check where it exists.
        var ours = WebPDecoder.Decode(webp, out var w, out var h);
        Assert.Equal((width, height), (w, h));
        Assert.True(picture.Bgra.AsSpan().SequenceEqual(ours), "test decoder: pixels differ from the encoded ones");
        var windows = DecodeWithWindows(webp, out _, out _);
        if (windows is not null) Assert.True(picture.Bgra.AsSpan().SequenceEqual(windows), "Windows decoder: pixels differ from the encoded ones");
    }

    [Fact]
    public void TheChainSendsLosslessWebPWhileItFitsAndKeepsThePixelsForZoom()
    {
        var result = ImageChain.ProcessChain(Sample(1080, 1920, 4), maxBytes: 500 * 1024);
        Assert.Equal("image/webp", result.MimeType);
        Assert.False(result.IsLossy);
        Assert.NotNull(result.Pixels);
        Assert.Equal((result.Width, result.Height), (result.Pixels!.Width, result.Pixels.Height));
    }

    [Fact]
    public void NearLosslessStepsStayWebPAndDecode()
    {
        // A photo-like screen too heavy for plain lossless: the 2- or 3-bit step still sends WebP, readable back.
        var random = new Random(7);
        var photo = Sample(1248, 1972, 7);
        for (var i = 0; i < photo.Bgra.Length; i += 4) { var v = (byte)((i / 4 % 1248) / 5 + random.Next(4)); photo.Bgra[i] = v; photo.Bgra[i + 1] = (byte)(v / 2); photo.Bgra[i + 2] = (byte)(255 - v); photo.Bgra[i + 3] = 255; }
        var lossless = WebP.Lossless(photo.Bgra, photo.Width, photo.Height).Length;
        var result = ImageChain.ProcessChain(photo, maxBytes: lossless - 1);
        Assert.Equal("image/webp", result.MimeType);
        Assert.True(result.IsLossy);
        Assert.True(result.Bytes.Length < lossless);
        var back = DecodeSent(result.Bytes);
        Assert.True(result.Pixels!.Bgra.AsSpan().SequenceEqual(back.Bgra), "near-lossless pixels differ");
    }

    [Fact]
    public void AnImageTooHeavyForLosslessStaysUnderTheCap()
    {
        var random = new Random(5);
        var noise = new PixelBuffer(1200, 1200);
        random.NextBytes(noise.Bgra);
        for (var i = 3; i < noise.Bgra.Length; i += 4) noise.Bgra[i] = 255;
        var result = ImageChain.ProcessChain(noise, maxBytes: 500 * 1024);
        Assert.True(result.Bytes.Length <= 500 * 1024, result.Bytes.Length + " bytes");
        Assert.True(result.IsLossy);
    }

    [Fact]
    public void FilesForTheUserStayPng()
    {
        var result = ImageChain.ProcessChain(Sample(200, 100, 6), maxBytes: 0, preferredLosslessFormat: "png");
        Assert.Equal("image/png", result.MimeType);
    }
}
