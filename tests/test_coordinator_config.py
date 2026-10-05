# Copyright (c) 2026 Adrien40
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for the configuration side of the coordinator: the passive Bluetooth
listener, status reporting, and how options become pending writes to the
device (and how the device's own config flows back into the options)."""

import logging
import time
from unittest.mock import MagicMock, patch

import pytest
from bleak.exc import BleakError

from custom_components.hydrao_custom import coordinator as coordinator_module
from custom_components.hydrao_custom.const import (
    BT_STATUS_ERROR,
    BT_STATUS_SUCCESS,
    BT_STATUS_WAITING,
    DEFAULT_MIN_TEMP_THRESHOLD,
)
from custom_components.hydrao_custom.coordinator import (
    ADVERTISEMENT_GRACE_PERIOD,
    HydraoDataUpdateCoordinator,
)

MOD = "custom_components.hydrao_custom.coordinator"

FULL_OPTIONS = {
    "threshold_1": 10,
    "threshold_2": 20,
    "threshold_3": 30,
    "threshold_4": 40,
    "threshold_1_color": [0, 0, 255],
    "threshold_2_color": [0, 255, 0],
    "threshold_3_color": [255, 255, 0],
    "threshold_4_color": [255, 0, 0],
    "soaping_duration": 120,
    "min_temp_threshold": 35.0,
    "auto_sync_at_comfort": True,
}


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


async def test_device_name_defaults_to_the_end_of_the_address(hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain="hydrao_custom", data={"address": "AA:BB:CC:DD:12:34"}
    )
    entry.add_to_hass(hass)

    coord = HydraoDataUpdateCoordinator(hass, entry)

    assert coord.device_name == "Hydrao 1234"
    assert coord.is_new_entry is True
    assert coord.min_temp_threshold == DEFAULT_MIN_TEMP_THRESHOLD
    assert coord.auto_sync_at_comfort is False
    assert "thresholds" not in coord.static_data


async def test_options_are_loaded_into_the_coordinator(hass, mock_entry):
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_entry, options=FULL_OPTIONS)

    coord = HydraoDataUpdateCoordinator(hass, mock_entry)

    assert coord.min_temp_threshold == 35.0
    assert coord.auto_sync_at_comfort is True
    assert coord.static_data["soaping_duration"] == 120
    assert coord.static_data["thresholds"] == [10, 20, 30, 40]
    assert coord.static_data["colors"] == [
        (0, 0, 255),
        (0, 255, 0),
        (255, 255, 0),
        (255, 0, 0),
    ]


async def test_partial_threshold_options_do_not_stop_the_setup(hass, mock_entry):
    """Only some thresholds / colors stored: the coordinator must still be
    created, and leave them to be read from the device."""
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_entry,
        options={"threshold_1": 10, "threshold_1_color": [0, 0, 255]},
    )

    coord = HydraoDataUpdateCoordinator(hass, mock_entry)

    assert "thresholds" not in coord.static_data
    assert "colors" not in coord.static_data


async def test_out_of_range_soaping_option_is_clamped_at_startup(hass, mock_entry):
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_entry, options={"soaping_duration": 5})

    coord = HydraoDataUpdateCoordinator(hass, mock_entry)

    assert coord.static_data["soaping_duration"] == 10


async def test_device_info_describes_the_device(coordinator):
    coordinator.static_data["firmware"] = "1.2.3"
    coordinator.static_data["hardware"] = "7"
    coordinator.static_data["device_id"] = "abcd"

    info = coordinator.device_info

    assert info["manufacturer"] == "Hydrao"
    assert info["sw_version"] == "1.2.3"
    assert info["hw_version"] == "7"
    assert info["serial_number"] == "abcd"


# ---------------------------------------------------------------------------
# Passive Bluetooth listener
# ---------------------------------------------------------------------------


async def test_listener_registers_a_passive_callback_and_tracks_adverts(coordinator):
    with (
        patch(f"{MOD}.async_last_service_info", return_value=None),
        patch(f"{MOD}.async_register_callback") as register,
    ):
        unsubscribe = coordinator.async_start_bluetooth_listener()

    assert unsubscribe is register.return_value
    assert coordinator._last_advertisement_time == 0.0

    on_advertisement = register.call_args.args[1]
    on_advertisement(MagicMock(), MagicMock())

    assert coordinator._last_advertisement_time > 0.0
    assert coordinator._has_recent_advertisement() is True


async def test_listener_counts_an_advert_already_seen_at_startup(coordinator):
    seen_just_now = MagicMock(time=time.monotonic())
    with (
        patch(f"{MOD}.async_last_service_info", return_value=seen_just_now),
        patch(f"{MOD}.async_register_callback"),
    ):
        coordinator.async_start_bluetooth_listener()

    assert coordinator._last_advertisement_time == seen_just_now.time
    assert coordinator._has_recent_advertisement() is True


async def test_listener_ignores_a_stale_advert_at_startup(coordinator):
    """A cached advertisement can be old: it must not make the device look
    present, which would trigger a pointless connection attempt."""
    stale = MagicMock(time=time.monotonic() - 60)
    with (
        patch(f"{MOD}.async_last_service_info", return_value=stale),
        patch(f"{MOD}.async_register_callback"),
    ):
        coordinator.async_start_bluetooth_listener()

    assert coordinator._has_recent_advertisement() is False


