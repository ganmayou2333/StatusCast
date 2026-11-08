using System;
using System.Collections.Generic;
using System.Diagnostics;

namespace StatusCast.Daemon.Services;

/// <summary>
/// One point-in-time reading of the daemon's own process counters.
/// </summary>
internal readonly record struct MemorySample(long PrivateBytes, int ThreadCount, int HandleCount);

/// <summary>
/// Warnings that have already been reported and have not re-armed yet. A flag
/// means "the breach is still present at the most recent sample"; it clears as
/// soon as the metric drops back under its threshold (see
/// <see cref="MemoryGuard.Decide"/> for the exact rule).
/// </summary>
internal readonly record struct MemoryThrottle(bool PrivateWarned, bool ThreadsWarned, bool GrowthWarned)
{
    /// <summary>No warning has fired: everything may warn immediately.</summary>
    internal static readonly MemoryThrottle Armed = default;
}

/// <summary>
/// What the guard should write for one sample: warning lines now, plus the
/// throttle state to carry into the next sample.
/// </summary>
internal readonly record struct MemoryDecision(IReadOnlyList<string> Warnings, MemoryThrottle Throttle)
{
    internal static MemoryDecision None(MemoryThrottle throttle)
        => new(Array.Empty<string>(), throttle);
}

/// <summary>
/// Occupancy guardrail for the round loop (spec R5,
/// <c>docs/specs/occupancy-and-stability.md</c>). It is a regression detector,
/// not an optimisation: the measured baseline is ~14 MB private / 26 threads
/// with no cross-round growth.
///
/// The split is deliberate: <see cref="ReadSample"/> talks to the process,
/// everything else (<see cref="Decide"/>, <see cref="FormatSample"/>) is a pure
/// function over a sample history, so the rules are testable without a live
/// daemon.
/// </summary>
internal sealed class MemoryGuard
{
    /// <summary>Log a baseline line every N completed rounds.</summary>
    internal const int SampleEveryRounds = 10;

    /// <summary>Private byte ceiling; crossing it means memory is not flat.</summary>
    internal const long PrivateBytesLimit = 96L * 1024 * 1024;

    /// <summary>Thread ceiling; the baseline is 26, so 64 is roughly 2.5x.</summary>
    internal const int ThreadLimit = 64;

    /// <summary>
    /// Number of samples that must rise strictly one after another before the
    /// trend counts as monotonic growth. Samples are taken every
    /// <see cref="SampleEveryRounds"/> rounds, so this window spans 50 rounds.
    /// </summary>
    internal const int GrowthWindow = 5;

    private const int BytesPerMegabyte = 1024 * 1024;

    // At most GrowthWindow entries: Decide only ever looks at the tail, and a
    // bounded history cannot itself become a leak.
    private readonly List<MemorySample> _history = new(GrowthWindow);
    private MemoryThrottle _throttle = MemoryThrottle.Armed;

    /// <summary>
    /// Called once per completed collection round. Samples and logs on the
    /// every-<see cref="SampleEveryRounds"/>-round cadence and otherwise does
    /// nothing (no process read, no allocation on the common path).
    /// </summary>
    public void OnRoundCompleted(int round)
    {
        if (!ShouldSample(round))
        {
            return;
        }

        var sample = ReadSample();
        if (sample is null)
        {
            // A guardrail must never break collection: if the counters cannot be
            // read this round, skip it and try again on the next cadence tick.
            return;
        }

        _history.Add(sample.Value);
        if (_history.Count > GrowthWindow)
        {
            _history.RemoveAt(0);
        }

        var decision = Decide(_history, _throttle);
        _throttle = decision.Throttle;

        WriteLine(FormatSample(sample.Value), ConsoleColor.DarkGray);
        foreach (var warning in decision.Warnings)
        {
            WriteLine(warning, ConsoleColor.Yellow);
        }
    }

    /// <summary>
    /// True when this completed round's counters should be sampled and logged.
    /// Kept separate from <see cref="OnRoundCompleted"/> so the cadence is a
    /// testable rule rather than an inline modulo.
    /// </summary>
    internal static bool ShouldSample(int round)
        => round > 0 && round % SampleEveryRounds == 0;

