using System.Buffers.Binary;
using System.IO.Compression;
using System.Runtime.InteropServices;

namespace NexusAndroid;

public sealed class ImageChainResult
{
    public required int Width { get; init; }
    public required int Height { get; init; }
    public required int OriginalWidth { get; init; }
    public required int OriginalHeight { get; init; }
    public required double ScaleX { get; init; }
    public required double ScaleY { get; init; }
    public required string Format { get; init; }
    public required string MimeType { get; init; }
    public required byte[] Bytes { get; init; }
    public required bool IsLossy { get; init; }
    public required int Quality { get; init; }

    public Dictionary<string, object> ToMetaDictionary() => new(StringComparer.Ordinal)
    {
        ["width"] = Width,
        ["height"] = Height,
        ["original_width"] = OriginalWidth,
        ["original_height"] = OriginalHeight,
        ["scale_x"] = ScaleX,
        ["scale_y"] = ScaleY,
        ["format"] = Format,
        ["mime_type"] = MimeType,
        ["bytes"] = Bytes.Length,
        ["is_lossy"] = IsLossy,
        ["quality"] = Quality,
    };
}

public sealed class PixelBuffer
{
    public int Width { get; }
    public int Height { get; }
    public byte[] Bgra { get; } // 4 bytes per pixel: B, G, R, A

    public PixelBuffer(int width, int height, byte[]? bgra = null)
    {
        ArgumentOutOfRangeException.ThrowIfLessThanOrEqual(width, 0);
        ArgumentOutOfRangeException.ThrowIfLessThanOrEqual(height, 0);
        Width = width;
        Height = height;
        Bgra = bgra ?? new byte[width * height * 4];
        if (Bgra.Length != width * height * 4)
        {
            throw new ArgumentException($"Invalid BGRA buffer length {Bgra.Length} for {width}x{height}.");
        }
    }

    public PixelBuffer Clone()
    {
        var copy = new byte[Bgra.Length];
        Buffer.BlockCopy(Bgra, 0, copy, 0, Bgra.Length);
        return new PixelBuffer(Width, Height, copy);
    }

    public static PixelBuffer CreateSyntheticScreen(int width, int height, bool noisy = false, int seed = 42)
    {
        var buf = new byte[width * height * 4];
        if (noisy)
        {
            var rng = new Random(seed);
            rng.NextBytes(buf);
            for (var i = 3; i < buf.Length; i += 4)
            {
                buf[i] = 255;
            }
            return new PixelBuffer(width, height, buf);
        }

        for (var y = 0; y < height; y++)
        {
            var r = (byte)((y * 180) / Math.Max(1, height));
            for (var x = 0; x < width; x++)
            {
                var idx = (y * width + x) * 4;
                buf[idx] = (byte)((x * 200) / Math.Max(1, width)); // B
                buf[idx + 1] = (byte)(((x + y) * 90) / Math.Max(1, width + height)); // G
                buf[idx + 2] = r; // R
                buf[idx + 3] = 255; // A
            }
        }
        return new PixelBuffer(width, height, buf);
    }

    public void FillRect(int x1, int y1, int x2, int y2, byte r, byte g, byte b, byte a = 255)
    {
        var left = Math.Clamp(Math.Min(x1, x2), 0, Width);
        var right = Math.Clamp(Math.Max(x1, x2), 0, Width);
        var top = Math.Clamp(Math.Min(y1, y2), 0, Height);
        var bottom = Math.Clamp(Math.Max(y1, y2), 0, Height);
        for (var y = top; y < bottom; y++)
        {
            var rowOffset = y * Width * 4;
            for (var x = left; x < right; x++)
            {
                var idx = rowOffset + x * 4;
                Bgra[idx] = b;
                Bgra[idx + 1] = g;
                Bgra[idx + 2] = r;
                Bgra[idx + 3] = a;
            }
        }
    }

