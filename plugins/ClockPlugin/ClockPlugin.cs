using StatusCast.Core;

namespace ClockPlugin;

public sealed class ClockPlugin : IStatusPlugin
{
    public string PluginName => "Clock";

    public Task<string> FetchStatusAsync(CancellationToken token)
    {
        return Task.FromResult($"🕐 {DateTime.Now:HH:mm:ss}");
    }
}
