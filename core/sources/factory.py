"""Composition helpers for source implementations.

Keeping construction out of the daemon makes adding a source a one-file change:
implement the interface, register it here, and expose a config value.
"""

from __future__ import annotations

from core.config import Config
from core.sources.base import BatterySource, MediaSource
from core.sources.battery_usb import BatteryUSB
from core.sources.media_airplay_mqtt import AirPlayMqttMediaSource
from core.sources.media_ams import AmsMediaSource


def build_battery_source(config: Config) -> BatterySource | None:
    if not config.battery.enabled:
        return None
    return BatteryUSB(
        serial=config.battery.serial,
        timeout=config.battery.timeout,
    )


def build_media_source(config: Config) -> MediaSource | None:
    if not config.media.enabled or config.media.source == "none":
        return None
    if config.media.source == "ams":
        return AmsMediaSource(
            address=config.media.address,
            pair=config.media.pair,
            scan_timeout=config.media.scan_timeout,
            retry_interval=config.media.retry_interval,
        )
    if config.media.source == "airplay_mqtt":
        return AirPlayMqttMediaSource(
            host=config.media.mqtt_host,
            port=config.media.mqtt_port,
            topic=config.media.mqtt_topic,
            retry_interval=config.media.retry_interval,
        )
    raise ValueError(f"不支持的 media.source: {config.media.source!r}")