async def test_no_advertisement_is_never_recent(coordinator):
    coordinator._last_advertisement_time = 0.0

    assert coordinator._has_recent_advertisement() is False


async def test_advertisement_goes_stale_after_the_grace_period(coordinator):
    coordinator._last_advertisement_time = time.monotonic()
    assert coordinator._has_recent_advertisement() is True

    coordinator._last_advertisement_time = (
        time.monotonic() - ADVERTISEMENT_GRACE_PERIOD - 1
    )

    assert coordinator._has_recent_advertisement() is False


# ---------------------------------------------------------------------------
# Status reporting
# ---------------------------------------------------------------------------


async def test_set_bt_status_publishes_only_real_changes(coordinator):
    listener = MagicMock()
    coordinator.async_add_listener(listener)

    coordinator.set_bt_status(BT_STATUS_SUCCESS)
    coordinator.set_bt_status(BT_STATUS_SUCCESS)

    assert coordinator.last_valid_data["bluetooth_status"] == BT_STATUS_SUCCESS
    assert coordinator.data["bluetooth_status"] == BT_STATUS_SUCCESS
    assert listener.call_count == 1


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


async def test_unchanged_options_are_ignored(coordinator):
    coordinator._last_processed_options = {"min_temp_threshold": 40.0}
    listener = MagicMock()
    coordinator.async_add_listener(listener)

    coordinator.async_update_options({"min_temp_threshold": 40.0})

    listener.assert_not_called()
    assert coordinator.min_temp_threshold == 33.0


async def test_changed_options_update_the_coordinator_and_notify(coordinator):
    listener = MagicMock()
    coordinator.async_add_listener(listener)

    coordinator.async_update_options(
        {"min_temp_threshold": 38.5, "auto_sync_at_comfort": True}
    )

    assert coordinator.min_temp_threshold == 38.5
    assert coordinator.auto_sync_at_comfort is True
    listener.assert_called_once()


async def test_options_without_known_keys_fall_back_to_defaults(coordinator):
    coordinator.async_update_options({"something_else": 1})

    assert coordinator.min_temp_threshold == DEFAULT_MIN_TEMP_THRESHOLD
    assert coordinator.auto_sync_at_comfort is False


# ---------------------------------------------------------------------------
# Options -> pending writes
# ---------------------------------------------------------------------------


async def test_new_soaping_duration_is_queued(coordinator):
    coordinator.static_data["soaping_duration"] = 180

    coordinator._queue_pending_writes_from_options({"soaping_duration": 240})

    assert coordinator.pending_soaping_duration == 240


async def test_soaping_duration_already_on_the_device_is_not_queued(coordinator):
    coordinator.static_data["soaping_duration"] = 180

    coordinator._queue_pending_writes_from_options({"soaping_duration": 180})

    assert coordinator.pending_soaping_duration is None


async def test_soaping_duration_is_queued_when_the_device_value_is_unknown(
    coordinator,
):
    coordinator._queue_pending_writes_from_options({"soaping_duration": 180})

    assert coordinator.pending_soaping_duration == 180


async def test_out_of_range_soaping_duration_is_clamped_with_a_warning(
    coordinator, caplog
):
    with caplog.at_level(logging.WARNING):
        coordinator._queue_pending_writes_from_options({"soaping_duration": 9999})

    assert coordinator.pending_soaping_duration == 600
    assert "out of range" in caplog.text


async def test_thresholds_and_colors_are_queued_when_they_differ(coordinator):
    coordinator.static_data["thresholds"] = [5, 15, 25, 35]
    coordinator.static_data["colors"] = [(1, 1, 1)] * 4

    coordinator._queue_pending_writes_from_options(FULL_OPTIONS)

    assert coordinator.pending_thresholds == [10, 20, 30, 40]
    assert coordinator.pending_colors == [
        (0, 0, 255),
        (0, 255, 0),
        (255, 255, 0),
        (255, 0, 0),
    ]


async def test_thresholds_and_colors_matching_the_device_are_not_queued(coordinator):
    coordinator.static_data["thresholds"] = [10, 20, 30, 40]
    coordinator.static_data["colors"] = [
        (0, 0, 255),
        (0, 255, 0),
        (255, 255, 0),
        (255, 0, 0),
    ]

    coordinator._queue_pending_writes_from_options(FULL_OPTIONS)

    assert coordinator.pending_thresholds is None
    assert coordinator.pending_colors is None


async def test_partial_options_are_completed_from_the_device_state(coordinator):
    coordinator.static_data["thresholds"] = [5, 15, 25, 35]
    coordinator.static_data["colors"] = [(1, 2, 3)] * 4

    coordinator._queue_pending_writes_from_options({"threshold_2": 18})

    assert coordinator.pending_thresholds == [5, 18, 25, 35]
    assert coordinator.pending_colors is None


