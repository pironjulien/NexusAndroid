using System.Text;

namespace NexusAndroid.Tests.Images;

/// <summary>Lossless WebP (VP8L) decoder for the tests, written from Google's bitstream specification
/// (developers.google.com/speed/webp/docs/webp_lossless_bitstream_specification), so CI servers without Windows' WebP
/// codec still read back every image the engine sends. Covers what a VP8L encoder may write except the colour cache,
/// meta prefix codes, colour and colour-indexing transforms, short-distance codes and predictor modes 5-13, which
/// NexusAndroid's encoder never writes: those throw instead of decoding wrongly.</summary>
internal static class WebPDecoder
{
    private sealed class Bits(byte[] data, int start)
    {
        private int position = start * 8;
        public int Read(int count)
        {
            var value = 0;
            for (var i = 0; i < count; i++, position++)
            {
                if (position >> 3 >= data.Length) throw new InvalidDataException("Truncated VP8L stream");
                value |= ((data[position >> 3] >> (position & 7)) & 1) << i;
            }
            return value;
        }
    }

    private sealed class Code
    {
        private readonly Dictionary<(int Length, int Code), int> symbols = [];
        private readonly int single = -1;
        public Code(int[] lengths)
        {
            var used = Enumerable.Range(0, lengths.Length).Where(i => lengths[i] > 0).ToList();
            if (used.Count == 1) { single = used[0]; return; }
            if (used.Count == 0) throw new InvalidDataException("Empty prefix code");
            var count = new int[16];
            foreach (var l in lengths) if (l > 0) count[l]++;
            var next = new int[16];
            var code = 0;
            for (var bits = 1; bits <= 15; bits++) { code = (code + count[bits - 1]) << 1; next[bits] = code; }
            for (var s = 0; s < lengths.Length; s++) if (lengths[s] > 0) symbols[(lengths[s], next[lengths[s]]++)] = s;
        }
        public int Read(Bits bits)
        {
            if (single >= 0) return single;
            var code = 0;
            for (var length = 1; length <= 15; length++)
            {
                code = (code << 1) | bits.Read(1);
                if (symbols.TryGetValue((length, code), out var symbol)) return symbol;
            }
            throw new InvalidDataException("Invalid prefix code");
        }
    }

    private static readonly int[] CodeLengthOrder = [17, 18, 0, 1, 2, 3, 4, 5, 16, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15];

    private static Code ReadCode(Bits bits, int alphabet)
    {
        var lengths = new int[alphabet];
        if (bits.Read(1) == 1)
        {
            var count = bits.Read(1) + 1;
            var first = bits.Read(bits.Read(1) == 1 ? 8 : 1);
            lengths[first] = 1;
            if (count == 2) lengths[bits.Read(8)] = 1;
            return new Code(lengths);
        }
        var clLengths = new int[19];
        var numCodes = bits.Read(4) + 4;
        for (var i = 0; i < numCodes; i++) clLengths[CodeLengthOrder[i]] = bits.Read(3);
        var clCode = new Code(clLengths);
        var maxSymbol = alphabet;
        if (bits.Read(1) == 1) maxSymbol = 2 + bits.Read(2 + 2 * bits.Read(3));
        var symbol = 0;
        var previous = 8;
        while (symbol < alphabet && maxSymbol-- > 0)
        {
            var length = clCode.Read(bits);
            if (length < 16) { lengths[symbol++] = length; if (length != 0) previous = length; continue; }
            var (repeat, value) = length switch { 16 => (3 + bits.Read(2), previous), 17 => (3 + bits.Read(3), 0), _ => (11 + bits.Read(7), 0) };
            for (var r = 0; r < repeat; r++) lengths[symbol++] = value;
        }
        return new Code(lengths);
    }

    private static int PrefixValue(Bits bits, int code)
    {
        if (code < 4) return code + 1;
        var extra = (code - 2) >> 1;
        return ((2 + (code & 1)) << extra) + bits.Read(extra) + 1;
    }

