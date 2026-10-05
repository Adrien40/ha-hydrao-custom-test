# Copyright (c) 2026 Adrien40
# SPDX-License-Identifier: GPL-3.0-only

"""The lifetime totals (wasted volume, comfort volume) are saved in their own
file, so they survive a crash and no longer depend on the sensors' state.

Covers: loading, the throttled delayed save, the final save when the entry
unloads, deleting the file when the entry is removed, and the migration from
1.0.0 (where the totals only lived in the sensors' restored state)."""

import logging
from unittest.mock import AsyncMock, patch

import pytest
from helpers import make_frames
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from custom_components.hydrao_custom.const import STORAGE_SAVE_DELAY, STORAGE_VERSION
from custom_components.hydrao_custom.util import storage_key

ADDRESS = "AA:BB:CC:DD:EE:FF"


def feed(coordinator, *, shower, ticks, temp):
    """Feed one BLE reading to the coordinator."""
    vol, dur, tmp = make_frames(
        total=1000, shower=shower, duration_ticks=ticks, temp_c=temp
    )
    coordinator._process_live_data(vol, dur, tmp, None)


def put_in_storage(hass_storage, key, data):
    """What a previous run left in the .storage folder."""
    hass_storage[key] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": key,
        "data": data,
    }


@pytest.fixture
def saved_totals(hass_storage, mock_entry):
    """Totals saved by a previous run, in place before the entry is set up."""
    key = storage_key(mock_entry.entry_id)
    put_in_storage(
        hass_storage,
        key,
        {"wasted_volume_total": 12.0, "shower_volume_comfort_total": 34.0},
    )
    return key


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


async def test_totals_are_loaded_from_the_saved_file(coordinator, hass_storage):
    key = storage_key(coordinator.config_entry.entry_id)
    put_in_storage(
        hass_storage,
        key,
        {"wasted_volume_total": 42.5, "shower_volume_comfort_total": 310.0},
    )

    await coordinator.async_load_totals()

    assert coordinator.lifetime_wasted_volume_total == 42.5
    assert coordinator.lifetime_shower_volume_comfort_total == 310.0
    assert coordinator.totals_loaded_from_store is True
    # published at once, so the sensors show them before any Bluetooth reading
    assert coordinator.data["wasted_volume_total"] == 42.5
    assert coordinator.data["shower_volume_comfort_total"] == 310.0


async def test_without_a_saved_file_the_totals_are_left_untouched(coordinator):
    await coordinator.async_load_totals()

    assert coordinator.lifetime_wasted_volume_total == 0.0
    assert coordinator.lifetime_shower_volume_comfort_total == 0.0
    assert coordinator.totals_loaded_from_store is False
    assert coordinator.data is None


@pytest.mark.parametrize(
    "data",
    [
        {"wasted_volume_total": 1.0},
        {"wasted_volume_total": "garbage", "shower_volume_comfort_total": 2.0},
        {"wasted_volume_total": None, "shower_volume_comfort_total": 2.0},
    ],
)
async def test_unreadable_saved_totals_are_ignored(
    coordinator, hass_storage, caplog, data
):
    put_in_storage(hass_storage, storage_key(coordinator.config_entry.entry_id), data)

    with caplog.at_level(logging.WARNING):
        await coordinator.async_load_totals()

    assert "Ignoring unreadable saved totals" in caplog.text
    assert coordinator.lifetime_wasted_volume_total == 0.0
    assert coordinator.totals_loaded_from_store is False


async def test_a_storage_error_does_not_stop_the_setup(coordinator, caplog):
    with (
        patch.object(
            coordinator._store,
            "async_load",
            AsyncMock(side_effect=HomeAssistantError("boom")),
        ),
        caplog.at_level(logging.WARNING),
    ):
        await coordinator.async_load_totals()

    assert "Could not read the saved totals" in caplog.text
    assert coordinator.totals_loaded_from_store is False


async def test_the_totals_are_loaded_when_the_entry_is_set_up(
    saved_totals, integration
):
    coordinator = integration.coordinator

    assert coordinator.lifetime_wasted_volume_total == 12.0
    assert coordinator.lifetime_shower_volume_comfort_total == 34.0
    assert coordinator.totals_loaded_from_store is True


# ---------------------------------------------------------------------------
# Saving: at most one pending save, whatever the number of readings
# ---------------------------------------------------------------------------


async def test_many_readings_schedule_a_single_save(coordinator):
    """The Store postpones its write at every call: calling it on each reading
    would keep pushing the save back until the shower is over."""
    with patch.object(coordinator._store, "async_delay_save") as delay_save:
        feed(coordinator, shower=10, ticks=500, temp=20.0)
        feed(coordinator, shower=20, ticks=1000, temp=20.0)
        feed(coordinator, shower=30, ticks=1500, temp=20.0)

    delay_save.assert_called_once_with(coordinator._totals_snapshot, STORAGE_SAVE_DELAY)


async def test_a_new_save_is_scheduled_once_the_previous_one_was_written(coordinator):
    with patch.object(coordinator._store, "async_delay_save") as delay_save:
        feed(coordinator, shower=10, ticks=500, temp=20.0)
        feed(coordinator, shower=20, ticks=1000, temp=20.0)
        assert delay_save.call_count == 1

        # what the Store does when the delay is over and it writes
        snapshot = coordinator._totals_snapshot()
        assert snapshot["wasted_volume_total"] == pytest.approx(20.0)

        feed(coordinator, shower=30, ticks=1500, temp=20.0)

    assert delay_save.call_count == 2


