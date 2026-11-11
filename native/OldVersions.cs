using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;

namespace NexusAndroid;

/// <summary>
/// Removes the versions an install left behind, from the engine itself: get.ps1 removes older versions only while it
/// installs (Remove-OldReleases) and keeps the one an AI app still runs, which then stayed until the next version; the
/// folders of install.ps1 (native\&lt;source fingerprint&gt;) were never removed at all. Each server start (stdio or
/// daemon) runs this in the background, never delaying or failing the start, under get.ps1's rules: only when
/// ~\.nexus\nexus-android\engine.txt names this very executable (setup writes it, so an older engine or a development
/// build never removes anything); never a folder a NexusAndroid process may use (its executable in it, its engine.lock
/// held, or a NexusAndroid process that cannot be read); an unused folder is renamed aside to retired-* first (refused
/// while a file in it is open), then deleted, and a retired-* folder that did not go at once goes on a later start.
/// A folder created less than an hour ago stays: an install writes engine.txt only after it has put its folder in
/// place. Cost: two folder listings when nothing is left (the usual case); the process scan (about 4 ms) only when
/// there is something to remove, so no throttling is needed.
/// </summary>
internal static partial class OldVersions
{
    /// <summary>Folders holding versions: get.ps1's releases\&lt;version&gt;, install.ps1's native\&lt;fingerprint&gt;.</summary>
    private static readonly string[] Roots = ["releases", "native"];

    /// <summary>A folder younger than this stays: an install in progress, engine.txt not yet pointing to it.</summary>
    internal static readonly TimeSpan Fresh = TimeSpan.FromHours(1);

    /// <summary>Image paths of the running NexusAndroid processes; Unknown: one runs but cannot be read.</summary>
    internal sealed record EngineImages(IReadOnlyList<string> Paths, bool Unknown);

    /// <summary>Server start: the clean-up in the background, its outcome on stderr (stdout carries the protocol).</summary>
    public static void StartInBackground()
    {
        if (Environment.ProcessPath is not { Length: > 0 } running)
            return;
        _ = Task.Run(() =>
        {
            try
            {
                var removed = Prune(Tools.Home, running, ReadEngineImages, DateTime.UtcNow);
                if (removed.Count > 0)
                    Console.Error.WriteLine("nexus-android: older versions removed: " + string.Join(", ", removed));
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or Win32Exception or InvalidOperationException)
            {
                Console.Error.WriteLine("nexus-android: older versions not removed: " + error.Message);
            }
        });
    }

    /// <summary>Removes the unused older versions under <paramref name="home"/>\.nexus\nexus-android when engine.txt
    /// names <paramref name="running"/>. Returns the folders removed (relative to that state folder).</summary>
    internal static List<string> Prune(string home, string running, Func<EngineImages> images, DateTime now)
    {
        var removed = new List<string>();
        var state = Path.Join(home, ".nexus", "nexus-android");
        var pointer = Path.Join(state, "engine.txt");
        if (!File.Exists(pointer))
            return removed;
        var recorded = File.ReadAllText(pointer).Trim();
        if (recorded.Length == 0)
            return removed;
        if (!Path.IsPathRooted(recorded))
            recorded = Path.Join(state, recorded);
        if (!Same(recorded, running))
            return removed;
        var keep = Path.GetDirectoryName(Path.GetFullPath(running))!;
        var retired = Folders(state, "retired-*");
        var old = Roots.SelectMany(root => Folders(Path.Join(state, root), "*"))
            .Where(folder => !Same(folder, keep) && now - Directory.GetCreationTimeUtc(folder) >= Fresh).ToList();
        if (retired.Count == 0 && old.Count == 0)
            return removed;
        var seen = images();
        foreach (var left in retired)
            if (!InUse(left, seen))
                Delete(left);
        foreach (var folder in old)
        {
            if (InUse(folder, seen))
                continue;
            var aside = Path.Join(state, "retired-" + Guid.NewGuid().ToString("N"));
            try
            {
                Directory.Move(folder, aside);
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                continue;  // a file in it is open: in use after all, or a scanner; next start
            }
            removed.Add(Path.GetRelativePath(state, folder));
            Delete(aside);
        }
        // install.ps1's folder left empty: nothing uses the old layout any more.
        var native = Path.Join(state, "native");
        if (removed.Any(r => r.StartsWith("native" + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) && Directory.Exists(native)
            && !Directory.EnumerateFileSystemEntries(native).Any())
        {
            try
            {
                Directory.Delete(native);
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException) { /* an install.ps1 publishing into it meanwhile */ }
        }
        return removed;
    }

    /// <summary>A folder may still be used when a NexusAndroid process cannot be read, when one runs from it, or when its
    /// engine.lock is held (EngineLock.cs: every engine from 0.24.2, whatever spelling of the path started it). Windows
    /// renames the folder of a running single-file program, so a successful rename alone proves nothing.</summary>
    internal static bool InUse(string folder, EngineImages images)
    {
        if (images.Unknown)
            return true;
        var prefix = folder.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (images.Paths.Any(p => p.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)))
            return true;
        var lockFile = Path.Join(folder, EngineLock.FileName);
        if (!File.Exists(lockFile))
            return false;
        try
        {
            new FileStream(lockFile, FileMode.Open, FileAccess.ReadWrite, FileShare.None).Dispose();
            return false;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return true;
        }
    }