    private static uint[] ReadImage(Bits bits, int width, int height, bool main)
    {
        if (bits.Read(1) == 1) throw new NotSupportedException("Colour cache");
        if (main && bits.Read(1) == 1) throw new NotSupportedException("Meta prefix codes");
        var green = ReadCode(bits, 256 + 24);
        var red = ReadCode(bits, 256);
        var blue = ReadCode(bits, 256);
        var alpha = ReadCode(bits, 256);
        var distance = ReadCode(bits, 40);
        var pixels = new uint[width * height];
        for (var i = 0; i < pixels.Length;)
        {
            var g = green.Read(bits);
            if (g < 256)
            {
                var r = red.Read(bits);
                var b = blue.Read(bits);
                var a = alpha.Read(bits);
                pixels[i++] = (uint)(a << 24 | r << 16 | g << 8 | b);
                continue;
            }
            var length = PrefixValue(bits, g - 256);
            var plane = PrefixValue(bits, distance.Read(bits));
            if (plane <= 120) throw new NotSupportedException("Short-distance code");
            var from = i - (plane - 120);
            if (from < 0 || i + length > pixels.Length) throw new InvalidDataException("Copy outside the image");
            for (var k = 0; k < length; k++) pixels[i + k] = pixels[from + k];
            i += length;
        }
        return pixels;
    }

    private static uint Add(uint a, uint b) =>
        (((a >> 24) + (b >> 24)) & 0xFF) << 24 | (((a >> 16) + (b >> 16)) & 0xFF) << 16 |
        (((a >> 8) + (b >> 8)) & 0xFF) << 8 | ((a + b) & 0xFF);

    /// <summary>A RIFF/WEBP/VP8L file to BGRA bytes.</summary>
    public static byte[] Decode(byte[] file, out int width, out int height)
    {
        if (Encoding.ASCII.GetString(file, 0, 4) != "RIFF" || Encoding.ASCII.GetString(file, 8, 4) != "WEBP" || Encoding.ASCII.GetString(file, 12, 4) != "VP8L")
            throw new InvalidDataException("Not a lossless WebP file");
        var bits = new Bits(file, 20);
        if (bits.Read(8) != 0x2F) throw new InvalidDataException("Bad VP8L signature");
        width = bits.Read(14) + 1;
        height = bits.Read(14) + 1;
        bits.Read(1);
        if (bits.Read(3) != 0) throw new InvalidDataException("Unknown VP8L version");
        var transforms = new List<(int Type, int BlockBits, uint[]? Modes)>();
        while (bits.Read(1) == 1)
        {
            var type = bits.Read(2);
            if (type == 2) { transforms.Add((2, 0, null)); continue; }
            if (type != 0) throw new NotSupportedException("Colour or colour-indexing transform");
            var blockBits = bits.Read(3) + 2;
            var bw = (width + (1 << blockBits) - 1) >> blockBits;
            var bh = (height + (1 << blockBits) - 1) >> blockBits;
            transforms.Add((0, blockBits, ReadImage(bits, bw, bh, false)));
        }
        var argb = ReadImage(bits, width, height, true);
        for (var t = transforms.Count - 1; t >= 0; t--)
        {
            var (type, blockBits, modes) = transforms[t];
            if (type == 2)
            {
                for (var i = 0; i < argb.Length; i++)
                {
                    var g = (argb[i] >> 8) & 0xFF;
                    argb[i] = (argb[i] & 0xFF00FF00u) | ((((argb[i] >> 16) + g) & 0xFF) << 16) | ((argb[i] + g) & 0xFF);
                }
                continue;
            }
            var blocksWide = (width + (1 << blockBits) - 1) >> blockBits;
            for (var y = 0; y < height; y++)
                for (var x = 0; x < width; x++)
                {
                    var i = y * width + x;
                    uint prediction;
                    if (x == 0 && y == 0) prediction = 0xFF000000u;
                    else if (y == 0) prediction = argb[i - 1];
                    else if (x == 0) prediction = argb[i - width];
                    else
                    {
                        var mode = (int)((modes![(y >> blockBits) * blocksWide + (x >> blockBits)] >> 8) & 0xF);
                        prediction = mode switch
                        {
                            0 => 0xFF000000u,
                            1 => argb[i - 1],
                            2 => argb[i - width],
                            // Top-right; on the rightmost column this index lands on the leftmost pixel of the current
                            // row, which is what the specification asks for.
                            3 => argb[i - width + 1],
                            4 => argb[i - width - 1],
                            _ => throw new NotSupportedException("Predictor mode " + mode),
                        };
                    }
                    argb[i] = Add(argb[i], prediction);
                }
        }
        var bgra = new byte[width * height * 4];
        for (var i = 0; i < argb.Length; i++)
        {
            bgra[i * 4] = (byte)argb[i];
            bgra[i * 4 + 1] = (byte)(argb[i] >> 8);
            bgra[i * 4 + 2] = (byte)(argb[i] >> 16);
            bgra[i * 4 + 3] = (byte)(argb[i] >> 24);
        }
        return bgra;
    }
}
