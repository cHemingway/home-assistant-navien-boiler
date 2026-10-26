"""Sensors for Navien Smart Plus."""

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import NavienConfigEntry
from .api import DeviceStatus, UsageRecord
from .coordinator import NavienCoordinator, NavienUsageCoordinator
from .entity import NavienEntity, NavienUsageEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class NavienStatusSensorDescription(SensorEntityDescription):
    """Describes a sensor read from the module's status."""

    value_fn: Callable[[DeviceStatus], StateType]
    exists_fn: Callable[[DeviceStatus], bool] = lambda _: True


STATUS_SENSORS: tuple[NavienStatusSensorDescription, ...] = (
    NavienStatusSensorDescription(
        key="outdoor_temperature",
        translation_key="outdoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda s: s.outside_temperature,
        exists_fn=lambda s: s.outside_temperature is not None,
    ),
    NavienStatusSensorDescription(
        key="error_code",
        translation_key="error_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.error_code,
    ),
)


@dataclass(frozen=True, kw_only=True)
class NavienUsageSensorDescription(SensorEntityDescription):
    """Describes a sensor read from the year's usage totals."""

    value_fn: Callable[[UsageRecord], StateType]


# Year-to-date running totals. They drop to 0 on 1 January, which `total_increasing` treats as a new cycle.
USAGE_SENSORS: tuple[NavienUsageSensorDescription, ...] = (
    NavienUsageSensorDescription(
        key="heating_gas",
        translation_key="heating_gas",
        device_class=SensorDeviceClass.GAS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=0,
        value_fn=lambda u: u.heating_usage,
    ),
    NavienUsageSensorDescription(
        key="hot_water_gas",
        translation_key="hot_water_gas",
        device_class=SensorDeviceClass.GAS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=0,
        value_fn=lambda u: u.hot_water_usage,
    ),
    NavienUsageSensorDescription(
        key="total_gas",
        translation_key="total_gas",
        device_class=SensorDeviceClass.GAS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=0,
        value_fn=lambda u: u.total_usage,
    ),
    NavienUsageSensorDescription(
        key="heating_hours",
        translation_key="heating_hours",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
        value_fn=lambda u: u.heating_hours,
    ),
    NavienUsageSensorDescription(
        key="hot_water_hours",
        translation_key="hot_water_hours",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
        value_fn=lambda u: u.hot_water_hours,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NavienConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the status sensors, and the usage sensors where fuel usage is reported."""
    entities: list[SensorEntity] = [
        NavienStatusSensor(coordinator, description)
        for coordinator in entry.runtime_data.coordinators.values()
        for description in STATUS_SENSORS
        if description.exists_fn(coordinator.data.status)
    ]
    for usage in entry.runtime_data.usage.values():
        entities += [
            NavienUsageSensor(usage, description) for description in USAGE_SENSORS
        ]
    async_add_entities(entities)


class NavienStatusSensor(NavienEntity, SensorEntity):
    """A sensor read from the module's status."""

    entity_description: NavienStatusSensorDescription

    def __init__(
        self, coordinator: NavienCoordinator, description: NavienStatusSensorDescription
    ) -> None:
        """Set up the sensor for one module."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    def _update_attrs(self) -> None:
        self._attr_native_value = self.entity_description.value_fn(self.status)


class NavienUsageSensor(NavienUsageEntity, SensorEntity):
    """A sensor read from the year's usage totals."""

    entity_description: NavienUsageSensorDescription

    def __init__(
        self,
        coordinator: NavienUsageCoordinator,
        description: NavienUsageSensorDescription,
    ) -> None:
        """Set up the sensor for one module."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    def _update_attrs(self) -> None:
        if self.coordinator.data is not None:
            self._attr_native_value = self.entity_description.value_fn(
                self.coordinator.data
            )
