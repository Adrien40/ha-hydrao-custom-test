"""Tests for the Blueriiot active cycle in the coordinator.

Drives a full active cycle with a fake probe that exposes the Blueriiot GATT
layout. A Zodiac probe must keep its original profile whatever the client
exposes: the last tests pin that down.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from bleak.exc import BleakError

from custom_components.blue_connect_local.const import (
    BT_STATUS_ERROR,
    BT_STATUS_SUCCESS,
    CHAR_AUTH_UUID,
    CHAR_NOTIFY_UUID,
    CHAR_TRIGGER_UUID,
)
from custom_components.blue_connect_local.coordinator import store_key

from .conftest import entity_id, make_entry
from .helpers import ACCESS_CODE, MAC, FakeBlueClient, build_frame, clean_hex
from .helpers_blueriiot import (
    AUTH,
    EXTRA_1,
    EXTRA_2,
    FRAME_HEX_LEN,
    NOTIFY_1,
    NOTIFY_2,
    NOTIFY_3,
    TRIGGER,
    FakeBlueriiotClient,
    blueriiot_layout,
    build_blueriiot_frame,
    make_services,
)


@pytest.fixture
def probe(ble) -> FakeBlueriiotClient:
    """Blueriiot probe wired into the simulated Bluetooth."""
    ble.client = FakeBlueriiotClient()
    return ble.client


# ---------------------------------------------------------------------------
# Coordinator: Blueriiot active cycle
# ---------------------------------------------------------------------------
async def test_blueriiot_cycle_populates_data(coordinator, probe):
    await coordinator.async_refresh()
    d = coordinator.data
    assert d["bluetooth_status"] == BT_STATUS_SUCCESS
    assert d["receive_method"] == "active"
    assert d["device_type"] == "blueriiot"
    assert d["temperature"] == pytest.approx(25.02)
    assert d["ph"] == pytest.approx(7.4, abs=0.01)
    assert d["orp"] == 700
    assert d["conductivity"] == pytest.approx(1061.5)
    assert d["battery"] == 3520
    assert d["battery_level"] == 50
    assert d["raw_frame"] == build_blueriiot_frame().hex().upper()
    assert len(d["raw_frame"]) == FRAME_HEX_LEN


async def test_blueriiot_values_reach_the_entities(hass, coordinator, probe):
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    def state(key):
        return hass.states.get(entity_id(hass, "sensor", key)).state

    assert float(state("temperature")) == pytest.approx(25.02)
    assert float(state("ph")) == pytest.approx(7.4, abs=0.01)
    assert float(state("orp")) == 700


async def test_blueriiot_uses_blueriiot_characteristics(coordinator, probe):
    await coordinator.async_refresh()
    assert probe.writes == [
        (AUTH, ACCESS_CODE.encode("ascii")),
        (TRIGGER, b"\x02"),
        (EXTRA_1, b"\x02"),
        (EXTRA_2, b"\x02"),
    ]
    # Nothing is ever sent to the Zodiac characteristics.
    written = {uuid for uuid, _ in probe.writes}
    assert not {CHAR_AUTH_UUID, CHAR_TRIGGER_UUID} & written


async def test_blueriiot_never_rewrites_the_authentication_characteristic(
    coordinator, probe
):
    await coordinator.async_refresh()
    assert [data for uuid, data in probe.writes if uuid == AUTH] == [
        ACCESS_CODE.encode("ascii")
    ]


async def test_blueriiot_subscribes_then_unsubscribes_every_notify_characteristic(
    coordinator, probe
):
    await coordinator.async_refresh()
    assert probe.started == [NOTIFY_1, NOTIFY_2, NOTIFY_3]
    assert probe.stopped == [NOTIFY_1, NOTIFY_2, NOTIFY_3]
    assert CHAR_NOTIFY_UUID not in probe.started


async def test_blueriiot_only_uses_the_characteristics_the_probe_exposes(
    coordinator, ble
):
    layout = blueriiot_layout()
    del layout[NOTIFY_2], layout[NOTIFY_3], layout[EXTRA_1]
    ble.client = FakeBlueriiotClient(layout=layout)
    await coordinator.async_refresh()
    assert ble.client.started == [NOTIFY_1]
    assert [uuid for uuid, _ in ble.client.writes] == [AUTH, TRIGGER, EXTRA_2]


async def test_blueriiot_foreign_notifications_are_ignored(coordinator, ble, caplog):
    ble.client = FakeBlueriiotClient(
        notifications=[
            (NOTIFY_2, b"\x01\x02\x03"),  # status-like frame, wrong length
            (NOTIFY_3, build_blueriiot_frame(marker=0x10)),  # wrong marker
            (NOTIFY_1, build_blueriiot_frame()),
        ]
    )
    with caplog.at_level("DEBUG"):
        await coordinator.async_refresh()
    assert coordinator.data["bluetooth_status"] == BT_STATUS_SUCCESS
    assert coordinator.data["raw_frame"] == build_blueriiot_frame().hex().upper()
    assert "ignoring Blueriiot notification" in caplog.text


async def test_blueriiot_without_a_measurement_frame_is_a_clean_failure(
    coordinator, ble
):
    ble.client = FakeBlueriiotClient(notifications=[(NOTIFY_2, b"\x01\x02\x03")])
    await coordinator.async_refresh()
    assert coordinator.data["bluetooth_status"] != BT_STATUS_SUCCESS
    assert "ph" not in coordinator.data
    assert ble.client.disconnected


async def test_blueriiot_tolerates_a_failing_notify_subscription(coordinator, ble):
    ble.client = FakeBlueriiotClient(fail_notify={NOTIFY_1})
    ble.client.notifications = [(NOTIFY_2, build_blueriiot_frame())]
    await coordinator.async_refresh()
    assert coordinator.data["bluetooth_status"] == BT_STATUS_SUCCESS
    assert ble.client.started == [NOTIFY_2, NOTIFY_3]
    assert ble.client.stopped == [NOTIFY_2, NOTIFY_3]


async def test_blueriiot_fails_cleanly_when_no_subscription_works(coordinator, ble):
    ble.client = FakeBlueriiotClient(fail_notify={NOTIFY_1, NOTIFY_2, NOTIFY_3})
    with patch.object(
        coordinator, "_handle_ble_error", wraps=coordinator._handle_ble_error
    ) as handle_error:
        await coordinator.async_refresh()
    handle_error.assert_called_once_with(
        "Communication error: No usable Blueriiot notification", BT_STATUS_ERROR
    )
    assert coordinator.data["bluetooth_status"] != BT_STATUS_SUCCESS
    assert ble.client.writes == []  # nothing is sent without a way to listen
    assert ble.client.disconnected


async def test_blueriiot_fails_cleanly_when_the_probe_has_no_notify_characteristic(
    coordinator, ble
):
    layout = blueriiot_layout()
    del layout[NOTIFY_1], layout[NOTIFY_2], layout[NOTIFY_3]
    ble.client = FakeBlueriiotClient(layout=layout)
    await coordinator.async_refresh()
    assert coordinator.data["bluetooth_status"] != BT_STATUS_SUCCESS
    assert ble.client.disconnected


async def test_blueriiot_ignores_failing_extra_triggers(coordinator, ble, caplog):
    ble.client = FakeBlueriiotClient(fail_write={EXTRA_1, EXTRA_2})
    with caplog.at_level("DEBUG"):
        await coordinator.async_refresh()
    assert coordinator.data["bluetooth_status"] == BT_STATUS_SUCCESS
    assert "extra trigger" in caplog.text


async def test_blueriiot_main_trigger_failure_is_still_an_error(coordinator, ble):
    ble.client = FakeBlueriiotClient(fail_write={TRIGGER})
    await coordinator.async_refresh()
    assert coordinator.data["bluetooth_status"] != BT_STATUS_SUCCESS


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
async def test_blueriiot_measurement_survives_a_restart(
    setup_integration, hass_storage
):
    frame = build_blueriiot_frame().hex().upper()
    hass_storage[store_key(MAC)] = {
        "version": 1,
        "minor_version": 1,
        "key": store_key(MAC),
        "data": {
            "raw_frame": frame,
            "last_received": "2026-01-02T03:04:05+00:00",
            "device_type": "blueriiot",
            "ph_raw": 7.4,
            "ph": 7.4,
        },
    }
    coord = await setup_integration(make_entry())
    assert coord.data["raw_frame"] == frame
    assert coord.data["ph"] == 7.4
    assert coord.data["device_type"] == "blueriiot"


async def test_blueriiot_measurement_is_saved_with_its_frame(
    coordinator, probe, hass_storage
):
    await coordinator.async_refresh()
    await coordinator.async_save_to_disk()
    saved = hass_storage[store_key(MAC)]["data"]
    assert saved["raw_frame"] == build_blueriiot_frame().hex().upper()


# ---------------------------------------------------------------------------
# Zodiac must keep its profile
# ---------------------------------------------------------------------------
async def test_zodiac_probe_exposing_gatt_services_keeps_the_zodiac_profile(
    coordinator, ble
):
    ble.client.services = make_services(
        {
            CHAR_AUTH_UUID: ["write"],
            CHAR_TRIGGER_UUID: ["write"],
            CHAR_NOTIFY_UUID: ["notify"],
        }
    )
    await coordinator.async_refresh()
    assert ble.client.writes == [
        (CHAR_AUTH_UUID, ACCESS_CODE.encode("ascii")),
        (CHAR_TRIGGER_UUID, b"\x02"),
    ]
    assert coordinator.data["raw_frame"] == clean_hex(build_frame())
    assert "device_type" not in coordinator.data
    assert coordinator.data["has_conductivity"] is True


async def test_zodiac_notify_failure_reports_the_original_error(coordinator, ble):
    """Only Blueriiot may skip a failing subscription: Zodiac keeps the real cause."""

    async def broken_start_notify(uuid, handler):
        raise BleakError("subscribe refused")

    ble.client.start_notify = broken_start_notify
    with patch.object(
        coordinator, "_handle_ble_error", wraps=coordinator._handle_ble_error
    ) as handle_error:
        await coordinator.async_refresh()
    handle_error.assert_called_once_with(
        "Communication error: subscribe refused", BT_STATUS_ERROR
    )
    assert coordinator.data["bluetooth_status"] != BT_STATUS_SUCCESS
    assert ble.client.writes == []
    assert not ble.client.notify_stopped


class _ClientWithUnreadableServices(FakeBlueClient):
    """Like a real BleakClient whose service discovery is not available.

    bleak raises BleakError from `client.services` in that case (also when the
    collection is empty): reading it must never break a Zodiac cycle.
    """

    @property
    def services(self):
        raise BleakError("Service Discovery has not been performed yet")


async def test_zodiac_cycle_survives_unreadable_gatt_services(coordinator, ble, caplog):
    ble.client = _ClientWithUnreadableServices(frames=[build_frame()])
    with caplog.at_level("DEBUG"):
        await coordinator.async_refresh()
    assert coordinator.data["bluetooth_status"] == BT_STATUS_SUCCESS
    assert ble.client.writes == [
        (CHAR_AUTH_UUID, ACCESS_CODE.encode("ascii")),
        (CHAR_TRIGGER_UUID, b"\x02"),
    ]
    assert coordinator.data["raw_frame"] == clean_hex(build_frame())
    assert "device_type" not in coordinator.data
    assert "assuming the ZODIAC profile" in caplog.text


async def test_zodiac_cycle_with_empty_gatt_services(coordinator, ble):
    ble.client.services = []
    await coordinator.async_refresh()
    assert coordinator.data["bluetooth_status"] == BT_STATUS_SUCCESS
    assert coordinator.data["raw_frame"] == clean_hex(build_frame())
    assert "device_type" not in coordinator.data
