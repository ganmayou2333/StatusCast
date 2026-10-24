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

    public string ModelDisplay => DeviceModel switch
    {
        "iPhone15,3" => "14 Pro Max",
        "iPhone14,3" => "13 Pro Max",
        _ => DeviceModel ?? "未知机型",
    };

    public string DisplayName =>
        string.IsNullOrWhiteSpace(Alias) ? ModelDisplay : Alias;

    public string PlatformIcon => Platform == "android" ? "🤖" : "📱";

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
    };
}

/// <summary>
/// Runs the modular Python dispatcher (status_core.py) and turns its JSON
/// snapshot into DeviceStatus objects.
/// </summary>
public sealed class StatusService
{
    public Task<IReadOnlyList<DeviceStatus>> ReadAsync(
        CancellationToken cancellationToken = default)
    {
        return PythonStatusRunner.ReadAsync(cancellationToken);
    }
}

internal static class PythonStatusRunner
{
    private const string ScriptName = "status_core.py";
    private static readonly TimeSpan Timeout = TimeSpan.FromSeconds(30);

    public static async Task<IReadOnlyList<DeviceStatus>> ReadAsync(
        CancellationToken cancellationToken)
    {
        var root = ProjectLocator.FindRoot();
        if (root is null)
        {
            return ErrorDevices("找不到项目根目录（status_core.py）");
        }

        var python = Path.Combine(root, ".venv", "Scripts", "python.exe");
        var script = Path.Combine(root, ScriptName);
        if (!File.Exists(python) || !File.Exists(script))
        {
            return ErrorDevices($"找不到 {ScriptName} 或 .venv\\Scripts\\python.exe");
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
                return ErrorDevices("无法启动 Python 进程");
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

                return ErrorDevices("读取超时");
            }

            var stdout = (await stdoutTask).Trim();
            var stderr = (await stderrTask).Trim();
            if (process.ExitCode != 0)
            {
                return ErrorDevices(string.IsNullOrWhiteSpace(stderr)
                    ? $"{ScriptName} 退出码 {process.ExitCode}"
                    : stderr);
            }

            if (string.IsNullOrWhiteSpace(stdout))
            {
                return ErrorDevices("Python 未返回数据");
            }

            return ParseDevices(stdout);
        }
        catch (JsonException ex)
        {
            return ErrorDevices($"无法解析 Python JSON：{ex.Message}");
        }
        catch (Exception ex)
        {
            return ErrorDevices(ex.Message);
        }
    }

    private static IReadOnlyList<DeviceStatus> ParseDevices(string stdout)
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
