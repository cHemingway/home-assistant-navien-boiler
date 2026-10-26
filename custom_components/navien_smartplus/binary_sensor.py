"""Binary sensors for Navien Smart Plus."""

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NavienConfigEntry
from .api import DeviceStatus
from .coordinator import NavienCoordinator
from .entity import NavienEntity, NavienZoneEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class NavienBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a binary sensor read from the module's status."""

    value_fn: Callable[[DeviceStatus], bool]


BINARY_SENSORS: tuple[NavienBinarySensorDescription, ...] = (
    NavienBinarySensorDescription(
        key="burner",
        translation_key="burner",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda s: s.burner_active,
    ),
    NavienBinarySensorDescription(
        key="hot_water_active",
        translation_key="hot_water_active",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda s: s.dhw_active,
    ),
    NavienBinarySensorDescription(
        key="fault",
        translation_key="fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: s.error_code != 0,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NavienConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    coordinators = entry.runtime_data.coordinators.values()
    entities: list[BinarySensorEntity] = [
        NavienBinarySensor(coordinator, description)
        for coordinator in coordinators
        for description in BINARY_SENSORS
    ]
    entities += [NavienOnlineSensor(coordinator) for coordinator in coordinators]
    entities += [
        NavienThermostatConnectionSensor(coordinator, zone_id)
        for coordinator in coordinators
        for zone_id in coordinator.data.status.zones
    ]
    async_add_entities(entities)


class NavienBinarySensor(NavienEntity, BinarySensorEntity):
    """A binary sensor read from the module's status."""

    entity_description: NavienBinarySensorDescription

    def __init__(
        self, coordinator: NavienCoordinator, description: NavienBinarySensorDescription
    ) -> None:
        """Set up the sensor for one module."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    def _update_attrs(self) -> None:
        self._attr_is_on = self.entity_description.value_fn(self.status)


class NavienOnlineSensor(NavienEntity, BinarySensorEntity):
    """Whether the module is connected to Navien's cloud."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "online"

    def __init__(self, coordinator: NavienCoordinator) -> None:
        """Set up the sensor for one module."""
        super().__init__(coordinator, "online")

    @property
    def available(self) -> bool:
        """Also available after failed updates once the module has reported going offline."""
        return super().available or self.coordinator.module_online is False

    def _update_attrs(self) -> None:
        self._attr_is_on = self.coordinator.module_online


class NavienThermostatConnectionSensor(NavienZoneEntity, BinarySensorEntity):
    """Whether a zone's thermostat has a radio link to the module."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "thermostat_connected"

    def __init__(self, coordinator: NavienCoordinator, zone_id: int) -> None:
        """Set up the sensor for one zone."""
        super().__init__(coordinator, "thermostat_connected", zone_id)
        if len(coordinator.data.status.zones) > 1:
            self._attr_translation_key = "thermostat_connected_zone"
            self._attr_translation_placeholders = {"zone": self._zone_name}

    def _update_attrs(self) -> None:
        if (zone := self.zone) is not None:
            self._attr_is_on = zone.thermostat_connected