    public void StrokeRect(int x1, int y1, int x2, int y2, byte r, byte g, byte b)
    {
        FillRect(x1, y1, x2, y1 + 1, r, g, b);
        FillRect(x1, y2 - 1, x2, y2, r, g, b);
        FillRect(x1, y1, x1 + 1, y2, r, g, b);
        FillRect(x2 - 1, y1, x2, y2, r, g, b);
    }

    // 5x7 bitmap digits for [1]..[N] overlays
    private static readonly Dictionary<char, string[]> Glyph5x7 = new()
    {
        ['0'] = ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
        ['1'] = ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
        ['2'] = ["01110", "10001", "00001", "00110", "01000", "10000", "11111"],
        ['3'] = ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
        ['4'] = ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
        ['5'] = ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
        ['6'] = ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
        ['7'] = ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
        ['8'] = ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
        ['9'] = ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    };

    public void DrawBadge(int index, int cx, int cy)
    {
        var label = index.ToString(System.Globalization.CultureInfo.InvariantCulture);
        var charW = 6;
        var textW = label.Length * charW - 1;
        var textH = 7;
        var pad = 3;
        var x1 = Math.Clamp(cx - textW / 2 - pad, 0, Math.Max(0, Width - 1));
        var y1 = Math.Clamp(cy - textH / 2 - pad, 0, Math.Max(0, Height - 1));
        var x2 = Math.Clamp(cx + (textW + 1) / 2 + pad, 0, Width);
        var y2 = Math.Clamp(cy + (textH + 1) / 2 + pad, 0, Height);

        FillRect(x1, y1, x2, y2, 0, 0, 0, 230);
        StrokeRect(x1, y1, x2, y2, 255, 214, 10);

        var startX = x1 + pad;
        var startY = y1 + pad;
        for (var i = 0; i < label.Length; i++)
        {
            if (!Glyph5x7.TryGetValue(label[i], out var rows))
            {
                continue;
            }
            for (var ry = 0; ry < rows.Length; ry++)
            {
                var py = startY + ry;
                if (py < 0 || py >= Height) continue;
                var row = rows[ry];
                for (var rx = 0; rx < row.Length; rx++)
                {
                    if (row[rx] != '1') continue;
                    var px = startX + i * charW + rx;
                    if (px < 0 || px >= Width) continue;
                    var idx = (py * Width + px) * 4;
                    Bgra[idx] = 10;
                    Bgra[idx + 1] = 214;
                    Bgra[idx + 2] = 255;
                    Bgra[idx + 3] = 255;
                }
            }
        }
    }
}

public static class ImageChain
{
    public const int DefaultMaxEdge = 2000;
    public const int DefaultMaxRawPixels = 2_560_000;
    public const int DefaultMaxInlineBytes = 500 * 1024;

    static ImageChain()
    {
        GdiPlusInterop.EnsureInitialized();
    }

    public static (int TargetW, int TargetH) ComputeScaledDimensions(
        int origW,
        int origH,
        int maxEdge = DefaultMaxEdge,
        int maxPixels = DefaultMaxRawPixels)
    {
        var w = Math.Max(1, origW);
        var h = Math.Max(1, origH);
        var scale = 1.0;

        if (maxEdge > 0 && Math.Max(w, h) > maxEdge)
        {
            scale = Math.Min(scale, (double)maxEdge / Math.Max(w, h));
        }

        if (maxPixels > 0 && (long)w * h > maxPixels)
        {
            var pixelScale = Math.Sqrt((double)maxPixels / ((double)w * h));
            scale = Math.Min(scale, pixelScale);
        }

        if (scale >= 0.999999)
        {
            return (w, h);
        }

        var targetW = Math.Max(1, (int)Math.Round(w * scale));
        var targetH = Math.Max(1, (int)Math.Round(h * scale));
        return (targetW, targetH);
    }

