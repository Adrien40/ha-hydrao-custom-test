# Copyright (c) 2026 Adrien40
# SPDX-License-Identifier: GPL-3.0-only

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_last_service_info,
    async_register_callback,
)
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util.unit_conversion import DurationConverter

from .const import (
    BT_STATUS_CONNECTING,
    BT_STATUS_ERROR,
    BT_STATUS_REBOOTING,
    BT_STATUS_SUCCESS,
    BT_STATUS_SYNC_APPLIED,
    BT_STATUS_SYNC_FAILED,
    BT_STATUS_WAITING,
    BT_STATUS_WRITING_SYNC,
    HydraoConfigEntry,
)
from .coordinator import HydraoDataUpdateCoordinator
from .entity import HydraoEntity

# Read-only entities, no I/O on update.
PARALLEL_UPDATES = 0

# The RSSI sensor writes its state at most this often (seconds). The signal
# strength is a support figure: a once-per-second refresh is plenty.
RSSI_MIN_UPDATE_INTERVAL = 1.0


@dataclass(frozen=True, kw_only=True)
class HydraoSensorEntityDescription(SensorEntityDescription):
    """A sensor whose value comes from the coordinator.

    `value_fn` reads the live value. When it returns None, the sensor falls
    back on the value restored from its last state (nothing has been received
    yet), unless `none_is_valid` is set: for those sensors "unknown" is a real
    answer once the coordinator has published data (a temperature that cannot
    be decoded, a time to comfort that was not observed).
    """

    value_fn: Callable[[HydraoDataUpdateCoordinator], Any]
    none_is_valid: bool = False


def _live(key: str) -> Callable[[HydraoDataUpdateCoordinator], Any]:
    """The value of `key` in the latest data published by the coordinator."""

    def value(coordinator: HydraoDataUpdateCoordinator) -> Any:
        return (coordinator.data or {}).get(key)

    return value


def _live_raw(key: str) -> Callable[[HydraoDataUpdateCoordinator], Any]:
    """Same, for the figures the coordinator keeps under its "raw" entry."""

    def value(coordinator: HydraoDataUpdateCoordinator) -> Any:
        return (coordinator.data or {}).get("raw", {}).get(key)

    return value


def _flow_rate(coordinator: HydraoDataUpdateCoordinator) -> Any:
    """The flow is 0 whenever there is nothing to report: no water, no data."""
    return (coordinator.data or {}).get("flow_rate") or 0.0


def _threshold(index: int) -> Callable[[HydraoDataUpdateCoordinator], Any]:
    """One of the four volume thresholds, as last read from the device."""

    def value(coordinator: HydraoDataUpdateCoordinator) -> Any:
        thresholds = coordinator.static_data.get("thresholds")
        if thresholds is None:
            return None
        try:
            return float(thresholds[index])
        except (IndexError, ValueError, TypeError):
            return None

    return value


