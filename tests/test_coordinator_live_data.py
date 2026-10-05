# Copyright (c) 2026 Adrien40
# SPDX-License-Identifier: GPL-3.0-only

"""Edge cases of live data processing that the state-machine tests do not
reach: bad frames, the first-ever connection flag, flow rate decoding and the
display reset helpers."""

import logging
import time
from unittest.mock import MagicMock, patch

from helpers import FakeBleClient, make_frames
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydrao_custom.const import (
    BT_STATUS_ERROR,
    BT_STATUS_SUCCESS,
    BT_STATUS_WAITING,
    CHAR_CONFIG,
)
from custom_components.hydrao_custom.coordinator import HydraoDataUpdateCoordinator

CONFIG_BYTES = bytes([10, 0, 0, 255, 20, 0, 255, 0, 30, 255, 255, 0, 40, 255, 0, 0])


def frames(shower=50, ticks=3000, temp=36.0):
    return make_frames(total=500, shower=shower, duration_ticks=ticks, temp_c=temp)


# ---------------------------------------------------------------------------
# Bad input
# ---------------------------------------------------------------------------


async def test_malformed_frames_are_ignored(coordinator, caplog):
    with caplog.at_level(logging.DEBUG):
        coordinator._process_live_data(bytearray(2), bytearray(2), bytearray(2), None)

    assert "Ignoring malformed BLE frame" in caplog.text
    assert coordinator.data is None
    assert coordinator.last_seen_time == 0.0


async def test_each_short_frame_is_rejected_on_its_own(coordinator):
    vol, dur, temp = frames()

    coordinator._process_live_data(vol[:3], dur, temp, None)
    coordinator._process_live_data(vol, dur[:1], temp, None)
    coordinator._process_live_data(vol, dur, temp[:1], None)

    assert coordinator.data is None


# ---------------------------------------------------------------------------
# First-ever connection
# ---------------------------------------------------------------------------


async def test_first_frame_marks_the_entry_as_having_connected_once(hass):
    entry = MockConfigEntry(
        domain="hydrao_custom", data={"address": "AA:BB:CC:DD:EE:FF"}
    )
    entry.add_to_hass(hass)
    coord = HydraoDataUpdateCoordinator(hass, entry)
    assert coord.is_new_entry is True

    coord._process_live_data(*frames(), None)

    assert coord.is_new_entry is False
    assert entry.data["has_connected_once"] is True


async def test_later_frames_do_not_rewrite_the_entry(hass, mock_entry, coordinator):
    assert coordinator.is_new_entry is False

    with patch.object(hass.config_entries, "async_update_entry") as update:
        coordinator._process_live_data(*frames(), None)

    update.assert_not_called()


# ---------------------------------------------------------------------------
# Session handling
# ---------------------------------------------------------------------------


async def test_volume_drop_without_a_pending_command_just_starts_a_new_session(
    coordinator,
):
    coordinator._process_live_data(*frames(shower=80, temp=20.0), None)

    coordinator._process_live_data(*frames(shower=5, ticks=200, temp=20.0), None)

    assert coordinator.pending_new_shower is False
    assert coordinator.session_wasted_volume == 5.0
    assert coordinator.last_valid_data["bluetooth_status"] == BT_STATUS_WAITING


async def test_no_auto_sync_when_the_water_was_warm_from_the_start(coordinator):
    """Nothing was wasted, so there is no cold phase to cut off."""
    coordinator.auto_sync_at_comfort = True

    coordinator._process_live_data(*frames(shower=50, temp=36.0), None)

    assert coordinator._comfort_sync_sent_for_session is True
    assert coordinator.pending_new_shower is False


async def test_auto_sync_is_triggered_once_comfort_is_reached_after_cold_water(
    coordinator,
):
    coordinator.auto_sync_at_comfort = True
    coordinator._process_live_data(*frames(shower=50, ticks=3000, temp=20.0), None)
    assert coordinator.pending_new_shower is False

    coordinator._process_live_data(*frames(shower=60, ticks=3500, temp=36.0), None)

    assert coordinator.pending_new_shower is True
    assert coordinator._preserve_wasted_on_next_reset is True


# ---------------------------------------------------------------------------
# Flow rate
# ---------------------------------------------------------------------------


async def test_flow_rate_is_decoded_from_the_raw_counter(coordinator):
    coordinator._process_live_data(*frames(), bytearray((300).to_bytes(2, "little")))

    assert coordinator.last_valid_data["flow_rate"] == 6.0


async def test_a_zero_flow_counter_means_no_flow(coordinator):
    coordinator._process_live_data(*frames(), bytearray(2))

    assert coordinator.last_valid_data["flow_rate"] == 0.0


async def test_a_truncated_flow_frame_is_ignored(coordinator):
    coordinator._process_live_data(*frames(), bytearray([5]))

    assert coordinator.last_valid_data["flow_rate"] == 0.0


