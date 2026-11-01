using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace StatusCast.Daemon.Services;

/// <summary>
/// Game-console presence block emitted by the Python layer as a nested
/// "console" object. Every member is optional: a missing or mistyped JSON
/// value maps to null rather than failing the whole device.
/// </summary>
public sealed class ConsoleStatus
{
    public bool? Online { get; init; }
    public string? Method { get; init; }
    public long? LastSeenAt { get; init; }
    public string? Mac { get; init; }
    public bool? IdleHint { get; init; }
    public string? Name { get; init; }
    public string? Type { get; init; }
    public string? StatusText { get; init; }
}

/// <summary>
/// SSH node transport block emitted by the Python layer as a nested
/// "node" object. Every member is optional for the same reason as
/// <see cref="ConsoleStatus"/>.
/// </summary>
public sealed class NodeStatus
{
    public bool? Ssh { get; init; }
    public bool? Online { get; init; }
    public string? StatusText { get; init; }
    public string? Method { get; init; }
    public string? Source { get; init; }
    public string? Hostname { get; init; }
    public long? LastSeenAt { get; init; }
    public long? UptimeSeconds { get; init; }
}

/// <summary>
/// Fallback-transport block emitted by the Python layer as a nested
/// "transport" object. Every member is optional: a missing or mistyped JSON
/// value maps to null rather than failing the whole device.
/// </summary>
public sealed class TransportStatus
{
    public string? Active { get; init; }
    public bool? Degraded { get; init; }
    public bool? Online { get; init; }
    public long? LastSeenAt { get; init; }
    public string? Detail { get; init; }
}

/// <summary>
/// BLE peripheral block emitted by the Python layer as a nested "ble" object.
/// Every member is optional: a missing or mistyped JSON value maps to null
/// rather than failing the whole device.
/// </summary>
public sealed class BleStatus
{
    public string? Address { get; init; }
    public string? Name { get; init; }
    public int? Rssi { get; init; }
    public List<string>? Services { get; init; }
    public bool? Connected { get; init; }
    public int? HeartRate { get; init; }
    public string? StatusText { get; init; }
    public long? LastSeenAt { get; init; }
}

public sealed class DeviceStatus
{
    public string Platform { get; init; } = "ios";
    public string DeviceId { get; init; } = "iphone";
    public int? BatteryLevel { get; init; }
    public bool? IsCharging { get; init; }
    public string? MediaTitle { get; init; }
    public string? MediaArtist { get; init; }
    public string MediaState { get; init; } = "unknown";
    public string? DeviceModel { get; init; }
    public string? Alias { get; init; }
    public string? ForegroundApp { get; init; }
    public string? ForegroundAppName { get; init; }
    public string? Error { get; init; }

    // Additive nested payloads from the Python collectors. Null means the
    // device has no such block, in which case status.json omits the key.
    public ConsoleStatus? Console { get; init; }
    public NodeStatus? Node { get; init; }
    public TransportStatus? Transport { get; init; }
    public BleStatus? Ble { get; init; }

    public string ModelDisplay => DeviceModel switch
    {
        "iPhone15,3" => "14 Pro Max",
        "iPhone14,3" => "13 Pro Max",
        _ => DeviceModel ?? "未知机型",
    };

    public string DisplayName =>
        string.IsNullOrWhiteSpace(Alias) ? ModelDisplay : Alias;

    public string PlatformIcon => Platform.ToLowerInvariant() switch
    {
        "android" => "🤖",
        "console" or "steam" => "🎮",
        "macos" => "💻",
        "linux" => "🐧",
        _ => "📱",
    };

    public string BatteryDisplay
    {
        get
        {
            if (BatteryLevel is null)
            {
                return "🔋 未知";
            }

            return IsCharging == true
                ? $"🔋 {BatteryLevel}% ⚡"
                : $"🔋 {BatteryLevel}%";
        }
    }

    public string MediaDisplay
    {
        get
        {
            if (string.IsNullOrWhiteSpace(MediaTitle))
            {
                return "🎵 暂无播放";
            }

            return string.IsNullOrWhiteSpace(MediaArtist)
                ? $"🎵 {MediaTitle}"
                : $"🎵 {MediaTitle} - {MediaArtist}";
        }
    }

    public string AppDisplay =>
        string.IsNullOrWhiteSpace(ForegroundAppName)
            ? ForegroundApp ?? "—"
            : ForegroundAppName;

    public string Text => $"{PlatformIcon} {DisplayName} | {BatteryDisplay} | {MediaDisplay}";

    public DeviceStatus WithAlias(string? alias) => new()
    {
        Platform = Platform,
        DeviceId = DeviceId,
        BatteryLevel = BatteryLevel,
        IsCharging = IsCharging,
        MediaTitle = MediaTitle,
        MediaArtist = MediaArtist,
        MediaState = MediaState,
        DeviceModel = DeviceModel,
        Alias = alias,
        ForegroundApp = ForegroundApp,
        ForegroundAppName = ForegroundAppName,
        Error = Error,
        Console = Console,
        Node = Node,
        Transport = Transport,
        Ble = Ble,
    };
}

