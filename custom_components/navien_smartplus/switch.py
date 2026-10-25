"""Holiday mode switches for Navien Smart Plus."""

from __future__ import annotations

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
        super().__init__(coordinator, "holiday", zone_id)
        if len(coordinator.data.status.zones) > 1:
            self._attr_translation_key = "holiday_zone"
            self._attr_translation_placeholders = {"zone": self._zone_name}

    @property
    def is_on(self) -> bool | None:
        return self.zone.holiday if self.zone else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._control(self.coordinator.client.async_set_holiday(self.device, self.zone_id, True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._control(self.coordinator.client.async_set_holiday(self.device, self.zone_id, False))
