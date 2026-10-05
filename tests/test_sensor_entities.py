# Copyright (c) 2026 Adrien40
# SPDX-License-Identifier: GPL-3.0-only

"""Behaviour of every sensor entity: live values, fallbacks to restored
state, colour attributes, the Bluetooth status, RSSI and pending config."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.sensor import (
    SensorExtraStoredData,
)
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er

from custom_components.hydrao_custom.const import (
    BT_STATUS_CONNECTING,
    BT_STATUS_ERROR,
    BT_STATUS_REBOOTING,
    BT_STATUS_SUCCESS,
    BT_STATUS_SYNC_APPLIED,
    BT_STATUS_SYNC_FAILED,
    BT_STATUS_WAITING,
    BT_STATUS_WRITING_SYNC,
)
from custom_components.hydrao_custom.sensor import (
    SENSOR_DESCRIPTIONS,
    SOAPING_DURATION_DESC,
    HydraoBluetoothStatusSensor,
    HydraoPendingConfigSensor,
    HydraoRealTimeRSSISensor,
    HydraoSensor,
    HydraoSoapingDurationSensor,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"
SENSOR_MODULE = "custom_components.hydrao_custom.sensor"


def description(key):
    return next(d for d in SENSOR_DESCRIPTIONS if d.key == key)


def make_sensor(coordinator, key):
    return HydraoSensor(coordinator, description(key))


def stored(value, unit=None):
    return SensorExtraStoredData(native_value=value, native_unit_of_measurement=unit)


# ---------------------------------------------------------------------------
# HydraoSensor.native_value
# ---------------------------------------------------------------------------


async def test_numeric_sensors_report_floats_from_live_data(coordinator):
    coordinator.data = {"temperature": 36, "total_volume": 120}

    assert make_sensor(coordinator, "temperature").native_value == 36.0
    assert make_sensor(coordinator, "total_volume").native_value == 120.0
    assert isinstance(make_sensor(coordinator, "temperature").native_value, float)


async def test_raw_sensors_read_from_the_raw_block(coordinator):
    coordinator.data = {"raw": {"shower_volume_raw": 42, "shower_duration": 90}}

    assert make_sensor(coordinator, "shower_volume_raw").native_value == 42.0
    assert make_sensor(coordinator, "shower_duration").native_value == 90.0


async def test_raw_sensors_without_a_raw_block_report_nothing(coordinator):
    coordinator.data = {"temperature": 30}

    assert make_sensor(coordinator, "shower_volume_raw").native_value is None


async def test_no_data_at_all_reports_nothing(coordinator):
    coordinator.data = None

    assert make_sensor(coordinator, "temperature").native_value is None


async def test_flow_rate_defaults_to_zero_without_data(coordinator):
    coordinator.data = None
    sensor = make_sensor(coordinator, "flow_rate")
    sensor._restored_value = 7.0  # never restored for the flow rate

    assert sensor.native_value == 0.0


async def test_missing_value_falls_back_to_the_restored_number(coordinator):
    sensor = make_sensor(coordinator, "wasted_volume_total")
    sensor._restored_value = "12.5"
    coordinator.data = {}

    assert sensor.native_value == 12.5


async def test_unconvertible_restored_value_is_returned_as_is(coordinator):
    sensor = make_sensor(coordinator, "wasted_volume_total")
    sensor._restored_value = "unknown"
    coordinator.data = {}

    assert sensor.native_value == "unknown"


def test_only_the_temperature_and_the_time_to_comfort_may_be_unknown():
    """For these two, "unknown" is a real answer once data has been published;
    every other sensor falls back on the value restored from its last state."""
    unknown_is_valid = {d.key for d in SENSOR_DESCRIPTIONS if d.none_is_valid}

    assert unknown_is_valid == {"temperature", "time_to_comfort"}


@pytest.mark.parametrize("desc", SENSOR_DESCRIPTIONS, ids=lambda d: d.key)
async def test_every_sensor_has_nothing_to_show_before_the_first_reading(
    coordinator, desc
):
    """No description may fail on a coordinator that has no data yet."""
    coordinator.data = None

    value = HydraoSensor(coordinator, desc).native_value

    assert value in (None, 0.0)  # the flow rate reads 0 when there is nothing


# ---------------------------------------------------------------------------
# Threshold sensors
# ---------------------------------------------------------------------------


async def test_threshold_sensors_report_the_device_thresholds(coordinator):
    coordinator.static_data["thresholds"] = [10, 20, 30, 40]

    values = [
        make_sensor(coordinator, f"threshold_{i}").native_value for i in range(1, 5)
    ]

    assert values == [10.0, 20.0, 30.0, 40.0]


async def test_threshold_sensor_falls_back_to_the_restored_value(coordinator):
    sensor = make_sensor(coordinator, "threshold_2")
    sensor._restored_value = "20"

    assert sensor.native_value == 20.0


async def test_threshold_sensor_with_an_unreadable_restored_value(coordinator):
    sensor = make_sensor(coordinator, "threshold_2")
    sensor._restored_value = "n/a"

    assert sensor.native_value == "n/a"


async def test_threshold_sensor_with_nothing_known_reports_nothing(coordinator):
    assert make_sensor(coordinator, "threshold_1").native_value is None


async def test_threshold_sensor_ignores_a_broken_device_list(coordinator):
    coordinator.static_data["thresholds"] = [10]
    sensor = make_sensor(coordinator, "threshold_4")
    sensor._restored_value = 40

    assert sensor.native_value == 40.0


# ---------------------------------------------------------------------------
# Colour attributes
# ---------------------------------------------------------------------------


async def test_threshold_sensor_exposes_its_colour(coordinator):
    coordinator.static_data["colors"] = [
        (0, 0, 255),
        (0, 255, 0),
        (255, 255, 0),
        (255, 0, 0),
    ]

    attrs = make_sensor(coordinator, "threshold_2").extra_state_attributes

    assert attrs == {"color_rgb": "0, 255, 0", "color_hex": "#00FF00"}


async def test_threshold_sensor_without_colours_has_no_attributes(coordinator):
    assert make_sensor(coordinator, "threshold_1").extra_state_attributes == {}


async def test_threshold_sensor_with_a_broken_colour_list_has_no_attributes(
    coordinator,
):
    coordinator.static_data["colors"] = [(1, 2, 3)]

    assert make_sensor(coordinator, "threshold_3").extra_state_attributes == {}


async def test_other_sensors_have_no_extra_attributes(coordinator):
    coordinator.static_data["colors"] = [(1, 2, 3)] * 4

    assert make_sensor(coordinator, "temperature").extra_state_attributes == {}


# ---------------------------------------------------------------------------
# Restoring state when added
# ---------------------------------------------------------------------------


async def test_lifetime_wasted_total_is_restored_into_the_coordinator(coordinator):
    sensor = make_sensor(coordinator, "wasted_volume_total")
    sensor.async_get_last_sensor_data = AsyncMock(return_value=stored(250.0))

    await sensor.async_added_to_hass()

    assert coordinator.lifetime_wasted_volume_total == 250.0


async def test_lifetime_comfort_total_is_restored_into_the_coordinator(coordinator):
    sensor = make_sensor(coordinator, "shower_volume_comfort_total")
    sensor.async_get_last_sensor_data = AsyncMock(return_value=stored(80.0))

    await sensor.async_added_to_hass()

    assert coordinator.lifetime_shower_volume_comfort_total == 80.0


@pytest.mark.parametrize(
    ("key", "attribute"),
    [
        ("wasted_volume_total", "lifetime_wasted_volume_total"),
        ("shower_volume_comfort_total", "lifetime_shower_volume_comfort_total"),
    ],
)
async def test_unreadable_lifetime_total_is_not_restored(coordinator, key, attribute):
    sensor = make_sensor(coordinator, key)
    sensor.async_get_last_sensor_data = AsyncMock(return_value=stored("garbage"))

    await sensor.async_added_to_hass()

    assert getattr(coordinator, attribute) == 0.0


@pytest.mark.parametrize(
    ("key", "attribute"),
    [
        ("wasted_volume_total", "lifetime_wasted_volume_total"),
        ("shower_volume_comfort_total", "lifetime_shower_volume_comfort_total"),
    ],
)
async def test_restored_total_is_ignored_once_the_totals_have_their_own_file(
    coordinator, key, attribute
):
    """After the first start with the saved file, the sensors' restored state
    is no longer the source of the totals."""
    setattr(coordinator, attribute, 300.0)
    coordinator.totals_loaded_from_store = True
    sensor = make_sensor(coordinator, key)
    sensor.async_get_last_sensor_data = AsyncMock(return_value=stored(250.0))

    await sensor.async_added_to_hass()

    assert getattr(coordinator, attribute) == 300.0
    # it is still kept as the display fallback
    assert sensor._restored_value == 250.0


async def test_adopting_a_restored_total_schedules_a_save(coordinator):
    sensor = make_sensor(coordinator, "wasted_volume_total")
    sensor.async_get_last_sensor_data = AsyncMock(return_value=stored(250.0))

    with patch.object(coordinator._store, "async_delay_save") as delay_save:
        await sensor.async_added_to_hass()

    delay_save.assert_called_once()


async def test_the_flow_rate_is_never_restored(coordinator):
    sensor = make_sensor(coordinator, "flow_rate")
    sensor.async_get_last_sensor_data = AsyncMock(return_value=stored(9.0))

    await sensor.async_added_to_hass()

    assert sensor._restored_value is None


async def test_nothing_stored_means_nothing_restored(coordinator):
    sensor = make_sensor(coordinator, "temperature")
    sensor.async_get_last_sensor_data = AsyncMock(return_value=None)

    await sensor.async_added_to_hass()

    assert sensor._restored_value is None


async def test_a_new_entry_restores_nothing(coordinator):
    coordinator.is_new_entry = True
    sensor = make_sensor(coordinator, "wasted_volume_total")
    sensor.async_get_last_sensor_data = AsyncMock(return_value=stored(250.0))

    await sensor.async_added_to_hass()

    assert sensor._restored_value is None
    assert coordinator.lifetime_wasted_volume_total == 0.0


async def test_restored_none_value_does_not_touch_the_totals(coordinator):
    sensor = make_sensor(coordinator, "wasted_volume_total")
    sensor.async_get_last_sensor_data = AsyncMock(return_value=stored(None))

    await sensor.async_added_to_hass()

    assert coordinator.lifetime_wasted_volume_total == 0.0


# ---------------------------------------------------------------------------
# Bluetooth status sensor
# ---------------------------------------------------------------------------


async def test_status_sensor_defaults_to_waiting_without_data(coordinator):
    coordinator.data = None

    assert HydraoBluetoothStatusSensor(coordinator).native_value == BT_STATUS_WAITING


async def test_status_sensor_reports_the_coordinator_status(coordinator):
    coordinator.data = {"bluetooth_status": BT_STATUS_SUCCESS}

    assert HydraoBluetoothStatusSensor(coordinator).native_value == BT_STATUS_SUCCESS


async def test_status_sensor_without_a_status_key_defaults_to_waiting(coordinator):
    coordinator.data = {"temperature": 30}

    assert HydraoBluetoothStatusSensor(coordinator).native_value == BT_STATUS_WAITING


async def test_status_sensor_offers_every_status(coordinator):
    options = HydraoBluetoothStatusSensor(coordinator).options

    assert set(options) == {
        BT_STATUS_WAITING,
        BT_STATUS_CONNECTING,
        BT_STATUS_SUCCESS,
        BT_STATUS_ERROR,
        BT_STATUS_WRITING_SYNC,
        BT_STATUS_SYNC_APPLIED,
        BT_STATUS_SYNC_FAILED,
        BT_STATUS_REBOOTING,
    }


# ---------------------------------------------------------------------------
# RSSI sensor
# ---------------------------------------------------------------------------


async def test_rssi_is_unavailable_until_a_value_is_known(coordinator):
    sensor = HydraoRealTimeRSSISensor(coordinator)

    assert sensor.available is False


@pytest.mark.parametrize(
    ("status", "available"),
    [
        (BT_STATUS_SUCCESS, True),
        (BT_STATUS_WRITING_SYNC, True),
        (BT_STATUS_SYNC_APPLIED, True),
        (BT_STATUS_SYNC_FAILED, True),
        (BT_STATUS_REBOOTING, True),
        (BT_STATUS_WAITING, False),
        (BT_STATUS_CONNECTING, False),
        (BT_STATUS_ERROR, False),
    ],
)
async def test_rssi_is_available_only_while_connected(coordinator, status, available):
    sensor = HydraoRealTimeRSSISensor(coordinator)
    sensor._attr_native_value = -60
    coordinator.data = {"bluetooth_status": status}

    assert sensor.available is available


async def test_rssi_is_unavailable_without_coordinator_data(coordinator):
    sensor = HydraoRealTimeRSSISensor(coordinator)
    sensor._attr_native_value = -60
    coordinator.data = None

    assert sensor.available is False


async def test_rssi_takes_the_last_known_value_when_added(hass, coordinator):
    sensor = HydraoRealTimeRSSISensor(coordinator)
    sensor.hass = hass
    sensor.async_write_ha_state = MagicMock()

    with (
        patch(
            f"{SENSOR_MODULE}.async_last_service_info", return_value=MagicMock(rssi=-71)
        ),
        patch(f"{SENSOR_MODULE}.async_register_callback"),
    ):
        await sensor.async_added_to_hass()

    assert sensor.native_value == -71
    sensor.async_write_ha_state.assert_called()


async def test_rssi_stays_empty_when_nothing_was_seen_yet(hass, coordinator):
    sensor = HydraoRealTimeRSSISensor(coordinator)
    sensor.hass = hass
    sensor.async_write_ha_state = MagicMock()

    with (
        patch(f"{SENSOR_MODULE}.async_last_service_info", return_value=None),
        patch(f"{SENSOR_MODULE}.async_register_callback"),
    ):
        await sensor.async_added_to_hass()

    assert sensor.native_value is None


async def _rssi_sensor_and_callback(hass, coordinator, last_rssi=None):
    """An RSSI sensor added to hass, and the advertisement callback it
    registered. State writes made while being added are forgotten."""
    sensor = HydraoRealTimeRSSISensor(coordinator)
    sensor.hass = hass
    sensor.async_write_ha_state = MagicMock()
    last_info = None if last_rssi is None else MagicMock(rssi=last_rssi)

    with (
        patch(f"{SENSOR_MODULE}.async_last_service_info", return_value=last_info),
        patch(f"{SENSOR_MODULE}.async_register_callback") as register,
    ):
        await sensor.async_added_to_hass()

    sensor.async_write_ha_state.reset_mock()
    return sensor, register.call_args.args[1]


async def test_rssi_does_not_rewrite_the_state_when_the_value_is_unchanged(
    hass, coordinator
):
    sensor, on_advertisement = await _rssi_sensor_and_callback(
        hass, coordinator, last_rssi=-70
    )

    on_advertisement(MagicMock(rssi=-70), MagicMock())

    sensor.async_write_ha_state.assert_not_called()


async def test_rssi_writes_its_state_at_most_once_per_second(hass, coordinator):
    sensor, on_advertisement = await _rssi_sensor_and_callback(
        hass, coordinator, last_rssi=-70
    )

    with patch(f"{SENSOR_MODULE}.time.monotonic") as clock:
        clock.return_value = 1000.0
        on_advertisement(MagicMock(rssi=-60), MagicMock())
        assert sensor.native_value == -60
        assert sensor.async_write_ha_state.call_count == 1

        # a change 0.4 s later is too soon: skipped, state untouched
        clock.return_value = 1000.4
        on_advertisement(MagicMock(rssi=-61), MagicMock())
        assert sensor.native_value == -60
        assert sensor.async_write_ha_state.call_count == 1

        # one second after the last write, the new value goes through
        clock.return_value = 1001.0
        on_advertisement(MagicMock(rssi=-61), MagicMock())

    assert sensor.native_value == -61
    assert sensor.async_write_ha_state.call_count == 2


async def test_rssi_is_disabled_by_default(hass, integration):
    """Signal strength is a support tool: off until the user asks for it."""
    registry = er.async_get(hass)
    target = registry.async_get_entity_id("sensor", "hydrao_custom", f"{ADDRESS}_rssi")

    assert (
        registry.async_get(target).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    )
    assert hass.states.get(target) is None


async def test_rssi_tracks_new_advertisements_once_enabled(hass, integration):
    registry = er.async_get(hass)
    target = registry.async_get_entity_id("sensor", "hydrao_custom", f"{ADDRESS}_rssi")

    registry.async_update_entity(target, disabled_by=None)
    await hass.config_entries.async_reload(integration.entry.entry_id)
    await hass.async_block_till_done()

    # Not connected yet: the passively scanned value is hidden.
    assert hass.states.get(target).state == STATE_UNAVAILABLE

    integration.entry.runtime_data.set_bt_status(BT_STATUS_SUCCESS)
    await hass.async_block_till_done()
    assert hass.states.get(target).state == "-70"

    integration.rssi_callbacks[-1](MagicMock(rssi=-55), MagicMock())
    await hass.async_block_till_done()
    assert hass.states.get(target).state == "-55"


# ---------------------------------------------------------------------------
# Soaping duration sensor
# ---------------------------------------------------------------------------


async def test_soaping_sensor_reports_the_device_value(coordinator):
    coordinator.static_data["soaping_duration"] = 150

    assert HydraoSoapingDurationSensor(coordinator).native_value == 150


async def test_soaping_sensor_falls_back_to_the_restored_value(coordinator):
    sensor = HydraoSoapingDurationSensor(coordinator)
    sensor._restored_value = "200"

    assert sensor.native_value == 200


async def test_soaping_sensor_ignores_an_unreadable_restored_value(coordinator):
    sensor = HydraoSoapingDurationSensor(coordinator)
    sensor._restored_value = "n/a"

    assert sensor.native_value is None


async def test_soaping_sensor_with_nothing_known_reports_nothing(coordinator):
    assert HydraoSoapingDurationSensor(coordinator).native_value is None


async def test_soaping_sensor_restores_its_last_value(coordinator):
    sensor = HydraoSoapingDurationSensor(coordinator)
    sensor.async_get_last_sensor_data = AsyncMock(return_value=stored(210))

    await sensor.async_added_to_hass()

    assert sensor.native_value == 210


async def test_soaping_sensor_with_nothing_stored(coordinator):
    sensor = HydraoSoapingDurationSensor(coordinator)
    sensor.async_get_last_sensor_data = AsyncMock(return_value=None)

    await sensor.async_added_to_hass()

    assert sensor.native_value is None


async def test_soaping_sensor_of_a_new_entry_restores_nothing(coordinator):
    coordinator.is_new_entry = True
    sensor = HydraoSoapingDurationSensor(coordinator)
    sensor.async_get_last_sensor_data = AsyncMock(return_value=stored(210))

    await sensor.async_added_to_hass()

    assert sensor.native_value is None


def test_soaping_description_is_a_diagnostic_duration():
    assert SOAPING_DURATION_DESC.key == "soaping_duration"


# ---------------------------------------------------------------------------
# Pending configuration sensor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("soaping", "thresholds", "colors", "expected"),
    [
        (None, None, None, "none"),
        (120, None, None, "soaping"),
        (None, [1, 2, 3, 4], None, "thresholds"),
        (None, None, [(1, 1, 1)] * 4, "colors"),
        (120, [1, 2, 3, 4], None, "soaping_thresholds"),
        (120, None, [(1, 1, 1)] * 4, "soaping_colors"),
        (None, [1, 2, 3, 4], [(1, 1, 1)] * 4, "thresholds_colors"),
        (120, [1, 2, 3, 4], [(1, 1, 1)] * 4, "soaping_thresholds_colors"),
    ],
)
async def test_pending_config_sensor_lists_what_is_waiting(
    coordinator, soaping, thresholds, colors, expected
):
    coordinator.pending_soaping_duration = soaping
    coordinator.pending_thresholds = thresholds
    coordinator.pending_colors = colors
    sensor = HydraoPendingConfigSensor(coordinator)

    assert sensor.native_value == expected
    assert expected in sensor.options
