using System;
using StatusCast.Daemon.Services;
using Xunit;

namespace StatusCast.Daemon.Tests;

/// <summary>
/// Spec R5 (docs/specs/occupancy-and-stability.md): the occupancy guardrail.
/// The decision rules are pure, so they are exercised against synthetic sample
/// histories instead of a live process.
/// </summary>
public sealed class MemoryGuardTests
{
    private static MemorySample Sample(long privateMegabytes, int threads = 26, int handles = 512)
        => new(privateMegabytes * 1024 * 1024, threads, handles);

    [Fact]
    public void SamplesInsideTheEnvelopeProduceNoWarning()
    {
        // The measured baseline: ~14 MB private, 26 threads, no growth.
        var decision = MemoryGuard.Decide(
            new[] { Sample(14), Sample(14), Sample(13), Sample(14), Sample(14) },
            MemoryThrottle.Armed);

        Assert.Empty(decision.Warnings);
        Assert.Equal(MemoryThrottle.Armed, decision.Throttle);
    }

    [Fact]
    public void PrivateBytesWarningFiresOnceAndRearmsAfterDroppingBack()
    {
        var throttle = MemoryThrottle.Armed;

        var crossing = MemoryGuard.Decide(new[] { Sample(97) }, throttle);
        var warning = Assert.Single(crossing.Warnings);
        Assert.Contains("private", warning);
        Assert.Contains("97", warning);
        Assert.Contains("96", warning);
        throttle = crossing.Throttle;

        // Still above the ceiling: the warning stays latched instead of repeating.
        var sustained = MemoryGuard.Decide(new[] { Sample(98) }, throttle);
        Assert.Empty(sustained.Warnings);
        throttle = sustained.Throttle;

        // Back under the ceiling: nothing to say, but the guard re-arms.
        var backUnder = MemoryGuard.Decide(new[] { Sample(20) }, throttle);
        Assert.Empty(backUnder.Warnings);
        throttle = backUnder.Throttle;

        var rearmed = MemoryGuard.Decide(new[] { Sample(97) }, throttle);
        Assert.Single(rearmed.Warnings);
    }

    [Fact]
    public void ThreadCountWarningFiresOnceAndRearmsAfterDroppingBack()
    {
        var throttle = MemoryThrottle.Armed;

        var crossing = MemoryGuard.Decide(new[] { Sample(14, threads: 65) }, throttle);
        var warning = Assert.Single(crossing.Warnings);
        Assert.Contains("threads 65", warning);
        Assert.Contains("64", warning);
        throttle = crossing.Throttle;

        var sustained = MemoryGuard.Decide(new[] { Sample(14, threads: 70) }, throttle);
        Assert.Empty(sustained.Warnings);
        throttle = sustained.Throttle;

        var backUnder = MemoryGuard.Decide(new[] { Sample(14, threads: 30) }, throttle);
        Assert.Empty(backUnder.Warnings);
        throttle = backUnder.Throttle;

        var rearmed = MemoryGuard.Decide(new[] { Sample(14, threads: 65) }, throttle);
        Assert.Single(rearmed.Warnings);
    }

    [Fact]
    public void ExactlyAtTheLimitIsNotAWarningButOneByteOverIs()
    {
        // The thresholds are strict ">" comparisons on the raw counters, not on
        // the whole-MB number shown in the log line.
        Assert.Empty(MemoryGuard.Decide(
            new[] { new MemorySample(MemoryGuard.PrivateBytesLimit, MemoryGuard.ThreadLimit, 512) },
            MemoryThrottle.Armed).Warnings);

        Assert.Single(MemoryGuard.Decide(
            new[] { new MemorySample(MemoryGuard.PrivateBytesLimit + 1, 26, 512) },
            MemoryThrottle.Armed).Warnings);

        Assert.Single(MemoryGuard.Decide(
            new[] { new MemorySample(14L * 1024 * 1024, MemoryGuard.ThreadLimit + 1, 512) },
            MemoryThrottle.Armed).Warnings);
    }

