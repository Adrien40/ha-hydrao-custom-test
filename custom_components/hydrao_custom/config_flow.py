# Copyright (c) 2026 Adrien40
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import (
    DEFAULT_MIN_TEMP_THRESHOLD,
    DEFAULT_SOAPING_DURATION,
    DOMAIN,
    MAX_SOAPING_DURATION,
    MAX_THRESHOLD_LITERS,
    MIN_SOAPING_DURATION,
    MIN_THRESHOLD_LITERS,
)
from .util import is_valid_comfort_threshold, pairwise_increasing_errors

if TYPE_CHECKING:
    from .coordinator import HydraoDataUpdateCoordinator

# NOTE: Number selectors below (min_temp_threshold, soaping_duration,
# thresholds) intentionally do NOT set `native_min_value`/`native_max_value`
# (or the equivalent selector min/max). Doing so would let the frontend
# block out-of-range input client-side, which means the value would never
# reach the backend and Home Assistant's own, translated error messages
# (e.g. "min_temp_out_of_range") would never be shown. Keeping the
# selectors open preserves full i18n support for these validation errors.
# This is a deliberate architectural choice, not an oversight — please
# don't "fix" it by adding min/max back.

_LOGGER = logging.getLogger(__name__)

THRESHOLD_KEYS = ["threshold_1", "threshold_2", "threshold_3", "threshold_4"]


def _number_selector(
    unit: str, *, step: float | None = None, read_only: bool = False
) -> selector.NumberSelector:
    """A number box with a unit, as every numeric field of the forms uses.

    Neither a minimum nor a maximum is set, on purpose: see the note at the top
    of this file.
    """
    config = selector.NumberSelectorConfig(
        mode=selector.NumberSelectorMode.BOX, unit_of_measurement=unit
    )
    if step is not None:
        config["step"] = step
    if read_only:
        config["read_only"] = True
    return selector.NumberSelector(config)


class HydraoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovery_info: dict[str, Any] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> config_entries.ConfigFlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        short_mac = discovery_info.address.replace(":", "")[-4:]
        self._discovery_info = {
            "address": discovery_info.address,
            "name": discovery_info.name or f"Hydrao {short_mac}",
        }
        self.context["title_placeholders"] = {"name": self._discovery_info["name"]}

        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors = {}
        default_temp = DEFAULT_MIN_TEMP_THRESHOLD

        if user_input is not None:
            default_temp = user_input.get(
                "min_temp_threshold", DEFAULT_MIN_TEMP_THRESHOLD
            )

            if not is_valid_comfort_threshold(default_temp):
                errors["min_temp_threshold"] = "min_temp_out_of_range"

            if not errors:
                return self.async_create_entry(
                    title=self._discovery_info["name"],
                    data={
                        CONF_ADDRESS: self._discovery_info["address"],
                        "name": self._discovery_info["name"],
                    },
                    options={
                        "min_temp_threshold": float(default_temp),
                    },
                )

        data_schema = vol.Schema(
            {
                vol.Required(
                    "min_temp_threshold", default=default_temp
                ): _number_selector("°C", step=0.5),
            }
        )

        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "name": self._discovery_info["name"],
                "address": self._discovery_info["address"],
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors = {}

        default_mac = vol.UNDEFINED
        default_temp = DEFAULT_MIN_TEMP_THRESHOLD

        if user_input is not None:
            default_mac = user_input.get(CONF_ADDRESS, vol.UNDEFINED)
            default_temp = user_input.get(
                "min_temp_threshold", DEFAULT_MIN_TEMP_THRESHOLD
            )

            cleaned = re.sub(r"[:\-/\s]", "", user_input[CONF_ADDRESS].upper())

            if not re.fullmatch(r"[0-9A-F]{12}", cleaned):
                errors["base"] = "invalid_mac"

            if not is_valid_comfort_threshold(default_temp):
                errors["min_temp_threshold"] = "min_temp_out_of_range"

            if not errors:
                mac = ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()

                short_mac = cleaned[-4:]
                default_name = f"Hydrao {short_mac}"

                return self.async_create_entry(
                    title=default_name,
                    data={CONF_ADDRESS: mac, "name": default_name},
                    options={
                        "min_temp_threshold": float(default_temp),
                    },
                )

        schema_fields: dict[vol.Marker, Any] = {}
        if default_mac is not vol.UNDEFINED:
            schema_fields[vol.Required(CONF_ADDRESS, default=default_mac)] = str
        else:
            schema_fields[vol.Required(CONF_ADDRESS)] = str

        schema_fields[vol.Required("min_temp_threshold", default=default_temp)] = (
            _number_selector("°C", step=0.5)
        )

        data_schema = vol.Schema(schema_fields)

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return HydraoOptionsFlowHandler()


