# Copyright (c) 2026 Adrien40
# SPDX-License-Identifier: GPL-3.0-only

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

from bleak import BleakClient
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection
from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_clear_advertisement_history,
    async_last_service_info,
    async_register_callback,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import (
    CONNECTION_BLUETOOTH,
    DeviceInfo,
)
from homeassistant.helpers.device_registry import (
    async_get as async_get_device_registry,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    BT_STATUS_CONNECTING,
    BT_STATUS_ERROR,
    BT_STATUS_REBOOTING,
    BT_STATUS_SUCCESS,
    BT_STATUS_SYNC_APPLIED,
    BT_STATUS_SYNC_FAILED,
    BT_STATUS_WAITING,
    BT_STATUS_WRITING_SYNC,
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
    DEFAULT_MIN_TEMP_THRESHOLD,
    DEFAULT_SOAPING_DURATION,
    DOMAIN,
    DURATION_TICKS_PER_SECOND,
    FLOW_RAW_CONSTANT,
    ISSUE_TRACKER_URL,
    MAX_NEW_SHOWER_ATTEMPTS,
    MAX_THRESHOLD_VALUE,
    MAX_WATER_TEMP,
    MIN_THRESHOLD_VALUE,
    MIN_WATER_TEMP,
    STORAGE_SAVE_DELAY,
    STORAGE_VERSION,
    TEMPERATURE_RAW_UNITS_PER_DEGREE,
    HydraoConfigEntry,
)
from .util import (
    clamp_soaping_duration,
    comfort_fraction,
    duration_ticks_delta,
    is_plausible_water_temp,
    storage_key,
    thresholds_fit_in_byte,
    thresholds_strictly_increasing,
)

_LOGGER = logging.getLogger(__name__)

# Exceptions that indicate a normal BLE hiccup (device off, out of range,
# transient link error) rather than a programming bug. Kept narrow on
# purpose so real bugs (IndexError, TypeError, etc.) still surface.
_BLE_TRANSIENT_ERRORS = (BleakError, TimeoutError, OSError, EOFError)

# How long we wait, after the last passively-seen advertisement, before we
# consider the device to have gone silent (water off / device asleep).
#
# We rely on bluetooth.async_register_callback(..., BluetoothScanningMode.
# PASSIVE), which is event-driven: it fires as soon as the underlying
# scanner (BlueZ, an ESPHome proxy, etc.) hands a new advertisement to
# Home Assistant's central Bluetooth manager, with no artificial polling
# delay added by Home Assistant itself.
#
# We deliberately do NOT use bluetooth.async_address_present() /
# async_track_unavailable() for this: those are built for a much coarser
# "is this entity still available" use case and can take up to several
# minutes to reflect that a device has actually gone silent (confirmed in
# testing: the device was still reported "present" 5 minutes after the
# water was turned off). That is far too slow for what we need here.
#
# Measured with nRF Connect on the real Hydrao hardware: the advertising
# interval while awake is ~101 ms (roughly 10 advertisements/second), and
# the device stops advertising entirely as soon as the water is turned
# off. A 1 second grace period is therefore both:
#   - Long enough to absorb an isolated dropped/collided packet (at a
#     101 ms interval, missing 1 second means missing ~10 packets in a
#     row, which is not a normal fluke).
#   - Short enough to reflect the "water off" state almost immediately,
#     well before the much longer soaping_duration timeout (which is a
#     separate concept: it ends the current shower *session*, it does not
#     detect loss of radio signal).
ADVERTISEMENT_GRACE_PERIOD = 1.0


class HydraoDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for Hydrao BLE device.

    This coordinator does not use the standard polling interval (update_interval=None)
    or _async_update_data. Instead, it relies on a continuous BLE background loop
    (async_run_loop) to process real-time notifications and spontaneous connections
    when the water flows.
    """

    config_entry: HydraoConfigEntry

    def __init__(self, hass: HomeAssistant, entry: HydraoConfigEntry) -> None:
        self.address = entry.data[CONF_ADDRESS]
        short_mac = self.address.replace(":", "")[-4:]
        default_name = f"Hydrao {short_mac}"

        self.device_name = entry.data.get("name", default_name)

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=self.device_name,
            update_interval=None,
        )

        self.static_data: dict[str, Any] = {
            "firmware": entry.data.get("firmware", "unknown"),
            "hardware": entry.data.get("hardware", "unknown"),
            "device_id": entry.data.get("device_id", "unknown"),
        }

        self.last_valid_data: dict[str, Any] = {"bluetooth_status": BT_STATUS_WAITING}
        self._raw_cfg: bytearray | None = None
        self._raw_cfg_lock = asyncio.Lock()

        self.pending_thresholds: list[int] | None = None
        self.pending_colors: list[tuple[int, int, int]] | None = None
        self.pending_soaping_duration: int | None = None
        self.pending_new_shower: bool = False
        self.new_shower_attempts: int = 0
        self._new_shower_write_sent: bool = False

        self.is_new_entry = not entry.data.get("has_connected_once", False)

        opts = entry.options if entry.options else entry.data
        self.min_temp_threshold = float(
            opts.get("min_temp_threshold", DEFAULT_MIN_TEMP_THRESHOLD)
        )
        self.auto_sync_at_comfort = bool(opts.get("auto_sync_at_comfort", False))

        self._last_processed_options = dict(opts) if entry.options else {}

        if "soaping_duration" in opts:
            self.static_data["soaping_duration"] = clamp_soaping_duration(
                int(opts["soaping_duration"])
            )

        # Only trust the stored thresholds / colors when all four are there:
        # a partial set must not stop the integration from loading. Whatever
        # is missing is read from the device on the first connection.
        threshold_keys = [f"threshold_{i}" for i in range(1, 5)]
        if all(key in opts for key in threshold_keys):
            self.static_data["thresholds"] = [int(opts[key]) for key in threshold_keys]

        color_keys = [f"threshold_{i}_color" for i in range(1, 5)]
        if all(key in opts for key in color_keys):
            self.static_data["colors"] = [tuple(opts[key]) for key in color_keys]

        self.last_seen_time = 0.0
        self.force_reset_flag = False

        # time.monotonic() timestamp of when the "new shower" (0x01) command
        # was requested — specifically, the last moment the water was
        # actually confirmed flowing at that point (see force_end_shower()
        # and _trigger_auto_sync_at_comfort()). Used to discard the command
        # if it's still pending by the time we reconnect but so much time
        # has passed that the device's own soaping_duration timeout has
        # since reset its counters on its own; sending a stale command at
        # that point would incorrectly reset an unrelated, already-running
        # session instead of doing nothing (which is what should happen).
        #
        # Deliberately time.monotonic(), not time.time(): this value is only
        # ever compared against another later reading of the same clock to
        # get an elapsed duration, never shown to the user as a wall-clock
        # timestamp. time.time() can jump forward or backward (NTP sync,
        # manual clock changes — common right after boot on a Pi/VM with no
        # battery-backed RTC), which would either discard a still-valid
        # pending command as falsely "stale", or make an actually-stale one
        # look fresh. time.monotonic() is unaffected by those jumps.
        self._new_shower_requested_at = 0.0

        # Timestamp (time.monotonic()-based, via HA's callback data) of
        # the last passively-seen BLE advertisement for this device,
        # independent of any connection attempt. Used to detect "no radio
        # signal at all" (water off) as distinct from "signal present but
        # the connection/read failed" (a genuine error). See
        # ADVERTISEMENT_GRACE_PERIOD above for the reasoning.
        self._last_advertisement_time = 0.0

        # All durations are in seconds.
        self.session_wasted_volume = 0.0
        self.session_shower_duration_comfort = 0.0
        self.session_shower_duration_cold = 0.0
        self.session_shower_volume_comfort = 0.0
        # Total duration of the session, accumulated from successive
        # deltas of the device's uint16 tick counter (so it keeps counting
        # correctly past the counter's wrap-around).
        self.session_duration_total = 0.0
        # Seconds of cold water before comfort was first reached in the
        # current session; None while not reached, or when it cannot be
        # known (the very first reading of a session was already warm).
        self.session_time_to_comfort: float | None = None
        self._session_comfort_seen = False

        self.lifetime_wasted_volume_total = 0.0
        self.lifetime_shower_volume_comfort_total = 0.0
        # The totals live in their own file. `totals_loaded_from_store` tells
        # the total sensors whether it already holds them: when it does, the
        # value those sensors restore from their last state is ignored. When it
        # does not (first start after an upgrade from 1.0.0, which kept the
        # totals only in the sensors' state), that restored value is adopted
        # once and written to the file: the migration.
        self._store: Store[dict[str, float]] = Store(
            hass, STORAGE_VERSION, storage_key(entry.entry_id)
        )
        self.totals_loaded_from_store = False
        self._totals_save_scheduled = False

        self._last_shower_raw = 0.0
        self._last_duration_ticks = 0
        # Temperature of the previous reading in the current session, used
        # to split an interval that straddles the comfort threshold.
        self._last_temp_raw: float | None = None

        # The last frames received from the device, as hex, kept for the
        # diagnostics file: they are what lets us tell how a device revision
        # encodes its values when the decoded figures look wrong.
        self.last_raw_frames: dict[str, str | None] = {}
        self._implausible_temperature_warned = False

        self._thresholds_read_for_session = False
        self._thresholds_need_reread = False

        self._awaiting_manual_reset_confirmation = False
        self._comfort_sync_sent_for_session = False
        self._preserve_wasted_on_next_reset = False

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.address)},
            connections={(CONNECTION_BLUETOOTH, self.address)},
            name=self.config_entry.title,
            manufacturer="Hydrao",
            sw_version=self.static_data.get("firmware", "unknown"),
            hw_version=self.static_data.get("hardware", "unknown"),
            serial_number=self.static_data.get("device_id", "unknown"),
        )

    def async_start_bluetooth_listener(self) -> Callable[[], None]:
        """Start passively tracking BLE advertisements for this device,
        independently of any connection attempt.

        This registers a callback with BluetoothScanningMode.PASSIVE,
        which only asks Home Assistant to notify us of advertisements it
        already receives — it does not request an active scan and does
        not compete with the connectable scan used by
        async_ble_device_from_address()/establish_connection() for the
        actual GATT connection.

        We record our own time.monotonic() reading when the callback
        fires (rather than trusting a timestamp field from the event),
        so there is a single, consistent clock used throughout for this
        check — see ADVERTISEMENT_GRACE_PERIOD above.

        Returns the unsubscribe callable; the caller is responsible for
        wiring it up to entry.async_on_unload().
        """
        last_info = async_last_service_info(self.hass, self.address, connectable=False)
        if last_info:
            # Use the advertisement's own timestamp (same monotonic clock),
            # not "now": a cached advertisement can be minutes old, and
            # counting it as fresh would trigger a pointless connection
            # attempt at startup.
            self._last_advertisement_time = last_info.time

        @callback
        def _async_on_advertisement(
            service_info: BluetoothServiceInfoBleak, change: BluetoothChange
        ) -> None:
            self._last_advertisement_time = time.monotonic()

        return async_register_callback(
            self.hass,
            _async_on_advertisement,
            BluetoothCallbackMatcher(address=self.address),
            BluetoothScanningMode.PASSIVE,
        )

    def _has_recent_advertisement(self) -> bool:
        """Return True if a BLE advertisement was passively seen recently
        enough (within ADVERTISEMENT_GRACE_PERIOD) to consider the device
        still "on the air" — as opposed to having gone silent because the
        water was turned off."""
        if self._last_advertisement_time <= 0:
            return False
        return (
            time.monotonic() - self._last_advertisement_time
        ) <= ADVERTISEMENT_GRACE_PERIOD

    def set_bt_status(self, status: str) -> None:
        if self.last_valid_data.get("bluetooth_status") != status:
            self.last_valid_data = dict(self.last_valid_data)
            self.last_valid_data["bluetooth_status"] = status
            self.async_set_updated_data(self.last_valid_data)

    def async_update_options(self, options: Mapping[str, Any]) -> None:
        if dict(options) == self._last_processed_options:
            return
        self._last_processed_options = dict(options)

        self.min_temp_threshold = float(
            options.get("min_temp_threshold", DEFAULT_MIN_TEMP_THRESHOLD)
        )
        self.auto_sync_at_comfort = bool(options.get("auto_sync_at_comfort", False))

        self._queue_pending_writes_from_options(options)

        self.async_update_listeners()

    def _queue_pending_writes_from_options(self, options: Mapping[str, Any]) -> None:
        """Compare the device's known config against `options` and queue
        whatever differs for the next write. Called both when options
        actually change (async_update_options) and after every fresh
        reconnect, so a change queued while HA was cold (before any
        device state was known) still gets picked up once the real
        device state becomes available again — even across restarts.
        """
        if "soaping_duration" in options:
            live_soaping = self.static_data.get("soaping_duration")
            requested_soaping = int(options["soaping_duration"])
            new_soaping = clamp_soaping_duration(requested_soaping)
            if new_soaping != requested_soaping:
                _LOGGER.warning(
                    "Soaping duration %ds is out of range, using %ds instead",
                    requested_soaping,
                    new_soaping,
                )
            if live_soaping is None or new_soaping != live_soaping:
                self.pending_soaping_duration = new_soaping

        live_thresholds = self.static_data.get("thresholds")
        live_colors = self.static_data.get("colors")

        new_thresh = []
        new_colors = []
        for i in range(4):
            key = f"threshold_{i + 1}"
            color_key = f"threshold_{i + 1}_color"

            if key in options:
                new_thresh.append(int(options[key]))
            elif live_thresholds is not None:
                new_thresh.append(live_thresholds[i])
            else:
                # Never connected since HA started, and this key wasn't
                # submitted either: nothing to queue, nothing to compare
                # against. Bail out entirely rather than build a partial
                # array.
                return

            if color_key in options:
                r, g, b = options[color_key]
                new_colors.append((int(r), int(g), int(b)))
            elif live_colors is not None:
                new_colors.append(tuple(live_colors[i]))
            else:
                return

        if not thresholds_strictly_increasing(new_thresh):
            _LOGGER.warning(
                "Refusing to sync thresholds %s to the device: they must "
                "be strictly increasing (Threshold 1 < 2 < 3 < 4).",
                new_thresh,
            )
        elif not thresholds_fit_in_byte(new_thresh):
            _LOGGER.warning(
                "Refusing to sync thresholds %s to the device: each one must "
                "be between %d and %d.",
                new_thresh,
                MIN_THRESHOLD_VALUE,
                MAX_THRESHOLD_VALUE,
            )
        elif live_thresholds is None or live_thresholds != new_thresh:
            self.pending_thresholds = new_thresh

        if live_colors is None or live_colors != new_colors:
            self.pending_colors = new_colors

    def _sync_device_config_to_ha_options(self) -> None:
        new_options = dict(self.config_entry.options)
        needs_update = False

        if "thresholds" in self.static_data and self.pending_thresholds is None:
            for i in range(4):
                k = f"threshold_{i + 1}"
                val = self.static_data["thresholds"][i]
                if new_options.get(k) != val:
                    new_options[k] = val
                    needs_update = True

        if "colors" in self.static_data and self.pending_colors is None:
            for i in range(4):
                k = f"threshold_{i + 1}_color"
                val = list(self.static_data["colors"][i])
                if new_options.get(k) != val:
                    new_options[k] = val
                    needs_update = True

        if (
            "soaping_duration" in self.static_data
            and self.pending_soaping_duration is None
        ):
            val = self.static_data["soaping_duration"]
            if new_options.get("soaping_duration") != val:
                new_options["soaping_duration"] = val
                needs_update = True

        if needs_update:
            self.hass.config_entries.async_update_entry(
                self.config_entry, options=new_options
            )

    def _no_pending_operation_in_progress(self) -> bool:
        """True if nothing is currently pending that would give a
        temporary radio silence (a reboot after a "new shower" command,
        or a config write in flight) a meaning other than "water off".

        Shared by async_run_loop() and _evaluate_offline_timeout() so
        both agree on when it's safe to show BT_STATUS_WAITING — see the
        call sites for why this matters.
        """
        return (
            not self.pending_thresholds
            and not self.pending_colors
            and self.pending_soaping_duration is None
            and not self.pending_new_shower
            and self.last_valid_data.get("bluetooth_status") != BT_STATUS_ERROR
        )

    def _handle_no_advertisement_tick(self) -> None:
        """Called once per loop iteration when no BLE advertisement has
        been seen recently (see _has_recent_advertisement()).

        Home Assistant's Bluetooth manager no longer considers the device
        present at all. This usually means water off / device asleep —
        but it's also exactly what happens for a second or two right
        after we send a "new shower" (0x01) command: the device reboots
        to apply it, and a rebooting device stops advertising too. Only
        report BT_STATUS_WAITING ("water off") when nothing we ourselves
        triggered explains the silence — otherwise leave whatever status
        _handle_pending_new_shower()/_apply_pending_config_write() already
        set (e.g. BT_STATUS_REBOOTING) on screen; it's more accurate to
        what's actually happening, and matches the same rule
        _evaluate_offline_timeout() already uses.

        Extracted out of async_run_loop() so it can be exercised directly
        in tests without needing to drive the surrounding infinite loop.
        """
        if self._no_pending_operation_in_progress():
            self.set_bt_status(BT_STATUS_WAITING)

        # Clear the advertisement de-dupe history *now*, while there is no
        # active connection to disturb. The Hydrao's advertisement payload
        # is static, so without this, the very next real advertisement
        # (e.g. when the water is turned back on) could be silently
        # dropped as a "duplicate" of one we saw long ago, and we'd never
        # get a fresh callback to update _last_advertisement_time —
        # permanently stuck here. Doing this every tick while idle keeps
        # us always ready to catch that next one.
        async_clear_advertisement_history(self.hass, self.address)

        self._evaluate_offline_timeout()

    async def async_run_loop(self) -> None:
        await asyncio.sleep(1)

        while True:
            if not self._has_recent_advertisement():
                self._handle_no_advertisement_tick()
                await asyncio.sleep(1)
                continue

            try:
                await self._connect_and_read_stream()
            except _BLE_TRANSIENT_ERRORS as e:
                # Reaching this point means the device WAS discoverable
                # (otherwise _connect_and_read_stream() would have already
                # returned early with BT_STATUS_WAITING without raising),
                # and the connection or a read still failed. That's a
                # genuine, user-visible failure, not just "water off".
                _LOGGER.debug(
                    "BLE connection/read failed (device was found but the "
                    "connection or a read failed): %s",
                    e,
                )
                self.set_bt_status(BT_STATUS_ERROR)
            except Exception:
                _LOGGER.exception("Unexpected error in the Hydrao BLE loop")
                self.set_bt_status(BT_STATUS_ERROR)

            self._evaluate_offline_timeout()
            await asyncio.sleep(1)

    async def _async_write_pending_config(
        self,
        client: BleakClient,
        thresholds: list[int] | None,
        colors: list[tuple[int, int, int]] | None,
    ) -> bool:
        if not thresholds and not colors:
            return True

        async with self._raw_cfg_lock:
            if self._raw_cfg is None:
                await self._read_thresholds_locked(client)
                if self._raw_cfg is None:
                    return False

            if thresholds and len(thresholds) != 4:
                _LOGGER.error(
                    "Refusing to write malformed thresholds payload: "
                    "expected 4 values, got %d",
                    len(thresholds),
                )
                return False

            if colors and len(colors) != 4:
                _LOGGER.error(
                    "Refusing to write malformed colors payload: "
                    "expected 4 values, got %d",
                    len(colors),
                )
                return False

            try:
                if thresholds:
                    for i in range(4):
                        self._raw_cfg[i * 4] = thresholds[i]

                if colors:
                    for i in range(4):
                        r, g, b = colors[i]
                        self._raw_cfg[i * 4 + 1] = max(0, min(255, int(r)))
                        self._raw_cfg[i * 4 + 2] = max(0, min(255, int(g)))
                        self._raw_cfg[i * 4 + 3] = max(0, min(255, int(b)))

                await client.write_gatt_char(
                    CHAR_CONFIG, bytes(self._raw_cfg), response=True
                )
                return True
            except _BLE_TRANSIENT_ERRORS as e:
                _LOGGER.error("Failed to write pending config: %s", e)
                return False

    @staticmethod
    def _duration_raw_ticks(dur_data: bytearray) -> int:
        """Return the device's raw duration counter (uint16, 1/50 s ticks)."""
        return (dur_data[1] << 8) | dur_data[0]

    async def _handle_pending_new_shower(self, client: BleakClient) -> None:
        if not self.pending_new_shower:
            return

        timeout_sec = self.static_data.get("soaping_duration", DEFAULT_SOAPING_DURATION)
        elapsed_since_water_stopped = time.monotonic() - self._new_shower_requested_at
        if elapsed_since_water_stopped > timeout_sec:
            # The device's own soaping_duration timeout has already reset
            # its internal counters on its own since the water stopped —
            # long enough ago that this command is now stale. Sending it
            # anyway would incorrectly reset an unrelated, already-running
            # session instead of doing nothing, so discard it silently.
            _LOGGER.info(
                "Discarding stale 'new shower' command: %.0fs have passed "
                "since the water stopped, longer than the device's own "
                "%ds soaping timeout — its counters have already reset "
                "on their own.",
                elapsed_since_water_stopped,
                timeout_sec,
            )
            self.pending_new_shower = False
            self.new_shower_attempts = 0
            self._awaiting_manual_reset_confirmation = False
            return

        if self.new_shower_attempts >= MAX_NEW_SHOWER_ATTEMPTS:
            _LOGGER.error(
                "Giving up on 'new shower' command after %d attempts; "
                "the device never reported a fresh volume reading.",
                self.new_shower_attempts,
            )
            self.pending_new_shower = False
            self.new_shower_attempts = 0
            self._awaiting_manual_reset_confirmation = False
            self.set_bt_status(BT_STATUS_ERROR)
            return

        self.new_shower_attempts += 1

        # Switch the UI to rebooting status immediately to prevent freezing
        self.set_bt_status(BT_STATUS_WRITING_SYNC)
        self.set_bt_status(BT_STATUS_REBOOTING)

        try:
            # Give the write just enough time to physically go out over
            # the radio (typically a few ms to a few tens of ms at the
            # BLE link layer), then stop waiting for an ACK that will
            # essentially never come — the device reboots on receipt
            # without acknowledging. Must not be ~0: cancelling the
            # awaited write before it has actually reached the BLE stack
            # could abort the transmission itself, not just the wait for
            # a reply, silently failing to reboot the device at all.
            await asyncio.wait_for(
                client.write_gatt_char(CHAR_NEW_SHOWER, b"\x01", response=True),
                timeout=0.5,
            )
            _LOGGER.debug(
                "Sent 'new shower' command (attempt %d/%d)",
                self.new_shower_attempts,
                MAX_NEW_SHOWER_ATTEMPTS,
            )
        except _BLE_TRANSIENT_ERRORS as e:
            _LOGGER.debug(
                "No ack for new shower command (device probably "
                "rebooting, this is expected): %s",
                e,
            )

        self._new_shower_write_sent = True

    async def _async_write_soaping_duration(
        self, client: BleakClient, value: int
    ) -> bool:
        try:
            payload = value.to_bytes(2, byteorder="little")
            await client.write_gatt_char(CHAR_SOAPING_DURATION, payload, response=True)
            return True
        except _BLE_TRANSIENT_ERRORS as e:
            _LOGGER.error("Failed to write soaping duration: %s", e)
            return False

    async def _apply_pending_config_write(self, client: BleakClient) -> bool:
        thresholds_to_write = self.pending_thresholds
        colors_to_write = self.pending_colors
        soaping_to_write = self.pending_soaping_duration

        if not thresholds_to_write and not colors_to_write and soaping_to_write is None:
            return True

        self.set_bt_status(BT_STATUS_WRITING_SYNC)

        if thresholds_to_write or colors_to_write:
            success = await self._async_write_pending_config(
                client, thresholds_to_write, colors_to_write
            )
            if success:
                if thresholds_to_write:
                    self.static_data["thresholds"] = list(thresholds_to_write)
                    if self.pending_thresholds == thresholds_to_write:
                        self.pending_thresholds = None
                if colors_to_write:
                    self.static_data["colors"] = list(colors_to_write)
                    if self.pending_colors == colors_to_write:
                        self.pending_colors = None
            else:
                self.set_bt_status(BT_STATUS_ERROR)
                return False

        if soaping_to_write is not None:
            success = await self._async_write_soaping_duration(client, soaping_to_write)
            if success:
                self.static_data["soaping_duration"] = soaping_to_write
                if self.pending_soaping_duration == soaping_to_write:
                    self.pending_soaping_duration = None
            else:
                self.set_bt_status(BT_STATUS_ERROR)
                return False

        self.set_bt_status(BT_STATUS_SYNC_APPLIED)
        return True

    async def _async_read_thresholds(self, client: BleakClient) -> None:
        async with self._raw_cfg_lock:
            await self._read_thresholds_locked(client)

    async def _read_thresholds_locked(self, client: BleakClient) -> None:
        """Read and store the thresholds/colors config.

        Must only be called with self._raw_cfg_lock already held (asyncio.Lock
        is not reentrant, so re-acquiring it from a task that already holds it
        would deadlock). Use _async_read_thresholds() instead when the lock
        is not already held.
        """
        try:
            cfg = await client.read_gatt_char(CHAR_CONFIG)
            if cfg and len(cfg) >= 16:
                self._raw_cfg = bytearray(cfg)
                self.static_data["thresholds"] = [cfg[0], cfg[4], cfg[8], cfg[12]]
                self.static_data["colors"] = [
                    (cfg[1], cfg[2], cfg[3]),
                    (cfg[5], cfg[6], cfg[7]),
                    (cfg[9], cfg[10], cfg[11]),
                    (cfg[13], cfg[14], cfg[15]),
                ]
                self.async_set_updated_data(self.last_valid_data)
        except _BLE_TRANSIENT_ERRORS as e:
            _LOGGER.warning("Could not read Config: %s", e)

    async def _async_read_soaping_duration(self, client: BleakClient) -> None:
        try:
            raw = await client.read_gatt_char(CHAR_SOAPING_DURATION)
            if raw and len(raw) >= 2:
                self.static_data["soaping_duration"] = (raw[1] << 8) | raw[0]
        except _BLE_TRANSIENT_ERRORS as e:
            _LOGGER.warning("Could not read Soaping Duration: %s", e)

    async def _connect_and_read_stream(self) -> None:
        ble_device = async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if not ble_device:
            # Device not even discoverable (water off / device asleep).
            # Whatever config is pending will simply be retried on the
            # next successful connection, there's nothing to do right
            # now, so always reflect "waiting" here regardless of any
            # pending writes.
            self.set_bt_status(BT_STATUS_WAITING)
            return

        self.set_bt_status(BT_STATUS_CONNECTING)
        client = await establish_connection(BleakClient, ble_device, self.address)
        self._raw_cfg = None

        try:
            if self._new_shower_write_sent and self.pending_new_shower:
                # We sent the reboot command last time and the connection has
                # since dropped and come back: the device reconnecting is far
                # stronger proof of an actual reboot than comparing volume
                # readings, which a fast flow during the reboot delay could
                # fool. Confirm here, before even reading fresh data.
                _LOGGER.debug(
                    "New shower command confirmed (device reconnected after reboot)"
                )
                self.pending_new_shower = False
                self.new_shower_attempts = 0
                self._awaiting_manual_reset_confirmation = False
                self.set_bt_status(BT_STATUS_SUCCESS)
            self._new_shower_write_sent = False

            new_data = dict(self.config_entry.data)
            entry_needs_update = False

            if new_data.get("firmware", "unknown") == "unknown":
                try:
                    fw = await client.read_gatt_char(CHAR_FIRMWARE)
                    new_data["firmware"] = fw.decode(errors="ignore").strip("\x00")
                    entry_needs_update = True
                except _BLE_TRANSIENT_ERRORS as e:
                    _LOGGER.warning("Could not read Firmware: %s", e)

            if new_data.get("hardware", "unknown") == "unknown":
                try:
                    hw = await client.read_gatt_char(CHAR_HARDWARE)
                    if hw:
                        new_data["hardware"] = str(hw[0])
                        entry_needs_update = True
                    else:
                        _LOGGER.warning("Could not read Hardware: empty value")
                except _BLE_TRANSIENT_ERRORS as e:
                    _LOGGER.warning("Could not read Hardware: %s", e)

            if new_data.get("device_id", "unknown") == "unknown":
                try:
                    uid = await client.read_gatt_char(CHAR_UNIQUE_ID)
                    new_data["device_id"] = uid.hex()
                    entry_needs_update = True
                except _BLE_TRANSIENT_ERRORS as e:
                    _LOGGER.warning("Could not read Unique ID: %s", e)

            if entry_needs_update:
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )

                dev_reg = async_get_device_registry(self.hass)
                dev_reg.async_get_or_create(
                    config_entry_id=self.config_entry.entry_id,
                    identifiers={(DOMAIN, self.address)},
                    connections={(CONNECTION_BLUETOOTH, self.address)},
                    manufacturer="Hydrao",
                    name=self.config_entry.title,
                    sw_version=new_data.get("firmware", "unknown"),
                    hw_version=new_data.get("hardware", "unknown"),
                    serial_number=new_data.get("device_id", "unknown"),
                )

            self.static_data["firmware"] = new_data.get("firmware", "unknown")
            self.static_data["hardware"] = new_data.get("hardware", "unknown")
            self.static_data["device_id"] = new_data.get("device_id", "unknown")

            inline_write_attempts = 0
            config_write_failed_this_session = False
            device_config_read = False

            while client.is_connected:
                try:
                    vol_data = await client.read_gatt_char(CHAR_VOLUME_AND_DURATION)
                    dur_data = await client.read_gatt_char(CHAR_DURATION_RAW)
                    temp_data = await client.read_gatt_char(CHAR_TEMPERATURE_RAW)
                except _BLE_TRANSIENT_ERRORS as e:
                    _LOGGER.debug("Skipping this read cycle: %s", e)
                    await asyncio.sleep(1)
                    continue

                try:
                    flow_raw_data = await client.read_gatt_char(CHAR_FLOW_RAW)
                except _BLE_TRANSIENT_ERRORS:
                    flow_raw_data = None

                self._process_live_data(vol_data, dur_data, temp_data, flow_raw_data)

                if not device_config_read:
                    # The device settings are read once the first live
                    # reading has been taken, not before it: the shower is
                    # already running, and each read before that first
                    # reading delays it, which can make the cold phase go
                    # unseen (see `time_to_comfort`).
                    device_config_read = True
                    await self._async_read_thresholds(client)
                    await self._async_read_soaping_duration(client)
                    self._queue_pending_writes_from_options(self.config_entry.options)
                    # This read follows a live reading, which is exactly
                    # what `_thresholds_need_reread` asks for.
                    self._thresholds_need_reread = False
                    self._sync_device_config_to_ha_options()

                if self._thresholds_need_reread:
                    self._thresholds_need_reread = False
                    await self._async_read_thresholds(client)
                    self._sync_device_config_to_ha_options()

                if self.pending_new_shower:
                    await self._handle_pending_new_shower(client)

                    # If the reboot command was actually sent, break out of the loop
                    # right away. This closes the connection on HA's side instantly,
                    # instead of waiting through the OS's ~10s timeout.
                    if self._new_shower_write_sent:
                        break

                    await asyncio.sleep(1)
                    continue

                if not config_write_failed_this_session and (
                    self.pending_thresholds
                    or self.pending_colors
                    or self.pending_soaping_duration is not None
                ):
                    success = await self._apply_pending_config_write(client)

                    if success:
                        inline_write_attempts = 0
                        continue

                    inline_write_attempts += 1
                    if inline_write_attempts < 2:
                        await asyncio.sleep(1)
                        continue

                    _LOGGER.error(
                        "Giving up on pending config write after %d failed "
                        "attempts this session; will retry automatically "
                        "the next time the device connects.",
                        inline_write_attempts,
                    )
                    config_write_failed_this_session = True
                    inline_write_attempts = 0
                    self.set_bt_status(BT_STATUS_SYNC_FAILED)

                if (
                    not config_write_failed_this_session
                    and not self.pending_thresholds
                    and not self.pending_colors
                    and self.pending_soaping_duration is None
                ):
                    self.set_bt_status(BT_STATUS_SUCCESS)

                await asyncio.sleep(0.3)

        finally:
            await self._async_close_connection(client)

            # The Hydrao advertisement payload is completely static
            # (empty manufacturer_data/service_data/service_uuids), so
            # Home Assistant's Bluetooth manager de-duplicates repeat
            # packets and won't hand a new one to our passive callback
            # until the payload changes. Clear the cached history on
            # every disconnect so the very next advertisement — even if
            # byte-for-byte identical to the last one — is delivered to
            # _async_on_advertisement() and refreshes
            # _last_advertisement_time.
            async_clear_advertisement_history(self.hass, self.address)

    async def _async_close_connection(self, client: BleakClient) -> None:
        """Close the connection, whatever ended the cycle.

        `establish_connection` returns a client that is already connected, so
        it is disconnected here. Entering it as a context manager would call
        `connect()` a second time: stock bleak refuses that, and Home
        Assistant's wrapper only ignores it, which is not something to rely on.

        A failure to disconnect is only logged. The link is often already gone
        when the shower stops, and that must neither turn the end of a shower
        into a connection error nor hide the exception that ended the cycle.
        """
        try:
            await client.disconnect()
        except _BLE_TRANSIENT_ERRORS as err:
            _LOGGER.debug("Error while closing the connection: %s", err)
        else:
            _LOGGER.debug("Bluetooth connection closed")

    def _process_live_data(
        self,
        vol_data: bytearray,
        dur_data: bytearray,
        temp_data: bytearray,
        flow_raw_data: bytearray | None,
    ) -> None:
        self._record_raw_frames(vol_data, dur_data, temp_data, flow_raw_data)

        if len(vol_data) < 4 or len(dur_data) < 2 or len(temp_data) < 2:
            _LOGGER.debug(
                "Ignoring malformed BLE frame (vol=%d, dur=%d, temp=%d bytes)",
                len(vol_data),
                len(dur_data),
                len(temp_data),
            )
            return

        total_raw = (vol_data[1] << 8) | vol_data[0]
        shower_raw = float((vol_data[3] << 8) | vol_data[2])
        duration_ticks = self._duration_raw_ticks(dur_data)
        decoded_temp = (
            (temp_data[1] << 8) | temp_data[0]
        ) / TEMPERATURE_RAW_UNITS_PER_DEGREE
        # None when the reading cannot be a real water temperature: it is
        # then left out of every cold / comfort computation instead of being
        # counted as (very) hot water.
        temperature: float | None = None
        if is_plausible_water_temp(decoded_temp):
            temperature = decoded_temp
        else:
            self._warn_implausible_temperature(decoded_temp)

        # time.monotonic(), not time.time(): last_seen_time is only ever
        # diffed against another later reading of this same clock (see
        # _evaluate_offline_timeout() below), never shown as a wall-clock
        # timestamp, so it must not be affected by a system clock jump.
        current_time = time.monotonic()
        is_new_session = self.force_reset_flag or self.last_seen_time == 0.0

        if self.is_new_entry:
            self.is_new_entry = False
            new_entry_data = dict(self.config_entry.data)
            new_entry_data["has_connected_once"] = True
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=new_entry_data
            )

        if is_new_session:
            self._reset_session_state(
                preserve_wasted=self._preserve_wasted_on_next_reset
            )
            self._preserve_wasted_on_next_reset = False
            self.force_reset_flag = False
            delta_vol = shower_raw
            delta_ticks = duration_ticks
        else:
            if shower_raw < self._last_shower_raw:
                self._reset_session_state(
                    preserve_wasted=self._preserve_wasted_on_next_reset
                )
                self._preserve_wasted_on_next_reset = False
                self._awaiting_manual_reset_confirmation = False
                if self.pending_new_shower:
                    _LOGGER.debug(
                        "New shower command confirmed (raw volume dropped "
                        "from %.0f to %.0f)",
                        self._last_shower_raw,
                        shower_raw,
                    )
                    self.pending_new_shower = False
                    self.new_shower_attempts = 0
                    self.set_bt_status(BT_STATUS_SUCCESS)
                delta_vol = shower_raw
                delta_ticks = duration_ticks
            else:
                delta_vol = shower_raw - self._last_shower_raw
                delta_ticks = duration_ticks_delta(
                    self._last_duration_ticks, duration_ticks
                )

        if self._awaiting_manual_reset_confirmation:
            delta_vol = 0.0
            delta_ticks = 0

        delta_vol = max(0.0, delta_vol)
        delta_dur = max(0, delta_ticks) / DURATION_TICKS_PER_SECOND

        previous_temp = self._last_temp_raw
        self.session_duration_total += delta_dur

        if temperature is not None:
            # Split the interval between comfort and cold according to how
            # the temperature evolved since the previous reading, instead of
            # attributing everything to the current reading's temperature.
            comfort_share = comfort_fraction(
                previous_temp, temperature, self.min_temp_threshold
            )

            comfort_vol = delta_vol * comfort_share
            cold_vol = delta_vol - comfort_vol
            comfort_dur = delta_dur * comfort_share
            cold_dur = delta_dur - comfort_dur

            self.session_wasted_volume += cold_vol
            self.lifetime_wasted_volume_total += cold_vol
            self.session_shower_duration_cold += cold_dur
            self.session_shower_volume_comfort += comfort_vol
            self.lifetime_shower_volume_comfort_total += comfort_vol
            self.session_shower_duration_comfort += comfort_dur

            if cold_vol > 0.0 or comfort_vol > 0.0:
                self._async_schedule_totals_save()

            # Comfort is reached on the first reading at or above the
            # threshold. This is deliberately NOT tested through
            # `comfort_share > 0`: the probe resolves 0.5 C and the threshold
            # is set in 0.5 C steps, so a reading landing exactly on the
            # threshold is common during a gradual warm-up. Interpolating
            # between the previous reading and that one gives a comfort share
            # of exactly 0, which would skip this block; the next reading
            # would then see a previous temperature that is no longer below
            # the threshold, and the time to comfort would stay unknown for
            # the whole session.
            if (
                temperature >= self.min_temp_threshold
                and not self._session_comfort_seen
            ):
                self._session_comfort_seen = True
                # Only report a time-to-comfort when we actually watched the
                # water go from cold to comfortable. If the session's very
                # first reading was already warm, the cold phase (if any)
                # happened before we connected and cannot be measured.
                if (
                    previous_temp is not None
                    and previous_temp < self.min_temp_threshold
                ):
                    self.session_time_to_comfort = self.session_shower_duration_cold

        if (
            self.auto_sync_at_comfort
            and not self._comfort_sync_sent_for_session
            and shower_raw > 0
            and temperature is not None
            and temperature >= self.min_temp_threshold
        ):
            self._comfort_sync_sent_for_session = True

            if self.session_wasted_volume > 0:
                self._trigger_auto_sync_at_comfort()

        flow_rate = 0.0
        if flow_raw_data and len(flow_raw_data) >= 2:
            raw_v1 = (flow_raw_data[1] << 8) | flow_raw_data[0]
            if raw_v1 > 0:
                flow_rate = FLOW_RAW_CONSTANT / raw_v1

        if delta_vol == 0 and delta_dur == 0:
            flow_rate = 0.0

        if shower_raw > 0 and not self._thresholds_read_for_session:
            self._thresholds_read_for_session = True
            self._thresholds_need_reread = True

        self._last_shower_raw = shower_raw
        self._last_duration_ticks = duration_ticks
        # A reading that was left out must not serve as the starting point of
        # the next interval.
        self._last_temp_raw = temperature
        self.last_seen_time = current_time

        new_data = {
            "firmware": self.static_data.get("firmware", "unknown"),
            "hardware": self.static_data.get("hardware", "unknown"),
            "device_id": self.static_data.get("device_id", "unknown"),
            "temperature": None
            if self._awaiting_manual_reset_confirmation
            else temperature,
            "total_volume": float(total_raw),
            "flow_rate": 0.0 if self._awaiting_manual_reset_confirmation else flow_rate,
            "wasted_volume": self.session_wasted_volume,
            "wasted_volume_total": self.lifetime_wasted_volume_total,
            "shower_volume_comfort_total": self.lifetime_shower_volume_comfort_total,
            "shower_volume_comfort": self.session_shower_volume_comfort,
            "shower_duration_comfort": self.session_shower_duration_comfort,
            "shower_duration_cold": self.session_shower_duration_cold,
            "time_to_comfort": self.session_time_to_comfort,
            "raw": {
                "shower_volume_raw": 0.0
                if self._awaiting_manual_reset_confirmation
                else shower_raw,
                "shower_duration": 0.0
                if self._awaiting_manual_reset_confirmation
                else self.session_duration_total,
            },
        }

        new_data["bluetooth_status"] = self.last_valid_data.get(
            "bluetooth_status", BT_STATUS_WAITING
        )

        self.last_valid_data = new_data
        self.async_set_updated_data(self.last_valid_data)

    def _record_raw_frames(
        self,
        vol_data: bytearray,
        dur_data: bytearray,
        temp_data: bytearray,
        flow_raw_data: bytearray | None,
    ) -> None:
        """Keep the frames as received, and log them whenever they change."""
        frames: dict[str, str | None] = {
            "volume": vol_data.hex(),
            "duration": dur_data.hex(),
            "temperature": temp_data.hex(),
            "flow": flow_raw_data.hex() if flow_raw_data is not None else None,
        }
        if frames != self.last_raw_frames:
            _LOGGER.debug("Raw BLE frames: %s", frames)
        self.last_raw_frames = frames

    def _warn_implausible_temperature(self, temperature: float) -> None:
        """Say once, with what is needed to fix it, that the temperature
        cannot be decoded for this device."""
        if self._implausible_temperature_warned:
            return
        self._implausible_temperature_warned = True
        _LOGGER.warning(
            "Ignoring a water temperature of %.1f °C (raw frame %s): it is "
            "outside the %.0f-%.0f °C range. This device (firmware %s, "
            "hardware %s) may encode the temperature differently. Please "
            "report it, with the diagnostics file, at %s",
            temperature,
            self.last_raw_frames.get("temperature"),
            MIN_WATER_TEMP,
            MAX_WATER_TEMP,
            self.static_data.get("firmware", "unknown"),
            self.static_data.get("hardware", "unknown"),
            ISSUE_TRACKER_URL,
        )

    def _reset_session_state(self, preserve_wasted: bool = False) -> None:
        """Zero the per-session accumulators.

        With `preserve_wasted` (comfort-mode auto-sync, where the physical
        shower goes on), everything that describes the cold phase is kept:
        wasted volume, cold duration and the time it took to reach comfort.
        """
        if not preserve_wasted:
            self.session_wasted_volume = 0.0
            self.session_shower_duration_cold = 0.0
            self.session_time_to_comfort = None
            self._session_comfort_seen = False
        self.session_shower_duration_comfort = 0.0
        self.session_shower_volume_comfort = 0.0
        self.session_duration_total = 0.0
        self._last_temp_raw = None
        self._thresholds_read_for_session = False

    def _evaluate_offline_timeout(self) -> None:
        if self.last_seen_time <= 0:
            return

        if self._no_pending_operation_in_progress():
            # Don't stomp on an ERROR status that was just set this same
            # tick (device found but connection/read failed) — let it
            # stay visible until the next connection attempt resolves it,
            # instead of silently reverting to "waiting" right away.
            self.set_bt_status(BT_STATUS_WAITING)

            if self.last_valid_data.get("flow_rate", 0) > 0:
                self.last_valid_data = dict(self.last_valid_data)
                self.last_valid_data["flow_rate"] = 0.0
                self.async_set_updated_data(self.last_valid_data)

        timeout_sec = self.static_data.get("soaping_duration", DEFAULT_SOAPING_DURATION)
        if (time.monotonic() - self.last_seen_time) > timeout_sec:
            self.force_reset_flag = True
            self.last_seen_time = 0.0
            self._comfort_sync_sent_for_session = False
            self._awaiting_manual_reset_confirmation = False

    def _reset_live_display_fields(self, reset_wasted: bool = False) -> None:
        if not self.last_valid_data:
            return

        self.last_valid_data = dict(self.last_valid_data)
        if reset_wasted:
            self.last_valid_data["wasted_volume"] = 0.0
            self.last_valid_data["shower_duration_cold"] = 0.0
            self.last_valid_data["time_to_comfort"] = None
        self.last_valid_data["shower_volume_comfort"] = 0.0
        self.last_valid_data["shower_duration_comfort"] = 0.0
        self.last_valid_data["flow_rate"] = 0.0
        # Unknown, not 0 C: a zero would be recorded as a real reading and
        # drag down the history and its averages.
        self.last_valid_data["temperature"] = None

        if "raw" in self.last_valid_data:
            self.last_valid_data["raw"] = dict(self.last_valid_data["raw"])
        else:
            self.last_valid_data["raw"] = {}

        self.last_valid_data["raw"]["shower_volume_raw"] = 0.0
        self.last_valid_data["raw"]["shower_duration"] = 0.0

        self.async_set_updated_data(self.last_valid_data)

    def force_end_shower(self) -> None:
        self.force_reset_flag = True
        # Capture when the water actually last stopped flowing BEFORE
        # resetting last_seen_time below, so we can later tell whether
        # the pending command has gone stale.
        self._new_shower_requested_at = (
            self.last_seen_time if self.last_seen_time > 0.0 else time.monotonic()
        )
        self.last_seen_time = 0.0
        self.pending_new_shower = True
        self.new_shower_attempts = 0
        self._comfort_sync_sent_for_session = False
        self._awaiting_manual_reset_confirmation = True
        self._reset_session_state()
        self._reset_live_display_fields(reset_wasted=True)

    def _trigger_auto_sync_at_comfort(self) -> None:
        self.pending_new_shower = True
        self.new_shower_attempts = 0
        self._new_shower_requested_at = (
            self.last_seen_time if self.last_seen_time > 0.0 else time.monotonic()
        )
        self._preserve_wasted_on_next_reset = True
        self._awaiting_manual_reset_confirmation = True
        self._reset_session_state(preserve_wasted=True)

    async def async_load_totals(self) -> None:
        """Load the lifetime totals saved by a previous run, if there are any.

        Without a saved file the totals are left untouched: see the comment on
        `totals_loaded_from_store` for how they are then migrated.
        """
        try:
            stored = await self._store.async_load()
        except HomeAssistantError as err:
            _LOGGER.warning(
                "Could not read the saved totals, using the last known values "
                "of the sensors instead: %s",
                err,
            )
            return

        if not stored:
            return

        try:
            wasted = float(stored["wasted_volume_total"])
            comfort = float(stored["shower_volume_comfort_total"])
        except (KeyError, TypeError, ValueError):
            _LOGGER.warning("Ignoring unreadable saved totals: %r", stored)
            return

        self.lifetime_wasted_volume_total = wasted
        self.lifetime_shower_volume_comfort_total = comfort
        self.totals_loaded_from_store = True

        # Publish them right away: the total sensors then show the saved
        # values even before the first Bluetooth reading, whatever state the
        # entities were restored to.
        self.last_valid_data = {
            **self.last_valid_data,
            "wasted_volume_total": wasted,
            "shower_volume_comfort_total": comfort,
        }
        self.async_set_updated_data(self.last_valid_data)

    def _totals_snapshot(self) -> dict[str, float]:
        """The data to save. Called by the Store when it actually writes."""
        self._totals_save_scheduled = False
        return {
            "wasted_volume_total": self.lifetime_wasted_volume_total,
            "shower_volume_comfort_total": self.lifetime_shower_volume_comfort_total,
        }

    @callback
    def _async_schedule_totals_save(self) -> None:
        """Have the totals written soon, at most once every STORAGE_SAVE_DELAY.

        Once a save is pending, further changes simply ride on it: the data is
        read when the write happens. Calling the Store on every reading instead
        would keep postponing the write (its delay restarts at each call), so
        nothing would be written until the shower is over.
        """
        if self._totals_save_scheduled:
            return
        self._totals_save_scheduled = True
        self._store.async_delay_save(self._totals_snapshot, STORAGE_SAVE_DELAY)

    async def async_save_totals(self) -> None:
        """Write the totals now, if a save is pending (the entry unloads)."""
        if not self._totals_save_scheduled:
            return
        await self._store.async_save(self._totals_snapshot())

    def restore_wasted_volume_total(self, value: float) -> None:
        self.lifetime_wasted_volume_total = value
        self._async_schedule_totals_save()

    def restore_shower_volume_comfort_total(self, value: float) -> None:
        self.lifetime_shower_volume_comfort_total = value
        self._async_schedule_totals_save()
