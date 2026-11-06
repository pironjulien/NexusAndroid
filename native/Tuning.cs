namespace NexusAndroid;

/// <summary>Timings and gesture shapes chosen on the Fold bench (benchmarks/Banc): one place, so the bench can compare
/// each value with and without on a real phone. The product never changes them at run time.</summary>
internal static class Tuning
{
    /// <summary>After a gesture: a screen that has not changed by then is returned as is (effect unverified).</summary>
    internal static int SettleNoChangeMs = 600;
    /// <summary>After a gesture: a screen that keeps changing (video, clock) is returned after this.</summary>
    internal static int SettleMaxMs = 1500;
    /// <summary>Pause between two reads while waiting for the screen to settle.</summary>
    internal static int SettlePollMs = 60;
    /// <summary>A changed screen is returned at the first change (false) or once two reads agree (true). Bench 2026-11-06:
    /// first change 650 ms vs 1,030 ms per tap, same 8/8 taps and 4/4 menus really done and reported ✓.</summary>
    internal static bool SettleTwoReads = false;
    /// <summary>scroll stops where the finger stops (motion events with a hold) instead of `input swipe`.</summary>
    internal static bool SteadyScroll = true;
    /// <summary>Bench 2026-11-06: 2 moves and a 60 ms hold scroll exactly as far as 4 or 8 moves (633 px for 657 px of
    /// travel, spread 0) and 11 % faster; `input swipe` flung on to 1,172 px.</summary>
    internal static int SteadyMoves = 2;
    internal static int SteadyHoldMs = 60;
    /// <summary>Largest image `look` sends: the shared rule of every Nexus product (NexusShared/images.md, 2026-10-27),
    /// lossless while it fits 500 KB, never more. Not a bench knob: the bench measures bytes and time, not how well the
    /// model reads small text, which is what the rule protects (98.8 % of characters read at full size, lossless).</summary>
    internal static int LookImageMaxBytes = 500 * 1024;
}