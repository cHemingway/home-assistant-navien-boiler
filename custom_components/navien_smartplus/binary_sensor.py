"""Binary sensors for Navien Smart Plus."""

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NavienConfigEntry
from .api import DeviceStatus
from .coordinator import NavienCoordinator
from .entity import NavienEntity

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
    async_add_entities(
        NavienBinarySensor(coordinator, description)
        for coordinator in entry.runtime_data.coordinators.values()
        for description in BINARY_SENSORS
    )


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
