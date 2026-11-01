using System;
using System.IO;
using StatusCast.Daemon.Services;
using Xunit;

namespace StatusCast.Daemon.Tests;

public sealed class ProjectLocatorTests
{
    private static string CreateTempRoot(bool withScript)
    {
        var path = TestTemp.Create("locator");
        if (withScript)
        {
            File.WriteAllText(Path.Combine(path, "status_core.py"), string.Empty);
        }

        return path;
    }

    private static string? WithRoot(string? value, Func<string?> action)
    {
        var previous = Environment.GetEnvironmentVariable("STATUSCAST_ROOT");
        try
        {
            Environment.SetEnvironmentVariable("STATUSCAST_ROOT", value);
            return action();
        }
        finally
        {
            Environment.SetEnvironmentVariable("STATUSCAST_ROOT", previous);
        }
    }

    [Fact]
    public void ExplicitRootWithScriptIsReturned()
    {
        var root = CreateTempRoot(withScript: true);
        try
        {
            var resolved = WithRoot(root, ProjectLocator.FindRoot);
            Assert.Equal(Path.GetFullPath(root), resolved);
        }
        finally
        {
            TestTemp.Delete(root);
        }
    }

    [Fact]
    public void ExplicitRootWithoutScriptReturnsNullInsteadOfWalkingUp()
    {
        var parent = CreateTempRoot(withScript: false);
        var child = Path.Combine(parent, "nested");
        Directory.CreateDirectory(child);
        File.WriteAllText(Path.Combine(parent, "status_core.py"), string.Empty);
        try
        {
            // The parent HAS the script; a strict explicit root must not climb to it.
            Assert.Null(WithRoot(child, ProjectLocator.FindRoot));
        }
        finally
        {
            TestTemp.Delete(parent);
        }
    }

    [Fact]
    public void MissingExplicitRootReturnsNull()
    {
        var missing = Path.Combine(
            AppContext.BaseDirectory, "tmp-tests",
            "missing-" + Guid.NewGuid().ToString("N"));
        Assert.Null(WithRoot(missing, ProjectLocator.FindRoot));
    }
}