    [Fact]
    public void FiveStrictlyIncreasingSamplesWarnAboutPrivateBytes()
    {
        var decision = MemoryGuard.Decide(
            new[] { Sample(14), Sample(15), Sample(16), Sample(17), Sample(18) },
            MemoryThrottle.Armed);

        var warning = Assert.Single(decision.Warnings);
        Assert.Contains("private", warning);
        Assert.Contains("18", warning);
    }

    [Fact]
    public void FiveStrictlyIncreasingThreadSamplesWarnAboutThreads()
    {
        var decision = MemoryGuard.Decide(
            new[]
            {
                Sample(14, threads: 20),
                Sample(14, threads: 21),
                Sample(14, threads: 22),
                Sample(14, threads: 23),
                Sample(14, threads: 24),
            },
            MemoryThrottle.Armed);

        var warning = Assert.Single(decision.Warnings);
        Assert.Contains("threads", warning);
    }

    [Theory]
    [InlineData(14, 14, 14, 14, 14)] // flat
    [InlineData(14, 15, 16, 17, 17)] // rises, then flat
    [InlineData(14, 15, 16, 17, 16)] // rises, then dips
    [InlineData(15, 14, 15, 16, 17)] // dip then rise: window is not monotonic
    public void FlatOrDippingWindowIsNotGrowth(int a, int b, int c, int d, int e)
    {
        var decision = MemoryGuard.Decide(
            new[] { Sample(a), Sample(b), Sample(c), Sample(d), Sample(e) },
            MemoryThrottle.Armed);

        Assert.Empty(decision.Warnings);
    }

    [Fact]
    public void GrowthWarningStaysLatchedUntilTheTrendBreaks()
    {
        var first = MemoryGuard.Decide(
            new[] { Sample(14), Sample(15), Sample(16), Sample(17), Sample(18) },
            MemoryThrottle.Armed);
        Assert.Single(first.Warnings);
        var throttle = first.Throttle;

        // The window slid one sample further up: still rising, no repeat.
        var stillRising = MemoryGuard.Decide(
            new[] { Sample(15), Sample(16), Sample(17), Sample(18), Sample(19) },
            throttle);
        Assert.Empty(stillRising.Warnings);
        throttle = stillRising.Throttle;

        // The trend breaks, which re-arms the guard.
        var broken = MemoryGuard.Decide(
            new[] { Sample(16), Sample(17), Sample(18), Sample(19), Sample(19) },
            throttle);
        Assert.Empty(broken.Warnings);
        throttle = broken.Throttle;

        var again = MemoryGuard.Decide(
            new[] { Sample(20), Sample(21), Sample(22), Sample(23), Sample(24) },
            throttle);
        Assert.Single(again.Warnings);
    }

    [Theory]
    [InlineData(1, false)]
    [InlineData(9, false)]
    [InlineData(10, true)]
    [InlineData(11, false)]
    [InlineData(20, true)]
    [InlineData(0, false)]
    [InlineData(-10, false)]
    public void BaselineLineIsSampledEveryTenRounds(int round, bool expected)
    {
        Assert.Equal(expected, MemoryGuard.ShouldSample(round));
    }

    [Fact]
    public void SampleLineHasTheDocumentedFormat()
    {
        var line = MemoryGuard.FormatSample(new MemorySample(14L * 1024 * 1024, 26, 512));

        Assert.Equal("[MEM] private=14 MB threads=26 handles=512", line);
    }

    [Fact]
    public void LiveSampleIsReadableAndFormatted()
    {
        // Proves the process-reading half composes with the formatter; the
        // values themselves are whatever this test process happens to use.
        var sample = MemoryGuard.ReadSample();
        Assert.True(sample.HasValue);

        var value = sample.Value;
        Assert.True(value.PrivateBytes > 0);
        Assert.True(value.ThreadCount > 0);
        Assert.Matches(
            @"^\[MEM\] private=\d+ MB threads=\d+ handles=\d+$",
            MemoryGuard.FormatSample(value));
    }
}
