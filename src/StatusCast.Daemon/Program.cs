using System;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using StatusCast.Daemon.Services;

namespace StatusCast.Daemon;

internal static class Program
{
    private static readonly TimeSpan RefreshInterval = TimeSpan.FromSeconds(30);

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

        Log("SYSTEM", "StatusCast Core v3.1 启动", ConsoleColor.Cyan);
        Log("SYSTEM", "纯后台采集引擎 · WinUI 已移除", ConsoleColor.DarkGray);

        var coordinator = new StatusCoordinator();
        using var server = new StatusServer();

        if (!server.Start())
        {
            Log("ERROR", $"API 启动失败：{server.LastError}", ConsoleColor.Red);
            return 1;
        }

        Log("API", $"监听 http://localhost:{StatusServer.Port}/status/", ConsoleColor.Green);
        Log("USB", "等待 iPhone 连接...", ConsoleColor.DarkCyan);
        Log("ADB", "等待 Android 连接...", ConsoleColor.DarkGreen);

        using var shutdown = new CancellationTokenSource();
        Console.CancelKeyPress += (_, eventArgs) =>
        {
            eventArgs.Cancel = true;
            shutdown.Cancel();
        };

        try
        {
            await RefreshAsync(coordinator, shutdown.Token);

            using var timer = new PeriodicTimer(RefreshInterval);
            while (await timer.WaitForNextTickAsync(shutdown.Token))
            {
                await RefreshAsync(coordinator, shutdown.Token);
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