    /// <summary>The running NexusAndroid processes' image paths, read by QueryFullProcessImageNameW with
    /// PROCESS_QUERY_LIMITED_INFORMATION (every process of this user, elevated ones included), as get.ps1 reads them.</summary>
    internal static EngineImages ReadEngineImages()
    {
        var paths = new List<string>();
        var unknown = false;
        foreach (var process in Process.GetProcessesByName("NexusAndroid"))
        {
            using (process)
            {
                var path = ImageOf(process.Id);
                if (path is null)
                    unknown = true;
                else if (path.Length > 0)
                    paths.Add(path);
            }
        }
        return new EngineImages(paths, unknown);
    }

    /// <summary>Image path of a process; "" once it has exited; null while it runs but cannot be read.</summary>
    private static unsafe string? ImageOf(int id)
    {
        var handle = OpenProcess(0x1000, false, id);  // PROCESS_QUERY_LIMITED_INFORMATION
        if (handle == IntPtr.Zero)
            return Marshal.GetLastPInvokeError() == 87 ? "" : null;  // 87: no such process
        try
        {
            var buffer = new char[32768];
            var size = (uint)buffer.Length;
            fixed (char* name = buffer)
            {
                if (QueryFullProcessImageName(handle, 0, name, ref size))
                    return new string(buffer, 0, (int)size);
            }
            return GetExitCodeProcess(handle, out var code) && code != 259 ? "" : null;  // 259: STILL_ACTIVE
        }
        finally
        {
            CloseHandle(handle);
        }
    }

    private static bool Same(string a, string b) =>
        string.Equals(Path.GetFullPath(a).TrimEnd(Path.DirectorySeparatorChar), Path.GetFullPath(b).TrimEnd(Path.DirectorySeparatorChar), StringComparison.OrdinalIgnoreCase);

    private static List<string> Folders(string parent, string pattern)
    {
        try
        {
            return Directory.Exists(parent) ? [.. Directory.EnumerateDirectories(parent, pattern)] : [];
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return [];
        }
    }

    private static void Delete(string folder)
    {
        try
        {
            Directory.Delete(folder, true);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { /* a file held open: a later start */ }
    }

    [LibraryImport("kernel32.dll", SetLastError = true)]
    private static partial IntPtr OpenProcess(uint access, [MarshalAs(UnmanagedType.Bool)] bool inherit, int id);

    [LibraryImport("kernel32.dll", EntryPoint = "QueryFullProcessImageNameW", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static unsafe partial bool QueryFullProcessImageName(IntPtr process, uint flags, char* name, ref uint size);

    [LibraryImport("kernel32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool GetExitCodeProcess(IntPtr process, out uint code);

    [LibraryImport("kernel32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool CloseHandle(IntPtr handle);
}
