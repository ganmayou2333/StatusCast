# Changelog

All notable changes to StatusCast are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

- Planned: AMS media metadata, Discord RPC adapter, MSIX packaging and login autostart.

### Added

- `collectors/nekotrak_collector.py`: NekoTrak on this PC becomes a device of
  its own (`platform: "windows"`, `id: "nekotrak"`) with an additive
  `nekotrak` object — version, uptime, TRACKER state, pet, current game and
  `warns`. Liveness is a double check (heartbeat file rewritten within 60 s or
  a live `NekoTrak.exe`); anything unreadable degrades one field plus a
  warning, and only "not running" becomes the standard error entry. The
  collector is registered only when the NekoTrak data root exists, so a
  machine that never ran it keeps byte-identical output.
- `schema/status-contract.json`: new `nekotrak` additive object with its field
  list, asserted on both the Python and the C# side. The daemon decodes and
  passes it through (`NekoTrakStatus`), keeps it in `WithAlias` /
  `ToStaleDevice` clones, and renders `platform: "windows"` with 🖥.
- `GET /health` now answers `{ok, deviceCount, generatedAt, schema}` read from
  the snapshot, with **503** while no usable snapshot exists, and both
  `localhost` and `127.0.0.1` prefixes are registered independently.

- `collectors/iphone_wireless_test.py`: independent iPhone Wi-Fi discovery probe
  (usbmuxd `Network` first, Bonjour/mobdev2 fallback). It is deliberately not
  imported by `status_core.py` and is not a registered collector.
- `collectors/console_collector.py`: C1 LAN (L0) console probe — ARP via
  `iphlpapi.SendARP` with ICMP `ping` fallback, hysteresis persisted to
  `run/console_state.json` (offline only after 2 consecutive failures, one
  success is online again). Reports an additive `console` object (`online` /
  `method` / `lastSeenAt` / `mac` / `idleHint` / `name` / `type` /
  `statusText`); an offline console is a normal `error: null` entry whose
  message comes from the per-console `offlineText` config key (default
  `离线`).
- `collectors/ssh_node_collector.py`: C2b SSH node collector for Mac / Linux
  machines (stdlib `subprocess` + Windows OpenSSH, `BatchMode` key-only,
  `IdentitiesOnly=yes`, `StrictHostKeyChecking=accept-new` TOFU,
  `ConnectTimeout=2`, 8 s cap per node); `sshKey` expands `~` and `%VAR%` and
  a missing path is a config error. Battery and model map onto the standard
  schema-3 fields (Linux multi-battery takes the first battery), desktops
  without batteries are a normal null, not an error. A `node` optional object
  (`ssh` / `method` / `source` / `hostname` / `lastSeenAt` / `uptimeSeconds`)
  rides along in the Python payload.
- `status_core.py` now runs collectors in a `ThreadPoolExecutor` (max 8
  workers) so node timeouts never stack, loads `config.json` (`Consoles` /
  `SshNodes` lists; missing or broken config keeps the v3.3 behaviour), and
  accepts `--config`.
- `tests/`: offline unittest suite with fixtures covering ARP/ping hysteresis,
  SSH output parsing, per-device fault isolation and config loading.
- C# daemon now passes the additive `console` / `node` objects through to
  `run/status.json` and `/status/` instead of dropping them.
- When the Python collection as a whole fails (timeout, crash or non-zero
  exit), the daemon keeps the previous round's devices and marks them
  `error: 读取超时` instead of emptying the snapshot. The runner reports that
  failure explicitly, so a normally disconnected phone (both phone collectors
  erroring) is never mistaken for a stale round.
- Daemon `PlatformIcon` renders `console`/`steam` as 🎮, `macos` as 💻 and
  `linux` as 🐧; hard fallback entries remain ios/android only.
- `docs/iphone-wireless-notes.md`: v3.4 research record, test commands, observed
  failures and the decision to keep USB as the stable iPhone transport.
- Shared LAN probe layer (`lan_probe`, ARP → ICMP ping) reused by the iPhone,
  Android, console and SSH node collectors.
- iPhone: LAN fallback via `IPhone.ip` after a USB failure, plus an experimental
  Wi-Fi data channel behind `wifiDataChannel` (usbmuxd `Network` →
  Bonjour/mobdev2). The Wi-Fi channel defaults to off and is expected to remain
  unavailable with the current Apple services.
- Android: wireless debugging auto-discovery via `adb mdns services` followed by
  `adb connect`, with Android 11+ `adb pair` pairing (`pairPort` / `pairCode`).
  The pairing code is only used for that pairing round and is never logged,
  persisted or written into the snapshot.
- Consoles: TCP port probing (`tcpPorts`, default `[9295, 3074, 80, 443]`,
  300 ms connect timeout per port) after ARP and ICMP both fail.