/// <summary>
/// One Python collection round. <see cref="IsFallback"/> is true only when the
/// runner itself failed (timeout, crash, bad JSON, missing runtime) and the
/// device list was replaced by synthetic error rows; a disconnected phone is a
/// normal result and leaves it false.
/// </summary>
public sealed record StatusReadResult(IReadOnlyList<DeviceStatus> Devices, bool IsFallback);

/// <summary>
/// Runs the modular Python dispatcher (status_core.py) and turns its JSON
/// snapshot into DeviceStatus objects.
/// </summary>
public sealed class StatusService
{
    public Task<StatusReadResult> ReadAsync(
        CancellationToken cancellationToken = default)
    {
        return PythonStatusRunner.ReadAsync(cancellationToken);
    }
}

internal static class PythonStatusRunner
{
    private const string ScriptName = "status_core.py";
    private static readonly TimeSpan Timeout = TimeSpan.FromSeconds(30);

    public static async Task<StatusReadResult> ReadAsync(
        CancellationToken cancellationToken)
    {
        var root = ProjectLocator.FindRoot();
        if (root is null)
        {
            return new StatusReadResult(
                ErrorDevices("找不到项目根目录（status_core.py）"),
                IsFallback: true);
        }

        var python = Path.Combine(root, ".venv", "Scripts", "python.exe");
        var script = Path.Combine(root, ScriptName);
        if (!File.Exists(python) || !File.Exists(script))
        {
            return new StatusReadResult(
                ErrorDevices($"找不到 {ScriptName} 或 .venv\\Scripts\\python.exe"),
                IsFallback: true);
        }

        var startInfo = new ProcessStartInfo
        {
            FileName = python,
            WorkingDirectory = root,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        startInfo.ArgumentList.Add(script);

        try
        {
            using var process = Process.Start(startInfo);
            if (process is null)
            {
                return new StatusReadResult(ErrorDevices("无法启动 Python 进程"), IsFallback: true);
            }

            var stdoutTask = process.StandardOutput.ReadToEndAsync(cancellationToken);
            var stderrTask = process.StandardError.ReadToEndAsync(cancellationToken);

            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(Timeout);
            try
            {
                await process.WaitForExitAsync(timeout.Token);
            }
            catch (OperationCanceledException)
            {
                try
                {
                    process.Kill(entireProcessTree: true);
                }
                catch (InvalidOperationException)
                {
                }

                return new StatusReadResult(ErrorDevices("读取超时"), IsFallback: true);
            }

            var stdout = (await stdoutTask).Trim();
            var stderr = (await stderrTask).Trim();
            if (process.ExitCode != 0)
            {
                return new StatusReadResult(
                    ErrorDevices(string.IsNullOrWhiteSpace(stderr)
                        ? $"{ScriptName} 退出码 {process.ExitCode}"
                        : stderr),
                    IsFallback: true);
            }

            if (string.IsNullOrWhiteSpace(stdout))
            {
                return new StatusReadResult(ErrorDevices("Python 未返回数据"), IsFallback: true);
            }

            return new StatusReadResult(ParseDevices(stdout), IsFallback: false);
        }
        catch (JsonException ex)
        {
            return new StatusReadResult(
                ErrorDevices($"无法解析 Python JSON：{ex.Message}"),
                IsFallback: true);
        }
        catch (Exception ex)
        {
            return new StatusReadResult(ErrorDevices(ex.Message), IsFallback: true);
        }
    }

    internal static IReadOnlyList<DeviceStatus> ParseDevices(string stdout)
    {
        using var document = JsonDocument.Parse(stdout);
        var devices = new List<DeviceStatus>();

        if (document.RootElement.TryGetProperty("devices", out var array)
            && array.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in array.EnumerateArray())
            {
                devices.Add(ParseDevice(item));
            }
        }

        EnsureDevice(devices, "ios", "iphone");
        EnsureDevice(devices, "android", "android");
        // Console / SSH node devices are config-driven: they come and go with
        // config.json, so no hard fallback entries are guaranteed for them.
        return devices;
    }

    private static DeviceStatus ParseDevice(JsonElement item)
    {
        return new DeviceStatus
        {
            Platform = GetString(item, "platform") ?? "unknown",
            DeviceId = GetString(item, "id") ?? "unknown",
            BatteryLevel = GetInt(item, "batteryLevel"),
            IsCharging = GetBool(item, "isCharging"),
            MediaTitle = GetString(item, "mediaTitle"),
            MediaArtist = GetString(item, "mediaArtist"),
            MediaState = GetString(item, "mediaState") ?? "unknown",
            DeviceModel = GetString(item, "model"),
            ForegroundApp = GetString(item, "foregroundApp"),
            ForegroundAppName = GetString(item, "foregroundAppName"),
            Error = GetString(item, "error"),
            Console = ParseConsole(item),
            Node = ParseNode(item),
            Transport = ParseTransport(item),
            Ble = ParseBle(item),
        };
    }

