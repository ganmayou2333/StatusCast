using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace StatusCast.Daemon.Services;

public sealed class StatusCoordinator
{
    internal static readonly JsonSerializerOptions SnapshotJsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = true,
    };

    private readonly StatusService _statusService = new();
    private readonly SemaphoreSlim _writeLock = new(1, 1);

    // "_writeLock" only serializes file writes; this separate gate protects the
    // last-good device cache. ReadAsync is currently driven serially by
    // Program.RefreshAsync, but the lock keeps the cache safe if it ever gains
    // another caller.
    private readonly object _lastGoodLock = new();
    private IReadOnlyList<DeviceStatus>? _lastGoodDevices;

    public async Task<StatusSnapshot> ReadAsync(CancellationToken cancellationToken = default)
    {
        var root = ProjectLocator.FindRoot();
        if (root is null)
        {
            return await BuildUnknownSnapshotAsync("找不到项目根目录", cancellationToken);
        }

        var config = StatusCastConfig.Load(root);
        var result = await _statusService.ReadAsync(cancellationToken);
        var devices = result.Devices
            .Select(device => ApplyAlias(device, config))
            .ToList();

        if (result.IsFallback)
        {
            // The Python pipeline failed as a whole (timeout, crash, bad JSON):
            // keep the last successfully collected devices so status.json does
            // not collapse to two bare error rows. A disconnected phone is a
            // normal result and never reaches this branch.
            if (TryGetLastGoodDevices(out var cached))
            {
                devices = cached.Select(ToStaleDevice).ToList();
            }
            else if (devices.Count == 0)
            {
                return await BuildUnknownSnapshotAsync("Python 未返回设备", cancellationToken);
            }
        }
        else
        {
            StoreLastGoodDevices(devices);
        }

        var primary = devices.FirstOrDefault(IsAvailable) ?? devices[0];
        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();

        var snapshot = new StatusSnapshot
        {
            GeneratedAt = now,
            Devices = devices,
            Primary = primary,
        };

        await WriteSnapshotAsync(root, snapshot, cancellationToken);
        return snapshot;
    }

    private async Task<StatusSnapshot> BuildUnknownSnapshotAsync(
        string error,
        CancellationToken cancellationToken)
    {
        await Task.CompletedTask;
        var iphone = new DeviceStatus
        {
            Platform = "ios",
            DeviceId = "iphone",
            Error = error,
        };
        var android = new DeviceStatus
        {
            Platform = "android",
            DeviceId = "android",
            Error = error,
        };

        return new StatusSnapshot
        {
            GeneratedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
            Devices = new[] { iphone, android },
            Primary = iphone,
        };
    }

    private static DeviceStatus ApplyAlias(DeviceStatus status, StatusCastConfig config)
    {
        if (status.DeviceModel is not null
            && config.Aliases.TryGetValue(status.DeviceModel, out var alias)
            && !string.IsNullOrWhiteSpace(alias))
        {
            return status.WithAlias(alias);
        }

        return status;
    }

    /// <summary>
    /// Reuses a cached device for a failed refresh: keeps identity/model and
    /// the additive console/node blocks, but wipes every live metric.
    /// </summary>
    internal static DeviceStatus ToStaleDevice(DeviceStatus device) => new()
    {
        Platform = device.Platform,
        DeviceId = device.DeviceId,
        MediaState = "unknown",
        DeviceModel = device.DeviceModel,
        Alias = device.Alias,
        Error = "读取超时",
        Console = device.Console,
        Node = device.Node,
        Ble = device.Ble,
    };

    private bool TryGetLastGoodDevices(out IReadOnlyList<DeviceStatus> devices)
    {
        lock (_lastGoodLock)
        {
            devices = _lastGoodDevices ?? Array.Empty<DeviceStatus>();
            return _lastGoodDevices is not null;
        }
    }

    private void StoreLastGoodDevices(IReadOnlyList<DeviceStatus> devices)
    {
        lock (_lastGoodLock)
        {
            _lastGoodDevices = devices;
        }
    }

    internal static bool IsAvailable(DeviceStatus status)
    {
        if (status.Error is not null)
        {
            return false;
        }

        // An offline console host must never be chosen as the primary device.
        if (string.Equals(status.Platform, "console", StringComparison.OrdinalIgnoreCase)
            && status.Console?.Online == false)
        {
            return false;
        }

        return status.BatteryLevel is not null || status.DeviceModel is not null;
    }

    /// <summary>
    /// Builds one status.json device row. Standard keys keep their existing
    /// null-writing behaviour; the additive console/node/transport/ble objects
    /// are attached only when present, so a device without them has no such key
    /// at all (rather than a JSON null).
    /// </summary>
    internal static Dictionary<string, object?> BuildDevicePayload(
        DeviceStatus device,
        long updatedAt)
    {
        var payload = new Dictionary<string, object?>
        {
            ["id"] = device.DeviceId,
            ["platform"] = device.Platform,
            ["model"] = device.DeviceModel,
            ["displayName"] = device.DisplayName,
            ["batteryLevel"] = device.BatteryLevel,
            ["isCharging"] = device.IsCharging,
            ["mediaTitle"] = device.MediaTitle,
            ["mediaArtist"] = device.MediaArtist,
            ["mediaState"] = device.MediaState,
            ["foregroundApp"] = device.ForegroundApp,
            ["foregroundAppName"] = device.ForegroundAppName,
            ["updatedAt"] = updatedAt,
            ["error"] = device.Error,
        };

        if (device.Console is not null)
        {
            payload["console"] = device.Console;
        }

        if (device.Node is not null)
        {
            payload["node"] = device.Node;
        }

        if (device.Transport is not null)
        {
            payload["transport"] = device.Transport;
        }

        if (device.Ble is not null)
        {
            payload["ble"] = device.Ble;
        }

        return payload;
    }

    /// <summary>
    /// Builds the envelope written to status.json and served by /status/.
    /// Exposed to the test project so the schema-3 contract is asserted on the
    /// exact same code path the daemon uses.
    /// </summary>
    internal static Dictionary<string, object?> BuildSnapshotPayload(StatusSnapshot snapshot)
    {
        var now = snapshot.GeneratedAt;
        return new Dictionary<string, object?>
        {
            ["schema"] = 3,
            ["generatedAt"] = now,
            ["devices"] = snapshot.Devices
                .Select(device => BuildDevicePayload(device, now))
                .ToArray(),
        };
    }

    private async Task WriteSnapshotAsync(
        string root,
        StatusSnapshot snapshot,
        CancellationToken cancellationToken)
    {
        var payload = BuildSnapshotPayload(snapshot);

        var runDirectory = Path.Combine(root, "run");
        Directory.CreateDirectory(runDirectory);
        var path = Path.Combine(runDirectory, "status.json");
        var tempPath = path + ".tmp-" + Environment.ProcessId;
        var json = JsonSerializer.Serialize(payload, SnapshotJsonOptions);

        await _writeLock.WaitAsync(cancellationToken);
        try
        {
            await File.WriteAllTextAsync(tempPath, json, cancellationToken);
            File.Move(tempPath, path, overwrite: true);
        }
        finally
        {
            _writeLock.Release();
            try
            {
                if (File.Exists(tempPath))
                {
                    File.Delete(tempPath);
                }
            }
            catch (IOException)
            {
            }
        }
    }
}
