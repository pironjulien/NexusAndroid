global using Xunit;
using System.Runtime.CompilerServices;

namespace NexusAndroid.Tests;

/// <summary>Test data of this run: ~\.nexus\build\nexus-android\test-data\&lt;pid&gt; (never OneDrive or AppData), removed
/// at exit. Files the engine must accept as ordinary PC files (transfers, recordings) cannot live under .nexus, which it
/// refuses as agent configuration: those go to a temporary folder of this run, removed at exit as well.</summary>
internal static class TestPaths
{
    public static readonly string Root = Path.Join(
        Environment.GetEnvironmentVariable("USERPROFILE") is { Length: > 0 } home ? home : Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
        ".nexus", "build", "nexus-android", "test-data", Environment.ProcessId.ToString());
    public static readonly string Transfers = Path.Join(Path.GetTempPath(), "nexus-android-tests", Environment.ProcessId.ToString());

    [ModuleInitializer]
    internal static void Offline()
    {
        Environment.SetEnvironmentVariable("NEXUS_ANDROID_KEEP_AWAKE", "0");
        Environment.SetEnvironmentVariable("NEXUS_JOURNAL", "0");
        Environment.SetEnvironmentVariable("NEXUS_ANDROID_ADB_DOWNLOAD", "0");
        Directory.CreateDirectory(Root);
        Directory.CreateDirectory(Transfers);
        AppDomain.CurrentDomain.ProcessExit += (_, _) =>
        {
            foreach (var folder in new[] { Root, Transfers })
                try { Directory.Delete(folder, true); } catch (Exception e) when (e is IOException or UnauthorizedAccessException) { /* best-effort cleanup at process exit */ }
        };
    }
}

/// <summary>A contract suite written as ordered `Test(name, body)` calls sharing fixtures: run once, in declaration
/// order (later contracts build on the fixture state of earlier ones), each contract reported as its own test case.</summary>
internal sealed class ContractSuite(Action<Action<string, Action>> run)
{
    private readonly Lazy<Dictionary<string, Exception?>> results = new(() =>
    {
        var found = new Dictionary<string, Exception?>(StringComparer.Ordinal);
        run((name, body) =>
        {
            try { body(); found[name] = null; }
            catch (Exception error) when (NativeSafety.Recoverable(error)) { found[name] = error; } // suite boundary: every failure is recorded against its own test case
        });
        return found;
    });

    /// <summary>Contract names, collected without running any contract body.</summary>
    public TheoryData<string> Names()
    {
        var names = new TheoryData<string>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        run((name, _) => { if (!seen.Add(name)) throw new InvalidOperationException("Duplicate contract name: " + name); names.Add(name); });
        return names;
    }

    public void Check(string name)
    {
        Assert.True(results.Value.TryGetValue(name, out var error), "contract not run: " + name);
        if (error is not null) Assert.Fail(error.GetType().Name + ": " + error.Message);
    }
}

public sealed class EngineContractTests
{
    private static readonly ContractSuite Suite = new(Engine.EngineContracts.Run);
    public static TheoryData<string> Contracts => Suite.Names();
    [Theory, MemberData(nameof(Contracts))] public void Contract(string name) => Suite.Check(name);
}

public sealed class AdvancedContractTests
{
    private static readonly ContractSuite Suite = new(Advanced.AdvancedContracts.Run);
    public static TheoryData<string> Contracts => Suite.Names();
    [Theory, MemberData(nameof(Contracts))] public void Contract(string name) => Suite.Check(name);
}

public sealed class ConnectionContractTests
{
    private static readonly ContractSuite Suite = new(Connection.ConnectionContracts.Run);
    public static TheoryData<string> Contracts => Suite.Names();
    [Theory, MemberData(nameof(Contracts))] public void Contract(string name) => Suite.Check(name);
}