    private static ConsoleStatus? ParseConsole(JsonElement item)
    {
        if (!item.TryGetProperty("console", out var value)
            || value.ValueKind != JsonValueKind.Object)
        {
            return null;
        }

        return new ConsoleStatus
        {
            Online = GetBool(value, "online"),
            Method = GetString(value, "method"),
            LastSeenAt = GetLong(value, "lastSeenAt"),
            Mac = GetString(value, "mac"),
            IdleHint = GetBool(value, "idleHint"),
            Name = GetString(value, "name"),
            Type = GetString(value, "type"),
            StatusText = GetString(value, "statusText"),
        };
    }

    private static NodeStatus? ParseNode(JsonElement item)
    {
        if (!item.TryGetProperty("node", out var value)
            || value.ValueKind != JsonValueKind.Object)
        {
            return null;
        }

        return new NodeStatus
        {
            Ssh = GetBool(value, "ssh"),
            Online = GetBool(value, "online"),
            StatusText = GetString(value, "statusText"),
            Method = GetString(value, "method"),
            Source = GetString(value, "source"),
            Hostname = GetString(value, "hostname"),
            LastSeenAt = GetLong(value, "lastSeenAt"),
            UptimeSeconds = GetLong(value, "uptimeSeconds"),
        };
    }

    private static TransportStatus? ParseTransport(JsonElement item)
    {
        if (!item.TryGetProperty("transport", out var value)
            || value.ValueKind != JsonValueKind.Object)
        {
            return null;
        }

        return new TransportStatus
        {
            Active = GetString(value, "active"),
            Degraded = GetBool(value, "degraded"),
            Online = GetBool(value, "online"),
            LastSeenAt = GetLong(value, "lastSeenAt"),
            Detail = GetString(value, "detail"),
        };
    }

    private static BleStatus? ParseBle(JsonElement item)
    {
        if (!item.TryGetProperty("ble", out var value)
            || value.ValueKind != JsonValueKind.Object)
        {
            return null;
        }

        return new BleStatus
        {
            Address = GetString(value, "address"),
            Name = GetString(value, "name"),
            Rssi = GetInt(value, "rssi"),
            Services = GetStringArray(value, "services"),
            Connected = GetBool(value, "connected"),
            HeartRate = GetInt(value, "heartRate"),
            StatusText = GetString(value, "statusText"),
            LastSeenAt = GetLong(value, "lastSeenAt"),
        };
    }

    private static void EnsureDevice(
        ICollection<DeviceStatus> devices,
        string platform,
        string deviceId)
    {
        if (devices.Any(device =>
                string.Equals(device.Platform, platform, StringComparison.OrdinalIgnoreCase)))
        {
            return;
        }

        devices.Add(new DeviceStatus
        {
            Platform = platform,
            DeviceId = deviceId,
            Error = "Python 未返回该设备",
        });
    }

    private static IReadOnlyList<DeviceStatus> ErrorDevices(string error)
    {
        return new[]
        {
            new DeviceStatus
            {
                Platform = "ios",
                DeviceId = "iphone",
                Error = error,
            },
            new DeviceStatus
            {
                Platform = "android",
                DeviceId = "android",
                Error = error,
            },
        };
    }

    private static string? GetString(JsonElement element, string name)
    {
        if (!element.TryGetProperty(name, out var value)
            || value.ValueKind != JsonValueKind.String)
        {
            return null;
        }

        var text = value.GetString();
        return string.IsNullOrWhiteSpace(text) ? null : text.Trim();
    }

    private static int? GetInt(JsonElement element, string name)
    {
        return element.TryGetProperty(name, out var value)
            && value.ValueKind == JsonValueKind.Number
            && value.TryGetInt32(out var number)
            ? number
            : null;
    }

    private static long? GetLong(JsonElement element, string name)
    {
        return element.TryGetProperty(name, out var value)
            && value.ValueKind == JsonValueKind.Number
            && value.TryGetInt64(out var number)
            ? number
            : null;
    }

    /// <summary>
    /// Reads a string array. Only actual JSON strings are kept: a non-array
    /// value yields null, and non-string elements inside an array are skipped
    /// instead of failing the whole block.
    /// </summary>
    private static List<string>? GetStringArray(JsonElement element, string name)
    {
        if (!element.TryGetProperty(name, out var value)
            || value.ValueKind != JsonValueKind.Array)
        {
            return null;
        }

        var items = new List<string>();
        foreach (var entry in value.EnumerateArray())
        {
            if (entry.ValueKind != JsonValueKind.String)
            {
                continue;
            }

            var text = entry.GetString();
            if (!string.IsNullOrWhiteSpace(text))
            {
                items.Add(text.Trim());
            }
        }

        return items;
    }

    private static bool? GetBool(JsonElement element, string name)
    {
        if (!element.TryGetProperty(name, out var value))
        {
            return null;
        }

        return value.ValueKind switch
        {
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            _ => null,
        };
    }
}