async def test_a_reading_that_changes_nothing_schedules_no_save(coordinator):
    with patch.object(coordinator._store, "async_delay_save") as delay_save:
        feed(coordinator, shower=0, ticks=0, temp=20.0)

    delay_save.assert_not_called()


async def test_the_snapshot_holds_both_totals_and_clears_the_pending_flag(coordinator):
    coordinator.lifetime_wasted_volume_total = 5.0
    coordinator.lifetime_shower_volume_comfort_total = 7.0
    coordinator._totals_save_scheduled = True

    snapshot = coordinator._totals_snapshot()

    assert snapshot == {
        "wasted_volume_total": 5.0,
        "shower_volume_comfort_total": 7.0,
    }
    assert coordinator._totals_save_scheduled is False


# ---------------------------------------------------------------------------
# Final save
# ---------------------------------------------------------------------------


async def test_pending_totals_are_written_by_the_final_save(coordinator, hass_storage):
    feed(coordinator, shower=10, ticks=500, temp=20.0)
    feed(coordinator, shower=20, ticks=1000, temp=20.0)
    key = storage_key(coordinator.config_entry.entry_id)
    assert key not in hass_storage  # still only scheduled

    await coordinator.async_save_totals()

    assert hass_storage[key]["version"] == STORAGE_VERSION
    assert hass_storage[key]["data"]["wasted_volume_total"] == pytest.approx(20.0)
    assert hass_storage[key]["data"]["shower_volume_comfort_total"] == 0.0
    assert coordinator._totals_save_scheduled is False


async def test_the_final_save_does_nothing_when_nothing_is_pending(
    coordinator, hass_storage
):
    """No needless file, and no zeros written over totals that were never
    loaded (for instance because the total sensors are disabled)."""
    await coordinator.async_save_totals()

    assert storage_key(coordinator.config_entry.entry_id) not in hass_storage


async def test_unloading_the_entry_writes_the_pending_totals(
    hass, hass_storage, integration
):
    key = storage_key(integration.entry.entry_id)
    integration.coordinator.restore_wasted_volume_total(77.0)
    assert key not in hass_storage

    assert await hass.config_entries.async_unload(integration.entry.entry_id)
    await hass.async_block_till_done()

    assert hass_storage[key]["data"]["wasted_volume_total"] == 77.0


# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------


async def test_removing_the_entry_deletes_the_saved_totals(
    hass, hass_storage, saved_totals, integration
):
    assert saved_totals in hass_storage

    await hass.config_entries.async_remove(integration.entry.entry_id)
    await hass.async_block_till_done()

    assert saved_totals not in hass_storage


# ---------------------------------------------------------------------------
# Migration from 1.0.0: the totals only lived in the sensors' restored state
# ---------------------------------------------------------------------------


async def test_a_total_adopted_from_the_sensor_schedules_a_save(coordinator):
    with patch.object(coordinator._store, "async_delay_save") as delay_save:
        coordinator.restore_wasted_volume_total(123.0)
        coordinator.restore_shower_volume_comfort_total(456.0)

    assert coordinator.lifetime_wasted_volume_total == 123.0
    assert coordinator.lifetime_shower_volume_comfort_total == 456.0
    # one pending save carries both values
    delay_save.assert_called_once_with(coordinator._totals_snapshot, STORAGE_SAVE_DELAY)
    assert coordinator._totals_snapshot() == {
        "wasted_volume_total": 123.0,
        "shower_volume_comfort_total": 456.0,
    }


def last_state_of(entity_id, value):
    """What Home Assistant restored for a total sensor: its last state."""
    extra = {"native_value": value, "native_unit_of_measurement": "L"}
    return State(entity_id, str(value)), extra


async def test_the_totals_survive_an_upgrade_from_1_0_0(
    hass, hass_storage, integration
):
    """End to end: 1.0.0 kept the totals only in the sensors' last state. The
    first start of the new version adopts them and writes them to the file;
    from then on the file is the source and a stale sensor state is ignored."""
    entry_id = integration.entry.entry_id
    key = storage_key(entry_id)
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", "hydrao_custom", f"{ADDRESS}_wasted_volume_total"
    )
    assert entity_id is not None

    # The 1.0.0 situation: no saved file, the total is in the sensor's state.
    assert await hass.config_entries.async_unload(entry_id)
    hass_storage.pop(key, None)
    mock_restore_cache_with_extra_data(hass, [last_state_of(entity_id, 250.0)])

    # First start of the new version: the total is adopted...
    assert await hass.config_entries.async_setup(entry_id)
    await hass.async_block_till_done()
    coordinator = integration.entry.runtime_data
    assert coordinator.lifetime_wasted_volume_total == 250.0
    assert coordinator.totals_loaded_from_store is False

    # ...and written to the file when the entry unloads.
    assert await hass.config_entries.async_unload(entry_id)
    await hass.async_block_till_done()
    assert hass_storage[key]["data"]["wasted_volume_total"] == 250.0

    # Later starts: the file is the source; an outdated state is ignored.
    mock_restore_cache_with_extra_data(hass, [last_state_of(entity_id, 999.0)])
    assert await hass.config_entries.async_setup(entry_id)
    await hass.async_block_till_done()
    coordinator = integration.entry.runtime_data
    assert coordinator.totals_loaded_from_store is True
    assert coordinator.lifetime_wasted_volume_total == 250.0
