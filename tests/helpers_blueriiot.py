"""Shared test utilities for Blueriiot probes: frame builder, GATT layout, fake probe.

Kept apart from helpers.py (Zodiac) on purpose. The decoder and the profile are
pure modules, loaded by file path (see _load_pure).
"""

from __future__ import annotations

from types import SimpleNamespace

from bleak.exc import BleakError

from ._load_pure import load_pure_module
from .helpers import FakeBlueClient

PROTOCOL = load_pure_module("protocol_blueriiot.py")
PROFILE = load_pure_module("profile_blueriiot.py")

FRAME_HEX_LEN = PROTOCOL.FRAME_HEX_LEN

AUTH = PROFILE.BLUERIIOT_CHAR_AUTH_UUID
TRIGGER = PROFILE.BLUERIIOT_CHAR_TRIGGER_UUID
NOTIFY_1, NOTIFY_2, NOTIFY_3 = PROFILE.BLUERIIOT_NOTIFY_UUIDS
EXTRA_1, EXTRA_2 = PROFILE.BLUERIIOT_EXTRA_TRIGGER_UUIDS


def build_blueriiot_frame(
    temp_c: float = 25.02,
    ph_raw: int = 1955,
    orp_raw: int = 2820,
    conductivity_raw: int = 1000,
    battery_mv: int = 3520,
    marker: int = 0x33,
    length: int = 12,
) -> bytes:
    """12-byte Blueriiot measurement frame (little-endian fields)."""
    body = bytes([marker])
    body += round(temp_c * 100).to_bytes(2, "little")
    body += ph_raw.to_bytes(2, "little")
    body += orp_raw.to_bytes(2, "little")
    body += conductivity_raw.to_bytes(2, "little")
    body += battery_mv.to_bytes(2, "little")
    body += b"\x00"
    return (body + b"\x00" * length)[:length]


def make_services(characteristics: dict[str, list[str]]) -> list[SimpleNamespace]:
    """GATT services as bleak exposes them: services -> characteristics."""
    return [
        SimpleNamespace(
            characteristics=[
                SimpleNamespace(uuid=uuid, properties=props)
                for uuid, props in characteristics.items()
            ]
        )
    ]


def blueriiot_layout() -> dict[str, list[str]]:
    return {
        AUTH: ["write"],
        TRIGGER: ["write"],
        NOTIFY_1: ["notify"],
        NOTIFY_2: ["notify"],
        NOTIFY_3: ["notify"],
        EXTRA_1: ["write"],
        EXTRA_2: ["write", "write-without-response"],
    }


class FakeBlueriiotClient(FakeBlueClient):
    """Blueriiot probe: no Zodiac characteristic, notifies on the trigger."""

    def __init__(
        self,
        notifications: list[tuple[str, bytes]] | None = None,
        layout: dict[str, list[str]] | None = None,
        fail_notify: set[str] | None = None,
        fail_write: set[str] | None = None,
    ) -> None:
        super().__init__()
        self.services = make_services(layout or blueriiot_layout())
        self.notifications = (
            [(NOTIFY_1, build_blueriiot_frame())]
            if notifications is None
            else notifications
        )
        self.fail_notify = fail_notify or set()
        self.fail_write = fail_write or set()
        self.started: list[str] = []
        self.stopped: list[str] = []

    async def start_notify(self, uuid, handler):
        if uuid in self.fail_notify:
            raise BleakError(f"cannot subscribe to {uuid}")
        self.started.append(uuid)
        self._handler = handler

    async def stop_notify(self, uuid):
        self.stopped.append(uuid)

    async def write_gatt_char(self, uuid, data, response=True):
        self.writes.append((uuid, bytes(data)))
        if uuid in self.fail_write:
            raise BleakError(f"cannot write to {uuid}")
        if uuid == TRIGGER and bytes(data) == b"\x02" and self._handler:
            for _uuid, payload in self.notifications:
                self._handler(None, bytearray(payload))

    async def read_gatt_char(self, uuid):
        raise BleakError(f"characteristic {uuid} not found")