async def test_flow_is_zero_when_neither_volume_nor_time_advanced(coordinator):
    flow = bytearray((300).to_bytes(2, "little"))
    coordinator._process_live_data(*frames(), flow)
    assert coordinator.last_valid_data["flow_rate"] == 6.0

    coordinator._process_live_data(*frames(), flow)

    assert coordinator.last_valid_data["flow_rate"] == 0.0


async def test_flow_rate_is_zeroed_when_the_device_goes_quiet(coordinator):
    coordinator._process_live_data(*frames(), bytearray((300).to_bytes(2, "little")))
    assert coordinator.last_valid_data["flow_rate"] == 6.0
    listener = MagicMock()
    coordinator.async_add_listener(listener)

    coordinator._evaluate_offline_timeout()

    assert coordinator.last_valid_data["flow_rate"] == 0.0
    assert coordinator.data["flow_rate"] == 0.0
    listener.assert_called()


async def test_offline_evaluation_before_any_data_does_nothing(coordinator):
    listener = MagicMock()
    coordinator.async_add_listener(listener)

    coordinator._evaluate_offline_timeout()

    listener.assert_not_called()


async def test_an_error_status_is_not_replaced_by_waiting(coordinator):
    coordinator._process_live_data(*frames(), bytearray((300).to_bytes(2, "little")))
    coordinator.set_bt_status(BT_STATUS_ERROR)

    coordinator._evaluate_offline_timeout()

    assert coordinator.last_valid_data["bluetooth_status"] == BT_STATUS_ERROR
    assert coordinator.last_valid_data["flow_rate"] == 6.0


# ---------------------------------------------------------------------------
# Display reset helper
# ---------------------------------------------------------------------------


async def test_resetting_the_display_with_no_data_is_a_no_op(coordinator):
    coordinator.last_valid_data = {}
    listener = MagicMock()
    coordinator.async_add_listener(listener)

    coordinator._reset_live_display_fields()

    listener.assert_not_called()


async def test_resetting_the_display_keeps_the_wasted_figures_by_default(coordinator):
    coordinator.last_valid_data = {"wasted_volume": 12.0, "temperature": 30.0}

    coordinator._reset_live_display_fields()

    data = coordinator.last_valid_data
    assert data["wasted_volume"] == 12.0
    assert data["temperature"] is None
    assert data["raw"] == {"shower_volume_raw": 0.0, "shower_duration": 0.0}


async def test_temperature_is_unknown_not_zero_while_a_reset_is_pending(coordinator):
    """0 C would be recorded as a real reading and skew the history."""
    coordinator._process_live_data(*frames(temp=36.0), None)

    coordinator.force_end_shower()
    assert coordinator.last_valid_data["temperature"] is None

    # a reading arriving before the device confirms the reset
    coordinator._process_live_data(*frames(temp=36.0), None)
    assert coordinator.last_valid_data["temperature"] is None


async def test_resetting_the_display_can_also_clear_the_wasted_figures(coordinator):
    coordinator._process_live_data(*frames(temp=20.0), None)
    assert coordinator.last_valid_data["wasted_volume"] > 0

    coordinator._reset_live_display_fields(reset_wasted=True)

    data = coordinator.last_valid_data
    assert data["wasted_volume"] == 0.0
    assert data["shower_duration_cold"] == 0.0
    assert data["time_to_comfort"] is None
    assert data["raw"]["shower_volume_raw"] == 0.0


# ---------------------------------------------------------------------------
# Applying only part of the pending config
# ---------------------------------------------------------------------------


async def test_only_thresholds_pending(coordinator):
    coordinator._raw_cfg = bytearray(CONFIG_BYTES)
    coordinator.pending_thresholds = [1, 2, 3, 4]
    client = FakeBleClient()

    assert await coordinator._apply_pending_config_write(client) is True

    assert coordinator.pending_thresholds is None
    assert "colors" not in coordinator.static_data
    assert coordinator.last_valid_data["bluetooth_status"] != BT_STATUS_ERROR


async def test_only_colors_pending(coordinator):
    coordinator._raw_cfg = bytearray(CONFIG_BYTES)
    coordinator.pending_colors = [(5, 5, 5)] * 4
    client = FakeBleClient()

    assert await coordinator._apply_pending_config_write(client) is True

    assert coordinator.pending_colors is None
    assert "thresholds" not in coordinator.static_data


async def test_config_is_read_first_when_only_colors_are_pending(coordinator):
    client = FakeBleClient({CHAR_CONFIG: CONFIG_BYTES})
    coordinator.pending_colors = [(5, 5, 5)] * 4

    assert await coordinator._apply_pending_config_write(client) is True

    assert client.read_log == [CHAR_CONFIG]


async def test_success_status_survives_a_clean_cycle(coordinator):
    coordinator.set_bt_status(BT_STATUS_SUCCESS)
    now = time.monotonic()
    coordinator._process_live_data(*frames(), None)

    assert coordinator.last_valid_data["bluetooth_status"] == BT_STATUS_SUCCESS
    assert coordinator.last_seen_time >= now
