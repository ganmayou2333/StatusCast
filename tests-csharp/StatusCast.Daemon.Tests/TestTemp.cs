using System;
using System.IO;

namespace StatusCast.Daemon.Tests;

/// <summary>
/// Temp directories for tests. The OS temp directory is not always writable
/// (sandboxed shells can deny it), so tests create their scratch directories
/// under the test binary folder instead and always clean them up.
/// </summary>
internal static class TestTemp
{
    public static string Create(string prefix)
    {
        var root = Path.Combine(
            AppContext.BaseDirectory, "tmp-tests",
            $"{prefix}-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        return root;
    }

    public static void Delete(string? path)
    {
        if (string.IsNullOrEmpty(path))
        {
            return;
        }

        try
        {
            if (Directory.Exists(path))
            {
                Directory.Delete(path, recursive: true);
            }
        }
        catch (IOException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }
    }
}
