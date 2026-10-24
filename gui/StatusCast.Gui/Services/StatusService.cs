using System;
using System.Diagnostics;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace StatusCast_Gui.Services;

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

public sealed class StatusService
{
    public Task<DeviceStatus> ReadAsync(CancellationToken cancellationToken = default)
    {
        return PythonStatusRunner.ReadAsync(
            "status_core.py",
            "ios",
            "iphone",
            cancellationToken);
    }
}

public sealed class AndroidStatusService
{
    public Task<DeviceStatus> ReadAsync(CancellationToken cancellationToken = default)
    {
        return PythonStatusRunner.ReadAsync(
            "status_core_android.py",
            "android",
            "android",
            cancellationToken);
    }
}

internal static class PythonStatusRunner
{
    private static readonly Regex StatusPattern = new(
        @"^STATUS:BATTERY:([^|]+)\|CHARGING:([^|]+)\|MEDIA:([^|]*)\|([^|]*)\|([^|]*)\|MODEL:([^|]*)(?:\|APP:([^|]*)\|APPNAME:([^|]*))?$",
        RegexOptions.Compiled);

    public static async Task<DeviceStatus> ReadAsync(
        string scriptName,
        string platform,
        string deviceId,
        CancellationToken cancellationToken)
    {
        var root = ProjectLocator.FindRoot();
        if (root is null)
        {
            return new DeviceStatus
            {
                Platform = platform,
                DeviceId = deviceId,
                Error = "找不到项目根目录（status_core.py）",
            };
        }

        var python = System.IO.Path.Combine(root, ".venv", "Scripts", "python.exe");
        var script = System.IO.Path.Combine(root, scriptName);
        if (!System.IO.File.Exists(python) || !System.IO.File.Exists(script))
        {
            return new DeviceStatus
            {
                Platform = platform,
                DeviceId = deviceId,
                Error = $"找不到 {scriptName} 或 .venv\\Scripts\\python.exe",
            };
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
                return new DeviceStatus
                {
                    Platform = platform,
                    DeviceId = deviceId,
                    Error = "无法启动 Python 进程",
                };
            }

            var stdoutTask = process.StandardOutput.ReadToEndAsync(cancellationToken);
            var stderrTask = process.StandardError.ReadToEndAsync(cancellationToken);

            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(TimeSpan.FromSeconds(30));
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

                return new DeviceStatus
                {
                    Platform = platform,
                    DeviceId = deviceId,
                    Error = "读取超时",
                };
            }

            var stdout = (await stdoutTask).Trim();
            var stderr = (await stderrTask).Trim();
            if (process.ExitCode != 0)
            {
                return new DeviceStatus
                {
                    Platform = platform,
                    DeviceId = deviceId,
                    Error = string.IsNullOrWhiteSpace(stderr)
                        ? $"{scriptName} 退出码 {process.ExitCode}"
                        : stderr,
                };
            }

            var match = StatusPattern.Match(stdout);
            if (!match.Success)
            {
                return new DeviceStatus
                {
                    Platform = platform,
                    DeviceId = deviceId,
                    Error = "无法解析 Python 输出",
                };
            }

            var batteryLevel = ParseBattery(match.Groups[1].Value);
            var deviceModel = Normalize(match.Groups[6].Value);
            if (batteryLevel is null && deviceModel is null)
            {
                return new DeviceStatus
                {
                    Platform = platform,
                    DeviceId = deviceId,
                    Error = platform == "android"
                        ? "未检测到 Android 设备"
                        : "未检测到 iPhone",
                };
            }

            return new DeviceStatus
            {
                Platform = platform,
                DeviceId = deviceId,
                BatteryLevel = batteryLevel,
                IsCharging = ParseCharging(match.Groups[2].Value),
                MediaTitle = Normalize(match.Groups[3].Value),
                MediaArtist = Normalize(match.Groups[4].Value),
                MediaState = Normalize(match.Groups[5].Value) ?? "unknown",
                DeviceModel = deviceModel,
                ForegroundApp = Normalize(match.Groups[7].Value),
                ForegroundAppName = Normalize(match.Groups[8].Value),
            };
        }
        catch (Exception ex)
        {
            return new DeviceStatus
            {
                Platform = platform,
                DeviceId = deviceId,
                Error = ex.Message,
            };
        }
    }

    private static int? ParseBattery(string value)
    {
        return int.TryParse(value.Trim(), out var level) ? level : null;
    }

    private static bool? ParseCharging(string value)
    {
        return value.Trim().ToLowerInvariant() switch
        {
            "true" => true,
            "false" => false,
            _ => null,
        };
    }

    private static string? Normalize(string value)
    {
        var trimmed = value.Trim();
        return trimmed is "" or "NA" ? null : trimmed;
    }
}
