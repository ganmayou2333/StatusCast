using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.Loader;

namespace StatusCast.Core;

public sealed class PluginRegistration
{
    public required string Name { get; init; }
    public required IStatusPlugin Instance { get; init; }
}

/// <summary>
/// Loads plugin assemblies from a directory and keeps only the names listed in
/// config.json's EnabledPlugins array.
/// </summary>
public static class PluginHost
{
    public static IReadOnlyList<PluginRegistration> Load(
        string pluginDirectory,
        IEnumerable<string>? enabledPlugins)
    {
        var enabled = new HashSet<string>(
            enabledPlugins ?? Array.Empty<string>(),
            StringComparer.OrdinalIgnoreCase);

        if (enabled.Count == 0 || !Directory.Exists(pluginDirectory))
        {
            return Array.Empty<PluginRegistration>();
        }

        var registrations = new List<PluginRegistration>();
        foreach (var dll in Directory.EnumerateFiles(pluginDirectory, "*.dll"))
        {
            Assembly assembly;
            try
            {
                assembly = AssemblyLoadContext.Default.LoadFromAssemblyPath(
                    Path.GetFullPath(dll));
            }
            catch (Exception)
            {
                continue;
            }

            foreach (var type in SafeGetTypes(assembly))
            {
                if (type.IsAbstract
                    || type.IsInterface
                    || !typeof(IStatusPlugin).IsAssignableFrom(type))
                {
                    continue;
                }

                if (Activator.CreateInstance(type) is not IStatusPlugin plugin)
                {
                    continue;
                }

                if (!enabled.Contains(plugin.PluginName))
                {
                    continue;
                }

                registrations.Add(new PluginRegistration
                {
                    Name = plugin.PluginName,
                    Instance = plugin,
                });
            }
        }

        return registrations;
    }

    private static IEnumerable<Type> SafeGetTypes(Assembly assembly)
    {
        try
        {
            return assembly.GetTypes();
        }
        catch (ReflectionTypeLoadException ex)
        {
            return ex.Types.OfType<Type>();
        }
        catch (Exception)
        {
            return Array.Empty<Type>();
        }
    }
}