    public static PixelBuffer ResizeBilinear(PixelBuffer src, int targetW, int targetH)
    {
        targetW = Math.Max(1, targetW);
        targetH = Math.Max(1, targetH);
        if (src.Width == targetW && src.Height == targetH)
        {
            return src.Clone();
        }

        var dst = new byte[targetW * targetH * 4];
        var srcBgra = src.Bgra;
        var srcW = src.Width;
        var srcH = src.Height;

        var xRatio = (double)srcW / targetW;
        var yRatio = (double)srcH / targetH;

        for (var y = 0; y < targetH; y++)
        {
            var sy = (y + 0.5) * yRatio - 0.5;
            var y0 = Math.Clamp((int)Math.Floor(sy), 0, srcH - 1);
            var y1 = Math.Clamp(y0 + 1, 0, srcH - 1);
            var fy = sy - Math.Floor(sy);
            if (sy < 0) fy = 0;
            var ify = 1.0 - fy;

            var row0 = y0 * srcW * 4;
            var row1 = y1 * srcW * 4;
            var dstRow = y * targetW * 4;

            for (var x = 0; x < targetW; x++)
            {
                var sx = (x + 0.5) * xRatio - 0.5;
                var x0 = Math.Clamp((int)Math.Floor(sx), 0, srcW - 1);
                var x1 = Math.Clamp(x0 + 1, 0, srcW - 1);
                var fx = sx - Math.Floor(sx);
                if (sx < 0) fx = 0;
                var ifx = 1.0 - fx;

                var w00 = ifx * ify;
                var w10 = fx * ify;
                var w01 = ifx * fy;
                var w11 = fx * fy;

                var p00 = row0 + x0 * 4;
                var p10 = row0 + x1 * 4;
                var p01 = row1 + x0 * 4;
                var p11 = row1 + x1 * 4;

                var dIdx = dstRow + x * 4;
                for (var c = 0; c < 4; c++)
                {
                    var v = srcBgra[p00 + c] * w00
                          + srcBgra[p10 + c] * w10
                          + srcBgra[p01 + c] * w01
                          + srcBgra[p11 + c] * w11;
                    dst[dIdx + c] = (byte)Math.Clamp((int)Math.Round(v), 0, 255);
                }
            }
        }

        return new PixelBuffer(targetW, targetH, dst);
    }

    public static byte[] EncodePng(PixelBuffer buf)
    {
        using var ms = new MemoryStream();
        // PNG Signature
        ms.Write([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]);

        // IHDR chunk (width, height, bit depth=8, color type=6 RGBA, compression=0, filter=0, interlace=0)
        Span<byte> ihdr = stackalloc byte[13];
        BinaryPrimitives.WriteUInt32BigEndian(ihdr[0..4], (uint)buf.Width);
        BinaryPrimitives.WriteUInt32BigEndian(ihdr[4..8], (uint)buf.Height);
        ihdr[8] = 8; // 8 bits per channel
        ihdr[9] = 6; // RGBA
        ihdr[10] = 0;
        ihdr[11] = 0;
        ihdr[12] = 0;
        WritePngChunk(ms, "IHDR"u8, ihdr);

        // IDAT chunk (zlib compressed scanlines with filter byte 0=None or 1=Sub)
        using var idatRaw = new MemoryStream();
        using (var zlib = new ZLibStream(idatRaw, CompressionLevel.Fastest, leaveOpen: true))
        {
            var rowBytes = new byte[1 + buf.Width * 4];
            var bgra = buf.Bgra;
            for (var y = 0; y < buf.Height; y++)
            {
                rowBytes[0] = 0; // Filter type 0 (None)
                var srcRow = y * buf.Width * 4;
                for (var x = 0; x < buf.Width; x++)
                {
                    var s = srcRow + x * 4;
                    var d = 1 + x * 4;
                    rowBytes[d] = bgra[s + 2];     // R
                    rowBytes[d + 1] = bgra[s + 1]; // G
                    rowBytes[d + 2] = bgra[s];     // B
                    rowBytes[d + 3] = bgra[s + 3]; // A
                }
                zlib.Write(rowBytes, 0, rowBytes.Length);
            }
        }
        WritePngChunk(ms, "IDAT"u8, idatRaw.ToArray());
        WritePngChunk(ms, "IEND"u8, []);
        return ms.ToArray();
    }

