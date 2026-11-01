using System.Reflection;
using Xunit;

namespace StatusCast.Daemon.Tests;

/// <summary>
/// Minimal reflection runner for the xunit-attributed tests in this assembly.
///
/// Why not `dotnet test`: the VSTest testhost aborts in environments where it
/// cannot open a handle to its parent process (observed here with
/// <c>Win32Exception (5)</c>), and this project still needs a green, runnable
/// entry point on the developer machine as well as in CI. The tests themselves
/// are ordinary xunit facts/theories, so they can move back to VSTest unchanged
/// if that host ever becomes usable.
///
/// Usage: <c>dotnet run --project tests-csharp/StatusCast.Daemon.Tests [-c Release] [-- filter]</c>
/// Exit code 0 = all passed, 1 = at least one failure.
/// </summary>
internal static class Program
{
    private static int Main(string[] args)
    {
        var filter = args.Length > 0 ? args[0] : null;
        var assembly = typeof(Program).Assembly;
        var passed = 0;
        var skipped = 0;
        var failures = new List<string>();

        foreach (var type in assembly.GetTypes()
                     .Where(t => t.IsClass && !t.IsAbstract)
                     .OrderBy(t => t.FullName, StringComparer.Ordinal))
        {
            foreach (var method in type.GetMethods(
                         BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static))
            {
                var fact = method.GetCustomAttribute<FactAttribute>();
                var theory = method.GetCustomAttribute<TheoryAttribute>();
                if (fact is null && theory is null)
                {
                    continue;
                }

                var name = $"{type.Name}.{method.Name}";
                if (filter is not null
                    && !name.Contains(filter, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                var explicitSkip = (fact ?? (FactAttribute?)theory)?.Skip;
                if (!string.IsNullOrEmpty(explicitSkip))
                {
                    skipped++;
                    continue;
                }

                var rows = theory is null
                    ? new[] { Array.Empty<object?>() }
                    : method.GetCustomAttributes<InlineDataAttribute>()
                        .SelectMany(attribute => attribute.GetData(method))
                        .ToArray();
                if (rows.Length == 0)
                {
                    failures.Add($"{name}: theory has no InlineData rows");
                    continue;
                }

                foreach (var row in rows)
                {
                    try
                    {
                        var instance = method.IsStatic ? null : Activator.CreateInstance(type);
                        var result = method.Invoke(instance, row);
                        if (result is Task task)
                        {
                            task.GetAwaiter().GetResult();
                        }

                        passed++;
                    }
                    catch (Exception exception)
                    {
                        var inner = exception is TargetInvocationException { InnerException: not null } wrapper
                            ? wrapper.InnerException!
                            : exception;
                        var suffix = theory is null ? string.Empty : $" [{string.Join(", ", row)}]";
                        failures.Add($"{name}{suffix}: {inner.GetType().Name}: {inner.Message}");
                    }
                }
            }
        }

        Console.WriteLine($"C# tests: passed={passed} skipped={skipped} failed={failures.Count}");
        foreach (var failure in failures)
        {
            Console.WriteLine($"  FAIL {failure}");
        }

        return failures.Count == 0 ? 0 : 1;
    }
}
