"""Tests for the Blueriiot GATT profile detection (profile_blueriiot.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.blue_connect_local.const import (
    CHAR_AUTH_UUID,
    CHAR_TRIGGER_UUID,
)

from .helpers_blueriiot import (
    AUTH,
    EXTRA_1,
    EXTRA_2,
    NOTIFY_1,
    NOTIFY_2,
    NOTIFY_3,
    PROFILE,
    TRIGGER,
    blueriiot_layout,
    make_services,
)


def test_blueriiot_profile_is_detected():
    assert PROFILE.is_blueriiot_services(make_services(blueriiot_layout()))


def test_detection_is_case_insensitive():
    layout = {AUTH.upper(): ["write"], TRIGGER.upper(): ["write"]}
    assert PROFILE.is_blueriiot_services(make_services(layout))


@pytest.mark.parametrize(
    "layout",
    [
        {},
        {AUTH: ["write"]},  # trigger missing
        {TRIGGER: ["write"]},  # auth missing
        {CHAR_AUTH_UUID: ["write"], CHAR_TRIGGER_UUID: ["write"]},  # Zodiac
    ],
)
def test_other_layouts_are_not_blueriiot(layout):
    assert not PROFILE.is_blueriiot_services(make_services(layout))


@pytest.mark.parametrize("services", [None, 42, [object()], [SimpleNamespace()]])
def test_unusable_services_are_never_blueriiot(services):
    assert not PROFILE.is_blueriiot_services(services)
    assert PROFILE.blueriiot_notify_uuids(services) == []
    assert PROFILE.blueriiot_extra_trigger_uuids(services) == []


def test_notify_uuids_keep_only_exposed_notifying_characteristics():
    layout = blueriiot_layout()
    layout[NOTIFY_2] = ["read"]  # exposed but cannot notify
    del layout[NOTIFY_3]  # not exposed
    services = make_services(layout)
    assert PROFILE.blueriiot_notify_uuids(services) == [NOTIFY_1]


def test_extra_triggers_keep_only_writable_characteristics():
    layout = blueriiot_layout()
    layout[EXTRA_1] = ["read"]
    services = make_services(layout)
    assert PROFILE.blueriiot_extra_trigger_uuids(services) == [EXTRA_2]


def test_authentication_characteristic_is_not_an_extra_trigger():
    assert AUTH not in PROFILE.BLUERIIOT_EXTRA_TRIGGER_UUIDS
