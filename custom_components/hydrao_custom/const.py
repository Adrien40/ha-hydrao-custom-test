# Copyright (c) 2026 Adrien40
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform

if TYPE_CHECKING:
    from .coordinator import HydraoDataUpdateCoordinator

DOMAIN = "hydrao_custom"
PLATFORMS = [Platform.SENSOR, Platform.BUTTON, Platform.NUMBER, Platform.SWITCH]

# Typed alias for the config entry, so entry.runtime_data is correctly
# typed as our coordinator wherever this alias is used instead of the
# plain ConfigEntry. The `type` statement is evaluated lazily, so the
# coordinator can be imported for type checking only (no import cycle).
type HydraoConfigEntry = ConfigEntry[HydraoDataUpdateCoordinator]

CHAR_FIRMWARE = "00002a26-0000-1000-8000-00805f9b34fb"
CHAR_VOLUME_AND_DURATION = "0000ca1c-0000-1000-8000-00805f9b34fb"
CHAR_CONFIG = "0000ca1d-0000-1000-8000-00805f9b34fb"
CHAR_NEW_SHOWER = "0000ca20-0000-1000-8000-00805f9b34fb"
CHAR_HARDWARE = "0000ca24-0000-1000-8000-00805f9b34fb"
CHAR_DURATION_RAW = "0000ca26-0000-1000-8000-00805f9b34fb"
CHAR_UNIQUE_ID = "0000ca28-0000-1000-8000-00805f9b34fb"
CHAR_FLOW_RAW = "0000ca31-0000-1000-8000-00805f9b34fb"
CHAR_TEMPERATURE_RAW = "0000ca32-0000-1000-8000-00805f9b34fb"
CHAR_SOAPING_DURATION = "0000ca33-0000-1000-8000-00805f9b34fb"

DEFAULT_SOAPING_DURATION = 180
MIN_SOAPING_DURATION = 10
MAX_SOAPING_DURATION = 600

# The device reports its shower duration as a little-endian uint16 counting
# 1/50 s ticks. Everything inside the integration is expressed in seconds
# (derived from integer tick differences); conversion to minutes, if wanted,
# is left to Home Assistant's unit display.
DURATION_TICKS_PER_SECOND = 50
DURATION_TICKS_WRAP = 1 << 16
# A drop of the raw duration counter is only interpreted as a uint16
# wrap-around when the previous reading was within this many ticks (10 s)
# of the counter's maximum. Any other drop is a device-side reset/glitch.
DURATION_WRAP_WINDOW_TICKS = 10 * DURATION_TICKS_PER_SECOND

DEFAULT_MIN_TEMP_THRESHOLD = 33.0

# Water temperatures outside this range cannot be real: a reading beyond it
# means the value was decoded with the wrong resolution (some device
# revisions may encode it differently) or the device sent a filler value.
MIN_WATER_TEMP = 0.0
MAX_WATER_TEMP = 100.0

# Raw frame decoding. The water temperature is a little-endian uint16 counted
# in half degrees; the flow, in litres per minute, is this constant divided by
# the raw value the device reports (the two are inversely proportional).
TEMPERATURE_RAW_UNITS_PER_DEGREE = 2.0
FLOW_RAW_CONSTANT = 1800.0

# What the options form accepts for a volume threshold (litres). The device
# itself can store more: see MAX_THRESHOLD_VALUE below.
MIN_THRESHOLD_LITERS = 1
MAX_THRESHOLD_LITERS = 100

# Each threshold is stored on a single byte of the device's config frame.
MIN_THRESHOLD_VALUE = 0
MAX_THRESHOLD_VALUE = 255

# The lifetime totals are kept in their own file (Home Assistant's `Store`),
# so they do not depend on the state of the entities that display them.
STORAGE_VERSION = 1
# While showers run, the totals are written to disk at most this often (s).
STORAGE_SAVE_DELAY = 10

ISSUE_TRACKER_URL = "https://github.com/Adrien40/ha-hydrao-custom/issues"

MAX_NEW_SHOWER_ATTEMPTS = 2

BT_STATUS_WAITING = "waiting"
BT_STATUS_CONNECTING = "connecting"
BT_STATUS_SUCCESS = "success"
BT_STATUS_ERROR = "error"
BT_STATUS_WRITING_SYNC = "writing_sync"
BT_STATUS_SYNC_APPLIED = "sync_applied"
BT_STATUS_SYNC_FAILED = "sync_failed"
BT_STATUS_REBOOTING = "rebooting"