- SSH nodes: LAN fallback after an SSH failure (`SshNodes[].fallback`) reporting
  degraded online/offline with `error: null` and `node.ssh=false`.
- New optional device-level `transport` object (`active` / `degraded` /
  `online` / `lastSeenAt` / `detail`) and new optional config keys
  `IPhone.{ip,fallback,wifiDataChannel}`,
  `Android.{ip,port,pairPort,pairCode,fallback}`,
  `Consoles[].{tcpPorts,fallback}` and `SshNodes[].fallback`. `fallback`
  defaults to `true`; `false` restores the previous "primary channel failure is
  an error" behaviour.
- `collectors/ble_collector.py`: BLE peripheral collection behind the new
  `BleDevices` config section (`id` / `name` / `address` or `nameFilter` /
  `services` / `heartRateWaitSeconds`). One shared 4 s `BleakScanner` scan per
  round, read-only GATT reads of Battery (`0x180F` / `0x2A19`) and Heart Rate
  (`0x180D` / `0x2A37`), additive `ble` object (`address` / `name` / `rssi` /
  `services` / `connected` / `heartRate` / `statusText` / `lastSeenAt`) and
  `transport.active="ble"`. Scan misses and "found but not connectable" are
  degraded states, not errors.
- `requirements.txt` now declares `bleak==3.0.2` for BLE devices (already
  present in the development venv).
- `tests-csharp/StatusCast.Daemon.Tests`: xunit-based test suite for the daemon
  (device parsing incl. malformed additive objects, `status.json` projection,
  `WithAlias`, `IsAvailable`, stale-row fallback, `ProjectLocator`, request
  routing). Ships a tiny reflection runner (`dotnet run`) because the VSTest
  testhost aborts in restricted shells; the tests are ordinary xunit facts.
- `schema/status-contract.json`: single source of truth for the schema-3 device
  shape (`collectorDeviceFields`, `snapshotDeviceFields`, `additiveObjects`,
  `additiveFields`). Both the Python suite (`tests/test_contract.py`) and the C#
  suite assert their output against it.
- CI: the daemon workflow now builds and runs the C# suite (including the
  loopback `/health` + `/status/` test), and a new `python-tests` workflow runs
  the offline unittest suite on Linux.
- `GET /health` on the daemon's loopback server, returning
  `{"status":"ok","snapshotReady":true|false}`.
- `collectors/base.py` exposes `error_entry()`; the dispatcher's invalid-config
  rows and `BaseCollector.error_payload()` now share one implementation.

### Changed

- `/health` is now actually served (it was logged as `[API] 健康检查 …` before
  but never registered). The server listens on `http://localhost:{port}/` and
  routes `/status/` and `/health`.
- `STATUSCAST_ROOT` is now authoritative: an explicit root without
  `status_core.py` makes the daemon report "找不到项目根目录" instead of silently
  walking up into an unrelated parent checkout and collecting the wrong config.
- Console devices no longer carry an offline `error`: `error` stays `null`
  and the offline state is `console.online=false` with the message in
  `console.statusText`. Consumers that keyed on `error` for consoles must
  switch to `console.online`.
- Documented that iPhone Wi-Fi collection is deferred on Windows: the
  Microsoft Store "Apple Devices" AMDS does not discover Network devices, and
  a classic iTunes + Apple Mobile Device Support 19.4.0.10 installation also
  failed to register a `Network` device on iOS 27.0. Bonjour/mobdev2 can only
  establish lockdown before the `diagnostics_relay` service connection is
  terminated by the device.
- Documented that iPhone Now Playing/media metadata is not available through
  `pymobiledevice3`; the iPhone collector keeps `mediaTitle`/`mediaArtist`/
  `mediaState` as placeholders, while Android media collection is unchanged.

## [3.3.0] - 2026-10-25

### Added

- Pluggable Python collector architecture under `collectors/`:
  `BaseCollector` (`get_data() -> dict`), `CollectorError`,
  `IPhoneCollector` and `AndroidCollector`.
- `status_core.py` is now the single dispatcher: it runs every collector,
  isolates failures per device and prints one standard schema-3 JSON snapshot.
- `status_core_android.py` kept as a legacy STATUS-line compatibility CLI for
  manual diagnostics.

### Changed

- C# `StatusService` now executes one `status_core.py` process and parses its
  JSON `devices` array instead of running two Python scripts and matching text
  with a regex.
- `StatusCoordinator` consumes the parsed device list, applies aliases and
  writes the snapshot; collection errors no longer abort the whole refresh.
- Bumped the daemon to `<Version>3.3.0</Version>`; startup log and update
  checker user agent follow the assembly version.

### Removed

- Duplicate per-device Python entry points from the daemon call path; all device
  collection now flows through the collector plugins.

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