SENSOR_DESCRIPTIONS = [
    HydraoSensorEntityDescription(
        key="temperature",
        value_fn=_live("temperature"),
        none_is_valid=True,
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    HydraoSensorEntityDescription(
        key="total_volume",
        value_fn=_live("total_volume"),
        translation_key="total_volume",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
    ),
    HydraoSensorEntityDescription(
        key="flow_rate",
        value_fn=_flow_rate,
        translation_key="flow_rate",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
    ),
    HydraoSensorEntityDescription(
        key="wasted_volume",
        value_fn=_live("wasted_volume"),
        translation_key="wasted_volume",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
    ),
    HydraoSensorEntityDescription(
        key="wasted_volume_total",
        value_fn=_live("wasted_volume_total"),
        translation_key="wasted_volume_total",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
    ),
    HydraoSensorEntityDescription(
        key="shower_volume_comfort",
        value_fn=_live("shower_volume_comfort"),
        translation_key="shower_volume_comfort",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
    ),
    HydraoSensorEntityDescription(
        key="shower_volume_comfort_total",
        value_fn=_live("shower_volume_comfort_total"),
        translation_key="shower_volume_comfort_total",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
    ),
    HydraoSensorEntityDescription(
        key="shower_volume_raw",
        value_fn=_live_raw("shower_volume_raw"),
        translation_key="shower_volume_raw",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
    ),
    HydraoSensorEntityDescription(
        key="shower_duration",
        value_fn=_live_raw("shower_duration"),
        translation_key="shower_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    HydraoSensorEntityDescription(
        key="shower_duration_comfort",
        value_fn=_live("shower_duration_comfort"),
        translation_key="shower_duration_comfort",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    HydraoSensorEntityDescription(
        key="shower_duration_cold",
        value_fn=_live("shower_duration_cold"),
        translation_key="shower_duration_cold",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    HydraoSensorEntityDescription(
        key="time_to_comfort",
        value_fn=_live("time_to_comfort"),
        none_is_valid=True,
        translation_key="time_to_comfort",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    HydraoSensorEntityDescription(
        key="threshold_1",
        value_fn=_threshold(0),
        translation_key="threshold_1",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfVolume.LITERS,
    ),
    HydraoSensorEntityDescription(
        key="threshold_2",
        value_fn=_threshold(1),
        translation_key="threshold_2",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfVolume.LITERS,
    ),
    HydraoSensorEntityDescription(
        key="threshold_3",
        value_fn=_threshold(2),
        translation_key="threshold_3",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfVolume.LITERS,
    ),
    HydraoSensorEntityDescription(
        key="threshold_4",
        value_fn=_threshold(3),
        translation_key="threshold_4",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfVolume.LITERS,
    ),
]

SOAPING_DURATION_DESC = SensorEntityDescription(
    key="soaping_duration",
    translation_key="soaping_duration",
    device_class=SensorDeviceClass.DURATION,
    native_unit_of_measurement=UnitOfTime.SECONDS,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HydraoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data

    sensors: list[SensorEntity] = [
        HydraoSensor(coordinator, desc) for desc in SENSOR_DESCRIPTIONS
    ]
    sensors.append(HydraoBluetoothStatusSensor(coordinator))
    sensors.append(HydraoRealTimeRSSISensor(coordinator))
    sensors.append(HydraoSoapingDurationSensor(coordinator))
    sensors.append(HydraoPendingConfigSensor(coordinator))

    async_add_entities(sensors)


class HydraoSensor(HydraoEntity, RestoreSensor):
    entity_description: HydraoSensorEntityDescription

    def __init__(
        self,
        coordinator: HydraoDataUpdateCoordinator,
        description: HydraoSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._restored_value: Any = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        if (
            not self.coordinator.is_new_entry
            and (last_sensor_data := await self.async_get_last_sensor_data())
            is not None
            and self.entity_description.key != "flow_rate"
        ):
            self._restored_value = self._convert_restored_duration(
                last_sensor_data.native_value,
                last_sensor_data.native_unit_of_measurement,
            )

            # The restored value of the two total sensors is only adopted when
            # the totals are not already saved in their own file (migration
            # from 1.0.0, see the coordinator).
            if (
                self.entity_description.key == "wasted_volume_total"
                and self._restored_value is not None
                and not self.coordinator.totals_loaded_from_store
            ):
                try:
                    self.coordinator.restore_wasted_volume_total(
                        float(self._restored_value)
                    )
                except (ValueError, TypeError):
                    pass
            elif (
                self.entity_description.key == "shower_volume_comfort_total"
                and self._restored_value is not None
                and not self.coordinator.totals_loaded_from_store
            ):
                try:
                    self.coordinator.restore_shower_volume_comfort_total(
                        float(self._restored_value)
                    )
                except (ValueError, TypeError):
                    pass

    def _convert_restored_duration(self, value: Any, stored_unit: str | None) -> Any:
        """Express a restored duration in this sensor's native unit.

        Duration sensors used to be stored in minutes; after the switch to
        seconds, an old restored value must not be re-read as seconds.
        Non-duration sensors, unknown units and unconvertible values are
        returned untouched.
        """
        native_unit = self.entity_description.native_unit_of_measurement
        if (
            self.entity_description.device_class != SensorDeviceClass.DURATION
            or value is None
            or stored_unit is None
            or native_unit is None
            or stored_unit == native_unit
        ):
            return value
        try:
            return DurationConverter.convert(float(value), stored_unit, native_unit)
        except (ValueError, TypeError, HomeAssistantError):
            return value

    @property
    def native_value(self) -> StateType:
        description = self.entity_description
        value = description.value_fn(self.coordinator)
        if value is not None:
            return float(value)

        if description.none_is_valid and description.key in (
            self.coordinator.data or {}
        ):
            return None

        return self._restored_number()

    def _restored_number(self) -> StateType:
        """The value restored from the last state, as a number when it is one."""
        restored = self._restored_value
        if restored is None:
            return None
        try:
            return float(restored)
        except (ValueError, TypeError):
            return cast(StateType, restored)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = {}

        if self.entity_description.key.startswith("threshold_"):
            idx = int(self.entity_description.key.split("_")[1]) - 1
            colors = self.coordinator.static_data.get("colors")
            if colors is not None:
                try:
                    r, g, b = (int(c) for c in colors[idx])
                    attrs["color_rgb"] = f"{r}, {g}, {b}"
                    attrs["color_hex"] = f"#{r:02X}{g:02X}{b:02X}"
                except (IndexError, ValueError, TypeError):
                    return attrs

        return attrs


class HydraoBluetoothStatusSensor(HydraoEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "bluetooth_status"

    def __init__(self, coordinator: HydraoDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "bluetooth_status")
        self._attr_options = [
            BT_STATUS_WAITING,
            BT_STATUS_CONNECTING,
            BT_STATUS_SUCCESS,
            BT_STATUS_ERROR,
            BT_STATUS_WRITING_SYNC,
            BT_STATUS_SYNC_APPLIED,
            BT_STATUS_SYNC_FAILED,
            BT_STATUS_REBOOTING,
        ]

    @property
    def native_value(self) -> StateType:
        if not self.coordinator.data:
            return BT_STATUS_WAITING
        return cast(
            StateType,
            self.coordinator.data.get("bluetooth_status", BT_STATUS_WAITING),
        )


# Developer's choice: This sensor inherits from HydraoEntity (a CoordinatorEntity) to ensure its state
# explicitly syncs with the main coordinator's update cycles and base availability logic,
# despite maintaining its own passive BLE listener for real-time RSSI updates.
class HydraoRealTimeRSSISensor(HydraoEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # Signal strength is a support tool, not something to show by default.
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "rssi"
    _attr_should_poll = False

    def __init__(self, coordinator: HydraoDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "rssi")
        self._attr_native_value = None
        # time.monotonic() of the last state written from an advertisement.
        self._last_state_write: float | None = None

    @property
    def available(self) -> bool:
        if self._attr_native_value is None:
            return False

        status = (
            self.coordinator.data.get("bluetooth_status")
            if self.coordinator.data
            else None
        )
        # Only "available" while genuinely connected to the device
        # (mid-shower, or writing/rebooting as part of that same
        # connection) — not while merely searching (waiting), attempting
        # a connection, or in error, where a passively-scanned value
        # could still be sitting around from an earlier session and
        # falsely suggest the device is currently reachable.
        return status in (
            BT_STATUS_SUCCESS,
            BT_STATUS_WRITING_SYNC,
            BT_STATUS_SYNC_APPLIED,
            BT_STATUS_SYNC_FAILED,
            BT_STATUS_REBOOTING,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        last_info = async_last_service_info(
            self.hass, self.coordinator.address, connectable=False
        )
        if last_info:
            self._attr_native_value = last_info.rssi

        self.async_write_ha_state()

        @callback
        def _async_on_bluetooth_change(
            info: BluetoothServiceInfoBleak, change: BluetoothChange
        ) -> None:
            if info.rssi == self._attr_native_value:
                return
            now = time.monotonic()
            if (
                self._last_state_write is not None
                and now - self._last_state_write < RSSI_MIN_UPDATE_INTERVAL
            ):
                return
            self._last_state_write = now
            self._attr_native_value = info.rssi
            self.async_write_ha_state()

        self.async_on_remove(
            async_register_callback(
                self.hass,
                _async_on_bluetooth_change,
                BluetoothCallbackMatcher(address=self.coordinator.address),
                BluetoothScanningMode.PASSIVE,
            )
        )


class HydraoSoapingDurationSensor(HydraoEntity, RestoreSensor):
    def __init__(
        self,
        coordinator: HydraoDataUpdateCoordinator,
    ) -> None:
        super().__init__(coordinator, SOAPING_DURATION_DESC.key)
        self.entity_description = SOAPING_DURATION_DESC
        self._restored_value: Any = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if not self.coordinator.is_new_entry:
            last_sensor_data = await self.async_get_last_sensor_data()
            if last_sensor_data is not None:
                self._restored_value = last_sensor_data.native_value

    @property
    def native_value(self) -> StateType:
        val = self.coordinator.static_data.get("soaping_duration")
        if val is not None:
            return cast(StateType, val)

        if self._restored_value is not None:
            try:
                return int(self._restored_value)
            except (ValueError, TypeError):
                pass

        return None


class HydraoPendingConfigSensor(HydraoEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "pending_config"

    def __init__(self, coordinator: HydraoDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "pending_config")
        self._attr_options = [
            "none",
            "soaping",
            "thresholds",
            "colors",
            "soaping_thresholds",
            "soaping_colors",
            "thresholds_colors",
            "soaping_thresholds_colors",
        ]

    @property
    def native_value(self) -> StateType:
        parts = []
        if self.coordinator.pending_soaping_duration is not None:
            parts.append("soaping")
        if self.coordinator.pending_thresholds is not None:
            parts.append("thresholds")
        if self.coordinator.pending_colors is not None:
            parts.append("colors")
        return "_".join(parts) if parts else "none"
