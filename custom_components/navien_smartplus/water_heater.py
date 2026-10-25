"""Hot water (combi DHW) for Navien Smart Plus."""

from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    STATE_OFF,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import STATE_ON, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NavienConfigEntry
from .api import HotWaterMode
from .coordinator import NavienCoordinator
from .entity import NavienEntity

PARALLEL_UPDATES = 1

# Operation names shown in Home Assistant.
OPERATION_AUTO = "auto"
OPERATION_BOOST = "boost"

OPERATION_FROM_NAVIEN = {
    HotWaterMode.AUTO: OPERATION_AUTO,
    HotWaterMode.ON: STATE_ON,
    HotWaterMode.ONCE: OPERATION_BOOST,
    HotWaterMode.OFF: STATE_OFF,
}
NAVIEN_FROM_OPERATION = {v: k for k, v in OPERATION_FROM_NAVIEN.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NavienConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(
        NavienHotWater(coordinator)
        for coordinator in entry.runtime_data.coordinators.values()
        if coordinator.data.features.has_hot_water
    )


class NavienHotWater(NavienEntity, WaterHeaterEntity):
    """Hot water mode. The combi reports no water temperatures, so this only switches modes."""

    _attr_translation_key = "hot_water"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_operation_list = [OPERATION_AUTO, STATE_ON, OPERATION_BOOST, STATE_OFF]
    _attr_supported_features = WaterHeaterEntityFeature.OPERATION_MODE | WaterHeaterEntityFeature.ON_OFF

    def __init__(self, coordinator: NavienCoordinator) -> None:
        super().__init__(coordinator, "hot_water")

    @property
    def current_operation(self) -> str | None:
        return OPERATION_FROM_NAVIEN.get(self.status.dhw_mode)  # type: ignore[arg-type]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"heating_water": self.status.dhw_active}

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        await self._control(
            self.coordinator.client.async_set_hot_water_mode(self.device, NAVIEN_FROM_OPERATION[operation_mode])
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.async_set_operation_mode(OPERATION_AUTO)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_set_operation_mode(STATE_OFF)
