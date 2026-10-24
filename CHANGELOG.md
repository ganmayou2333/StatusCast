# Changelog

All notable changes to StatusCast are documented here.

## [3.1.0] - 2026-10-24

### Changed

- Removed the WinUI 3 GUI and all Windows App SDK dependencies.
- Added the headless `StatusCast.Daemon` console application.
- Moved `StatusService`, `AndroidStatusService`, `StatusCoordinator` and `StatusServer` into `src/StatusCast.Daemon/Services`.
- Added colored startup/collection logs and graceful Ctrl+C shutdown.
- Added `scripts/run_daemon.ps1` and silent/service deployment notes.
- NekoTool now targets `StatusCast.Daemon.exe` for watchdog startup.

## [3.0.0] - 2026-10-24

### Changed

- StatusCast is now a pure data layer: removed `StatusCast.Core`, `IStatusPlugin` and dynamic DLL loading.
- Snapshot schema is now `3`; `status.json` contains only `schema`, `generatedAt` and the `devices` array.
- Removed top-level `battery`, `media`, `plugins`, `errors` and `text` fields from the snapshot.
- `config.json` now only contains `Aliases`.
- Plugin and aggregation logic moved to the separate [NekoTool](https://github.com/ganmayou2333/NekoTool) repository.

## [2.0.0] - 2026-10-24

### Added

- Android collection via ADB (`status_core_android.py`): battery, model, foreground app and best-effort media session.
- Parallel iPhone + Android collection with `Task.WhenAll` and a v2 snapshot containing a `devices` array.
- `config.json` with `Aliases` and `EnabledPlugins`.
- `StatusCast.Core` plugin contract (`IStatusPlugin`) and dynamic DLL loading from `plugins/`.
- Sample `ClockPlugin`, plugin build script, and a plugin status chip in the WinUI header.
- Android secondary device card in the WinUI dashboard.
- Multi-device aliases such as `iPhone15,3 -> 主力机`.

### Changed

- Snapshot schema is now `2`; legacy `battery`, `media` and `text` fields remain for NekoTool clients.
- `StatusService` only collects iPhone data; `StatusCoordinator` writes the combined snapshot.

### Known issues

- Realtek Bluetooth adapter cannot complete BLE pairing with the test iPhone; AMS returns `Insufficient Authentication`.
- Android media metadata is best-effort and depends on the media app exposing a MediaSession.

## [0.2.0] - 2026-10-23

### Added

- USB battery, charging and iPhone model collection via `pymobiledevice3`.
- WinUI 3 desktop app with MVVM, Mica backdrop, data binding and a dynamic battery arc.
- Single acquisition source: `StatusService` refreshes every 30 seconds and atomically writes `run/status.json`.
- Local HTTP API at `http://localhost:5099/status/` serving the latest JSON snapshot.
- NekoTrak integration through the HTTP endpoint.
- MIT license.

### Known issues

- Realtek Bluetooth adapter cannot complete BLE pairing with the test iPhone; AMS returns `Insufficient Authentication`.
- Apple Media Service (AMS) and Discord Rich Presence are not integrated yet.

## [Unreleased]

- Planned: AMS media metadata, Discord RPC adapter, MSIX packaging and login autostart.
