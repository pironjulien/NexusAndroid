using System;
using System.IO;

namespace NexusAndroid;

/// <summary>
/// Marks this engine's version folder as in use for the whole life of the process. get.ps1 writes an empty engine.lock
/// in each releases\&lt;version&gt; folder; every engine opens the one beside it without delete sharing and never closes
/// it, so the clean-up of older versions (get.ps1, Test-FolderInUse) sees the folder in use whatever spelling of the path
/// started the process, and Windows refuses to rename it. A single-file engine is otherwise only mapped: Windows renames
/// the folder of such a running program and deletes every file beside it. No file (development build, or a version
/// installed before 0.24.2) or the clean-up testing it at that instant: nothing held.
/// </summary>
internal static class EngineLock
{
    public const string FileName = "engine.lock";
    private static FileStream? held;

    public static void Hold()
    {
        if (held is not null)
            return;
        try
        {
            held = new FileStream(Path.Join(AppContext.BaseDirectory, FileName), FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { /* no lock file, or the clean-up tests it */ }
    }
}
