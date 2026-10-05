# Copyright (c) 2026 Adrien40
# SPDX-License-Identifier: GPL-3.0-only

import logging

from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import PLATFORMS, STORAGE_VERSION, HydraoConfigEntry
from .coordinator import HydraoDataUpdateCoordinator
from .util import storage_key

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: HydraoConfigEntry) -> bool:
    coordinator = HydraoDataUpdateCoordinator(hass, entry)
    entry.runtime_data = coordinator

    # Before the entities exist, so they find the saved totals in place.
    await coordinator.async_load_totals()

    entry.async_on_unload(entry.add_update_listener(async_reload_options))
    entry.async_on_unload(coordinator.async_start_bluetooth_listener())
    # Write any pending change of the totals when the entry unloads.
    entry.async_on_unload(coordinator.async_save_totals)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_create_background_task(
        hass, coordinator.async_run_loop(), "hydrao_ble_loop"
    )

    return True


async def async_reload_options(hass: HomeAssistant, entry: HydraoConfigEntry) -> None:
    """Handle options update without crashing the BLE loop."""
    entry.runtime_data.async_update_options(entry.options)


async def async_unload_entry(hass: HomeAssistant, entry: HydraoConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: HydraoConfigEntry) -> None:
    # The entry is already unloaded here, so nothing can write the file again.
    store: Store[dict[str, float]] = Store(
        hass, STORAGE_VERSION, storage_key(entry.entry_id)
    )
    await store.async_remove()
    _LOGGER.info(
        "Successfully removed Hydrao integration and cleared data for %s",
        entry.data.get(CONF_ADDRESS, "unknown device"),
    )
