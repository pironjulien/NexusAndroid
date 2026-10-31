using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using System.Xml.Linq;

namespace NexusAndroid;

public sealed record UiElement(
    string Id,
    string Text,
    string ContentDesc,
    string ClassName,
    string PackageName,
    int Left,
    int Top,
    int Right,
    int Bottom,
    int CenterX,
    int CenterY,
    bool Clickable,
    bool Scrollable,
    bool Enabled,
    bool Focused,
    bool IsPassword,
    bool Editable = false,
    bool Checked = false,
    bool LongClickable = false,
    string RawText = "",
    string ResourceId = "",
    string RawDescription = "",
    bool IsOcr = false,
    string ZPath = "",
    bool Selected = false,
    bool ShowingHint = false
);

public static class ScreenParser
{
    private static readonly Regex BoundsRegex = new(@"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", RegexOptions.Compiled);
    private static readonly Regex SensitiveKeywordRegex = new(@"\b(password|mot de passe|secret|pin|cvv|otp|code de validation)\b", RegexOptions.IgnoreCase | RegexOptions.Compiled);

    public static List<UiElement> ParseXml(string xmlContent)
    {
        var elements = new List<UiElement>();
        if (string.IsNullOrWhiteSpace(xmlContent)) return elements;

        try
        {
            var doc = XDocument.Parse(xmlContent);
            int index = 0;
            foreach (var node in doc.Descendants("node"))
            {
                string text = node.Attribute("text")?.Value ?? "";
                string desc = node.Attribute("content-desc")?.Value ?? "";
                string resId = node.Attribute("resource-id")?.Value ?? "";
                string cls = node.Attribute("class")?.Value ?? "";
                string pkg = node.Attribute("package")?.Value ?? "";
                string bounds = node.Attribute("bounds")?.Value ?? "";
                bool clickable = bool.TryParse(node.Attribute("clickable")?.Value, out bool c) && c;
                bool scrollable = bool.TryParse(node.Attribute("scrollable")?.Value, out bool s) && s;
                bool enabled = !bool.TryParse(node.Attribute("enabled")?.Value, out bool e) || e;
                bool focused = bool.TryParse(node.Attribute("focused")?.Value, out bool f) && f;
                bool isPassword = bool.TryParse(node.Attribute("password")?.Value, out bool p) && p;
                bool editable=node.Attribute("editable")?.Value=="true"||cls.EndsWith("EditText")||cls.EndsWith("AutoCompleteTextView");

                var showingHint=node.Attribute("showing-hint")?.Value=="true";var rawText = showingHint?"":text;var rawDescription=desc;
                if (isPassword || SensitiveKeywordRegex.IsMatch(resId))
                {
                    if (!string.IsNullOrEmpty(text)) text = "[MASKED]";
                    if (!string.IsNullOrEmpty(desc)) desc = "[MASKED]";
                }

                var (left, top, right, bottom) = ParseBounds(bounds);
                int cx = (left + right) / 2;
                int cy = (top + bottom) / 2;

                // Keep actionable or informative elements
                if (clickable || scrollable || editable || !string.IsNullOrWhiteSpace(text) || !string.IsNullOrWhiteSpace(desc))
                {
                    elements.Add(new UiElement(
                        $"e{++index}",
                        text,
                        desc,
                        cls,
                        pkg,
                        left,
                        top,
                        right,
                        bottom,
                        cx,
                        cy,
                        clickable,
                        scrollable,
                        enabled,
                        focused,
                        isPassword,
                        editable,
                        node.Attribute("checked")?.Value == "true",
                        node.Attribute("long-clickable")?.Value == "true",
                        rawText,
                        resId,
                        rawDescription,
                        false,
                        string.Join('/',node.AncestorsAndSelf().Reverse().Where(e=>e.Name=="node").Select(e=>Math.Max(0,int.TryParse(e.Attribute("drawing-order")?.Value,out var order)?order:0).ToString("D8")+":"+e.ElementsBeforeSelf("node").Count().ToString("D8"))),
                        node.Attribute("selected")?.Value=="true",
                        showingHint
                    ));
                }
            }
        }
        catch (Exception e) when (e is System.Xml.XmlException or FormatException or OverflowException)
        {
            // Fallback: return empty on unparseable xml or bounds
        }

        var context=NativeSafety.HasCodeKeyword(string.Join(' ',elements.Select(e=>e.RawText+" "+e.RawDescription)));
        for(var i=0;i<elements.Count;i++)elements[i]=elements[i] with {Text=NativeSafety.MaskCodes(NativeSafety.Mask(elements[i].Text),context),ContentDesc=NativeSafety.MaskCodes(NativeSafety.Mask(elements[i].ContentDesc),context)};
        return elements;
    }

    public static (int Left, int Top, int Right, int Bottom) ParseBounds(string boundsStr)
    {
        var m = BoundsRegex.Match(boundsStr ?? "");
        if (m.Success)
        {
            int l = int.Parse(m.Groups[1].Value);
            int t = int.Parse(m.Groups[2].Value);
            int r = int.Parse(m.Groups[3].Value);
            int b = int.Parse(m.Groups[4].Value);
            return (l, t, r, b);
        }
        return (0, 0, 0, 0);
    }

    public static string FormatCompactTree(IEnumerable<UiElement> elements,double scaleX=1,double scaleY=1)
    {
        var sb = new StringBuilder();
        foreach (var el in elements)
        {
            var flags = new List<string>();
            if (el.Clickable) flags.Add("click");
            if (el.Scrollable) flags.Add("scroll");
            if (el.Focused) flags.Add("focus");
            if (el.IsPassword) flags.Add("pwd");

            string flagStr = flags.Count > 0 ? $" [{string.Join(",", flags)}]" : "";
            string label = !string.IsNullOrEmpty(el.Text) ? $"\"{el.Text}\"" : (!string.IsNullOrEmpty(el.ContentDesc) ? $"desc:\"{el.ContentDesc}\"" : el.ClassName);

            label = NativeSafety.Mask(label);
            sb.AppendLine($"[{el.Id.TrimStart('e')}] {el.ClassName.Split('.').Last()} {label}{flagStr} @{(int)Math.Round(el.CenterX*scaleX)},{(int)Math.Round(el.CenterY*scaleY)}" + (el.ResourceId.Length > 0 ? $" #{el.ResourceId}" : ""));
        }
        return sb.ToString();
    }
}
