namespace NexusAndroid.Tests.Connection;

/// <summary>Antigravity's MCP client never initializes again after a 404: only its own daemon-format session id is
/// opened again; Claude and Codex keep the standard 404 and new initialize.</summary>
public sealed class StaleSessionTests
{
    private const string Session = "0123456789abcdef0123456789abcdef";

    [Theory]
    [InlineData("/mcp?agent=antigravity", Session, true)]
    [InlineData("/mcp?x=1&agent=Antigravity", Session, true)]
    [InlineData("/mcp?agent=claude", Session, false)]
    [InlineData("/mcp?agent=codex", Session, false)]
    [InlineData("/mcp", Session, false)]
    [InlineData("/mcp?agent=antigravity", "not-a-daemon-session-id", false)]
    [InlineData("/mcp?agent=antigravity", "0123456789ABCDEF0123456789ABCDEF", false)]
    public void OnlyAntigravitysOwnSessionIdIsResumed(string target, string session, bool resumed)
        => Assert.Equal(resumed, McpServer.ResumesStaleSession(target, session));
}
