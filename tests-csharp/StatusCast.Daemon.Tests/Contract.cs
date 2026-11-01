using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;

namespace StatusCast.Daemon.Tests;

/// <summary>
/// Loads <c>schema/status-contract.json</c> (copied next to the test binary).
/// Tests on both sides of the Python/C# boundary assert against it so a field
/// cannot silently exist on one side only.
/// </summary>
internal static class Contract
{
    public sealed class Document
    {
        public int Schema { get; init; }

        public List<string> CollectorDeviceFields { get; init; } = new();

        public List<string> SnapshotDeviceFields { get; init; } = new();

        public List<string> AdditiveObjects { get; init; } = new();

        public Dictionary<string, List<string>> AdditiveFields { get; init; } = new();
    }

    public static Document Current { get; } = Load();

    private static Document Load()
    {
        var path = Path.Combine(AppContext.BaseDirectory, "status-contract.json");
        var json = File.ReadAllText(path);
        var options = new JsonSerializerOptions { PropertyNameCaseInsensitive = true };
        return JsonSerializer.Deserialize<Document>(json, options)
            ?? throw new InvalidOperationException("status-contract.json could not be parsed");
    }
}
