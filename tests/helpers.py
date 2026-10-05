# Copyright (c) 2026 Adrien40
# SPDX-License-Identifier: GPL-3.0-only

"""Helpers shared by the Hydrao test modules."""

from typing import Self

from bleak.exc import BleakError


def u16le(value: int) -> tuple[int, int]:
    """Split a 16-bit value into (low_byte, high_byte), little-endian."""
    return value & 0xFF, (value >> 8) & 0xFF


def make_frames(
    total: int, shower: int, duration_ticks: int, temp_c: float
) -> tuple[bytearray, bytearray, bytearray]:
    """Build (vol_data, dur_data, temp_data) BLE frames as the device would
    send them, from human-friendly values.

    `duration_ticks` is the raw uint16 duration counter (1/50 s per tick).
    """
    t_lo, t_hi = u16le(total)
    s_lo, s_hi = u16le(shower)
    vol_data = bytearray([t_lo, t_hi, s_lo, s_hi])

    d_lo, d_hi = u16le(duration_ticks)
    dur_data = bytearray([d_lo, d_hi])

    temp_ticks = round(temp_c * 2)
    tm_lo, tm_hi = u16le(temp_ticks)
    temp_data = bytearray([tm_lo, tm_hi])

    return vol_data, dur_data, temp_data


class FakeBleClient:
    """Minimal stand-in for a connected `BleakClient`.

    `reads` maps a characteristic UUID to what reading it returns: bytes, an
    exception instance (raised), or a list used as a queue (its last element
    repeats once the others have been consumed). Reading an unknown
    characteristic raises BleakError, like a missing GATT characteristic.

    `connected_checks` is how many times the integration may ask
    `is_connected` before the link "drops", which is what ends the read loop.

    `establish_connection` hands back a client that is *already connected*:
    entering it as a context manager would call `connect()` a second time,
    which stock bleak refuses (Home Assistant's own wrapper merely ignores it,
    which is not something to rely on). The fake is as strict as the stricter
    of the two, so code that works with it works with both.

    `disconnect_calls` counts the calls to `disconnect()`, and
    `disconnect_error` is raised by it when set.
    """

    def __init__(
        self,
        reads: dict[str, object] | None = None,
        connected_checks: int = 1,
        write_errors: dict[str, Exception] | None = None,
        disconnect_error: Exception | None = None,
    ) -> None:
        self.reads: dict[str, object] = dict(reads or {})
        self.write_errors: dict[str, Exception] = dict(write_errors or {})
        self.writes: list[tuple[str, bytes]] = []
        self.read_log: list[str] = []
        self._checks_left = connected_checks
        self.disconnect_calls = 0
        self.disconnect_error = disconnect_error

    @property
    def is_connected(self) -> bool:
        if self._checks_left <= 0:
            return False
        self._checks_left -= 1
        return True

    async def __aenter__(self) -> Self:
        raise BleakError("Client is already connected")

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        if self.disconnect_error is not None:
            raise self.disconnect_error

    async def read_gatt_char(self, uuid: str) -> bytearray:
        self.read_log.append(uuid)
        value = self.reads.get(uuid)
        if isinstance(value, list):
            value = value.pop(0) if len(value) > 1 else value[0]
        if value is None:
            raise BleakError(f"no characteristic {uuid}")
        if isinstance(value, Exception):
            raise value
        return bytearray(value)  # type: ignore[arg-type]

    async def write_gatt_char(
        self, uuid: str, data: bytes, response: bool = False
    ) -> None:
        error = self.write_errors.get(uuid)
        if error is not None:
            raise error
        self.writes.append((uuid, bytes(data)))
