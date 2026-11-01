using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using StatusCast.Daemon.Services;
using Xunit;

namespace StatusCast.Daemon.Tests;

public sealed class StatusPayloadTests
{
    private static DeviceStatus FullDevice() => new()
    {
        Platform = "console",
        DeviceId = "ps5",
        DeviceModel = "PS5",
        Alias = "客厅",
        BatteryLevel = 50,
        IsCharging = true,
        MediaTitle = "title",
        MediaArtist = "artist",
        MediaState = "playing",
        ForegroundApp = "com.example",
        ForegroundAppName = "Example",
        Error = null,
        Console = new ConsoleStatus
        {
            Online = true,
            Method = "arp",
            LastSeenAt = 1,
            Mac = "AA:BB:CC:DD:EE:FF",
            IdleHint = true,
            Name = "PS5",
            Type = "ps5",
            StatusText = null,
        },
        Node = new NodeStatus
        {
            Ssh = true,
            Online = null,
            StatusText = null,
            Method = "ssh",
            Source = "ssh",
            Hostname = "host",
            LastSeenAt = 1,
            UptimeSeconds = 60,
        },
        Ble = new BleStatus
        {
            Address = "AA:BB:CC:DD:EE:FF",
            Name = "AirPods",
            Rssi = -60,
            Services = new List<string> { "battery" },
            Connected = true,
            HeartRate = 70,
            StatusText = null,
            LastSeenAt = 1,
        },
        Transport = new TransportStatus
        {
            Active = "arp",
            Degraded = false,
            Online = true,
            LastSeenAt = 1,
            Detail = "arp",
        },
    };

    [Fact]
    public void StandardDeviceRowMatchesContract()
    {
        var payload = StatusCoordinator.BuildDevicePayload(
            new DeviceStatus { Platform = "ios", DeviceId = "iphone" }, 123);

        Assert.Equal(
            Contract.Current.SnapshotDeviceFields.OrderBy(x => x, System.StringComparer.Ordinal),
            payload.Keys.OrderBy(x => x, System.StringComparer.Ordinal));
        Assert.Equal(123L, payload["updatedAt"]);
    }

    [Fact]
    public void AdditiveObjectsAreAttachedOnlyWhenPresent()
    {
        var device = new DeviceStatus { Platform = "ble", DeviceId = "airpods" };
        var noObjects = StatusCoordinator.BuildDevicePayload(device, 1);
        Assert.DoesNotContain("console", noObjects.Keys);
        Assert.DoesNotContain("node", noObjects.Keys);
        Assert.DoesNotContain("ble", noObjects.Keys);
        Assert.DoesNotContain("transport", noObjects.Keys);

        var full = StatusCoordinator.BuildDevicePayload(FullDevice(), 1);
        foreach (var name in Contract.Current.AdditiveObjects)
        {
            Assert.True(full.ContainsKey(name), $"missing additive object {name}");
        }
    }

    [Theory]
    [InlineData("console")]
    [InlineData("node")]
    [InlineData("ble")]
    [InlineData("transport")]
    public void EachAdditiveObjectSerializesExactlyContractFields(string name)
    {
        var payload = StatusCoordinator.BuildDevicePayload(FullDevice(), 1);
        var json = JsonSerializer.Serialize(payload, StatusCoordinator.SnapshotJsonOptions);
        using var document = JsonDocument.Parse(json);

        var expected = Contract.Current.AdditiveFields[name]
            .OrderBy(x => x, System.StringComparer.Ordinal)
            .ToArray();
        var actual = document.RootElement.GetProperty(name)
            .EnumerateObject()
            .Select(property => property.Name)
            .OrderBy(x => x, System.StringComparer.Ordinal)
            .ToArray();

        Assert.Equal(expected, actual);
    }

    [Fact]
    public void SnapshotEnvelopeMatchesContract()
    {
        var snapshot = new StatusSnapshot
        {
            GeneratedAt = 1793458293,
            Devices = new[] { FullDevice() },
            Primary = FullDevice(),
        };

        var payload = StatusCoordinator.BuildSnapshotPayload(snapshot);
        var json = JsonSerializer.Serialize(payload, StatusCoordinator.SnapshotJsonOptions);
        using var document = JsonDocument.Parse(json);

        Assert.Equal(Contract.Current.Schema, document.RootElement.GetProperty("schema").GetInt32());
        Assert.Equal(1793458293, document.RootElement.GetProperty("generatedAt").GetInt64());
        Assert.Equal(JsonValueKind.Array, document.RootElement.GetProperty("devices").ValueKind);

        var row = document.RootElement.GetProperty("devices")[0];
        var expected = Contract.Current.SnapshotDeviceFields
            .Concat(Contract.Current.AdditiveObjects)
            .OrderBy(x => x, System.StringComparer.Ordinal)
            .ToArray();
        var actual = row.EnumerateObject()
            .Select(property => property.Name)
            .OrderBy(x => x, System.StringComparer.Ordinal)
            .ToArray();
        Assert.Equal(expected, actual);
    }

    [Fact]
    public void EverySerializedPropertyNameIsCamelCase()
    {
        var snapshot = new StatusSnapshot
        {
            GeneratedAt = 1,
            Devices = new[] { FullDevice() },
            Primary = FullDevice(),
        };
        var json = JsonSerializer.Serialize(
            StatusCoordinator.BuildSnapshotPayload(snapshot),
            StatusCoordinator.SnapshotJsonOptions);
        using var document = JsonDocument.Parse(json);

        var names = new List<string>();
        CollectNames(document.RootElement, names);

        Assert.All(names, name => Assert.True(
            name.Length > 0 && char.IsLower(name[0]),
            $"property '{name}' is not camelCase"));
    }

    private static void CollectNames(JsonElement element, List<string> names)
    {
        switch (element.ValueKind)
        {
            case JsonValueKind.Object:
                foreach (var property in element.EnumerateObject())
                {
                    names.Add(property.Name);
                    CollectNames(property.Value, names);
                }

                break;
            case JsonValueKind.Array:
                foreach (var item in element.EnumerateArray())
                {
                    CollectNames(item, names);
                }

                break;
        }
    }
}
