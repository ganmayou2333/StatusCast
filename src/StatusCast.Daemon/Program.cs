using System;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using StatusCast.Daemon.Services;

namespace StatusCast.Daemon;

internal static class Program
{
    private static async Task<int> Main()
    {
        try
        {
            Console.OutputEncoding = Encoding.UTF8;
        }
        catch (Exception)
        {
        }

        Console.Title = "StatusCast Core";

        var root = ProjectLocator.FindRoot() ?? AppContext.BaseDirectory;
        var config = StatusCastConfig.Load(root);

        // Read the version instead of hardcoding it: the banner used to say v3.3.0
        // long after <Version> moved, and UpdateChecker compares against the
        // assembly version, so a stale banner is a lie users can read.
        var assemblyVersion = System.Reflection.Assembly.GetExecutingAssembly().GetName().Version;
        var versionText = assemblyVersion is null
            ? "?"
            : $"{assemblyVersion.Major}.{assemblyVersion.Minor}.{assemblyVersion.Build}";

        Log("SYSTEM", $"StatusCast Core v{versionText} 启动", ConsoleColor.Cyan);
        Log("SYSTEM", $"纯后台采集引擎 · 轮询间隔 {config.RefreshInterval.TotalSeconds:0} 秒", ConsoleColor.DarkGray);

        // Fire-and-forget: the GitHub Release check must never block startup.
        _ = UpdateChecker.CheckForUpdatesAsync();

        var coordinator = new StatusCoordinator();
        using var server = new StatusServer();

        if (!server.Start())
        {
            Log("ERROR", $"API 启动失败：{server.LastError}", ConsoleColor.Red);
            return 1;
        }

        Log("API", $"监听 http://localhost:{StatusServer.Port}/status/", ConsoleColor.Green);
        Log("API", $"健康检查 http://localhost:{StatusServer.Port}/health", ConsoleColor.Green);
        Log("USB", "等待 iPhone 连接...", ConsoleColor.DarkCyan);
        Log("ADB", "等待 Android 连接...", ConsoleColor.DarkGreen);

        using var shutdown = new CancellationTokenSource();
        Console.CancelKeyPress += (_, eventArgs) =>
        {
            eventArgs.Cancel = true;
            shutdown.Cancel();
        };

        // Occupancy guardrail (spec R5): one baseline line every 10 rounds plus a
        // throttled warning when memory or thread count leaves its envelope.
        var memoryGuard = new MemoryGuard();
        var rounds = 0;

        try
        {
            await RefreshAsync(coordinator, shutdown.Token);
            memoryGuard.OnRoundCompleted(++rounds);

            using var timer = new PeriodicTimer(config.RefreshInterval);
            while (await timer.WaitForNextTickAsync(shutdown.Token))
            {
                await RefreshAsync(coordinator, shutdown.Token);
                memoryGuard.OnRoundCompleted(++rounds);
            }
        }
        catch (OperationCanceledException)
        {
            // Ctrl+C or service stop.
        }
        catch (Exception ex)
        {
            Log("ERROR", ex.Message, ConsoleColor.Red);
            return 1;
        }
        finally
        {
            server.Stop();
            Log("SYSTEM", "StatusCast Core 已停止", ConsoleColor.DarkGray);
        }

        return 0;
    }

    private static async Task RefreshAsync(
        StatusCoordinator coordinator,
        CancellationToken cancellationToken)
    {
        try
        {
            var snapshot = await coordinator.ReadAsync(cancellationToken);

            foreach (var device in snapshot.Devices)
            {
                if (!string.IsNullOrWhiteSpace(device.Error))
                {
                    Log(device.Platform.ToUpperInvariant(), device.Error, ConsoleColor.DarkYellow);
                    continue;
                }

                Log(
                    device.Platform.ToUpperInvariant(),
                    $"{device.DisplayName} | {device.BatteryDisplay} | {device.MediaDisplay}",
                    device.Platform == "android" ? ConsoleColor.Green : ConsoleColor.Cyan);
            }

            Log(
                "SNAPSHOT",
                $"run/status.json 已更新 · {snapshot.GeneratedAt}",
                ConsoleColor.DarkGray);
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (Exception ex)
        {
            Log("ERROR", $"采集失败：{ex.Message}", ConsoleColor.Red);
        }
    }

    private static void Log(string tag, string message, ConsoleColor color)
    {
        var previous = Console.ForegroundColor;
        try
        {
            Console.ForegroundColor = color;
            Console.Write($"[{tag}] ");
            Console.ForegroundColor = ConsoleColor.Gray;
            Console.WriteLine(message);
        }
        finally
        {
            Console.ForegroundColor = previous;
        }
    }
}