    /// <summary>
    /// Reads the current process counters. Returns null instead of throwing so
    /// a transient failure cannot take down the round loop. The
    /// <see cref="Process"/> handle is disposed on every path.
    /// </summary>
    internal static MemorySample? ReadSample()
    {
        try
        {
            using var process = Process.GetCurrentProcess();
            // Counters are cached per Process instance; Refresh() makes the
            // handle we just opened actually report current values.
            process.Refresh();
            return new MemorySample(
                process.PrivateMemorySize64,
                process.Threads.Count,
                process.HandleCount);
        }
        catch (Exception)
        {
            return null;
        }
    }

    /// <summary>
    /// Formats the periodic baseline line. Private bytes are truncated to whole
    /// MB for readability; the threshold checks below use the exact byte count
    /// so a value cannot straddle the limit because of rounding.
    /// </summary>
    internal static string FormatSample(in MemorySample sample)
    {
        return $"[MEM] private={sample.PrivateBytes / BytesPerMegabyte} MB"
            + $" threads={sample.ThreadCount}"
            + $" handles={sample.HandleCount}";
    }

    /// <summary>
    /// Pure decision step: given the sample history (oldest first, most recent
    /// last) and the warnings already reported, returns the warning lines to
    /// write now and the throttle state for the next sample.
    ///
    /// Throttling rule: a warning stays latched while its condition holds and
    /// re-arms only once the condition clears -- for the byte/thread ceilings
    /// that means the metric came back under the threshold, for the trend it
    /// means the window is no longer strictly increasing (a flat or dipping
    /// sample). One sustained breach therefore produces one warning, not one
    /// per sample.
    /// </summary>
    internal static MemoryDecision Decide(IReadOnlyList<MemorySample> history, MemoryThrottle throttle)
    {
        if (history.Count == 0)
        {
            return MemoryDecision.None(throttle);
        }

        var latest = history[history.Count - 1];

        var privateOver = latest.PrivateBytes > PrivateBytesLimit;
        var threadsOver = latest.ThreadCount > ThreadLimit;

        var privateRising = IsStrictlyIncreasing(history, sample => sample.PrivateBytes);
        var threadsRising = IsStrictlyIncreasing(history, sample => sample.ThreadCount);
        var growth = privateRising || threadsRising;

        var warnings = new List<string>(3);

        if (privateOver && !throttle.PrivateWarned)
        {
            warnings.Add(
                $"[MEM] 告警：private {latest.PrivateBytes / BytesPerMegabyte} MB"
                + $"（{latest.PrivateBytes} 字节）超过阈值 {PrivateBytesLimit / BytesPerMegabyte} MB；"
                + "请检查逐轮新增的集合/缓存/订阅是否未释放。");
        }

        if (threadsOver && !throttle.ThreadsWarned)
        {
            warnings.Add(
                $"[MEM] 告警：threads {latest.ThreadCount} 超过阈值 {ThreadLimit}；"
                + "请检查逐轮创建的线程、定时器或未取消的 Task 是否未释放。");
        }

        if (growth && !throttle.GrowthWarned)
        {
            var metrics = privateRising && threadsRising
                ? "private 与 threads"
                : privateRising ? "private" : "threads";
            warnings.Add(
                $"[MEM] 告警：最近 {GrowthWindow} 次采样中 {metrics} 严格递增"
                + $"（private {latest.PrivateBytes / BytesPerMegabyte} MB、"
                + $"threads {latest.ThreadCount}、handles {latest.HandleCount}），"
                + $"达到 {GrowthWindow} 次单调增长判定阈值；疑似逐轮泄漏，"
                + "请检查每轮新建的对象与线程。");
        }

        return new MemoryDecision(
            warnings,
            new MemoryThrottle(privateOver, threadsOver, growth));
    }

    private static bool IsStrictlyIncreasing(
        IReadOnlyList<MemorySample> history,
        Func<MemorySample, long> selector)
    {
        if (history.Count < GrowthWindow)
        {
            return false;
        }

        var start = history.Count - GrowthWindow;
        for (var i = start + 1; i < history.Count; i++)
        {
            if (selector(history[i]) <= selector(history[i - 1]))
            {
                return false;
            }
        }

        return true;
    }

    private static void WriteLine(string line, ConsoleColor color)
    {
        var previous = Console.ForegroundColor;
        try
        {
            Console.ForegroundColor = color;
            Console.WriteLine(line);
        }
        finally
        {
            Console.ForegroundColor = previous;
        }
    }
}
