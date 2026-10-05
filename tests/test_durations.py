# Copyright (c) 2026 Adrien40
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for duration handling in the coordinator.

Covers: seconds as the single internal unit, splitting an interval that
crosses the comfort threshold, cold duration, time to comfort, wrap-around
of the device's uint16 duration counter, the session timeout, and the
soaping-duration bounds.
"""

import logging
import time
from unittest.mock import AsyncMock

import pytest
from helpers import make_frames
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydrao_custom.const import (
    DOMAIN,
    MAX_SOAPING_DURATION,
    MIN_SOAPING_DURATION,
)
from custom_components.hydrao_custom.coordinator import HydraoDataUpdateCoordinator


def feed(coordinator, *, shower, ticks, temp, total=1000):
    """Feed one BLE reading to the coordinator."""
    vol, dur, tmp = make_frames(
        total=total, shower=shower, duration_ticks=ticks, temp_c=temp
    )
    coordinator._process_live_data(vol, dur, tmp, None)


@pytest.fixture
def clock(monkeypatch):
    """A controllable time.monotonic(); advance it with clock.advance(s)."""

    class Clock:
        now = 10_000.0

        def advance(self, seconds: float) -> None:
            self.now += seconds

    c = Clock()
    monkeypatch.setattr(time, "monotonic", lambda: c.now)
    return c


# ---------------------------------------------------------------------------
# Seconds as the internal unit
# ---------------------------------------------------------------------------


async def test_durations_are_expressed_in_seconds(coordinator):
    """3000 ticks of 1/50 s are 60 s, not 1 minute."""
    feed(coordinator, shower=50, ticks=3000, temp=35.0)

    data = coordinator.last_valid_data
    assert data["raw"]["shower_duration"] == 60.0
    assert data["shower_duration_comfort"] == 60.0
    assert data["shower_duration_cold"] == 0.0


async def test_cold_reading_counts_as_cold_duration(coordinator):
    feed(coordinator, shower=50, ticks=3000, temp=20.0)

    data = coordinator.last_valid_data
    assert data["shower_duration_cold"] == 60.0
    assert data["shower_duration_comfort"] == 0.0
    assert data["raw"]["shower_duration"] == 60.0


async def test_comfort_plus_cold_duration_equals_total(coordinator):
    feed(coordinator, shower=50, ticks=3000, temp=20.0)
    feed(coordinator, shower=80, ticks=4500, temp=35.0)

    data = coordinator.last_valid_data
    assert data["shower_duration_comfort"] + data["shower_duration_cold"] == (
        pytest.approx(data["raw"]["shower_duration"])
    )
    assert data["raw"]["shower_duration"] == pytest.approx(90.0)


# ---------------------------------------------------------------------------
# Interval crossing the comfort threshold
# ---------------------------------------------------------------------------


async def test_interval_crossing_the_threshold_is_split(coordinator):
    """20 -> 35 C over 30 L / 30 s with a 33 C threshold: 2/15 of the
    interval was comfortable, the rest cold."""
    coordinator.min_temp_threshold = 33.0

    feed(coordinator, shower=50, ticks=3000, temp=20.0)
    feed(coordinator, shower=80, ticks=4500, temp=35.0)

    assert coordinator.session_shower_volume_comfort == pytest.approx(4.0)
    assert coordinator.session_wasted_volume == pytest.approx(50.0 + 26.0)
    assert coordinator.session_shower_duration_comfort == pytest.approx(4.0)
    assert coordinator.session_shower_duration_cold == pytest.approx(60.0 + 26.0)
    # lifetime totals follow the same split
    assert coordinator.lifetime_shower_volume_comfort_total == pytest.approx(4.0)
    assert coordinator.lifetime_wasted_volume_total == pytest.approx(76.0)


async def test_first_reading_of_a_session_is_attributed_to_its_own_temperature(
    coordinator,
):
    """With no previous temperature there is nothing to interpolate from."""
    feed(coordinator, shower=50, ticks=3000, temp=35.0)

    assert coordinator.session_shower_volume_comfort == 50.0
    assert coordinator.session_wasted_volume == 0.0


# ---------------------------------------------------------------------------
# Time to comfort
# ---------------------------------------------------------------------------


async def test_time_to_comfort_is_none_while_water_is_cold(coordinator):
    feed(coordinator, shower=50, ticks=3000, temp=20.0)
    feed(coordinator, shower=60, ticks=3500, temp=25.0)

    assert coordinator.last_valid_data["time_to_comfort"] is None


async def test_time_to_comfort_is_the_cold_time_before_comfort(coordinator):
    """60 s fully cold, then 26 s of the crossing interval, then comfort."""
    feed(coordinator, shower=50, ticks=3000, temp=20.0)
    feed(coordinator, shower=80, ticks=4500, temp=35.0)

    assert coordinator.last_valid_data["time_to_comfort"] == pytest.approx(86.0)

    # staying comfortable does not move it
    feed(coordinator, shower=90, ticks=5000, temp=36.0)
    assert coordinator.last_valid_data["time_to_comfort"] == pytest.approx(86.0)


async def test_time_to_comfort_unknown_when_session_starts_warm(coordinator):
    """If the first reading is already warm, the cold phase (if any) took
    place before we connected: report unknown, and don't let a later
    cold -> warm round trip pretend it was the first time."""
    feed(coordinator, shower=10, ticks=500, temp=35.0)
    feed(coordinator, shower=20, ticks=1000, temp=20.0)
    feed(coordinator, shower=30, ticks=1500, temp=36.0)

    assert coordinator.last_valid_data["time_to_comfort"] is None


async def test_time_to_comfort_when_a_reading_lands_exactly_on_the_threshold(
    coordinator,
):
    """The probe resolves 0.5 C and the threshold moves in 0.5 C steps, so a
    reading equal to the threshold is common during a gradual warm-up. The
    interpolated comfort share of that interval is exactly 0, which must not
    prevent the time to comfort from being recorded."""
    threshold = coordinator.min_temp_threshold
    feed(coordinator, shower=10, ticks=500, temp=threshold - 2.0)  # 10 s cold
    feed(coordinator, shower=20, ticks=1000, temp=threshold)  # right on it
    feed(coordinator, shower=30, ticks=1500, temp=threshold + 1.0)

    assert coordinator.last_valid_data["time_to_comfort"] == pytest.approx(20.0)


async def test_time_to_comfort_with_a_gradual_half_degree_warm_up(coordinator):
    """Realistic warm-up: the temperature rises by one probe step (0.5 C)
    between readings, 10 s apart, and passes through the threshold itself."""
    threshold = coordinator.min_temp_threshold
    # 7 readings up to and including the one at the threshold, then 2 above.
    temps = [threshold - 3.0 + 0.5 * step for step in range(9)]
    for i, temp in enumerate(temps, start=1):
        feed(coordinator, shower=10 * i, ticks=500 * i, temp=temp)

    assert coordinator.last_valid_data["time_to_comfort"] == pytest.approx(70.0)
    assert coordinator.session_shower_duration_cold == pytest.approx(70.0)
    assert coordinator.session_shower_duration_comfort == pytest.approx(20.0)


async def test_new_session_clears_cold_duration_and_time_to_comfort(coordinator):
    feed(coordinator, shower=50, ticks=3000, temp=20.0)
    feed(coordinator, shower=80, ticks=4500, temp=35.0)
    assert coordinator.session_time_to_comfort is not None

    coordinator.force_reset_flag = True  # what the offline timeout sets
    feed(coordinator, shower=5, ticks=250, temp=20.0)

    assert coordinator.session_time_to_comfort is None
    assert coordinator.last_valid_data["time_to_comfort"] is None
    assert coordinator.session_shower_duration_cold == 5.0  # only the new reading


async def test_force_end_shower_clears_cold_duration_and_time_to_comfort(coordinator):
    feed(coordinator, shower=50, ticks=3000, temp=20.0)
    feed(coordinator, shower=80, ticks=4500, temp=35.0)

    coordinator.force_end_shower()

    assert coordinator.session_time_to_comfort is None
    assert coordinator.last_valid_data["time_to_comfort"] is None
    assert coordinator.last_valid_data["shower_duration_cold"] == 0.0
    assert coordinator.last_valid_data["shower_duration_comfort"] == 0.0


# ---------------------------------------------------------------------------
# Comfort-mode auto-sync keeps the cold phase
# ---------------------------------------------------------------------------


async def test_auto_sync_keeps_cold_duration_and_time_to_comfort(coordinator):
    """Auto-sync resets the device counters when comfort is reached, but the
    cold phase belongs to the same physical shower and must be kept - also
    after the device has actually rebooted and its counters restart."""
    coordinator.auto_sync_at_comfort = True

    feed(coordinator, shower=50, ticks=3000, temp=20.0)
    feed(coordinator, shower=80, ticks=4500, temp=35.0)  # triggers the sync

    assert coordinator.pending_new_shower is True
    assert coordinator.session_shower_duration_cold == pytest.approx(86.0)
    assert coordinator.session_shower_duration_comfort == 0.0
    assert coordinator.last_valid_data["time_to_comfort"] == pytest.approx(86.0)

    # the device reboots: raw counters start over
    feed(coordinator, shower=5, ticks=250, temp=36.0)

    assert coordinator.pending_new_shower is False
    assert coordinator.session_shower_duration_cold == pytest.approx(86.0)
    assert coordinator.session_time_to_comfort == pytest.approx(86.0)
    assert coordinator.session_shower_duration_comfort == pytest.approx(5.0)
    assert coordinator.last_valid_data["raw"]["shower_duration"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# uint16 duration counter
# ---------------------------------------------------------------------------


async def test_duration_keeps_counting_across_counter_wraparound(coordinator):
    """The raw counter holds ~21.8 min: past that it wraps to 0 while the
    shower (and its volume) carries on."""
    feed(coordinator, shower=50, ticks=65_500, temp=35.0)
    assert coordinator.last_valid_data["raw"]["shower_duration"] == 1310.0

    feed(coordinator, shower=51, ticks=100, temp=35.0)  # wrapped: +136 ticks

    assert coordinator.last_valid_data["raw"]["shower_duration"] == pytest.approx(
        1312.72
    )
    assert coordinator.session_shower_duration_comfort == pytest.approx(1312.72)


async def test_duration_drop_far_from_maximum_adds_nothing(coordinator):
    feed(coordinator, shower=50, ticks=3000, temp=35.0)
    feed(coordinator, shower=51, ticks=200, temp=35.0)  # glitch, no wrap

    assert coordinator.last_valid_data["raw"]["shower_duration"] == 60.0

    # the glitch is rebased: counting resumes from the new value
    feed(coordinator, shower=52, ticks=250, temp=35.0)
    assert coordinator.last_valid_data["raw"]["shower_duration"] == pytest.approx(61.0)


# ---------------------------------------------------------------------------
# Session timeout: Home Assistant ends the session as early as it can
# ---------------------------------------------------------------------------


async def test_session_ends_as_soon_as_soaping_duration_has_elapsed(coordinator, clock):
    """Deliberate choice: Home Assistant declares a new shower as soon as
    soaping_duration has passed since its last successful read, with no extra
    margin. It would rather start a new session a moment too early than keep
    treating the water as the current shower after the device has reset."""
    coordinator.static_data["soaping_duration"] = 180
    feed(coordinator, shower=50, ticks=3000, temp=20.0)

    clock.advance(179)
    coordinator._evaluate_offline_timeout()
    assert coordinator.force_reset_flag is False

    clock.advance(2)  # 181 s: just past soaping_duration
    coordinator._evaluate_offline_timeout()
    assert coordinator.force_reset_flag is True


# ---------------------------------------------------------------------------
# Soaping duration bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (70_000, MAX_SOAPING_DURATION),
        (5, MIN_SOAPING_DURATION),
        (120, 120),
    ],
)
async def test_queued_soaping_duration_is_clamped(coordinator, requested, expected):
    coordinator._queue_pending_writes_from_options({"soaping_duration": requested})

    assert coordinator.pending_soaping_duration == expected


async def test_out_of_range_soaping_duration_is_logged(coordinator, caplog):
    with caplog.at_level(logging.WARNING):
        coordinator._queue_pending_writes_from_options({"soaping_duration": 70_000})

    assert "out of range" in caplog.text


async def test_clamped_soaping_duration_can_always_be_written(coordinator):
    """An out-of-range option used to raise OverflowError from to_bytes(),
    which is not a BLE error: it was retried forever. Now it is written."""
    coordinator._queue_pending_writes_from_options({"soaping_duration": 70_000})
    client = AsyncMock()

    ok = await coordinator._async_write_soaping_duration(
        client, coordinator.pending_soaping_duration
    )

    assert ok is True
    client.write_gatt_char.assert_awaited_once()
    assert client.write_gatt_char.await_args.args[1] == MAX_SOAPING_DURATION.to_bytes(
        2, byteorder="little"
    )


async def test_out_of_range_stored_option_is_clamped_at_startup(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"address": "AA:BB:CC:DD:EE:FF", "has_connected_once": True},
        options={"soaping_duration": 99_999},
    )
    entry.add_to_hass(hass)

    coordinator = HydraoDataUpdateCoordinator(hass, entry)

    assert coordinator.static_data["soaping_duration"] == MAX_SOAPING_DURATION
