#!/usr/bin/env python3
import asyncio
import sys

from bleak import BleakClient, BleakScanner
AMS_SERVICE = "89d3502b-0f36-433a-8ef4-c502ad55f8dc"
AMS_UPDATE = "2f7cabce-808d-411f-9a0c-bb92ba96c102"
AMS_REMOTE = "9b3c81d8-57b1-4a8a-b8df-0e56f7ca51c2"
# Leave both empty to auto-detect an AMS-capable Apple device.
TARGET_NAME = ""
TARGET_ADDRESS = ""
PLAYER, TRACK = 0, 2
PLAYBACK_INFO, ARTIST, TITLE = 1, 0, 2
STATE_NAMES = {0: "paused", 1: "playing"}


def parse_notification(data: bytearray, media: dict) -> None:
    raw = bytes(data)
    if len(raw) < 3:
        return
    entity, attr = raw[0], raw[1]
    value = raw[3:].decode("utf-8", errors="replace")
    if entity == TRACK and attr == TITLE:
        media["title"] = value
    elif entity == TRACK and attr == ARTIST:
        media["artist"] = value
    elif entity == PLAYER and attr == PLAYBACK_INFO:
        try:
            media["state"] = STATE_NAMES.get(int(value.split(",")[0]), "unknown")
        except (ValueError, IndexError):
            pass


async def find_ams():
    found = await BleakScanner.discover(timeout=8.0, return_adv=True)
    for device, adv in found.values():
        if TARGET_ADDRESS and device.address.upper() != TARGET_ADDRESS.upper():
            continue
        if TARGET_NAME and TARGET_NAME.lower() not in (device.name or "").lower():
            continue
        if not TARGET_ADDRESS and 0x004C not in (adv.manufacturer_data or {}):
            continue
        client = BleakClient(device, timeout=20)
        try:
            await client.connect()
            if client.services.get_service(AMS_SERVICE):
                return client
        except Exception:
            pass
        try:
            if client.is_connected:
                await client.disconnect()
        except Exception:
            pass
    return None

async def main() -> int:
    client = None
    try:
        client = await find_ams()
        if client is None:
            raise RuntimeError("no AMS-capable iPhone found")
        media = {"title": None, "artist": None, "state": "unknown"}
        await client.start_notify(
            AMS_UPDATE, lambda _c, data: parse_notification(data, media)
        )
        try:
            await client.start_notify(AMS_REMOTE, lambda *_: None)
        except Exception:
            pass
        await client.write_gatt_char(
            AMS_UPDATE, bytes([PLAYER, PLAYBACK_INFO]), response=True
        )
        await client.write_gatt_char(
            AMS_UPDATE, bytes([TRACK, ARTIST, TITLE]), response=True
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 6.0
        while loop.time() < deadline and not (media["title"] and media["artist"]):
            await asyncio.sleep(0.2)
        if not media["title"]:
            raise RuntimeError("no media information received")
        title = media["title"].replace("|", "/")
        artist = (media["artist"] or "NA").replace("|", "/")
        print(f"MEDIA:{title}|{artist}|{media['state']}")
        return 0
    except Exception as exc:
        print("MEDIA:NA|NA|unknown")
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if client is not None and client.is_connected:
            try:
                await client.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
