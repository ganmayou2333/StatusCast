using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using StatusCast.Core;

namespace StatusCast_Gui.Services;

public sealed class StatusCoordinator
{
    private static readonly JsonSerializerOptions SnapshotJsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = true,
    };

    private readonly StatusService _iphoneService = new();
    private readonly AndroidStatusService _androidService = new();
    private readonly SemaphoreSlim _writeLock = new(1, 1);

    public async Task<StatusSnapshot> ReadAsync(CancellationToken cancellationToken = default)
    {
        var root = ProjectLocator.FindRoot();
        if (root is null)
        {
            return await BuildUnknownSnapshotAsync("找不到项目根目录", cancellationToken);
        }

        var config = StatusCastConfig.Load(root);
        var iphoneTask = _iphoneService.ReadAsync(cancellationToken);
        var androidTask = _androidService.ReadAsync(cancellationToken);
        var pluginTask = FetchPluginsAsync(root, config, cancellationToken);

        await Task.WhenAll(iphoneTask, androidTask, pluginTask);

        var devices = new List<DeviceStatus>
        {
            ApplyAlias(await iphoneTask, config),
            ApplyAlias(await androidTask, config),
        };
        var plugins = await pluginTask;
        var primary = devices.FirstOrDefault(IsAvailable) ?? devices[0];
        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();

        var snapshot = new StatusSnapshot
        {
            GeneratedAt = now,
            Devices = devices,
            Plugins = plugins,
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
            Plugins = Array.Empty<PluginStatus>(),
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

    private static bool IsAvailable(DeviceStatus status)
    {
        return status.Error is null
            && (status.BatteryLevel is not null || status.DeviceModel is not null);
    }

    private async Task<IReadOnlyList<PluginStatus>> FetchPluginsAsync(
        string root,
        StatusCastConfig config,
        CancellationToken cancellationToken)
    {
        var pluginDirectory = Path.Combine(root, "plugins");
        var plugins = PluginHost.Load(pluginDirectory, config.EnabledPlugins);
        if (plugins.Count == 0)
        {
            return Array.Empty<PluginStatus>();
        }

        var tasks = plugins.Select(plugin => FetchPluginAsync(plugin, cancellationToken));
        return await Task.WhenAll(tasks);
    }

    private static async Task<PluginStatus> FetchPluginAsync(
        PluginRegistration plugin,
        CancellationToken cancellationToken)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(3));
        try
        {
            var status = await plugin.Instance.FetchStatusAsync(timeout.Token);
            return new PluginStatus
            {
                Name = plugin.Name,
                Status = string.IsNullOrWhiteSpace(status) ? null : status.Trim(),
            };
        }
        catch (Exception ex)
        {
            return new PluginStatus
            {
                Name = plugin.Name,
                Error = ex.Message,
            };
        }
    }

    private async Task WriteSnapshotAsync(
        string root,
        StatusSnapshot snapshot,
        CancellationToken cancellationToken)
    {
        var now = snapshot.GeneratedAt;
        var primary = snapshot.Primary;
        var payload = new
        {
            schema = 2,
            generatedAt = now,
            primaryDeviceId = primary.DeviceId,
            devices = snapshot.Devices.Select(device => new
            {
                id = device.DeviceId,
                platform = device.Platform,
                model = device.DeviceModel,
                displayName = device.DisplayName,
                batteryLevel = device.BatteryLevel,
                isCharging = device.IsCharging,
                mediaTitle = device.MediaTitle,
                mediaArtist = device.MediaArtist,
                mediaState = device.MediaState,
                foregroundApp = device.ForegroundApp,
                foregroundAppName = device.ForegroundAppName,
                updatedAt = now,
                error = device.Error,
                text = device.Text,
            }).ToArray(),
            plugins = snapshot.Plugins.Select(plugin => new
            {
                name = plugin.Name,
                status = plugin.Status,
                error = plugin.Error,
            }).ToArray(),
            battery = new
            {
                level = primary.BatteryLevel,
                isCharging = primary.IsCharging,
                updatedAt = now,
                error = primary.Error,
            },
            media = new
            {
                title = primary.MediaTitle,
                artist = primary.MediaArtist,
                album = (string?)null,
                playbackState = primary.MediaState,
                durationSeconds = (double?)null,
                elapsedSeconds = (double?)null,
                playerName = (string?)null,
                updatedAt = now,
                error = (string?)null,
            },
            errors = snapshot.Devices
                .Where(device => !string.IsNullOrWhiteSpace(device.Error))
                .Select(device => $"{device.DisplayName}: {device.Error}")
                .Concat(snapshot.Plugins
                    .Where(plugin => !string.IsNullOrWhiteSpace(plugin.Error))
                    .Select(plugin => $"{plugin.Name}: {plugin.Error}"))
                .ToArray(),
            text = primary.Text,
        };

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
