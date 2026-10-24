using System.Threading;
using System.Threading.Tasks;

namespace StatusCast.Core;

/// <summary>
/// A dynamically loaded StatusCast plugin. Implementations live in a class
/// library under <c>plugins/</c> and are enabled by name in config.json.
/// </summary>
public interface IStatusPlugin
{
    string PluginName { get; }

    Task<string> FetchStatusAsync(CancellationToken token);
}
