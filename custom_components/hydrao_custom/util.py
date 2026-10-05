# Copyright (c) 2026 Adrien40
# SPDX-License-Identifier: GPL-3.0-only

from itertools import pairwise

from .const import (
    DOMAIN,
    DURATION_TICKS_WRAP,
    DURATION_WRAP_WINDOW_TICKS,
    MAX_SOAPING_DURATION,
    MAX_THRESHOLD_VALUE,
    MAX_WATER_TEMP,
    MIN_SOAPING_DURATION,
    MIN_THRESHOLD_VALUE,
    MIN_WATER_TEMP,
)


def storage_key(entry_id: str) -> str:
    """Name of the file (in Home Assistant's .storage folder) that holds the
    lifetime totals of one config entry."""
    return f"{DOMAIN}.{entry_id}"


def thresholds_strictly_increasing(values: list[int]) -> bool:
    """Return True if each value is strictly greater than the previous one."""
    return all(previous < following for previous, following in pairwise(values))


def thresholds_fit_in_byte(values: list[int]) -> bool:
    """Return True if every threshold fits the single byte the device uses."""
    return all(MIN_THRESHOLD_VALUE <= value <= MAX_THRESHOLD_VALUE for value in values)


def pairwise_increasing_errors(
    values: dict[str, int | None], order: list[str], error_code: str
) -> dict[str, str]:
    """Check a sequence of optional threshold values pairwise and return a
    {field_key: error_code} mapping for any pair where both values are
    present and the sequence isn't strictly increasing.

    `values` maps each key in `order` to its submitted value (or None if
    not submitted this round). Pairs with a missing value are skipped.
    """
    errors: dict[str, str] = {}
    for prev_key, next_key in pairwise(order):
        prev_val = values.get(prev_key)
        next_val = values.get(next_key)
        if prev_val is not None and next_val is not None and next_val <= prev_val:
            errors[next_key] = error_code
    return errors


def is_valid_comfort_threshold(temp: float) -> bool:
    """Check that a comfort temperature setting is within 0-50 C.

    This bounds what the user may set (it matches the `number` entity); it is
    not the plausibility check of a measured temperature, which is
    `is_plausible_water_temp`.
    """
    return 0 <= temp <= 50


def is_plausible_water_temp(temp: float) -> bool:
    """Check if a decoded temperature reading can be a real water temperature."""
    return MIN_WATER_TEMP <= temp <= MAX_WATER_TEMP


def clamp_soaping_duration(value: int) -> int:
    """Clamp a soaping duration (seconds) to the range the integration
    accepts, so an out-of-range option can never reach the 2-byte BLE write.
    """
    return max(MIN_SOAPING_DURATION, min(MAX_SOAPING_DURATION, value))


def comfort_fraction(
    previous_temp: float | None, temp: float, threshold: float
) -> float:
    """Return the share (0.0-1.0) of the interval between two readings
    during which the water was at or above the comfort threshold.

    Water temperature is assumed to change linearly between two readings:
      - both readings on the same side of the threshold: the whole interval
        counts on that side;
      - readings straddling the threshold: the interval is split at the
        interpolated crossing point;
      - no previous reading (first reading of a session): there is nothing
        to interpolate from, so the current reading decides for the whole
        interval.
    """
    if previous_temp is None:
        return 1.0 if temp >= threshold else 0.0

    previous_ok = previous_temp >= threshold
    current_ok = temp >= threshold

    if previous_ok and current_ok:
        return 1.0
    if not previous_ok and not current_ok:
        return 0.0
    if current_ok:
        # Heating up: comfortable from the crossing point to the end.
        return (temp - threshold) / (temp - previous_temp)
    # Cooling down: comfortable from the start to the crossing point.
    return (previous_temp - threshold) / (previous_temp - temp)


def duration_ticks_delta(previous: int, current: int) -> int:
    """Return the number of duration ticks elapsed between two raw reads of
    the device's uint16 duration counter.

    A decrease is only treated as a wrap-around when the previous value was
    within DURATION_WRAP_WINDOW_TICKS of the counter's maximum; any other
    decrease means the device reset or glitched, and yields 0 rather than a
    huge, wrong delta.
    """
    if current >= previous:
        return current - previous
    if previous >= DURATION_TICKS_WRAP - DURATION_WRAP_WINDOW_TICKS:
        return current + DURATION_TICKS_WRAP - previous
    return 0
