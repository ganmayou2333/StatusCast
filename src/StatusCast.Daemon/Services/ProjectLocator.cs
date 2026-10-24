using System;
using System.IO;

namespace StatusCast.Daemon.Services;

public static class ProjectLocator
{
    public static string? FindRoot()
    {
        var starts = new[]
        {
            Environment.GetEnvironmentVariable("STATUSCAST_ROOT"),
            AppContext.BaseDirectory,
            Environment.CurrentDirectory,
        };

        foreach (var start in starts)
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
}
