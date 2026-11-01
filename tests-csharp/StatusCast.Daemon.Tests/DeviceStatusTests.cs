using StatusCast.Daemon.Services;
using Xunit;

namespace StatusCast.Daemon.Tests;

public sealed class DeviceStatusTests
{
    [Fact]
    public void WithAliasKeepsAdditiveObjectsAndOverridesDisplayName()
    {
        var device = new DeviceStatus
        {
            Platform = "console",
            DeviceId = "ps5",
            DeviceModel = "PS5",
            Console = new ConsoleStatus { Online = true },
            Node = new NodeStatus { Ssh = false },
            Ble = new BleStatus { Connected = false },
            Transport = new TransportStatus { Active = "arp" },
        };

        var aliased = device.WithAlias("客厅");

        Assert.Equal("客厅", aliased.Alias);
        Assert.Equal("客厅", aliased.DisplayName);
        Assert.Same(device.Console, aliased.Console);
        Assert.Same(device.Node, aliased.Node);
        Assert.Same(device.Ble, aliased.Ble);
        Assert.Same(device.Transport, aliased.Transport);
    }

    [Theory]
    [InlineData("ios", "📱")]
    [InlineData("android", "🤖")]
    [InlineData("console", "🎮")]
    [InlineData("steam", "🎮")]
    [InlineData("macos", "💻")]
    [InlineData("linux", "🐧")]
    [InlineData("ble", "📱")]
    public void PlatformIconMappingIsStable(string platform, string icon)
    {
        var device = new DeviceStatus { Platform = platform };
        Assert.Equal(icon, device.PlatformIcon);
    }

    [Fact]
    public void ModelDisplayPassesUnknownModelsThrough()
    {
        Assert.Equal("PS5", new DeviceStatus { DeviceModel = "PS5" }.ModelDisplay);
        Assert.Equal("14 Pro Max", new DeviceStatus { DeviceModel = "iPhone15,3" }.ModelDisplay);
        Assert.Equal("未知机型", new DeviceStatus().ModelDisplay);
    }

    [Fact]
    public void BatteryAndMediaDisplaysHandleNulls()
    {
        var idle = new DeviceStatus();
        Assert.Equal("🔋 未知", idle.BatteryDisplay);
        Assert.Equal("🎵 暂无播放", idle.MediaDisplay);

        var charging = new DeviceStatus { BatteryLevel = 80, IsCharging = true, MediaTitle = "t" };
        Assert.Equal("🔋 80% ⚡", charging.BatteryDisplay);
        Assert.Equal("🎵 t", charging.MediaDisplay);
    }
}
