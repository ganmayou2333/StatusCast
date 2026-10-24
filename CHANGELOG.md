# Changelog

All notable changes to StatusCast are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

- Planned: AMS media metadata, Discord RPC adapter, MSIX packaging and login autostart.

## [3.2.0] - 2026-10-25

### Added

- Background GitHub Release check at startup: compares `tag_name` with the
  assembly version, prints a colored `[UPDATE]` notice and the first three
  lines of the release `body`. It never blocks collection and never downloads
  or overwrites the running daemon.
- Android foreground app lookup now cross-checks two `dumpsys` views:
  `dumpsys activity activities` (`topResumedActivity` / `mResumedActivity`)
  first, then `dumpsys window displays` (`mCurrentFocus`) as a fallback.
- `<Version>3.2.0</Version>` in `StatusCast.Daemon.csproj`; startup log now
  reports `v3.2.0`.

### Changed

- Android foreground app parsing is more tolerant of OEM/Android-version
  output differences and ignores transient system packages.

### Removed

- Legacy Python daemon architecture: `core/`, `adapters/`, `cli/`, `legacy/`,
  `integrations/`, `tests/`, `scripts/`.
- Stale build/config artifacts: `build/`, `dist/`, `run/`, `pyproject.toml`,
  `config.toml`, `statuscast-cli.spec`, `requirements-build.txt` and
  `docs/architecture.md`.
- WinUI 3 remnants and any GUI/frontend code; StatusCast is now strictly the
  C# daemon plus the two Python collectors.

## [3.1.0] - 2026-10-24

### Changed

- Removed the WinUI 3 GUI and all Windows App SDK dependencies.
- Added the headless `StatusCast.Daemon` console application.
- Moved `StatusService`, `StatusCoordinator` and `StatusServer` into
  `src/StatusCast.Daemon/Services`.
- Added colored startup/collection logs and graceful Ctrl+C shutdown.
- NekoTool now targets `StatusCast.Daemon.exe` for watchdog startup.

## [3.0.0] - 2026-10-24

### Changed

- StatusCast is now a pure data layer: removed `StatusCast.Core`,
  `IStatusPlugin` and dynamic DLL loading.
- Snapshot schema is now `3`; `status.json` contains only `schema`,
  `generatedAt` and the `devices` array.
- Removed top-level `battery`, `media`, `plugins`, `errors` and `text` fields.
- `config.json` now only contains `Aliases`.
- Plugin and aggregation logic moved to the separate
  [NekoTool](https://github.com/ganmayou2333/NekoTool) repository.

## [2.0.0] - 2026-10-24

### Added

- Android collection via ADB (`status_core_android.py`): battery, model,
  foreground app and best-effort media session.
- Parallel iPhone + Android collection with `Task.WhenAll` and a v2 snapshot
  containing a `devices` array.
- `config.json` with `Aliases` and `EnabledPlugins`.
- WinUI multi-device dashboard, aliases and a plugin status chip.

### Known issues

- Realtek Bluetooth adapter cannot complete BLE pairing with the test iPhone;
  AMS returns `Insufficient Authentication`.
- Android media metadata is best-effort and depends on the media app exposing
  a MediaSession.

## [1.0.0] - 2026-10-23

### Added

- First public stable release.
- USB battery, charging and iPhone model collection via `pymobiledevice3`.
- WinUI 3 desktop app with a dynamic battery arc and 30-second refresh.
- Local HTTP API at `http://localhost:5099/status/` and NekoTrak integration.
- MIT license.

## [0.2.0] - 2026-10-23

### Added

- Initial USB collector and WinUI 3 prototype.
- `run/status.json` snapshot and the local HTTP API.
