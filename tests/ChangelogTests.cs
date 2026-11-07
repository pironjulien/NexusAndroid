using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;

namespace NexusAndroid.Tests.Registration;

/// <summary>CHANGELOG.md follows Keep a Changelog 1.1.0: an [Unreleased] section, and every version heading links to
/// its changes on GitHub (compare from the version before, or the release that first shipped it), so a release
/// cannot be published without its link.</summary>
public sealed partial class ChangelogTests
{
    private const string Repository = "https://github.com/pironjulien/NexusAndroid/";

    private static string Changelog([CallerFilePath] string source = "") =>
        File.ReadAllText(Path.GetFullPath(Path.Join(Path.GetDirectoryName(source)!, "..", "CHANGELOG.md")));

    [GeneratedRegex(@"^## \[(Unreleased|\d+\.\d+\.\d+)\]", RegexOptions.Multiline)]
    private static partial Regex Heading();

    [GeneratedRegex(@"^\[([^\]]+)\]: (\S+)$", RegexOptions.Multiline)]
    private static partial Regex LinkDefinition();

    [Fact]
    public void EveryVersionLinksToItsChanges()
    {
        string text = Changelog();
        var headings = Heading().Matches(text).Select(m => m.Groups[1].Value).ToList();
        var links = LinkDefinition().Matches(text).ToDictionary(m => m.Groups[1].Value, m => m.Groups[2].Value);
        Assert.Equal("Unreleased", headings[0]);
        string newest = headings[1];
        Assert.Equal($"{Repository}compare/v{newest}...HEAD", links["Unreleased"]);
        Assert.Matches($@"(\.\.\.|/tag/)v{Regex.Escape(newest)}$", links[newest]);
        Assert.Equal<string>([], headings.Where(h => !links.ContainsKey(h)));  // headings without a link
        Assert.Equal<string>([], links.Keys.Where(k => !headings.Contains(k) && k.Contains('.', StringComparison.Ordinal)));  // links without a heading
        Assert.All(headings, h => Assert.Matches($@"^{Regex.Escape(Repository)}(compare/v\d+\.\d+\.\d+\.\.\.(v\d+\.\d+\.\d+|HEAD)|releases/tag/v\d+\.\d+\.\d+)$", links[h]));
    }
}
