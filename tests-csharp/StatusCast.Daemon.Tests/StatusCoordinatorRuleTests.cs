using StatusCast.Daemon.Services;
using Xunit;

namespace StatusCast.Daemon.Tests;

public sealed class StatusCoordinatorRuleTests
{
    [Fact]
    public void ErrorDeviceIsNeverAvailable()
    {
        var device = new DeviceStatus { Platform = "ios", DeviceModel = "iPhone15,3", Error = "x" };
        Assert.False(StatusCoordinator.IsAvailable(device));
    }

    [Fact]
    public void OnlineConsoleWithModelIsAvailable()
    {
        var device = new DeviceStatus
        {
            Platform = "console",
            DeviceModel = "PS5",
            Console = new ConsoleStatus { Online = true },
        };
        Assert.True(StatusCoordinator.IsAvailable(device));
    }

    [Fact]
    public void OfflineConsoleIsNotAvailable()
    {
        var device = new DeviceStatus
        {
            Platform = "console",
            DeviceModel = "PS5",
            Console = new ConsoleStatus { Online = false },
        };
        Assert.False(StatusCoordinator.IsAvailable(device));
    }

    [Fact]
    public void PhoneNeedsBatteryOrModel()
    {
        Assert.True(StatusCoordinator.IsAvailable(
            new DeviceStatus { Platform = "ios", BatteryLevel = 10 }));
        Assert.True(StatusCoordinator.IsAvailable(
            new DeviceStatus { Platform = "ios", DeviceModel = "iPhone15,3" }));
        Assert.False(StatusCoordinator.IsAvailable(
            new DeviceStatus { Platform = "ios" }));
    }

    [Fact]
    public void StaleDeviceKeepsIdentityAndAdditiveObjectsButWipesMetrics()
    {
        var source = new DeviceStatus
        {
            Platform = "console",
            DeviceId = "ps5",
            DeviceModel = "PS5",
            Alias = "客厅",
            BatteryLevel = 90,
            IsCharging = true,
            MediaTitle = "title",
            MediaArtist = "artist",
            ForegroundApp = "app",
            ForegroundAppName = "App",
            Console = new ConsoleStatus { Online = true, Name = "PS5" },
            Node = new NodeStatus { Ssh = true },
            Ble = new BleStatus { Connected = true },
            Transport = new TransportStatus { Active = "arp", Online = true },
        };

        var stale = StatusCoordinator.ToStaleDevice(source);

        Assert.Equal("读取超时", stale.Error);
        Assert.Equal("console", stale.Platform);
        Assert.Equal("ps5", stale.DeviceId);
        Assert.Equal("PS5", stale.DeviceModel);
        Assert.Equal("客厅", stale.Alias);
        Assert.Null(stale.BatteryLevel);
        Assert.Null(stale.IsCharging);
        Assert.Null(stale.MediaTitle);
        Assert.Null(stale.MediaArtist);
        Assert.Null(stale.ForegroundApp);
        Assert.Null(stale.ForegroundAppName);
        Assert.Equal("unknown", stale.MediaState);
        Assert.NotNull(stale.Console);
        Assert.NotNull(stale.Node);
        Assert.NotNull(stale.Ble);
        // Transport describes the current round, so a stale row must not claim it.
        Assert.Null(stale.Transport);
    }
}
