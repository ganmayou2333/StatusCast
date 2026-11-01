using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace StatusCast.Daemon.Services;

public sealed class StatusServer : IDisposable
{
    public const int Port = 5099;

    private readonly object _gate = new();
    private readonly string _snapshotPath;
    private readonly int _port;
    private HttpListener? _listener;
    private CancellationTokenSource? _cancellation;
    private Task? _listenTask;

    public StatusServer(int port = Port)
    {
        _port = port;
        _snapshotPath = ResolveSnapshotPath();
    }

    public bool IsRunning { get; private set; }

    public string? LastError { get; private set; }

    public bool Start()
    {
        lock (_gate)
        {
            if (IsRunning)
            {
                return true;
            }

            var listener = new HttpListener();
            // One prefix for the whole loopback origin: both /status/ and the
            // /health probe are routed in HandleRequestAsync.
            listener.Prefixes.Add($"http://localhost:{_port}/");
            try
            {
                listener.Start();
            }
            catch (Exception ex)
            {
                LastError = ex.Message;
                Debug.WriteLine($"[StatusServer] start failed: {ex}");
                try
                {
                    listener.Close();
                }
                catch (Exception)
                {
                }

                return false;
            }

            _listener = listener;
            _cancellation = new CancellationTokenSource();
            _listenTask = Task.Run(() => ListenLoopAsync(listener, _cancellation.Token));
            IsRunning = true;
            Debug.WriteLine($"[StatusServer] listening on http://localhost:{_port}/");
            return true;
        }
    }

    public void Stop()
    {
        lock (_gate)
        {
            if (!IsRunning)
            {
                return;
            }

            IsRunning = false;
            try
            {
                _cancellation?.Cancel();
            }
            catch (ObjectDisposedException)
            {
            }

            try
            {
                _listener?.Stop();
                _listener?.Close();
            }
            catch (Exception ex)
            {
                Debug.WriteLine($"[StatusServer] stop failed: {ex}");
            }

            var task = _listenTask;
            if (task is not null)
            {
                try
                {
                    task.Wait(TimeSpan.FromSeconds(2));
                }
                catch (AggregateException)
                {
                }
            }

            _cancellation?.Dispose();
            _cancellation = null;
            _listener = null;
            _listenTask = null;
        }
    }

    public void Dispose()
    {
        Stop();
    }

    private async Task ListenLoopAsync(HttpListener listener, CancellationToken cancellationToken)
    {
        while (!cancellationToken.IsCancellationRequested)
        {
            HttpListenerContext context;
            try
            {
                context = await listener.GetContextAsync();
            }
            catch (HttpListenerException) when (cancellationToken.IsCancellationRequested)
            {
                break;
            }
            catch (ObjectDisposedException)
            {
                break;
            }
            catch (Exception ex)
            {
                if (!cancellationToken.IsCancellationRequested)
                {
                    Debug.WriteLine($"[StatusServer] accept failed: {ex}");
                }

                break;
            }

            _ = Task.Run(() => HandleRequestAsync(context, cancellationToken), cancellationToken);
        }
    }

    /// <summary>Outcome of routing one request; kept pure so tests need no socket.</summary>
    public enum RouteKind
    {
        Health,
        Status,
        MethodNotAllowed,
        NotFound,
    }

    internal static RouteKind RouteFor(string? httpMethod, string? absolutePath)
    {
        if (!string.Equals(httpMethod, "GET", StringComparison.OrdinalIgnoreCase))
        {
            return RouteKind.MethodNotAllowed;
        }

        return absolutePath switch
        {
            "/health" => RouteKind.Health,
            "/status/" => RouteKind.Status,
            _ => RouteKind.NotFound,
        };
    }

    private async Task HandleRequestAsync(HttpListenerContext context, CancellationToken cancellationToken)
    {
        var response = context.Response;
        try
        {
            response.Headers["Access-Control-Allow-Origin"] = "*";
            response.Headers["Cache-Control"] = "no-store";

            var path = context.Request.Url?.AbsolutePath;
            switch (RouteFor(context.Request.HttpMethod, path))
            {
                case RouteKind.MethodNotAllowed:
                    await WriteJsonAsync(
                        response, 405, "{\"error\":\"method not allowed\"}", cancellationToken);
                    return;

                case RouteKind.Health:
                    var snapshotReady = File.Exists(_snapshotPath);
                    await WriteJsonAsync(
                        response,
                        200,
                        snapshotReady
                            ? "{\"status\":\"ok\",\"snapshotReady\":true}"
                            : "{\"status\":\"ok\",\"snapshotReady\":false}",
                        cancellationToken);
                    return;

                case RouteKind.NotFound:
                    await WriteJsonAsync(
                        response, 404, "{\"error\":\"not found\"}", cancellationToken);
                    return;
            }

            byte[] body;
            try
            {
                var json = await File.ReadAllTextAsync(_snapshotPath, cancellationToken);
                body = Encoding.UTF8.GetBytes(json);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                await WriteJsonAsync(
                    response,
                    503,
                    "{\"error\":\"status snapshot not ready\"}",
                    cancellationToken);
                return;
            }

            response.StatusCode = 200;
            response.ContentType = "application/json; charset=utf-8";
            response.ContentEncoding = Encoding.UTF8;
            response.ContentLength64 = body.Length;
            await response.OutputStream.WriteAsync(body, cancellationToken);
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"[StatusServer] request failed: {ex}");
        }
        finally
        {
            try
            {
                response.Close();
            }
            catch (Exception)
            {
            }
        }
    }

    private static async Task WriteJsonAsync(
        HttpListenerResponse response,
        int statusCode,
        string json,
        CancellationToken cancellationToken)
    {
        var body = Encoding.UTF8.GetBytes(json);
        response.StatusCode = statusCode;
        response.ContentType = "application/json; charset=utf-8";
        response.ContentEncoding = Encoding.UTF8;
        response.ContentLength64 = body.Length;
        await response.OutputStream.WriteAsync(body, cancellationToken);
    }

    private static string ResolveSnapshotPath()
    {
        var root = ProjectLocator.FindRoot();
        return Path.Combine(root ?? AppContext.BaseDirectory, "run", "status.json");
    }
}
