using System;
using System.Collections.Generic;

namespace StatusCast.Daemon.Services;

public sealed class StatusSnapshot
{
    public long GeneratedAt { get; init; }
    public required IReadOnlyList<DeviceStatus> Devices { get; init; }
    public required DeviceStatus Primary { get; init; }
}
