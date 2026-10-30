using Windows.Graphics.Imaging;
using Windows.Media.Ocr;
using Windows.Storage.Streams;

namespace NexusAndroid;

public sealed record OcrLine(string Text,int Left,int Top,int Right,int Bottom);
public static class NativeOcr
{
    public static List<OcrLine> Read(byte[] png)
    {
        using var stream=new InMemoryRandomAccessStream();using(var writer=new DataWriter(stream.GetOutputStreamAt(0))){writer.WriteBytes(png);writer.StoreAsync().AsTask().GetAwaiter().GetResult();writer.FlushAsync().AsTask().GetAwaiter().GetResult();}
        stream.Seek(0);var decoder=BitmapDecoder.CreateAsync(stream).AsTask().GetAwaiter().GetResult();using var source=decoder.GetSoftwareBitmapAsync().AsTask().GetAwaiter().GetResult();using var bitmap=SoftwareBitmap.Convert(source,BitmapPixelFormat.Bgra8,BitmapAlphaMode.Premultiplied);var engine=OcrEngine.TryCreateFromUserProfileLanguages()??throw new IOException("No Windows OCR language installed");var result=engine.RecognizeAsync(bitmap).AsTask().GetAwaiter().GetResult();var lines=new List<OcrLine>();
        foreach(var line in result.Lines){if(line.Words.Count==0)continue;var rects=line.Words.Select(w=>w.BoundingRect).ToList();lines.Add(new(line.Text,(int)Math.Floor(rects.Min(r=>r.X)),(int)Math.Floor(rects.Min(r=>r.Y)),(int)Math.Ceiling(rects.Max(r=>r.X+r.Width)),(int)Math.Ceiling(rects.Max(r=>r.Y+r.Height))));}return lines;
    }
}
