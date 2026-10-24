# StatusCast Plugins

A StatusCast plugin is a .NET class library that references
`StatusCast.Core` and implements `IStatusPlugin`.

```csharp
using StatusCast.Core;

public sealed class MyPlugin : IStatusPlugin
{
    public string PluginName => "MyPlugin";

    public Task<string> FetchStatusAsync(CancellationToken token)
        => Task.FromResult("hello");
}
```

## Build and install

Build the plugin project, then copy its DLL into the repository-level
`plugins/` directory. The WinUI app loads `plugins/*.dll` at startup and
keeps only the names listed in `config.json`:

```json
{
  "EnabledPlugins": ["Clock"]
}
```

Use the included sample:

```powershell
.\scripts\build_plugins.ps1
```

This builds `plugins/ClockPlugin` and copies `ClockPlugin.dll` next to the
plugin loader. Restart StatusCast to pick it up.

Plugin status strings are collected with a 3-second timeout and exposed in the
v2 snapshot under the `plugins` array.
