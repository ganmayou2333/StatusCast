"""Configuration loading for StatusCast.

A single ``config.toml`` drives sources and adapters. Relative paths are
resolved against the directory containing that file, which keeps the daemon
predictable regardless of the current working directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]


class ConfigError(ValueError):
    """Raised when config.toml has an invalid shape or value."""


@dataclass(slots=True)
class AppConfig:
    poll_interval: float = 5.0
    log_level: str = "INFO"
    log_file: str | None = "run/statuscast.log"
    stop_file: str | None = "run/stop.request"


@dataclass(slots=True)
class BatteryConfig:
    enabled: bool = True
    serial: str | None = None
    timeout: float = 20.0
    poll_interval: float = 10.0


@dataclass(slots=True)
class MediaConfig:
    enabled: bool = True
    source: str = "ams"
    address: str | None = None
    pair: bool = False
    scan_timeout: float = 8.0
    retry_interval: float = 10.0
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_topic: str = "shairport-sync/metadata"


@dataclass(slots=True)
class AdapterConfig:
    enabled: bool = False
    path: str | None = None


@dataclass(slots=True)
class Config:
    app: AppConfig = field(default_factory=AppConfig)
    battery: BatteryConfig = field(default_factory=BatteryConfig)
    media: MediaConfig = field(default_factory=MediaConfig)
    stdout: AdapterConfig = field(default_factory=lambda: AdapterConfig(enabled=True))
    snapshot: AdapterConfig = field(
        default_factory=lambda: AdapterConfig(enabled=True, path="run/status.json")
    )
    nekotrak: AdapterConfig = field(default_factory=AdapterConfig)
    base_dir: Path = field(default_factory=Path.cwd)

    @classmethod
    def load(cls, path: str | Path | None = "config.toml") -> "Config":
        config_path = Path(path) if path else None
        if config_path is None or not config_path.exists():
            return cls(base_dir=Path.cwd())

        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)
        if not isinstance(raw, dict):
            raise ConfigError("config.toml 顶层必须是表（table）")

        base_dir = config_path.resolve().parent
        app_raw = _section(raw, "app")
        battery_raw = _section(raw, "battery")
        media_raw = _section(raw, "media")
        adapters_raw = _section(raw, "adapters")

        log_file = _optional_text(app_raw, "log_file")
        if "log_file" not in app_raw:
            log_file = "run/statuscast.log"
        stop_file = _optional_text(app_raw, "stop_file")
        if "stop_file" not in app_raw:
            stop_file = "run/stop.request"
        app = AppConfig(
            poll_interval=_number(app_raw, "poll_interval", 5.0, minimum=0.2),
            log_level=_text(app_raw, "log_level", "INFO"),
            log_file=log_file,
            stop_file=stop_file,
        )
        battery = BatteryConfig(
            enabled=_boolean(battery_raw, "enabled", True),
            serial=_optional_text(battery_raw, "serial"),
            timeout=_number(battery_raw, "timeout", 20.0, minimum=1.0),
            poll_interval=_number(battery_raw, "poll_interval", 10.0, minimum=0.5),
        )
        media = MediaConfig(
            enabled=_boolean(media_raw, "enabled", True),
            source=_text(media_raw, "source", "ams").lower(),
            address=_optional_text(media_raw, "address"),
            pair=_boolean(media_raw, "pair", False),
            scan_timeout=_number(media_raw, "scan_timeout", 8.0, minimum=1.0),
            retry_interval=_number(media_raw, "retry_interval", 10.0, minimum=1.0),
            mqtt_host=_text(media_raw, "mqtt_host", "127.0.0.1"),
            mqtt_port=_integer(media_raw, "mqtt_port", 1883, minimum=1, maximum=65535),
            mqtt_topic=_text(
                media_raw, "mqtt_topic", "shairport-sync/metadata"
            ),
        )
        if media.source not in {"ams", "airplay_mqtt", "none"}:
            raise ConfigError(
                f"不支持的 media.source: {media.source!r}"
                "（支持 ams/airplay_mqtt/none）"
            )

        stdout = _adapter(adapters_raw, "stdout", enabled=True)
        snapshot = _adapter(adapters_raw, "snapshot", enabled=True, path="run/status.json")
        nekotrak = _adapter(adapters_raw, "nekotrak", enabled=False)

        return cls(
            app=app,
            battery=battery,
            media=media,
            stdout=stdout,
            snapshot=snapshot,
            nekotrak=nekotrak,
            base_dir=base_dir,
        )

    def resolve_path(self, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.base_dir / path


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] 必须是一个表（table）")
    return value


def _text(raw: dict[str, Any], key: str, default: str) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str):
        raise ConfigError(f"{key} 必须是字符串")
    return value


def _optional_text(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{key} 必须是字符串")
    return value


def _number(
    raw: dict[str, Any],
    key: str,
    default: float,
    *,
    minimum: float | None = None,
) -> float:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{key} 必须是数字")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ConfigError(f"{key} 必须 >= {minimum:g}")
    return result


def _integer(
    raw: dict[str, Any],
    key: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} 必须是整数")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{key} 必须 >= {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{key} 必须 <= {maximum}")
    return value


def _boolean(raw: dict[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} 必须是 true/false")
    return value


def _adapter(
    raw: dict[str, Any],
    name: str,
    *,
    enabled: bool,
    path: str | None = None,
) -> AdapterConfig:
    section = _section(raw, name)
    return AdapterConfig(
        enabled=_boolean(section, "enabled", enabled),
        path=_optional_text(section, "path") or path,
    )
