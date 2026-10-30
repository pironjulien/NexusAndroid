using System.Text.Json.Nodes;

namespace NexusAndroid.Tests;

/// <summary>xUnit's own entry point, plus one fixture mode: `--argv-fixture …` prints its arguments as JSON, so the
/// adb process test can prove each argument reaches a real child process unchanged (quotes, spaces, Unicode).</summary>
internal static class Entry
{
    public static int Main(string[] args)
    {
        if (args.Contains("--argv-fixture", StringComparer.Ordinal))
        {
            Console.OutputEncoding = new System.Text.UTF8Encoding(false);
            Console.WriteLine(new JsonArray(args.Select(a => (JsonNode?)JsonValue.Create(a)).ToArray()).ToJsonString());
            return 0;
        }
        if (args.Any(arg => arg is "--server" or "--internal-msbuild-node"))
            return Xunit.MicrosoftTestingPlatform.TestPlatformTestFramework.RunAsync(args, SelfRegisteredExtensions.AddSelfRegisteredExtensions).GetAwaiter().GetResult();
        return Xunit.Runner.InProc.SystemConsole.ConsoleRunner.Run(args).GetAwaiter().GetResult();
    }
}
