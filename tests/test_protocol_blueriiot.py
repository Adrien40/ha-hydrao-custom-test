"""Tests for the Blueriiot measurement frame decoder (protocol_blueriiot.py)."""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from .helpers import build_frame
from .helpers_blueriiot import FRAME_HEX_LEN, PROTOCOL, build_blueriiot_frame

parse_blueriiot_frame = PROTOCOL.parse_blueriiot_frame
is_blueriiot_frame = PROTOCOL.is_blueriiot_frame


def test_parse_valid_frame():
    parsed = parse_blueriiot_frame(build_blueriiot_frame())
    assert parsed["device_type"] == "blueriiot"
    assert parsed["temp_raw"] == pytest.approx(25.02)
    assert parsed["ph_raw"] == pytest.approx(7.4, abs=0.01)
    assert parsed["orp_raw"] == pytest.approx(700.0)
    assert parsed["conductivity"] == pytest.approx(1061.5)
    assert parsed["salinity"] == pytest.approx(0.53)
    assert parsed["battery"] == 3520
    assert parsed["battery_percent"] == 50


def test_parse_accepts_bytearray():
    assert parse_blueriiot_frame(bytearray(build_blueriiot_frame())) is not None


def test_parse_zero_conductivity_means_no_reading():
    parsed = parse_blueriiot_frame(build_blueriiot_frame(conductivity_raw=0))
    assert parsed["conductivity"] is None
    assert parsed["salinity"] is None


@pytest.mark.parametrize(
    ("battery_mv", "expected"),
    [(3000, 0), (3400, 0), (3520, 50), (3640, 100), (4200, 100)],
)
def test_battery_percent_is_clamped(battery_mv, expected):
    parsed = parse_blueriiot_frame(build_blueriiot_frame(battery_mv=battery_mv))
    assert parsed["battery_percent"] == expected


@pytest.mark.parametrize(
    "frame",
    [
        b"",
        build_blueriiot_frame(length=11),
        build_blueriiot_frame(length=13),
        build_blueriiot_frame(marker=0x00),
        build_frame(),  # a Zodiac frame is not a Blueriiot frame
    ],
)
def test_parse_rejects_anything_but_a_measurement_frame(frame):
    assert not is_blueriiot_frame(frame)
    assert parse_blueriiot_frame(frame) is None


def test_hex_length_matches_frame_length():
    assert len(build_blueriiot_frame().hex()) == FRAME_HEX_LEN == 24


@given(st.binary(max_size=40))
def test_parse_never_raises(data):
    parsed = parse_blueriiot_frame(data)
    assert (parsed is not None) == is_blueriiot_frame(data)
