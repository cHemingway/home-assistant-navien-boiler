"""Climate entities (one per thermostat zone) for Navien Smart Plus."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    PRESET_BOOST,
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import NavienConfigEntry
from .api import HeatingMode
from .const import DOMAIN, PRESET_OVERRIDE
from .coordinator import NavienCoordinator
from .entity import NavienZoneEntity

PARALLEL_UPDATES = 1

HVAC_MODE_FROM_NAVIEN = {
    HeatingMode.AUTO: HVACMode.AUTO,
    HeatingMode.OVERRIDE: HVACMode.AUTO,
    HeatingMode.ON: HVACMode.HEAT,
    HeatingMode.BOOST: HVACMode.HEAT,
    HeatingMode.OFF: HVACMode.OFF,
}
NAVIEN_FROM_HVAC_MODE = {
    HVACMode.AUTO: HeatingMode.AUTO,
    HVACMode.HEAT: HeatingMode.ON,
    HVACMode.OFF: HeatingMode.OFF,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NavienConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(
        NavienZoneClimate(coordinator, zone_id)
        for coordinator in entry.runtime_data.coordinators.values()
        for zone_id in coordinator.data.status.zones
    )


class NavienZoneClimate(NavienZoneEntity, ClimateEntity):
    """A heating zone controlled by an NRC-10R thermostat."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_precision = PRECISION_TENTHS
    _attr_hvac_modes = [HVACMode.AUTO, HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = [PRESET_NONE, PRESET_BOOST, PRESET_OVERRIDE]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_translation_key = "zone"

    def __init__(self, coordinator: NavienCoordinator, zone_id: int) -> None:
        super().__init__(coordinator, "climate", zone_id)
        self._attr_name = self._zone_name
        features = coordinator.data.features
        self._attr_min_temp = features.min_zone_temperature
        self._attr_max_temp = features.max_zone_temperature

    @property
    def _mode(self) -> HeatingMode | int | None:
        return self.zone.mode if self.zone else None

    @property
    def current_temperature(self) -> float | None:
        return self.zone.current_temperature if self.zone else None

    @property
    def target_temperature(self) -> float | None:
        if not self.zone:
            return None
        if self.zone.mode == HeatingMode.OFF:
            return None
        return self.zone.target_temperature(self.coordinator.data.schedules.get(self.zone_id), dt_util.now())

    @property
    def hvac_mode(self) -> HVACMode | None:
        return HVAC_MODE_FROM_NAVIEN.get(self._mode)  # type: ignore[arg-type]

    @property
    def hvac_action(self) -> HVACAction | None:
        if not self.zone:
            return None
        if self.zone.mode == HeatingMode.OFF:
            return HVACAction.OFF
        return HVACAction.HEATING if self.zone.heating_active else HVACAction.IDLE

    @property
    def preset_mode(self) -> str | None:
        if self._mode == HeatingMode.BOOST:
            return PRESET_BOOST
        if self._mode == HeatingMode.OVERRIDE:
            return PRESET_OVERRIDE
        return PRESET_NONE

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.zone:
            return None
        return {"program_slot": self.zone.program_number, "holiday": self.zone.holiday}

    def _target_or_current(self) -> float:
        """Setpoint to send with a mode change (the mode command always carries one)."""
        if self.target_temperature is not None:
            return self.target_temperature
        return self.zone.setting_temperature if self.zone else 20.0

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None and hvac_mode != self.hvac_mode:
            await self.async_set_hvac_mode(hvac_mode)
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        if self._mode == HeatingMode.OFF:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="zone_off", translation_placeholders={"zone": self._zone_name}
            )
        # In Auto this starts an Override until the next program slot, as on the thermostat.
        await self._control(
            self.coordinator.client.async_set_zone_temperature(self.device, self.zone_id, temperature, self._mode)
        )

    async def _set_mode(self, mode: HeatingMode) -> None:
        await self._control(
            self.coordinator.client.async_set_zone_mode(self.device, self.zone_id, mode, self._target_or_current())
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self._set_mode(NAVIEN_FROM_HVAC_MODE[hvac_mode])

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode == PRESET_BOOST:
            mode = HeatingMode.BOOST
        elif preset_mode == PRESET_OVERRIDE:
            mode = HeatingMode.OVERRIDE
        elif self._mode == HeatingMode.BOOST:
            mode = HeatingMode.ON  # leaving boost returns to manual heating
        elif self._mode == HeatingMode.OVERRIDE:
            mode = HeatingMode.AUTO  # cancelling an override returns to the schedule
        else:
            return
        await self._set_mode(mode)

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.AUTO)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)
