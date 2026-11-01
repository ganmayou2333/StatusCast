using System;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using System.Threading.Tasks;
using StatusCast.Daemon.Services;
using Xunit;

namespace StatusCast.Daemon.Tests;

public sealed class StatusServerTests
{
    [Theory]
    [InlineData("GET", "/health", StatusServer.RouteKind.Health)]
    [InlineData("get", "/health", StatusServer.RouteKind.Health)]
    [InlineData("GET", "/status/", StatusServer.RouteKind.Status)]
    [InlineData("GET", "/status", StatusServer.RouteKind.NotFound)]
    [InlineData("GET", "/nope", StatusServer.RouteKind.NotFound)]
    [InlineData("GET", null, StatusServer.RouteKind.NotFound)]
    [InlineData("POST", "/status/", StatusServer.RouteKind.MethodNotAllowed)]
    [InlineData("OPTIONS", "/health", StatusServer.RouteKind.MethodNotAllowed)]
    [InlineData(null, "/health", StatusServer.RouteKind.MethodNotAllowed)]
    public void RoutesRequests(string? method, string? path, StatusServer.RouteKind expected)
    {
        Assert.Equal(expected, StatusServer.RouteFor(method, path));
    }

    /// <summary>
    /// End-to-end socket check. Skipped by default because some sandboxed
    /// shells deny HTTP.sys request-queue creation to non build-output binaries
    /// (`HttpListenerException: 句柄无效`), which would make the suite
    /// environment-dependent. Run manually with STATUSCAST_NETWORK_TESTS=1
    /// (verified against the real daemon: GET /health → 200 with snapshotReady).
    /// </summary>
    [Fact]
    public async Task HealthAndStatusOverLoopback()
    {
        if (Environment.GetEnvironmentVariable("STATUSCAST_NETWORK_TESTS") != "1")
        {
            Console.WriteLine("  (skipped HealthAndStatusOverLoopback: set STATUSCAST_NETWORK_TESTS=1)");
            return;
        }

        var root = TestTemp.Create("server");
        File.WriteAllText(Path.Combine(root, "status_core.py"), string.Empty);
        var previous = Environment.GetEnvironmentVariable("STATUSCAST_ROOT");
        StatusServer? server = null;
        try
        {
            Environment.SetEnvironmentVariable("STATUSCAST_ROOT", root);
            var port = FreePort();
            server = new StatusServer(port);
            Assert.True(server.Start(), server.LastError);

            using var client = new HttpClient
            {
                BaseAddress = new Uri($"http://localhost:{port}/"),
                Timeout = TimeSpan.FromSeconds(10),
            };

            var health = await client.GetAsync("health");
            Assert.Equal(HttpStatusCode.OK, health.StatusCode);
            Assert.Contains("\"snapshotReady\":false", await health.Content.ReadAsStringAsync());

            Directory.CreateDirectory(Path.Combine(root, "run"));
            await File.WriteAllTextAsync(
                Path.Combine(root, "run", "status.json"),
                "{\"schema\":3,\"devices\":[]}");

            Assert.Contains("\"snapshotReady\":true", await client.GetStringAsync("health"));
            Assert.Equal(
                "{\"schema\":3,\"devices\":[]}",
                await client.GetStringAsync("status/"));

            Assert.Equal(HttpStatusCode.NotFound, (await client.GetAsync("nope")).StatusCode);

            using var post = new HttpRequestMessage(HttpMethod.Post, "status/")
            {
                // HttpListener/http.sys answers 411 before our handler when a
                // POST has no Content-Length, so send an explicit empty body.
                Content = new StringContent(string.Empty),
            };
            Assert.Equal(HttpStatusCode.MethodNotAllowed, (await client.SendAsync(post)).StatusCode);
        }
        finally
        {
            server?.Stop();
            Environment.SetEnvironmentVariable("STATUSCAST_ROOT", previous);
            TestTemp.Delete(root);
        }
    }

    private static int FreePort()
    {
        using var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        var port = ((IPEndPoint)probe.LocalEndpoint).Port;
        probe.Stop();
        return port;
    }
}