    private static void WritePngChunk(Stream stream, ReadOnlySpan<byte> type, ReadOnlySpan<byte> data)
    {
        Span<byte> lenBuf = stackalloc byte[4];
        BinaryPrimitives.WriteUInt32BigEndian(lenBuf, (uint)data.Length);
        stream.Write(lenBuf);
        stream.Write(type);
        if (!data.IsEmpty)
        {
            stream.Write(data);
        }

        var crc = ComputeCrc32(type, data);
        Span<byte> crcBuf = stackalloc byte[4];
        BinaryPrimitives.WriteUInt32BigEndian(crcBuf, crc);
        stream.Write(crcBuf);
    }

    private static readonly uint[] CrcTable = BuildCrc32Table();

    private static uint[] BuildCrc32Table()
    {
        var table = new uint[256];
        for (uint n = 0; n < 256; n++)
        {
            var c = n;
            for (var k = 0; k < 8; k++)
            {
                c = (c & 1) != 0 ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            }
            table[n] = c;
        }
        return table;
    }

    private static uint ComputeCrc32(ReadOnlySpan<byte> first, ReadOnlySpan<byte> second)
    {
        var c = 0xFFFFFFFFu;
        foreach (var b in first)
        {
            c = CrcTable[(c ^ b) & 0xFF] ^ (c >> 8);
        }
        foreach (var b in second)
        {
            c = CrcTable[(c ^ b) & 0xFF] ^ (c >> 8);
        }
        return c ^ 0xFFFFFFFFu;
    }

    /// <summary>
    /// Encodes a lossless WebP (RIFF/WEBP/VP8L) container with exact VP8L 14-bit dimensions header
    /// and lossless compressed BGRA scanlines so both header inspection and round-trip Decode work without external DLLs.
    /// </summary>
    public static byte[] EncodeLossyJpeg(PixelBuffer buf, int quality = 80)
    {
        quality = Math.Clamp(quality, 1, 100);
        return GdiPlusInterop.EncodeJpeg(buf, quality);
    }

    public static PixelBuffer Decode(ReadOnlySpan<byte> imageBytes) => GdiPlusInterop.DecodeImage(imageBytes);

