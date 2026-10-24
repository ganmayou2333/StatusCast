# Changelog

All notable changes to StatusCast are documented here.

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
