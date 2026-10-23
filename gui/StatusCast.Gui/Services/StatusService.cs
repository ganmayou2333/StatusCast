using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace StatusCast_Gui.Services;

public sealed class DeviceStatus
{
    public int? BatteryLevel { get; init; }
    public bool? IsCharging { get; init; }
    public string? MediaTitle { get; init; }
    public string? MediaArtist { get; init; }
    public string MediaState { get; init; } = "unknown";
    public string? DeviceModel { get; init; }
    public string? Error { get; init; }

    public string ModelDisplay => DeviceModel switch
    {
        "iPhone15,3" => "14 Pro Max",
        "iPhone14,3" => "13 Pro Max",
        _ => DeviceModel ?? "未知机型",
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
            if (string.IsNullOrWhiteSpace(MediaTitle)
                || string.IsNullOrWhiteSpace(MediaArtist)
                || MediaTitle == "NA"
                || MediaArtist == "NA")
            {
                return "🎵 暂无播放";
            }

            return $"🎵 {MediaTitle} - {MediaArtist}";
        }
    }
}

public sealed class StatusService
{
    private static readonly Regex StatusPattern = new(
        @"^STATUS:BATTERY:([^|]+)\|CHARGING:([^|]+)\|MEDIA:([^|]*)\|([^|]*)\|([^|]*)\|MODEL:([^|]*)$",
        RegexOptions.Compiled);

    private static readonly JsonSerializerOptions SnapshotJsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = true,
    };

    private readonly SemaphoreSlim _writeLock = new(1, 1);

    public async Task<DeviceStatus> ReadAsync(CancellationToken cancellationToken = default)
    {
        var paths = FindPaths();
        if (paths is null)
        {
            return new DeviceStatus
            {
                Error = "找不到 .venv\\Scripts\\python.exe 或 status_core.py",
            };
        }

        var (python, script, workingDirectory) = paths.Value;
        var startInfo = new ProcessStartInfo
        {
            FileName = python,
            WorkingDirectory = workingDirectory,
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
                return new DeviceStatus { Error = "无法启动 Python 进程" };
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

                return new DeviceStatus { Error = "读取超时" };
            }

            var stdout = (await stdoutTask).Trim();
            var stderr = (await stderrTask).Trim();
            if (process.ExitCode != 0)
            {
                return new DeviceStatus
                {
                    Error = string.IsNullOrWhiteSpace(stderr)
                        ? $"Python 退出码 {process.ExitCode}"
                        : stderr,
                };
            }

            var match = StatusPattern.Match(stdout);
            if (!match.Success)
            {
                return new DeviceStatus { Error = "无法解析 Python 输出" };
            }

            var status = new DeviceStatus
            {
                BatteryLevel = ParseBattery(match.Groups[1].Value),
                IsCharging = ParseCharging(match.Groups[2].Value),
                MediaTitle = Normalize(match.Groups[3].Value),
                MediaArtist = Normalize(match.Groups[4].Value),
                MediaState = Normalize(match.Groups[5].Value) ?? "unknown",
                DeviceModel = Normalize(match.Groups[6].Value),
            };

            await WriteSnapshotAsync(status, workingDirectory, cancellationToken);
            return status;
        }
        catch (Exception ex)
        {
            return new DeviceStatus { Error = ex.Message };
        }
    }

    private async Task WriteSnapshotAsync(
        DeviceStatus status,
        string workingDirectory,
        CancellationToken cancellationToken)
    {
        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        var snapshot = new
        {
            schema = 1,
            battery = new
            {
                level = status.BatteryLevel,
                isCharging = status.IsCharging,
                updatedAt = now,
                error = (string?)null,
            },
            media = new
            {
                title = status.MediaTitle,
                artist = status.MediaArtist,
                album = (string?)null,
                playbackState = status.MediaState,
                durationSeconds = (double?)null,
                elapsedSeconds = (double?)null,
                playerName = (string?)null,
                updatedAt = now,
                error = (string?)null,
            },
            generatedAt = now,
            errors = Array.Empty<string>(),
            text = $"{status.BatteryDisplay} | {status.MediaDisplay}",
        };

        var runDirectory = Path.Combine(workingDirectory, "run");
        Directory.CreateDirectory(runDirectory);
        var path = Path.Combine(runDirectory, "status.json");
        var tempPath = path + ".tmp-" + Environment.ProcessId;
        var json = JsonSerializer.Serialize(snapshot, SnapshotJsonOptions);

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

    private static (string Python, string Script, string WorkingDirectory)? FindPaths()
    {
        var starts = new[]
        {
            Environment.GetEnvironmentVariable("STATUSCAST_ROOT"),
            AppContext.BaseDirectory,
            Environment.CurrentDirectory,
        };

        foreach (var start in starts)
        {
            if (string.IsNullOrWhiteSpace(start))
            {
                continue;
            }

            DirectoryInfo? directory;
            try
            {
                directory = new DirectoryInfo(start);
            }
            catch (Exception)
            {
                continue;
            }

            for (var depth = 0; directory is not null && depth < 12; depth++, directory = directory.Parent)
            {
                var python = Path.Combine(directory.FullName, ".venv", "Scripts", "python.exe");
                var script = Path.Combine(directory.FullName, "status_core.py");
                if (File.Exists(python) && File.Exists(script))
                {
                    return (python, script, directory.FullName);
                }
            }
        }

        return null;
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