    /// <summary>
    /// Full image processing chain:
    /// - Blackout sensitive bounding boxes in original pixel coordinates
    /// - Resize to maxEdge / maxRawPixels
    /// - Draw [1]..[N] markers at scaled coordinates
    /// - Encode standard lossless PNG if within maxBytes budget and !forceLossy
    /// - Otherwise apply lossy JPEG compression + adaptive resolution scaling until strictly <= maxBytes
    /// </summary>
    public static ImageChainResult ProcessChain(
        PixelBuffer source,
        int maxEdge = DefaultMaxEdge,
        int maxBytes = DefaultMaxInlineBytes,
        string preferredLosslessFormat = "png",
        bool forceLossy = false,
        int maxRawPixels = DefaultMaxRawPixels,
        IReadOnlyList<(int X1, int Y1, int X2, int Y2)>? blackouts = null,
        IReadOnlyList<(int Index, int Cx, int Cy)>? badges = null)
    {
        ArgumentNullException.ThrowIfNull(source);
        if(!string.Equals(preferredLosslessFormat,"png",StringComparison.OrdinalIgnoreCase))throw new ArgumentException("The native lossless encoder supports standard PNG; no private WebP format is accepted");
        var origW = source.Width;
        var origH = source.Height;

        var workingSource = source;
        if (blackouts is { Count: > 0 })
        {
            workingSource = source.Clone();
            foreach (var (x1, y1, x2, y2) in blackouts)
            {
                workingSource.FillRect(x1 - 2, y1 - 2, x2 + 2, y2 + 2, 0, 0, 0, 255);
            }
        }

        var (targetW, targetH) = ComputeScaledDimensions(origW, origH, maxEdge, maxRawPixels);
        var scaled = ResizeBilinear(workingSource, targetW, targetH);

        if (badges is { Count: > 0 })
        {
            var invScaleX = (double)targetW / origW;
            var invScaleY = (double)targetH / origH;
            foreach (var (index, cx, cy) in badges)
            {
                var bx = (int)Math.Round(cx * invScaleX);
                var by = (int)Math.Round(cy * invScaleY);
                scaled.DrawBadge(index, bx, by);
            }
        }

        if (!forceLossy)
        {
            const bool isPng = true;
            var losslessBytes = EncodePng(scaled);
            if (maxBytes <= 0 || losslessBytes.Length <= maxBytes)
            {
                return new ImageChainResult
                {
                    Width = scaled.Width,
                    Height = scaled.Height,
                    OriginalWidth = origW,
                    OriginalHeight = origH,
                    ScaleX = Math.Round((double)origW / scaled.Width, 4),
                    ScaleY = Math.Round((double)origH / scaled.Height, 4),
                    Format = isPng ? "png" : "webp",
                    MimeType = isPng ? "image/png" : "image/webp",
                    Bytes = losslessBytes,
                    IsLossy = false,
                    Quality = 100,
                };
            }
        }

        // Lossy compression + adaptive resolution scaling to strictly obey maxBytes (Anti-Crash 400)
        int[] qualities = [82, 72, 60, 48, 36, 26, 18];
        var currentBuf = scaled;
        byte[] bestBytes = [];
        var bestQuality = 82;

        for (var scaleAttempt = 0; scaleAttempt < 8; scaleAttempt++)
        {
            foreach (var q in qualities)
            {
                var candidate = EncodeLossyJpeg(currentBuf, q);
                bestBytes = candidate;
                bestQuality = q;
                if (maxBytes <= 0 || candidate.Length <= maxBytes)
                {
                    return new ImageChainResult
                    {
                        Width = currentBuf.Width,
                        Height = currentBuf.Height,
                        OriginalWidth = origW,
                        OriginalHeight = origH,
                        ScaleX = Math.Round((double)origW / currentBuf.Width, 4),
                        ScaleY = Math.Round((double)origH / currentBuf.Height, 4),
                        Format = "jpeg",
                        MimeType = "image/jpeg",
                        Bytes = candidate,
                        IsLossy = true,
                        Quality = q,
                    };
                }
            }

            // Adaptive resolution downscaling (3/4 per step) when byte budget is still exceeded
            var nextW = Math.Max(48, (currentBuf.Width * 3) / 4);
            var nextH = Math.Max(48, (currentBuf.Height * 3) / 4);
            if (nextW == currentBuf.Width && nextH == currentBuf.Height)
            {
                break;
            }
            currentBuf = ResizeBilinear(scaled, nextW, nextH);
        }

        return new ImageChainResult
        {
            Width = currentBuf.Width,
            Height = currentBuf.Height,
            OriginalWidth = origW,
            OriginalHeight = origH,
            ScaleX = Math.Round((double)origW / currentBuf.Width, 4),
            ScaleY = Math.Round((double)origH / currentBuf.Height, 4),
            Format = "jpeg",
            MimeType = "image/jpeg",
            Bytes = bestBytes,
            IsLossy = true,
            Quality = bestQuality,
        };
    }

