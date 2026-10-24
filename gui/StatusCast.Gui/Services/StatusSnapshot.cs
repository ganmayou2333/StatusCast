using System;
using System.Collections.Generic;

namespace StatusCast_Gui.Services;

public sealed class PluginStatus
{
    public required string Name { get; init; }
    public string? Status { get; init; }
    public string? Error { get; init; }
}

public sealed class StatusSnapshot
{
    public long GeneratedAt { get; init; }
    public required IReadOnlyList<DeviceStatus> Devices { get; init; }
    public required IReadOnlyList<PluginStatus> Plugins { get; init; }
    public required DeviceStatus Primary { get; init; }
}
