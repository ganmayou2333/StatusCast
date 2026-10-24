using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;

namespace StatusCast_Gui.Services;

public sealed class StatusCastConfig
{
    public Dictionary<string, string> Aliases { get; init; } =
        new(StringComparer.OrdinalIgnoreCase);

    public List<string> EnabledPlugins { get; init; } = new();

    public static StatusCastConfig Load(string root)
    {
        var path = Path.Combine(root, "config.json");
        if (!File.Exists(path))
        {
            return new StatusCastConfig();
        }

        try
        {
            var json = File.ReadAllText(path);
            var loaded = JsonSerializer.Deserialize<StatusCastConfig>(json, new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true,
                ReadCommentHandling = JsonCommentHandling.Skip,
                AllowTrailingCommas = true,
            });

            if (loaded is null)
            {
                return new StatusCastConfig();
            }

            return new StatusCastConfig
            {
                Aliases = new Dictionary<string, string>(
                    loaded.Aliases ?? new Dictionary<string, string>(),
                    StringComparer.OrdinalIgnoreCase),
                EnabledPlugins = loaded.EnabledPlugins ?? new List<string>(),
            };
        }
        catch (Exception)
        {
            return new StatusCastConfig();
        }
    }
}
