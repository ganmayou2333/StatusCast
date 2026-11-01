using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using StatusCast.Daemon.Services;
using Xunit;

namespace StatusCast.Daemon.Tests;

public sealed class StatusParsingTests
{
    private const string FullSnapshot = """
        {
          "schema": 3,
          "generatedAt": 1793458293,
          "devices": [
            {
              "id": "iphone", "platform": "ios", "model": "iPhone15,3",
              "batteryLevel": 87, "isCharging": true, "mediaState": "playing",
              "error": null,
              "transport": {"active": "usb", "degraded": false, "online": true,
                            "lastSeenAt": 1793458293, "detail": "usb"}
            },
            {
              "id": "ps5", "platform": "console", "model": "客厅PS5",
              "batteryLevel": null, "mediaState": "unknown", "error": null,
              "console": {"online": false, "method": "lan", "lastSeenAt": 1793450000,
                          "mac": "AA:BB:CC:DD:EE:FF", "idleHint": true,
                          "name": "PS5", "type": "ps5", "statusText": "已关机"},
              "transport": {"active": "none", "degraded": false, "online": false,
                            "lastSeenAt": 1793450000, "detail": null}
            },
            {
              "id": "macbook", "platform": "macos", "model": "Mac14,2",
              "batteryLevel": 55, "isCharging": false, "mediaState": "unknown",
              "error": null,
              "node": {"ssh": false, "online": true, "statusText": null,
                       "method": "lan", "source": "lan", "hostname": null,
                       "lastSeenAt": 1793458293, "uptimeSeconds": null},
              "transport": {"active": "lan", "degraded": true, "online": true,
                            "lastSeenAt": 1793458293, "detail": "arp"}
            },
            {
              "id": "airpods", "platform": "ble", "model": "AirPods Pro",
              "batteryLevel": 85, "mediaState": "unknown", "error": null,
              "ble": {"address": "AA:BB:CC:DD:EE:FF", "name": "AirPods Pro",
                      "rssi": -61, "services": ["battery", "heart_rate"],
                      "connected": true, "heartRate": 72,
                      "statusText": null, "lastSeenAt": 1793458293},
              "transport": {"active": "ble", "degraded": false, "online": true,
                            "lastSeenAt": 1793458293, "detail": "AA:BB:CC:DD:EE:FF"}
            },
            {"id": "android", "platform": "android", "model": "Pixel 7",
             "batteryLevel": 62, "mediaState": "unknown", "error": null}
          ]
        }
        """;

    [Fact]
    public void ParsesEveryAdditiveObject()
    {
        var devices = PythonStatusRunner.ParseDevices(FullSnapshot).ToList();

        Assert.Equal(5, devices.Count);

        var iphone = devices[0];
        Assert.Equal("ios", iphone.Platform);
        Assert.Equal(87, iphone.BatteryLevel);
        Assert.NotNull(iphone.Transport);
        Assert.Equal("usb", iphone.Transport!.Active);
        Assert.False(iphone.Transport.Degraded);
        Assert.True(iphone.Transport.Online);

        var console = devices[1];
        Assert.NotNull(console.Console);
        Assert.False(console.Console!.Online);
        Assert.Equal("lan", console.Console.Method);
        Assert.True(console.Console.IdleHint);
        Assert.Equal("已关机", console.Console.StatusText);
        Assert.Equal("AA:BB:CC:DD:EE:FF", console.Console.Mac);
        Assert.Equal(1793450000, console.Console.LastSeenAt);

        var node = devices[2];
        Assert.NotNull(node.Node);
        Assert.False(node.Node!.Ssh);
        Assert.True(node.Node.Online);
        Assert.Null(node.Node.StatusText);
        Assert.Equal("lan", node.Node.Source);
        Assert.True(node.Transport!.Degraded);

        var ble = devices[3];
        Assert.NotNull(ble.Ble);
        Assert.Equal("AA:BB:CC:DD:EE:FF", ble.Ble!.Address);
        Assert.Equal(-61, ble.Ble.Rssi);
        Assert.Equal(new[] { "battery", "heart_rate" }, ble.Ble.Services);
        Assert.True(ble.Ble.Connected);
        Assert.Equal(72, ble.Ble.HeartRate);
    }

    [Fact]
    public void MissingAdditiveObjectsStayNull()
    {
        const string json = """
            {"devices":[
              {"id":"iphone","platform":"ios","batteryLevel":50,"error":null},
              {"id":"android","platform":"android","batteryLevel":10,"error":null}
            ]}
            """;

        var devices = PythonStatusRunner.ParseDevices(json).ToList();

        Assert.Equal(2, devices.Count);
        Assert.All(devices, device =>
        {
            Assert.Null(device.Console);
            Assert.Null(device.Node);
            Assert.Null(device.Ble);
            Assert.Null(device.Transport);
        });
    }

    [Fact]
    public void MistypedAdditiveMembersDegradeToNull()
    {
        const string json = """
            {"devices":[
              {"id":"x","platform":"console","error":null,
               "console": {"online":"yes","method":5,"lastSeenAt":"bad","mac":true,
                           "idleHint":"no","name":[],"type":{},"statusText":1},
               "transport": {"active":5,"degraded":"yes","online":"no",
                             "lastSeenAt":"x","detail":[]},
               "ble": {"address":7,"name":[],"rssi":"-1","services":"battery",
                       "connected":"yes","heartRate":"70","statusText":1,"lastSeenAt":[]},
               "node": {"ssh":"yes","online":"no","statusText":1,"method":[],
                        "source":{},"hostname":2,"lastSeenAt":"x","uptimeSeconds":1.5}}
            ]}
            """;

        var device = PythonStatusRunner.ParseDevices(json)[0];

        Assert.NotNull(device.Console);
        Assert.Null(device.Console!.Online);
        Assert.Null(device.Console.Method);
        Assert.Null(device.Console.LastSeenAt);
        Assert.Null(device.Transport!.Active);
        Assert.Null(device.Transport.Online);
        Assert.Null(device.Ble!.Address);
        Assert.Null(device.Ble.Services);
        Assert.Null(device.Node!.Ssh);
        Assert.Null(device.Node.UptimeSeconds);
    }

    [Fact]
    public void MissingDevicesArrayFallsBackToPhones()
    {
        var devices = PythonStatusRunner.ParseDevices("{\"schema\":3}").ToList();

        Assert.Equal(new[] { "iphone", "android" }, devices.Select(d => d.DeviceId));
        Assert.All(devices, device => Assert.Equal("Python 未返回该设备", device.Error));
    }

    [Fact]
    public void CleartextAdditiveObjectInsideDeviceDoesNotBreakParsing()
    {
        const string json = """
            {"devices":[
              {"id":"x","platform":"ble","error":null,
               "ble":"oops","node":5,"console":[],"transport":null},
              {"id":"y","platform":"ios","batteryLevel":10,"error":null},
              {"id":"z","platform":"android","batteryLevel":10,"error":null}
            ]}
            """;

        var devices = PythonStatusRunner.ParseDevices(json).ToList();
        Assert.Equal(3, devices.Count);
        Assert.All(devices, device =>
        {
            Assert.Null(device.Console);
            Assert.Null(device.Node);
            Assert.Null(device.Ble);
            Assert.Null(device.Transport);
        });
    }
}