    public static ImageChainResult ProcessEncodedImage(
        ReadOnlySpan<byte> encodedBytes,
        int maxEdge = DefaultMaxEdge,
        int maxBytes = DefaultMaxInlineBytes,
        string preferredLosslessFormat = "png",
        bool forceLossy = false,
        int maxRawPixels = DefaultMaxRawPixels,
        IReadOnlyList<(int X1, int Y1, int X2, int Y2)>? blackouts = null,
        IReadOnlyList<(int Index, int Cx, int Cy)>? badges = null)
    {
        var decoded = Decode(encodedBytes);
        return ProcessChain(decoded, maxEdge, maxBytes, preferredLosslessFormat, forceLossy, maxRawPixels, blackouts, badges);
    }
}

internal static class GdiPlusInterop
{
    private static IntPtr _gdiplusToken;
    private static readonly object InitLock = new();

    private static readonly Guid JpegEncoderClsid = new("557CF401-1A04-11D3-9A73-0000F81EF32E");
    private static readonly Guid EncoderQualityGuid = new("1D5BE4B5-FA4A-452D-9CDD-5DB35105E7EB");

    private const int PixelFormat32bppARGB = 0x0026200A;
    private const uint ImageLockModeRead = 1;

    [StructLayout(LayoutKind.Sequential)]
    private struct GdiplusStartupInput
    {
        public uint GdiplusVersion;
        public IntPtr DebugEventCallback;
        public int SuppressBackgroundThread;
        public int SuppressExternalCodecs;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct GdiRect
    {
        public int X;
        public int Y;
        public int Width;
        public int Height;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct BitmapData
    {
        public uint Width;
        public uint Height;
        public int Stride;
        public int PixelFormat;
        public IntPtr Scan0;
        public UIntPtr Reserved;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct EncoderParameter
    {
        public Guid Guid;
        public uint NumberOfValues;
        public uint Type;
        public IntPtr Value;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct EncoderParametersOne
    {
        public uint Count;
        public EncoderParameter Parameter;
    }

    [DllImport("gdiplus.dll", ExactSpelling = true)]
    private static extern int GdiplusStartup(out IntPtr token, ref GdiplusStartupInput input, out IntPtr output);

    [DllImport("gdiplus.dll", ExactSpelling = true)]
    private static extern int GdipCreateBitmapFromScan0(
        int width,
        int height,
        int stride,
        int format,
        IntPtr scan0,
        out IntPtr bitmap);

    [DllImport("gdiplus.dll", ExactSpelling = true)]
    private static extern int GdipCreateBitmapFromStream(IntPtr stream, out IntPtr bitmap);
    [DllImport("gdiplus.dll", ExactSpelling=true)]
    private static extern int GdipGetPropertyItemSize(IntPtr image,int property,out uint size);
    [DllImport("gdiplus.dll", ExactSpelling=true)]
    private static extern int GdipGetPropertyItem(IntPtr image,int property,uint size,IntPtr item);
    [DllImport("gdiplus.dll", ExactSpelling=true)]
    private static extern int GdipImageRotateFlip(IntPtr image,int rotateFlipType);
    [StructLayout(LayoutKind.Sequential)]
    private struct PropertyItem{public int Id;public uint Length;public short Type;public IntPtr Value;}

    [DllImport("gdiplus.dll", ExactSpelling = true)]
    private static extern int GdipGetImageWidth(IntPtr image, out uint width);

    [DllImport("gdiplus.dll", ExactSpelling = true)]
    private static extern int GdipGetImageHeight(IntPtr image, out uint height);

    [DllImport("gdiplus.dll", ExactSpelling = true)]
    private static extern int GdipBitmapLockBits(
        IntPtr bitmap,
        ref GdiRect rect,
        uint flags,
        int format,
        out BitmapData lockedBitmapData);

    [DllImport("gdiplus.dll", ExactSpelling = true)]
    private static extern int GdipBitmapUnlockBits(IntPtr bitmap, ref BitmapData lockedBitmapData);

    [DllImport("gdiplus.dll", ExactSpelling = true)]
    private static extern int GdipSaveImageToStream(
        IntPtr image,
        IntPtr stream,
        ref Guid clsidEncoder,
        ref EncoderParametersOne encoderParams);

    [DllImport("gdiplus.dll", ExactSpelling = true)]
    private static extern int GdipDisposeImage(IntPtr image);

    [DllImport("ole32.dll", ExactSpelling = true)]
    private static extern int CreateStreamOnHGlobal(IntPtr hGlobal, bool fDeleteOnRelease, out IntPtr ppstm);

    [DllImport("ole32.dll", ExactSpelling = true)]
    private static extern int GetHGlobalFromStream(IntPtr pstm, out IntPtr phglobal);

    [DllImport("kernel32.dll", ExactSpelling = true)]
    private static extern IntPtr GlobalAlloc(uint uFlags, UIntPtr dwBytes);

    [DllImport("kernel32.dll", ExactSpelling = true)]
    private static extern IntPtr GlobalLock(IntPtr hMem);

    [DllImport("kernel32.dll", ExactSpelling = true)]
    private static extern bool GlobalUnlock(IntPtr hMem);

    [DllImport("kernel32.dll", ExactSpelling = true)]
    private static extern UIntPtr GlobalSize(IntPtr hMem);

    public static void EnsureInitialized()
    {
        if (_gdiplusToken != IntPtr.Zero) return;
        lock (InitLock)
        {
            if (_gdiplusToken != IntPtr.Zero) return;
            var input = new GdiplusStartupInput { GdiplusVersion = 1 };
            var status = GdiplusStartup(out _gdiplusToken, ref input, out _);
            if (status != 0)
            {
                throw new InvalidOperationException($"GdiplusStartup failed with status {status}.");
            }
        }
    }

    public static unsafe byte[] EncodeJpeg(PixelBuffer buf, int quality)
    {
        EnsureInitialized();
        var stride = buf.Width * 4;
        fixed (byte* pScan0 = buf.Bgra)
        {
            var st = GdipCreateBitmapFromScan0(
                buf.Width,
                buf.Height,
                stride,
                PixelFormat32bppARGB,
                (IntPtr)pScan0,
                out var bitmap);
            if (st != 0 || bitmap == IntPtr.Zero)
            {
                throw new InvalidOperationException($"GdipCreateBitmapFromScan0 failed with status {st}.");
            }

            try
            {
                var hr = CreateStreamOnHGlobal(IntPtr.Zero, true, out var stream);
                if (hr != 0 || stream == IntPtr.Zero)
                {
                    throw new InvalidOperationException($"CreateStreamOnHGlobal failed with HRESULT 0x{hr:X8}.");
                }

                try
                {
                    long qualityVal = Math.Clamp(quality, 1, 100);
                    var clsid = JpegEncoderClsid;
                    var encParams = new EncoderParametersOne
                    {
                        Count = 1,
                        Parameter = new EncoderParameter
                        {
                            Guid = EncoderQualityGuid,
                            NumberOfValues = 1,
                            Type = 4, // EncoderParameterValueTypeLong
                            Value = (IntPtr)(&qualityVal),
                        },
                    };

                    var saveSt = GdipSaveImageToStream(bitmap, stream, ref clsid, ref encParams);
                    if (saveSt != 0)
                    {
                        throw new InvalidOperationException($"GdipSaveImageToStream failed with status {saveSt}.");
                    }

                    var gHr = GetHGlobalFromStream(stream, out var hGlobal);
                    if (gHr != 0 || hGlobal == IntPtr.Zero)
                    {
                        throw new InvalidOperationException($"GetHGlobalFromStream failed with HRESULT 0x{gHr:X8}.");
                    }

                    var size = (int)GlobalSize(hGlobal);
                    var ptr = GlobalLock(hGlobal);
                    if (ptr == IntPtr.Zero)
                    {
                        throw new InvalidOperationException("GlobalLock failed.");
                    }
                    try
                    {
                        var bytes = new byte[size];
                        Marshal.Copy(ptr, bytes, 0, size);
                        return bytes;
                    }
                    finally
                    {
                        GlobalUnlock(hGlobal);
                    }
                }
                finally
                {
                    Marshal.Release(stream);
                }
            }
            finally
            {
                GdipDisposeImage(bitmap);
            }
        }
    }

    public static unsafe PixelBuffer DecodeImage(ReadOnlySpan<byte> imageBytes)
    {
        EnsureInitialized();
        var hGlobal = GlobalAlloc(0x0002 /* GMEM_MOVEABLE */, (UIntPtr)imageBytes.Length);
        if (hGlobal == IntPtr.Zero)
        {
            throw new OutOfMemoryException("GlobalAlloc failed.");
        }

        var locked = GlobalLock(hGlobal);
        if (locked == IntPtr.Zero)
        {
            throw new InvalidOperationException("GlobalLock failed.");
        }
        try
        {
            imageBytes.CopyTo(new Span<byte>((void*)locked, imageBytes.Length));
        }
        finally
        {
            GlobalUnlock(hGlobal);
        }

        var hr = CreateStreamOnHGlobal(hGlobal, true, out var stream);
        if (hr != 0 || stream == IntPtr.Zero)
        {
            throw new InvalidOperationException($"CreateStreamOnHGlobal failed with HRESULT 0x{hr:X8}.");
        }

        try
        {
            var st = GdipCreateBitmapFromStream(stream, out var bitmap);
            if (st != 0 || bitmap == IntPtr.Zero)
            {
                throw new InvalidOperationException($"GdipCreateBitmapFromStream failed with status {st}.");
            }

            try
            {
                // Match ImageOps.exif_transpose in the reference image pipeline.
                if(GdipGetPropertyItemSize(bitmap,0x112,out var orientationSize)==0&&orientationSize>=Marshal.SizeOf<PropertyItem>()&&orientationSize<16384)
                {
                    var item=Marshal.AllocHGlobal((int)orientationSize);try{if(GdipGetPropertyItem(bitmap,0x112,orientationSize,item)==0){var property=Marshal.PtrToStructure<PropertyItem>(item);if(property.Type==3&&property.Length>=2){var orientation=Marshal.ReadInt16(property.Value);int[] transforms=[0,0,4,2,6,5,1,7,3];if(orientation is >=2 and <=8&&GdipImageRotateFlip(bitmap,transforms[orientation])!=0)throw new IOException("Could not apply image EXIF orientation");}}}finally{Marshal.FreeHGlobal(item);}
                }
                GdipGetImageWidth(bitmap, out var width);
                GdipGetImageHeight(bitmap, out var height);
                var rect = new GdiRect { X = 0, Y = 0, Width = (int)width, Height = (int)height };
                var lockSt = GdipBitmapLockBits(
                    bitmap,
                    ref rect,
                    ImageLockModeRead,
                    PixelFormat32bppARGB,
                    out var bmpData);
                if (lockSt != 0)
                {
                    throw new InvalidOperationException($"GdipBitmapLockBits failed with status {lockSt}.");
                }

                try
                {
                    var w = (int)width;
                    var h = (int)height;
                    var outBgra = new byte[w * h * 4];
                    var rowBytes = w * 4;
                    for (var y = 0; y < h; y++)
                    {
                        var srcRow = bmpData.Scan0 + y * bmpData.Stride;
                        Marshal.Copy(srcRow, outBgra, y * rowBytes, rowBytes);
                    }
                    return new PixelBuffer(w, h, outBgra);
                }
                finally
                {
                    GdipBitmapUnlockBits(bitmap, ref bmpData);
                }
            }
            finally
            {
                GdipDisposeImage(bitmap);
            }
        }
        finally
        {
            Marshal.Release(stream);
        }
    }
}