async def test_partial_options_without_device_state_queue_nothing(coordinator):
    """Never connected yet and only some thresholds submitted: no partial
    array must ever be built."""
    coordinator._queue_pending_writes_from_options({"threshold_2": 18})

    assert coordinator.pending_thresholds is None
    assert coordinator.pending_colors is None


async def test_thresholds_given_without_colors_and_no_device_state_queue_nothing(
    coordinator,
):
    options = {f"threshold_{i}": i * 10 for i in range(1, 5)}

    coordinator._queue_pending_writes_from_options(options)

    assert coordinator.pending_thresholds is None
    assert coordinator.pending_colors is None


async def test_non_increasing_thresholds_are_refused_with_a_warning(
    coordinator, caplog
):
    coordinator.static_data["thresholds"] = [5, 15, 25, 35]
    coordinator.static_data["colors"] = [(0, 0, 0)] * 4
    options = {**FULL_OPTIONS, "threshold_2": 5}

    with caplog.at_level(logging.WARNING):
        coordinator._queue_pending_writes_from_options(options)

    assert coordinator.pending_thresholds is None
    assert "strictly increasing" in caplog.text


async def test_thresholds_that_do_not_fit_one_byte_are_refused_with_a_warning(
    coordinator, caplog
):
    coordinator.static_data["thresholds"] = [5, 15, 25, 35]
    coordinator.static_data["colors"] = [(0, 0, 0)] * 4
    options = {**FULL_OPTIONS, "threshold_4": 300}

    with caplog.at_level(logging.WARNING):
        coordinator._queue_pending_writes_from_options(options)

    assert coordinator.pending_thresholds is None
    assert "between 0 and 255" in caplog.text


# ---------------------------------------------------------------------------
# Device config -> HA options
# ---------------------------------------------------------------------------


async def test_device_config_is_copied_into_the_options(hass, mock_entry, coordinator):
    coordinator.static_data["thresholds"] = [10, 20, 30, 40]
    coordinator.static_data["colors"] = [(0, 0, 255), (0, 255, 0), (255, 255, 0)] + [
        (255, 0, 0)
    ]
    coordinator.static_data["soaping_duration"] = 150

    coordinator._sync_device_config_to_ha_options()

    options = mock_entry.options
    assert [options[f"threshold_{i}"] for i in range(1, 5)] == [10, 20, 30, 40]
    assert options["threshold_1_color"] == [0, 0, 255]
    assert options["threshold_4_color"] == [255, 0, 0]
    assert options["soaping_duration"] == 150
    assert options["min_temp_threshold"] == 33.0


async def test_device_config_is_not_written_back_over_pending_changes(
    hass, mock_entry, coordinator
):
    coordinator.static_data["thresholds"] = [10, 20, 30, 40]
    coordinator.static_data["colors"] = [(0, 0, 0)] * 4
    coordinator.static_data["soaping_duration"] = 150
    coordinator.pending_thresholds = [11, 21, 31, 41]
    coordinator.pending_colors = [(1, 1, 1)] * 4
    coordinator.pending_soaping_duration = 200

    coordinator._sync_device_config_to_ha_options()

    assert mock_entry.options == {"min_temp_threshold": 33.0}


async def test_unchanged_device_config_does_not_touch_the_entry(
    hass, mock_entry, coordinator
):
    hass.config_entries.async_update_entry(
        mock_entry,
        options={
            "threshold_1": 10,
            "threshold_2": 20,
            "threshold_3": 30,
            "threshold_4": 40,
        },
    )
    coordinator.static_data["thresholds"] = [10, 20, 30, 40]

    with patch.object(hass.config_entries, "async_update_entry") as update:
        coordinator._sync_device_config_to_ha_options()

    update.assert_not_called()


async def test_nothing_known_about_the_device_leaves_options_alone(
    hass, mock_entry, coordinator
):
    with patch.object(hass.config_entries, "async_update_entry") as update:
        coordinator._sync_device_config_to_ha_options()

    update.assert_not_called()


# ---------------------------------------------------------------------------
# Restoring lifetime totals
# ---------------------------------------------------------------------------


async def test_lifetime_totals_can_be_restored(coordinator):
    coordinator.restore_wasted_volume_total(123.0)
    coordinator.restore_shower_volume_comfort_total(456.0)

    assert coordinator.lifetime_wasted_volume_total == 123.0
    assert coordinator.lifetime_shower_volume_comfort_total == 456.0


# ---------------------------------------------------------------------------
# Module-level sanity
# ---------------------------------------------------------------------------


def test_transient_ble_errors_cover_the_expected_exceptions():
    assert BleakError in coordinator_module._BLE_TRANSIENT_ERRORS
    assert TimeoutError in coordinator_module._BLE_TRANSIENT_ERRORS
    assert OSError in coordinator_module._BLE_TRANSIENT_ERRORS


@pytest.mark.parametrize("status", [BT_STATUS_ERROR, BT_STATUS_WAITING])
async def test_status_can_move_between_any_two_states(coordinator, status):
    coordinator.set_bt_status(BT_STATUS_SUCCESS)
    coordinator.set_bt_status(status)

    assert coordinator.data["bluetooth_status"] == status
