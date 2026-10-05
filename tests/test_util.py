# Copyright (c) 2026 Adrien40
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for the pure helper functions in util.py."""

import pytest

from custom_components.hydrao_custom.const import (
    DURATION_TICKS_WRAP,
    DURATION_WRAP_WINDOW_TICKS,
    MAX_SOAPING_DURATION,
    MIN_SOAPING_DURATION,
)
from custom_components.hydrao_custom.util import (
    clamp_soaping_duration,
    comfort_fraction,
    duration_ticks_delta,
    is_valid_comfort_threshold,
    pairwise_increasing_errors,
    storage_key,
    thresholds_fit_in_byte,
    thresholds_strictly_increasing,
)

THRESHOLD = 33.0


# ---------------------------------------------------------------------------
# comfort_fraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("temp", "expected"),
    [(20.0, 0.0), (32.5, 0.0), (33.0, 1.0), (40.0, 1.0)],
)
def test_comfort_fraction_without_previous_reading_uses_current_temp(temp, expected):
    """On the first reading of a session there is nothing to interpolate
    from: the current reading decides for the whole interval."""
    assert comfort_fraction(None, temp, THRESHOLD) == expected


@pytest.mark.parametrize(
    ("previous", "temp", "expected"),
    [(20.0, 25.0, 0.0), (33.0, 40.0, 1.0), (35.0, 36.0, 1.0)],
)
def test_comfort_fraction_same_side_of_threshold(previous, temp, expected):
    assert comfort_fraction(previous, temp, THRESHOLD) == expected


def test_comfort_fraction_heating_through_threshold_is_interpolated():
    """20 -> 35 C with a 33 C threshold: the threshold is crossed 13/15 of
    the way through, so only the last 2/15 of the interval is comfortable."""
    assert comfort_fraction(20.0, 35.0, THRESHOLD) == pytest.approx(2 / 15)


def test_comfort_fraction_cooling_through_threshold_is_interpolated():
    """35 -> 20 C: comfortable only until the crossing, i.e. the first 2/15."""
    assert comfort_fraction(35.0, 20.0, THRESHOLD) == pytest.approx(2 / 15)


def test_comfort_fraction_reaching_threshold_exactly_adds_no_comfort():
    """Heating to exactly the threshold: the crossing is at the very end of
    the interval, so none of it was comfortable yet."""
    assert comfort_fraction(30.0, 33.0, THRESHOLD) == 0.0


def test_comfort_fraction_leaving_threshold_exactly_keeps_the_whole_interval():
    """Starting exactly at the threshold and cooling: comfortable only at the
    very first instant, which is a zero-length share of the interval."""
    assert comfort_fraction(33.0, 30.0, THRESHOLD) == 0.0


@pytest.mark.parametrize("previous", [None, 0.0, 20.0, 32.9, 33.0, 45.0, 50.0])
@pytest.mark.parametrize("temp", [0.0, 20.0, 32.9, 33.0, 33.1, 45.0, 50.0])
def test_comfort_fraction_is_always_a_valid_share(previous, temp):
    assert 0.0 <= comfort_fraction(previous, temp, THRESHOLD) <= 1.0


# ---------------------------------------------------------------------------
# duration_ticks_delta
# ---------------------------------------------------------------------------


def test_duration_ticks_delta_normal_increase():
    assert duration_ticks_delta(3000, 4500) == 1500


def test_duration_ticks_delta_no_change():
    assert duration_ticks_delta(3000, 3000) == 0


def test_duration_ticks_delta_wraps_around_near_the_counter_maximum():
    assert duration_ticks_delta(65500, 100) == 100 + DURATION_TICKS_WRAP - 65500


def test_duration_ticks_delta_wrap_window_boundary():
    edge = DURATION_TICKS_WRAP - DURATION_WRAP_WINDOW_TICKS
    assert duration_ticks_delta(edge, 0) == DURATION_WRAP_WINDOW_TICKS
    # one tick further from the maximum: no longer a plausible wrap
    assert duration_ticks_delta(edge - 1, 0) == 0


def test_duration_ticks_delta_ignores_a_drop_far_from_the_maximum():
    """A drop in the middle of the range is a device reset or a glitch, not
    a wrap: it must not turn into a ~20 minute jump."""
    assert duration_ticks_delta(3000, 200) == 0


# ---------------------------------------------------------------------------
# clamp_soaping_duration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-5, MIN_SOAPING_DURATION),
        (0, MIN_SOAPING_DURATION),
        (MIN_SOAPING_DURATION, MIN_SOAPING_DURATION),
        (180, 180),
        (MAX_SOAPING_DURATION, MAX_SOAPING_DURATION),
        (70_000, MAX_SOAPING_DURATION),
    ],
)
def test_clamp_soaping_duration(value, expected):
    assert clamp_soaping_duration(value) == expected


def test_clamped_soaping_duration_always_fits_the_two_byte_write():
    """The BLE write uses value.to_bytes(2, 'little'): any clamped value must
    fit, whatever was requested."""
    for requested in (-1, 0, 65_535, 65_536, 10**9):
        clamp_soaping_duration(requested).to_bytes(2, byteorder="little")


# ---------------------------------------------------------------------------
# Pre-existing helpers (previously untested)
# ---------------------------------------------------------------------------


def test_thresholds_strictly_increasing():
    assert thresholds_strictly_increasing([10, 20, 30, 40])
    assert not thresholds_strictly_increasing([10, 20, 20, 40])
    assert not thresholds_strictly_increasing([10, 30, 20, 40])


def test_thresholds_fit_in_byte():
    assert thresholds_fit_in_byte([0, 10, 100, 255])
    assert not thresholds_fit_in_byte([10, 20, 30, 256])
    assert not thresholds_fit_in_byte([-1, 20, 30, 40])


def test_storage_key_is_unique_per_config_entry():
    assert storage_key("abc") == "hydrao_custom.abc"
    assert storage_key("abc") != storage_key("def")


def test_pairwise_increasing_errors_flags_the_offending_field():
    order = ["t1", "t2", "t3"]
    errors = pairwise_increasing_errors(
        {"t1": 10, "t2": 5, "t3": 20}, order, "not_increasing"
    )
    assert errors == {"t2": "not_increasing"}


def test_pairwise_increasing_errors_skips_missing_values():
    order = ["t1", "t2", "t3"]
    assert pairwise_increasing_errors({"t1": 10, "t2": None, "t3": 5}, order, "e") == {}


@pytest.mark.parametrize(
    ("temp", "valid"), [(-0.1, False), (0, True), (50, True), (50.1, False)]
)
def test_is_valid_comfort_threshold(temp, valid):
    assert is_valid_comfort_threshold(temp) is valid
