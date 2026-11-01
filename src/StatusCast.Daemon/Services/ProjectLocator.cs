using System;
using System.IO;

namespace StatusCast.Daemon.Services;

public static class ProjectLocator
{
    /// <summary>
    /// Resolves the directory that owns <c>status_core.py</c>.
    ///
    /// <c>STATUSCAST_ROOT</c> is authoritative when set: it is used as-is and a
    /// missing script returns <c>null</c> instead of silently walking up into an
    /// unrelated parent checkout (that used to switch the daemon onto the wrong
    /// config/snapshot while the caller still thought it was testing a temp root).
    /// Without the variable we walk up from the binary directory and then the
    /// working directory, capped at 12 levels.
    /// </summary>
    public static string? FindRoot()
    {
        var explicitRoot = Environment.GetEnvironmentVariable("STATUSCAST_ROOT");
        if (!string.IsNullOrWhiteSpace(explicitRoot))
        {
            return HasStatusCore(explicitRoot) ? SafeFullPath(explicitRoot) : null;
        }

        foreach (var start in new[] { AppContext.BaseDirectory, Environment.CurrentDirectory })
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
                if (File.Exists(Path.Combine(directory.FullName, "status_core.py")))
                {
                    return directory.FullName;
                }
            }
        }

        return null;
    }

    private static bool HasStatusCore(string path)
    {
        try
        {
            var full = Path.GetFullPath(path);
            return File.Exists(Path.Combine(full, "status_core.py"));
        }
        catch (Exception)
        {
            return false;
        }
    }

    private static string? SafeFullPath(string path)
    {
        try
        {
            return Path.GetFullPath(path);
        }
        catch (Exception)
        {
            return null;
        }
    }
}