class HydraoOptionsFlowHandler(config_entries.OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors = {}

        if user_input is not None:
            flat_input: dict[str, Any] = {}
            for key, val in user_input.items():
                if isinstance(val, dict):
                    flat_input.update(val)
                else:
                    flat_input[key] = val
            user_input = flat_input

            if user_input.get("reset_to_defaults"):
                comfort_temp_val = user_input.get("min_temp_threshold")
                if comfort_temp_val is not None and not is_valid_comfort_threshold(
                    comfort_temp_val
                ):
                    errors["min_temp_threshold"] = "min_temp_out_of_range"
                    errors["base"] = "min_temp_out_of_range"

                if not errors:
                    default_options = {
                        "soaping_duration": DEFAULT_SOAPING_DURATION,
                        "threshold_1": 10,
                        "threshold_2": 20,
                        "threshold_3": 30,
                        "threshold_4": 40,
                        "threshold_1_color": [0, 255, 0],
                        "threshold_2_color": [0, 0, 255],
                        "threshold_3_color": [255, 0, 180],
                        "threshold_4_color": [255, 0, 0],
                    }
                    new_options = dict(self.config_entry.options)
                    new_options.update(default_options)

                    if "min_temp_threshold" in user_input:
                        new_options["min_temp_threshold"] = user_input[
                            "min_temp_threshold"
                        ]
                    if "auto_sync_at_comfort" in user_input:
                        new_options["auto_sync_at_comfort"] = user_input[
                            "auto_sync_at_comfort"
                        ]

                    return self.async_create_entry(title="", data=new_options)
            else:
                threshold_values = {k: user_input.get(k) for k in THRESHOLD_KEYS}
                errors.update(
                    pairwise_increasing_errors(
                        threshold_values, THRESHOLD_KEYS, "thresholds_not_increasing"
                    )
                )

                for key, val in user_input.items():
                    if (
                        val is not None
                        and key.startswith("threshold_")
                        and not key.endswith("_color")
                        and key not in errors
                        and not (MIN_THRESHOLD_LITERS <= val <= MAX_THRESHOLD_LITERS)
                    ):
                        errors[key] = "value_out_of_range"

                soaping_val = user_input.get("soaping_duration")
                if soaping_val is not None and not (
                    MIN_SOAPING_DURATION <= soaping_val <= MAX_SOAPING_DURATION
                ):
                    errors["soaping_duration"] = "soaping_duration_out_of_range"

                comfort_temp_val = user_input.get("min_temp_threshold")
                if comfort_temp_val is not None and not is_valid_comfort_threshold(
                    comfort_temp_val
                ):
                    errors["min_temp_threshold"] = "min_temp_out_of_range"

                if errors:
                    # Surface the first offending field's error at form
                    # level too (drives the error banner); each field also
                    # shows its own inline error message.
                    errors["base"] = next(iter(errors.values()))

                if not errors:
                    submitted = {
                        k: v
                        for k, v in user_input.items()
                        if v is not None and k != "reset_to_defaults"
                    }
                    new_options = dict(self.config_entry.options)
                    new_options.update(submitted)
                    return self.async_create_entry(title="", data=new_options)

        opts = self.config_entry.options
        data = self.config_entry.data

        # getattr (not entry.runtime_data directly): options flows can be
        # opened before the entry has finished loading, in which case
        # runtime_data isn't set yet.
        coordinator = getattr(self.config_entry, "runtime_data", None)

        live_thresholds = (
            coordinator.static_data.get("thresholds") if coordinator else None
        )
        live_colors = coordinator.static_data.get("colors") if coordinator else None

        def _get_val(key: str, cast_type: type) -> Any:
            if user_input and user_input.get(key) is not None:
                try:
                    return cast_type(user_input[key])
                except (ValueError, TypeError) as e:
                    _LOGGER.warning(
                        "Invalid submitted value for %s (%r): %s",
                        key,
                        user_input[key],
                        e,
                    )
            if key.startswith("threshold_") and live_thresholds:
                idx = int(key.split("_")[1]) - 1
                try:
                    return cast_type(live_thresholds[idx])
                except (IndexError, ValueError, TypeError) as e:
                    _LOGGER.warning(
                        "Could not read live threshold value for %s: %s", key, e
                    )
            try:
                if key in opts:
                    return cast_type(opts[key])
                if key in data:
                    return cast_type(data[key])
                return vol.UNDEFINED
            except (ValueError, TypeError) as e:
                _LOGGER.warning(
                    "Stored value for %s is invalid (%r), treating as unset: %s",
                    key,
                    opts.get(key, data.get(key)),
                    e,
                )
                return vol.UNDEFINED

        def _get_color_val(key: str) -> Any:
            if user_input and user_input.get(key) is not None:
                try:
                    return list(user_input[key])
                except (TypeError, ValueError) as e:
                    _LOGGER.warning(
                        "Invalid submitted color for %s (%r): %s",
                        key,
                        user_input[key],
                        e,
                    )
            idx = int(key.split("_")[1]) - 1
            if live_colors:
                try:
                    return list(live_colors[idx])
                except (IndexError, TypeError) as e:
                    _LOGGER.warning(
                        "Could not read live color value for %s: %s", key, e
                    )
            if key in opts:
                try:
                    return list(opts[key])
                except (TypeError, ValueError) as e:
                    _LOGGER.warning(
                        "Stored color for %s is invalid (%r), treating as unset: %s",
                        key,
                        opts[key],
                        e,
                    )
                    return vol.UNDEFINED
            if key in data:
                try:
                    return list(data[key])
                except (TypeError, ValueError) as e:
                    _LOGGER.warning(
                        "Stored color for %s is invalid (%r), treating as unset: %s",
                        key,
                        data[key],
                        e,
                    )
                    return vol.UNDEFINED
            return vol.UNDEFINED

        def _threshold_key(key: str) -> tuple[vol.Marker, selector.Selector[Any]]:
            return self._threshold_field(key, _get_val)

        def _color_key(key: str) -> tuple[vol.Marker, selector.Selector[Any]]:
            return self._color_field(key, _get_color_val)

        def _soaping_duration_key() -> tuple[vol.Marker, selector.Selector[Any]]:
            return self._soaping_duration_field(user_input, coordinator, opts, data)

        def _comfort_temp_key() -> tuple[vol.Marker, selector.Selector[Any]]:
            return self._comfort_temp_field(user_input, coordinator, opts, data)

        def _auto_sync_key() -> tuple[vol.Marker, selector.Selector[Any]]:
            return self._auto_sync_field(user_input, coordinator, opts, data)

        comfort_fields: dict[vol.Marker, selector.Selector[Any]] = {}
        threshold_fields: dict[vol.Marker, selector.Selector[Any]] = {}
        color_fields: dict[vol.Marker, selector.Selector[Any]] = {}

        comfort_marker, comfort_selector = _comfort_temp_key()
        comfort_fields[comfort_marker] = comfort_selector
        soaping_marker, soaping_selector = _soaping_duration_key()
        comfort_fields[soaping_marker] = soaping_selector
        auto_sync_marker, auto_sync_selector = _auto_sync_key()
        comfort_fields[auto_sync_marker] = auto_sync_selector

        for i in range(1, 5):
            t_marker, t_selector = _threshold_key(f"threshold_{i}")
            threshold_fields[t_marker] = t_selector
            c_marker, c_selector = _color_key(f"threshold_{i}_color")
            color_fields[c_marker] = c_selector

        default_reset = False
        if user_input is not None and "reset_to_defaults" in user_input:
            default_reset = bool(user_input["reset_to_defaults"])

        schema = vol.Schema(
            {
                vol.Required("comfort"): section(
                    vol.Schema(comfort_fields), {"collapsed": False}
                ),
                vol.Required("thresholds"): section(
                    vol.Schema(threshold_fields), {"collapsed": False}
                ),
                vol.Required("colors"): section(
                    vol.Schema(color_fields), {"collapsed": False}
                ),
                vol.Optional(
                    "reset_to_defaults", default=default_reset
                ): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    @staticmethod
    def _threshold_field(
        key: str, get_val: Callable[[str, type], Any]
    ) -> tuple[vol.Marker, selector.Selector[Any]]:
        current = get_val(key, int)
        if current is vol.UNDEFINED:
            return vol.Optional(key), _number_selector("L", read_only=True)
        return vol.Required(key, default=current), _number_selector("L", step=1)

    @staticmethod
    def _color_field(
        key: str, get_color_val: Callable[[str], Any]
    ) -> tuple[vol.Marker, selector.Selector[Any]]:
        current = get_color_val(key)
        if current is vol.UNDEFINED:
            return vol.Optional(key), selector.ColorRGBSelector(
                selector.ColorRGBSelectorConfig(read_only=True)
            )
        return vol.Required(key, default=current), selector.ColorRGBSelector()

    @staticmethod
    def _soaping_duration_field(
        user_input: dict[str, Any] | None,
        coordinator: HydraoDataUpdateCoordinator | None,
        opts: Mapping[str, Any],
        data: Mapping[str, Any],
    ) -> tuple[vol.Marker, selector.Selector[Any]]:
        if user_input and user_input.get("soaping_duration") is not None:
            return vol.Required(
                "soaping_duration", default=int(user_input["soaping_duration"])
            ), _number_selector("s", step=10)

        live_soaping = (
            coordinator.static_data.get("soaping_duration") if coordinator else None
        )
        if live_soaping is None:
            live_soaping = opts.get("soaping_duration", data.get("soaping_duration"))

        if live_soaping is None:
            return vol.Optional("soaping_duration"), _number_selector(
                "s", read_only=True
            )
        return vol.Required(
            "soaping_duration", default=int(live_soaping)
        ), _number_selector("s", step=10)

    @staticmethod
    def _comfort_temp_field(
        user_input: dict[str, Any] | None,
        coordinator: HydraoDataUpdateCoordinator | None,
        opts: Mapping[str, Any],
        data: Mapping[str, Any],
    ) -> tuple[vol.Marker, selector.Selector[Any]]:
        default = (
            coordinator.min_temp_threshold
            if coordinator
            else float(
                opts.get(
                    "min_temp_threshold",
                    data.get("min_temp_threshold", DEFAULT_MIN_TEMP_THRESHOLD),
                )
            )
        )
        if user_input and user_input.get("min_temp_threshold") is not None:
            default = float(user_input["min_temp_threshold"])
        return vol.Required("min_temp_threshold", default=default), _number_selector(
            "°C", step=0.5
        )

    @staticmethod
    def _auto_sync_field(
        user_input: dict[str, Any] | None,
        coordinator: HydraoDataUpdateCoordinator | None,
        opts: Mapping[str, Any],
        data: Mapping[str, Any],
    ) -> tuple[vol.Marker, selector.Selector[Any]]:
        default = (
            coordinator.auto_sync_at_comfort
            if coordinator
            else bool(
                opts.get(
                    "auto_sync_at_comfort", data.get("auto_sync_at_comfort", False)
                )
            )
        )
        if user_input and user_input.get("auto_sync_at_comfort") is not None:
            default = bool(user_input["auto_sync_at_comfort"])
        return vol.Required(
            "auto_sync_at_comfort", default=default
        ), selector.BooleanSelector()
