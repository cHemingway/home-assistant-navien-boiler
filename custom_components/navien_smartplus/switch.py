"""Holiday mode switches for Navien Smart Plus."""

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NavienConfigEntry
from .coordinator import NavienCoordinator
from .entity import NavienZoneEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NavienConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a holiday mode switch for each zone, where supported."""
    async_add_entities(
        NavienHolidaySwitch(coordinator, zone_id)
        for coordinator in entry.runtime_data.coordinators.values()
        if coordinator.data.features.has_holiday
        for zone_id in coordinator.data.status.zones
    )


class NavienHolidaySwitch(NavienZoneEntity, SwitchEntity):
    """Holiday (away) mode of a zone."""

    _attr_translation_key = "holiday"

    def __init__(self, coordinator: NavienCoordinator, zone_id: int) -> None:
        """Set up the switch for one zone."""
        super().__init__(coordinator, "holiday", zone_id)
        if len(coordinator.data.status.zones) > 1:
            self._attr_translation_key = "holiday_zone"
            self._attr_translation_placeholders = {"zone": self._zone_name}

    def _update_attrs(self) -> None:
        if (zone := self.zone) is not None:
            self._attr_is_on = zone.holiday

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn holiday mode on."""
        await self._control(
            self.coordinator.client.async_set_holiday(self.device, self.zone_id, True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn holiday mode off."""
        await self._control(
            self.coordinator.client.async_set_holiday(self.device, self.zone_id, False)
        )
