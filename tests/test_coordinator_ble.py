# Copyright (c) 2026 Adrien40
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for the coordinator's Bluetooth side: the connection cycle, config
writes, the "new shower" reboot and the background loop.

The BLE client is replaced by `FakeBleClient`, so every characteristic read
and write is scripted and inspected without any radio."""

import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.exc import BleakError
from helpers import FakeBleClient, make_frames
from homeassistant.helpers import device_registry as dr

from custom_components.hydrao_custom import coordinator as coordinator_module
from custom_components.hydrao_custom.const import (
    BT_STATUS_CONNECTING,
    BT_STATUS_ERROR,
    BT_STATUS_REBOOTING,
    BT_STATUS_SUCCESS,
    BT_STATUS_SYNC_APPLIED,
    BT_STATUS_SYNC_FAILED,
    BT_STATUS_WAITING,
    CHAR_CONFIG,
    CHAR_DURATION_RAW,
    CHAR_FIRMWARE,
    CHAR_FLOW_RAW,
    CHAR_HARDWARE,
    CHAR_NEW_SHOWER,
    CHAR_SOAPING_DURATION,
    CHAR_TEMPERATURE_RAW,
    CHAR_UNIQUE_ID,
    CHAR_VOLUME_AND_DURATION,
    MAX_NEW_SHOWER_ATTEMPTS,
)

MOD = "custom_components.hydrao_custom.coordinator"

# Thresholds 10/20/30/40 l, each with its own colour (threshold, r, g, b).
CONFIG_BYTES = bytes([10, 0, 0, 255, 20, 0, 255, 0, 30, 255, 255, 0, 40, 255, 0, 0])
OTHER_CONFIG_BYTES = bytes(
    [11, 0, 0, 255, 21, 0, 255, 0, 31, 255, 255, 0, 41, 255, 0, 0]
)

REAL_SLEEP = asyncio.sleep


class StopLoop(Exception):
    """Raised by the fake sleep to break out of an infinite loop."""


@pytest.fixture
def ble():
    """Replace every Home Assistant Bluetooth entry point the coordinator uses.

    `ble.device` is what `async_ble_device_from_address` returns (set it to
    None for "device not discoverable"); `ble.connect(client)` makes
    `establish_connection` hand back that client."""
    with (
        patch(f"{MOD}.async_clear_advertisement_history") as clear,
        patch(f"{MOD}.async_ble_device_from_address") as get_device,
        patch(f"{MOD}.establish_connection", new_callable=AsyncMock) as establish,
    ):
        get_device.return_value = MagicMock(name="ble_device")

        class Handles:
            pass

        handles = Handles()
        handles.clear = clear
        handles.get_device = get_device
        handles.establish = establish
        handles.connect = lambda client: setattr(establish, "return_value", client)
        yield handles


@pytest.fixture
def fast_sleep():
    """Make the coordinator's `asyncio.sleep` instantaneous.

    Returns the list of requested delays. Setting `stop_after` makes the
    n-th call raise StopLoop, to break out of the infinite background loop."""

    class Sleeps(list):
        stop_after: int | None = None

    sleeps = Sleeps()

    async def _sleep(delay, *args):
        sleeps.append(delay)
        if sleeps.stop_after is not None and len(sleeps) >= sleeps.stop_after:
            raise StopLoop
        await REAL_SLEEP(0)

    with patch.object(coordinator_module.asyncio, "sleep", _sleep):
        yield sleeps


def live_reads(
    shower: int = 50,
    ticks: int = 3000,
    temp: float = 36.0,
    flow_raw: int | None = None,
    config: bytes | list | None = CONFIG_BYTES,
    soaping: int | None = 120,
) -> dict[str, object]:
    """The characteristics the connection cycle reads, as a device sends them."""
    vol, dur, tmp = make_frames(
        total=500, shower=shower, duration_ticks=ticks, temp_c=temp
    )
    reads: dict[str, object] = {
        CHAR_VOLUME_AND_DURATION: bytes(vol),
        CHAR_DURATION_RAW: bytes(dur),
        CHAR_TEMPERATURE_RAW: bytes(tmp),
    }
    if flow_raw is not None:
        reads[CHAR_FLOW_RAW] = flow_raw.to_bytes(2, "little")
    if config is not None:
        reads[CHAR_CONFIG] = config
    if soaping is not None:
        reads[CHAR_SOAPING_DURATION] = soaping.to_bytes(2, "little")
    return reads


def with_identity(hass, entry) -> None:
    """Pretend the device was already identified on an earlier connection."""
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            "firmware": "1.0.0",
            "hardware": "3",
            "device_id": "00ff",
        },
    )


# ---------------------------------------------------------------------------
# Connection cycle
# ---------------------------------------------------------------------------


async def test_device_not_discoverable_reports_waiting_without_connecting(
    coordinator, ble
):
    ble.get_device.return_value = None

    await coordinator._connect_and_read_stream()

    # "waiting" is already the initial status, so nothing needs publishing
    assert coordinator.last_valid_data["bluetooth_status"] == BT_STATUS_WAITING
    ble.establish.assert_not_called()


async def test_first_connection_reads_and_stores_the_device_identity(
    hass, mock_entry, coordinator, ble, fast_sleep
):
    client = FakeBleClient(
        {
            CHAR_FIRMWARE: b"1.2.3\x00",
            CHAR_HARDWARE: bytes([4]),
            CHAR_UNIQUE_ID: bytes.fromhex("0a0b"),
            **live_reads(),
        }
    )
    ble.connect(client)

    await coordinator._connect_and_read_stream()

    assert mock_entry.data["firmware"] == "1.2.3"
    assert mock_entry.data["hardware"] == "4"
    assert mock_entry.data["device_id"] == "0a0b"
    assert coordinator.static_data["firmware"] == "1.2.3"
    assert coordinator.static_data["device_id"] == "0a0b"

    device = dr.async_get(hass).async_get_device(
        identifiers={("hydrao_custom", "AA:BB:CC:DD:EE:FF")}
    )
    assert device is not None
    assert device.sw_version == "1.2.3"
    assert device.hw_version == "4"
    assert device.serial_number == "0a0b"


async def test_known_identity_is_not_read_again(
    hass, mock_entry, coordinator, ble, fast_sleep
):
    with_identity(hass, mock_entry)
    client = FakeBleClient(live_reads())
    ble.connect(client)

    await coordinator._connect_and_read_stream()

    assert CHAR_FIRMWARE not in client.read_log
    assert CHAR_HARDWARE not in client.read_log
    assert CHAR_UNIQUE_ID not in client.read_log
    assert coordinator.static_data["firmware"] == "1.0.0"


async def test_unreadable_identity_is_logged_and_does_not_stop_the_connection(
    hass, mock_entry, coordinator, ble, fast_sleep, caplog
):
    client = FakeBleClient(live_reads())
    ble.connect(client)

    with caplog.at_level(logging.WARNING):
        await coordinator._connect_and_read_stream()

    assert "Could not read Firmware" in caplog.text
    assert "Could not read Hardware" in caplog.text
    assert "Could not read Unique ID" in caplog.text
    assert "firmware" not in mock_entry.data
    # the live data was still processed
    assert coordinator.last_valid_data["total_volume"] == 500.0


async def test_empty_hardware_value_is_ignored_without_crashing(
    hass, mock_entry, coordinator, ble, fast_sleep, caplog
):
    """An empty characteristic used to raise an IndexError (hw[0])."""
    client = FakeBleClient(
        {
            CHAR_FIRMWARE: b"1.2.3\x00",
            CHAR_HARDWARE: b"",
            CHAR_UNIQUE_ID: bytes.fromhex("0a0b"),
            **live_reads(),
        }
    )
    ble.connect(client)

    with caplog.at_level(logging.WARNING):
        await coordinator._connect_and_read_stream()

    assert "Could not read Hardware: empty value" in caplog.text
    assert "hardware" not in mock_entry.data
    # the rest of the identity and the live data were still handled
    assert mock_entry.data["firmware"] == "1.2.3"
    assert coordinator.last_valid_data["total_volume"] == 500.0


async def test_connection_publishes_live_data_and_success_status(
    hass, mock_entry, coordinator, ble, fast_sleep
):
    with_identity(hass, mock_entry)
    ble.connect(FakeBleClient(live_reads(shower=50, temp=36.0, flow_raw=300)))

    await coordinator._connect_and_read_stream()

    data = coordinator.data
    assert data["bluetooth_status"] == BT_STATUS_SUCCESS
    assert data["temperature"] == 36.0
    assert data["raw"]["shower_volume_raw"] == 50.0
    # 1800 / 300 raw ticks
    assert data["flow_rate"] == 6.0
    ble.clear.assert_called_with(hass, "AA:BB:CC:DD:EE:FF")


async def test_missing_flow_characteristic_still_yields_data(
    hass, mock_entry, coordinator, ble, fast_sleep
):
    with_identity(hass, mock_entry)
    ble.connect(FakeBleClient(live_reads(flow_raw=None)))

    await coordinator._connect_and_read_stream()

    assert coordinator.data["flow_rate"] == 0.0
    assert coordinator.data["bluetooth_status"] == BT_STATUS_SUCCESS


async def test_failed_read_cycle_is_skipped_and_retried(
    hass, mock_entry, coordinator, ble, fast_sleep, caplog
):
    with_identity(hass, mock_entry)
    reads = live_reads()
    reads[CHAR_VOLUME_AND_DURATION] = [
        BleakError("link hiccup"),
        reads[CHAR_VOLUME_AND_DURATION],
    ]
    client = FakeBleClient(reads, connected_checks=2)
    ble.connect(client)

    with caplog.at_level(logging.DEBUG):
        await coordinator._connect_and_read_stream()

    assert "Skipping this read cycle" in caplog.text
    assert coordinator.data["total_volume"] == 500.0
    assert 1 in fast_sleep


async def test_the_first_live_reading_is_taken_before_the_device_config(
    hass, mock_entry, coordinator, ble, fast_sleep
):
    """Each read before the first live reading delays it, which can make the
    cold phase of the shower go unseen: the settings come after it."""
    with_identity(hass, mock_entry)
    client = FakeBleClient(live_reads())
    ble.connect(client)

    await coordinator._connect_and_read_stream()

    log = client.read_log
    first_live = log.index(CHAR_VOLUME_AND_DURATION)
    assert first_live < log.index(CHAR_CONFIG)
    assert first_live < log.index(CHAR_SOAPING_DURATION)
    # nothing but the live characteristics is read before the config
    assert set(log[: log.index(CHAR_CONFIG)]) <= {
        CHAR_VOLUME_AND_DURATION,
        CHAR_DURATION_RAW,
        CHAR_TEMPERATURE_RAW,
        CHAR_FLOW_RAW,
    }


async def test_the_device_config_waits_for_a_live_reading_that_succeeded(
    hass, mock_entry, coordinator, ble, fast_sleep
):
    with_identity(hass, mock_entry)
    reads = live_reads()
    reads[CHAR_VOLUME_AND_DURATION] = [
        BleakError("link hiccup"),
        reads[CHAR_VOLUME_AND_DURATION],
    ]
    client = FakeBleClient(reads, connected_checks=2)
    ble.connect(client)

    await coordinator._connect_and_read_stream()

    log = client.read_log
    second_attempt = [i for i, c in enumerate(log) if c == CHAR_VOLUME_AND_DURATION][1]
    assert log.index(CHAR_CONFIG) > second_attempt


async def test_thresholds_are_read_once_when_water_already_flows_at_connection(
    hass, mock_entry, coordinator, ble, fast_sleep
):
    """The read that follows the first live reading is also the one that has
    to happen once water flows: no second read right after it."""
    with_identity(hass, mock_entry)
    client = FakeBleClient(
        live_reads(shower=50, config=[CONFIG_BYTES, OTHER_CONFIG_BYTES])
    )
    ble.connect(client)

    await coordinator._connect_and_read_stream()

    assert client.read_log.count(CHAR_CONFIG) == 1
    assert client.read_log.count(CHAR_SOAPING_DURATION) == 1
    assert coordinator.static_data["thresholds"] == [10, 20, 30, 40]


async def test_thresholds_are_read_again_when_water_starts_after_the_connection(
    hass, mock_entry, coordinator, ble, fast_sleep
):
    with_identity(hass, mock_entry)
    water_off = live_reads(shower=0, ticks=0)
    water_on = live_reads(shower=50)
    reads = {
        **water_on,
        CHAR_VOLUME_AND_DURATION: [
            water_off[CHAR_VOLUME_AND_DURATION],
            water_on[CHAR_VOLUME_AND_DURATION],
        ],
        CHAR_DURATION_RAW: [
            water_off[CHAR_DURATION_RAW],
            water_on[CHAR_DURATION_RAW],
        ],
        CHAR_CONFIG: [CONFIG_BYTES, OTHER_CONFIG_BYTES],
    }
    client = FakeBleClient(reads, connected_checks=2)
    ble.connect(client)

    await coordinator._connect_and_read_stream()

    assert client.read_log.count(CHAR_CONFIG) == 2
    assert coordinator.static_data["thresholds"] == [11, 21, 31, 41]
    assert mock_entry.options["threshold_1"] == 11


async def test_thresholds_are_not_read_again_while_the_water_is_off(
    hass, mock_entry, coordinator, ble, fast_sleep
):
    with_identity(hass, mock_entry)
    client = FakeBleClient(live_reads(shower=0, ticks=0))
    ble.connect(client)

    await coordinator._connect_and_read_stream()

    assert client.read_log.count(CHAR_CONFIG) == 1


async def test_connection_status_is_connecting_before_the_link_is_up(
    hass, mock_entry, coordinator, ble, fast_sleep
):
    with_identity(hass, mock_entry)
    seen = []

    async def establish(*args, **kwargs):
        seen.append(coordinator.last_valid_data["bluetooth_status"])
        return FakeBleClient(live_reads())

    ble.establish.side_effect = establish

    await coordinator._connect_and_read_stream()

    assert seen == [BT_STATUS_CONNECTING]


# ---------------------------------------------------------------------------
# Pending config written during a connection
# ---------------------------------------------------------------------------


async def test_pending_options_are_written_to_the_device(
    hass, mock_entry, coordinator, ble, fast_sleep
):
    with_identity(hass, mock_entry)
    hass.config_entries.async_update_entry(
        mock_entry,
        options={
            **mock_entry.options,
            "threshold_1": 12,
            "threshold_2": 22,
            "threshold_3": 32,
            "threshold_4": 42,
            "threshold_1_color": [1, 2, 3],
            "threshold_2_color": [4, 5, 6],
            "threshold_3_color": [7, 8, 9],
            "threshold_4_color": [10, 11, 12],
            "soaping_duration": 90,
        },
    )
    client = FakeBleClient(live_reads(shower=0, ticks=0), connected_checks=2)
    ble.connect(client)

    await coordinator._connect_and_read_stream()

    assert (
        CHAR_CONFIG,
        bytes([12, 1, 2, 3, 22, 4, 5, 6, 32, 7, 8, 9, 42, 10, 11, 12]),
    ) in client.writes
    assert (CHAR_SOAPING_DURATION, (90).to_bytes(2, "little")) in client.writes
    assert coordinator.pending_thresholds is None
    assert coordinator.pending_colors is None
    assert coordinator.pending_soaping_duration is None
    assert coordinator.static_data["soaping_duration"] == 90
    assert coordinator.data["bluetooth_status"] == BT_STATUS_SUCCESS


async def test_failing_config_write_is_retried_then_given_up_for_the_session(
    hass, mock_entry, coordinator, ble, fast_sleep, caplog
):
    with_identity(hass, mock_entry)
    hass.config_entries.async_update_entry(
        mock_entry, options={**mock_entry.options, "soaping_duration": 90}
    )
    client = FakeBleClient(
        live_reads(shower=0, ticks=0),
        connected_checks=4,
        write_errors={CHAR_SOAPING_DURATION: BleakError("write refused")},
    )
    ble.connect(client)

    with caplog.at_level(logging.ERROR):
        await coordinator._connect_and_read_stream()

    assert "Giving up on pending config write after 2 failed" in caplog.text
    assert coordinator.data["bluetooth_status"] == BT_STATUS_SYNC_FAILED
    # still queued: it will be retried on the next connection
    assert coordinator.pending_soaping_duration == 90


# ---------------------------------------------------------------------------
# Closing the connection
# ---------------------------------------------------------------------------


async def test_the_connection_is_closed_at_the_end_of_a_cycle(
    hass, mock_entry, coordinator, ble, fast_sleep
):
    with_identity(hass, mock_entry)
    client = FakeBleClient(live_reads())
    ble.connect(client)

    await coordinator._connect_and_read_stream()

    assert client.disconnect_calls == 1
    ble.clear.assert_called()


async def test_the_connection_is_closed_after_the_new_shower_command(
    hass, mock_entry, coordinator, ble, fast_sleep
):
    with_identity(hass, mock_entry)
    coordinator.pending_new_shower = True
    coordinator._new_shower_requested_at = time.monotonic()
    client = FakeBleClient(live_reads(), connected_checks=5)
    ble.connect(client)

    await coordinator._connect_and_read_stream()

    assert coordinator._new_shower_write_sent is True
    assert client.disconnect_calls == 1


async def test_the_connection_is_closed_when_processing_fails(
    hass, mock_entry, coordinator, ble, fast_sleep
):
    with_identity(hass, mock_entry)
    client = FakeBleClient(live_reads())
    ble.connect(client)

    with (
        patch.object(
            coordinator, "_process_live_data", side_effect=RuntimeError("boom")
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
        await coordinator._connect_and_read_stream()

    assert client.disconnect_calls == 1
    ble.clear.assert_called()


async def test_the_connection_is_closed_when_the_task_is_cancelled(
    hass, mock_entry, coordinator, ble, fast_sleep
):
    """The entry unloading cancels the background task mid-connection."""
    with_identity(hass, mock_entry)
    client = FakeBleClient(live_reads())
    ble.connect(client)

    with (
        patch.object(
            coordinator, "_process_live_data", side_effect=asyncio.CancelledError
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await coordinator._connect_and_read_stream()

    assert client.disconnect_calls == 1


async def test_a_failing_disconnect_is_logged_and_does_not_fail_the_cycle(
    hass, mock_entry, coordinator, ble, fast_sleep, caplog
):
    """The link is often already gone when the shower stops: failing to close
    it must not turn the end of a shower into a "connection error"."""
    with_identity(hass, mock_entry)
    client = FakeBleClient(live_reads(), disconnect_error=BleakError("link gone"))
    ble.connect(client)

    with caplog.at_level(logging.DEBUG):
        await coordinator._connect_and_read_stream()

    assert client.disconnect_calls == 1
    assert "Error while closing the connection" in caplog.text
    assert coordinator.data["bluetooth_status"] == BT_STATUS_SUCCESS
    ble.clear.assert_called()


async def test_a_failing_disconnect_does_not_hide_the_original_error(
    hass, mock_entry, coordinator, ble, fast_sleep
):
    with_identity(hass, mock_entry)
    client = FakeBleClient(live_reads(), disconnect_error=BleakError("link gone"))
    ble.connect(client)

    with (
        patch.object(
            coordinator, "_process_live_data", side_effect=RuntimeError("boom")
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
        await coordinator._connect_and_read_stream()


async def test_closing_the_connection_is_logged(
    hass, mock_entry, coordinator, ble, fast_sleep, caplog
):
    """Lets a real-device test see, in the debug log, that each connection
    was closed."""
    with_identity(hass, mock_entry)
    ble.connect(FakeBleClient(live_reads()))

    with caplog.at_level(logging.DEBUG):
        await coordinator._connect_and_read_stream()

    assert "Bluetooth connection closed" in caplog.text


def make_client_class(reads, *, refuse_second_connect):
    """A client *class*, as `establish_connection` instantiates it.

    It behaves like stock bleak (`refuse_second_connect`: a second `connect()`
    raises) or like Home Assistant's wrapper (a second `connect()` is ignored).
    """
    created = []

    class Client(FakeBleClient):
        def __init__(self, device, **kwargs):
            super().__init__(reads)
            self.connected = False
            created.append(self)

        async def connect(self, **kwargs):
            if self.connected and refuse_second_connect:
                raise BleakError("Client is already connected")
            self.connected = True

        async def __aenter__(self):
            await self.connect()
            return self

        async def __aexit__(self, *exc_info):
            await self.disconnect()

        async def disconnect(self):
            await super().disconnect()
            self.connected = False

    return Client, created


@pytest.mark.parametrize(
    "refuse_second_connect", [True, False], ids=["stock-bleak", "ha-wrapper"]
)
async def test_a_cycle_works_with_the_real_establish_connection(
    hass, mock_entry, coordinator, fast_sleep, refuse_second_connect
):
    """Nothing is stubbed between the coordinator and the client class: the
    real `establish_connection` connects it, and the coordinator must then
    use it as an already-connected client and close it."""
    from bleak.backends.device import BLEDevice

    with_identity(hass, mock_entry)
    client_class, created = make_client_class(
        live_reads(), refuse_second_connect=refuse_second_connect
    )
    device = BLEDevice("AA:BB:CC:DD:EE:FF", "HYDRAO", {})

    with (
        patch(f"{MOD}.async_ble_device_from_address", return_value=device),
        patch(f"{MOD}.async_clear_advertisement_history"),
        patch(f"{MOD}.BleakClient", client_class),
    ):
        await coordinator._connect_and_read_stream()

    (client,) = created
    assert coordinator.data["total_volume"] == 500.0  # the cycle really ran
    assert client.disconnect_calls == 1
    assert client.connected is False


# ---------------------------------------------------------------------------
# "New shower" command during a connection
# ---------------------------------------------------------------------------


async def test_pending_new_shower_is_sent_and_the_connection_is_dropped(
    hass, mock_entry, coordinator, ble, fast_sleep
):
    with_identity(hass, mock_entry)
    coordinator.pending_new_shower = True
    coordinator._new_shower_requested_at = time.monotonic()
    client = FakeBleClient(live_reads(), connected_checks=5)
    ble.connect(client)

    await coordinator._connect_and_read_stream()

    assert (CHAR_NEW_SHOWER, b"\x01") in client.writes
    assert coordinator.new_shower_attempts == 1
    assert coordinator._new_shower_write_sent is True
    assert coordinator.data["bluetooth_status"] == BT_STATUS_REBOOTING


async def test_reconnecting_after_the_reboot_confirms_the_new_shower(
    hass, mock_entry, coordinator, ble, fast_sleep
):
    with_identity(hass, mock_entry)
    coordinator.pending_new_shower = True
    coordinator.new_shower_attempts = 1
    coordinator._new_shower_write_sent = True
    coordinator._awaiting_manual_reset_confirmation = True
    ble.connect(FakeBleClient(live_reads(shower=0, ticks=0)))

    await coordinator._connect_and_read_stream()

    assert coordinator.pending_new_shower is False
    assert coordinator.new_shower_attempts == 0
    assert coordinator._new_shower_write_sent is False
    assert coordinator.data["bluetooth_status"] == BT_STATUS_SUCCESS


async def test_stale_new_shower_is_dropped_and_the_cycle_goes_on(
    hass, mock_entry, coordinator, ble, fast_sleep
):
    with_identity(hass, mock_entry)
    coordinator.pending_new_shower = True
    coordinator._new_shower_requested_at = time.monotonic() - 10_000
    client = FakeBleClient(live_reads(), connected_checks=2)
    ble.connect(client)

    await coordinator._connect_and_read_stream()

    assert coordinator.pending_new_shower is False
    assert (CHAR_NEW_SHOWER, b"\x01") not in client.writes
    assert coordinator.data["bluetooth_status"] == BT_STATUS_SUCCESS


async def test_new_shower_gives_up_after_the_maximum_attempts_inside_a_connection(
    hass, mock_entry, coordinator, ble, fast_sleep, caplog
):
    with_identity(hass, mock_entry)
    coordinator.pending_new_shower = True
    coordinator._new_shower_requested_at = time.monotonic()
    coordinator.new_shower_attempts = MAX_NEW_SHOWER_ATTEMPTS
    ble.connect(FakeBleClient(live_reads(), connected_checks=2))

    with caplog.at_level(logging.ERROR):
        await coordinator._connect_and_read_stream()

    assert "Giving up on 'new shower' command" in caplog.text
    assert coordinator.pending_new_shower is False


# ---------------------------------------------------------------------------
# _async_write_pending_config
# ---------------------------------------------------------------------------


async def test_writing_nothing_is_a_success_without_any_io(coordinator):
    client = FakeBleClient()

    assert await coordinator._async_write_pending_config(client, None, None) is True
    assert client.writes == []


async def test_write_fails_when_the_current_config_cannot_be_read(coordinator):
    client = FakeBleClient()  # CHAR_CONFIG unreadable

    result = await coordinator._async_write_pending_config(client, [1, 2, 3, 4], None)

    assert result is False
    assert client.writes == []


async def test_malformed_thresholds_are_refused(coordinator, caplog):
    coordinator._raw_cfg = bytearray(CONFIG_BYTES)
    client = FakeBleClient()

    with caplog.at_level(logging.ERROR):
        result = await coordinator._async_write_pending_config(client, [1, 2, 3], None)

    assert result is False
    assert "expected 4 values, got 3" in caplog.text
    assert client.writes == []


async def test_malformed_colors_are_refused(coordinator, caplog):
    coordinator._raw_cfg = bytearray(CONFIG_BYTES)
    client = FakeBleClient()

    with caplog.at_level(logging.ERROR):
        result = await coordinator._async_write_pending_config(
            client, None, [(1, 2, 3)] * 3
        )

    assert result is False
    assert "colors payload" in caplog.text
    assert client.writes == []


async def test_colors_are_clamped_to_a_byte_and_thresholds_are_written(coordinator):
    coordinator._raw_cfg = bytearray(CONFIG_BYTES)
    client = FakeBleClient()

    result = await coordinator._async_write_pending_config(
        client,
        [1, 2, 3, 4],
        [(300, -5, 7), (0, 0, 0), (1, 2, 3), (255, 255, 255)],
    )

    assert result is True
    assert client.writes == [
        (CHAR_CONFIG, bytes([1, 255, 0, 7, 2, 0, 0, 0, 3, 1, 2, 3, 4, 255, 255, 255]))
    ]


async def test_colors_alone_leave_the_thresholds_untouched(coordinator):
    coordinator._raw_cfg = bytearray(CONFIG_BYTES)
    client = FakeBleClient()

    await coordinator._async_write_pending_config(client, None, [(9, 9, 9)] * 4)

    written = client.writes[0][1]
    assert [written[0], written[4], written[8], written[12]] == [10, 20, 30, 40]


async def test_config_write_error_is_reported_as_a_failure(coordinator, caplog):
    coordinator._raw_cfg = bytearray(CONFIG_BYTES)
    client = FakeBleClient(write_errors={CHAR_CONFIG: BleakError("nope")})

    with caplog.at_level(logging.ERROR):
        result = await coordinator._async_write_pending_config(
            client, [1, 2, 3, 4], None
        )

    assert result is False
    assert "Failed to write pending config" in caplog.text


# ---------------------------------------------------------------------------
# _async_write_soaping_duration
# ---------------------------------------------------------------------------


async def test_soaping_duration_is_written_little_endian(coordinator):
    client = FakeBleClient()

    assert await coordinator._async_write_soaping_duration(client, 300) is True
    assert client.writes == [(CHAR_SOAPING_DURATION, bytes([0x2C, 0x01]))]


async def test_soaping_duration_write_error_is_reported(coordinator, caplog):
    client = FakeBleClient(write_errors={CHAR_SOAPING_DURATION: TimeoutError()})

    with caplog.at_level(logging.ERROR):
        result = await coordinator._async_write_soaping_duration(client, 300)

    assert result is False
    assert "Failed to write soaping duration" in caplog.text


# ---------------------------------------------------------------------------
# _apply_pending_config_write
# ---------------------------------------------------------------------------


async def test_applying_with_nothing_pending_is_a_no_op(coordinator):
    client = FakeBleClient()

    assert await coordinator._apply_pending_config_write(client) is True
    assert client.writes == []


async def test_applying_everything_pending_clears_it_and_updates_the_device_view(
    coordinator,
):
    coordinator._raw_cfg = bytearray(CONFIG_BYTES)
    coordinator.pending_thresholds = [1, 2, 3, 4]
    coordinator.pending_colors = [(1, 1, 1)] * 4
    coordinator.pending_soaping_duration = 200
    client = FakeBleClient()

    assert await coordinator._apply_pending_config_write(client) is True

    assert coordinator.pending_thresholds is None
    assert coordinator.pending_colors is None
    assert coordinator.pending_soaping_duration is None
    assert coordinator.static_data["thresholds"] == [1, 2, 3, 4]
    assert coordinator.static_data["colors"] == [(1, 1, 1)] * 4
    assert coordinator.static_data["soaping_duration"] == 200
    assert coordinator.data["bluetooth_status"] == BT_STATUS_SYNC_APPLIED


async def test_a_change_queued_during_the_write_is_kept(coordinator):
    """If the user changes the options again while a write is in flight, the
    newer value stays pending instead of being wiped by the older write."""
    coordinator.pending_thresholds = [1, 2, 3, 4]
    coordinator.pending_colors = [(1, 1, 1)] * 4
    coordinator.pending_soaping_duration = 200

    async def write_and_change(client, thresholds, colors):
        coordinator.pending_thresholds = [9, 9, 9, 9]
        coordinator.pending_colors = [(2, 2, 2)] * 4
        coordinator.pending_soaping_duration = 250
        return True

    async def write_soaping(client, value):
        coordinator.pending_soaping_duration = 250
        return True

    with (
        patch.object(coordinator, "_async_write_pending_config", write_and_change),
        patch.object(coordinator, "_async_write_soaping_duration", write_soaping),
    ):
        assert await coordinator._apply_pending_config_write(FakeBleClient()) is True

    assert coordinator.pending_thresholds == [9, 9, 9, 9]
    assert coordinator.pending_colors == [(2, 2, 2)] * 4
    assert coordinator.pending_soaping_duration == 250


async def test_a_failed_config_write_reports_an_error_and_keeps_it_pending(coordinator):
    coordinator._raw_cfg = bytearray(CONFIG_BYTES)
    coordinator.pending_thresholds = [1, 2, 3, 4]
    client = FakeBleClient(write_errors={CHAR_CONFIG: BleakError("no")})

    assert await coordinator._apply_pending_config_write(client) is False

    assert coordinator.pending_thresholds == [1, 2, 3, 4]
    assert coordinator.data["bluetooth_status"] == BT_STATUS_ERROR


async def test_a_failed_soaping_write_reports_an_error_and_keeps_it_pending(
    coordinator,
):
    coordinator.pending_soaping_duration = 200
    client = FakeBleClient(write_errors={CHAR_SOAPING_DURATION: OSError("no")})

    assert await coordinator._apply_pending_config_write(client) is False

    assert coordinator.pending_soaping_duration == 200
    assert coordinator.data["bluetooth_status"] == BT_STATUS_ERROR


# ---------------------------------------------------------------------------
# Reading the device config
# ---------------------------------------------------------------------------


async def test_config_is_decoded_into_thresholds_and_colors(coordinator):
    client = FakeBleClient({CHAR_CONFIG: CONFIG_BYTES})

    await coordinator._async_read_thresholds(client)

    assert coordinator.static_data["thresholds"] == [10, 20, 30, 40]
    assert coordinator.static_data["colors"] == [
        (0, 0, 255),
        (0, 255, 0),
        (255, 255, 0),
        (255, 0, 0),
    ]
    assert coordinator._raw_cfg == bytearray(CONFIG_BYTES)
    assert coordinator.data is not None


async def test_a_truncated_config_is_ignored(coordinator):
    client = FakeBleClient({CHAR_CONFIG: bytes(10)})

    await coordinator._async_read_thresholds(client)

    assert "thresholds" not in coordinator.static_data
    assert coordinator._raw_cfg is None


async def test_an_unreadable_config_only_logs_a_warning(coordinator, caplog):
    client = FakeBleClient({CHAR_CONFIG: BleakError("gone")})

    with caplog.at_level(logging.WARNING):
        await coordinator._async_read_thresholds(client)

    assert "Could not read Config" in caplog.text
    assert coordinator._raw_cfg is None


async def test_soaping_duration_is_decoded_little_endian(coordinator):
    client = FakeBleClient({CHAR_SOAPING_DURATION: bytes([0x2C, 0x01])})

    await coordinator._async_read_soaping_duration(client)

    assert coordinator.static_data["soaping_duration"] == 300


async def test_a_truncated_soaping_duration_is_ignored(coordinator):
    client = FakeBleClient({CHAR_SOAPING_DURATION: bytes([1])})

    await coordinator._async_read_soaping_duration(client)

    assert "soaping_duration" not in coordinator.static_data


async def test_an_unreadable_soaping_duration_only_logs_a_warning(coordinator, caplog):
    client = FakeBleClient({CHAR_SOAPING_DURATION: BleakError("gone")})

    with caplog.at_level(logging.WARNING):
        await coordinator._async_read_soaping_duration(client)

    assert "Could not read Soaping Duration" in caplog.text


# ---------------------------------------------------------------------------
# "New shower" write details
# ---------------------------------------------------------------------------


async def test_new_shower_command_is_not_sent_when_nothing_is_pending(coordinator):
    client = FakeBleClient()

    await coordinator._handle_pending_new_shower(client)

    assert client.writes == []
    assert coordinator.new_shower_attempts == 0


async def test_new_shower_command_is_written_and_counted(coordinator):
    coordinator.pending_new_shower = True
    coordinator._new_shower_requested_at = time.monotonic()
    client = FakeBleClient()

    await coordinator._handle_pending_new_shower(client)

    assert client.writes == [(CHAR_NEW_SHOWER, b"\x01")]
    assert coordinator.new_shower_attempts == 1
    assert coordinator._new_shower_write_sent is True


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------


async def test_loop_reports_waiting_while_nothing_is_advertising(
    coordinator, ble, fast_sleep
):
    fast_sleep.stop_after = 3

    with pytest.raises(StopLoop):
        await coordinator.async_run_loop()

    assert coordinator.last_valid_data["bluetooth_status"] == BT_STATUS_WAITING
    ble.clear.assert_called()
    # one initial pause, then one pause per idle tick
    assert fast_sleep[:3] == [1, 1, 1]


async def test_loop_connects_when_the_device_is_advertising(
    coordinator, ble, fast_sleep
):
    coordinator._last_advertisement_time = time.monotonic()
    fast_sleep.stop_after = 2
    connect = AsyncMock()

    with (
        patch.object(coordinator, "_connect_and_read_stream", connect),
        pytest.raises(StopLoop),
    ):
        await coordinator.async_run_loop()

    connect.assert_awaited_once()


async def test_loop_reports_an_error_when_the_connection_fails(
    coordinator, ble, fast_sleep, caplog
):
    coordinator._last_advertisement_time = time.monotonic()
    fast_sleep.stop_after = 3
    connect = AsyncMock(side_effect=BleakError("link lost"))

    with (
        caplog.at_level(logging.DEBUG),
        patch.object(coordinator, "_connect_and_read_stream", connect),
        pytest.raises(StopLoop),
    ):
        await coordinator.async_run_loop()

    assert coordinator.data["bluetooth_status"] == BT_STATUS_ERROR
    assert "BLE connection/read failed" in caplog.text


async def test_loop_survives_and_logs_an_unexpected_error(
    coordinator, ble, fast_sleep, caplog
):
    coordinator._last_advertisement_time = time.monotonic()
    fast_sleep.stop_after = 3
    connect = AsyncMock(side_effect=RuntimeError("bug"))

    with (
        caplog.at_level(logging.ERROR),
        patch.object(coordinator, "_connect_and_read_stream", connect),
        pytest.raises(StopLoop),
    ):
        await coordinator.async_run_loop()

    assert coordinator.data["bluetooth_status"] == BT_STATUS_ERROR
    assert "Unexpected error in the Hydrao BLE loop" in caplog.text
