using System;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Reflection;
using System.Text.Json;
using System.Threading.Tasks;

namespace StatusCast.Daemon.Services;

/// <summary>
/// Best-effort GitHub Release check. Runs in the background and never blocks
/// startup or collection; all failures are swallowed on purpose.
/// </summary>
internal static class UpdateChecker
{
    private const string ReleasesApi =
        "https://api.github.com/repos/ganmayou2333/StatusCast/releases/latest";

    private static readonly HttpClient Http = CreateHttpClient();

    public static Task CheckForUpdatesAsync()
    {
        return RunAsync();
    }

    private static HttpClient CreateHttpClient()
    {
        var client = new HttpClient
        {
            Timeout = TimeSpan.FromSeconds(8),
        };
        client.DefaultRequestHeaders.UserAgent.Add(
            new ProductInfoHeaderValue("StatusCast-Daemon", GetCurrentVersion().ToString()));
        client.DefaultRequestHeaders.Accept.Add(
            new MediaTypeWithQualityHeaderValue("application/vnd.github+json"));
        return client;
    }

    private static async Task RunAsync()
    {
        try
        {
            using var response = await Http.GetAsync(ReleasesApi).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode)
            {
                return;
            }

            await using var stream = await response.Content
                .ReadAsStreamAsync()
                .ConfigureAwait(false);

            using var document = await JsonDocument
                .ParseAsync(stream)
                .ConfigureAwait(false);

            var root = document.RootElement;
            if (!root.TryGetProperty("tag_name", out var tagElement))
            {
                return;
            }

            var tag = tagElement.GetString();
            if (!TryParseVersion(tag, out var latestVersion))
            {
                return;
            }

            var currentVersion = GetCurrentVersion();
            if (latestVersion <= currentVersion)
            {
                return;
            }

            var body = root.TryGetProperty("body", out var bodyElement)
                ? bodyElement.GetString()
                : null;

            WriteUpdateNotice(tag!, body);
        }
        catch (Exception)
        {
            // An update check must never take down the daemon.
        }
    }

    private static Version GetCurrentVersion()
    {
        var version = Assembly.GetExecutingAssembly().GetName().Version
            ?? new Version(0, 0, 0, 0);
        return Normalize(version);
    }

    private static Version Normalize(Version version)
    {
        return new Version(
            version.Major,
            version.Minor,
            Math.Max(version.Build, 0));
    }

    private static bool TryParseVersion(string? tag, out Version version)
    {
        version = new Version(0, 0, 0, 0);
        if (string.IsNullOrWhiteSpace(tag))
        {
            return false;
        }

        var text = tag.Trim();
        if (text.StartsWith("v", StringComparison.OrdinalIgnoreCase))
        {
            text = text[1..];
        }

        // Drop pre-release / build metadata: v3.2.1-beta.1 -> 3.2.1
        var suffix = text.IndexOfAny(new[] { '-', '+' });
        if (suffix >= 0)
        {
            text = text[..suffix];
        }

        if (!Version.TryParse(text, out var parsed) || parsed is null)
        {
            return false;
        }

        version = Normalize(parsed);
        return true;
    }

    private static void WriteUpdateNotice(string tag, string? body)
    {
        var lines = string.IsNullOrWhiteSpace(body)
            ? Array.Empty<string>()
            : body
                .Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries)
                .Select(line => line.Trim())
                .Where(line => line.Length > 0)
                .Take(3)
                .ToArray();

        var previousColor = Console.ForegroundColor;
        try
        {
            Console.ForegroundColor = ConsoleColor.Yellow;
            Console.WriteLine(
                $"[UPDATE] 发现新版本 {tag}！请前往 GitHub Release 下载最新 Daemon。");

            if (lines.Length > 0)
            {
                Console.WriteLine("[UPDATE] 更新内容：");
                foreach (var line in lines)
                {
                    Console.WriteLine($"[UPDATE]   {line}");
                }
            }

            Console.WriteLine("[UPDATE] 本次不会自动下载或覆盖正在运行的程序。");
        }
        finally
        {
            Console.ForegroundColor = previousColor;
        }
    }
}
