"""Climate entities (one per thermostat zone) for Navien Smart Plus."""

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
PRESET_FROM_NAVIEN = {
    HeatingMode.BOOST: PRESET_BOOST,
    HeatingMode.OVERRIDE: PRESET_OVERRIDE,
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
    """Set up a climate entity for each zone."""
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
        """Set up the entity for one zone."""
        super().__init__(coordinator, "climate", zone_id)
        self._attr_name = self._zone_name
        features = coordinator.data.features
        self._attr_min_temp = features.min_zone_temperature
        self._attr_max_temp = features.max_zone_temperature

    @property
    def _mode(self) -> HeatingMode | int | None:
        return self.zone.mode if self.zone else None

    def _update_attrs(self) -> None:
        if (zone := self.zone) is None:
            return
        self._attr_current_temperature = zone.current_temperature
        self._attr_hvac_mode = HVAC_MODE_FROM_NAVIEN.get(zone.mode)  # type: ignore[call-overload]
        self._attr_preset_mode = PRESET_FROM_NAVIEN.get(zone.mode, PRESET_NONE)  # type: ignore[call-overload]
        self._attr_extra_state_attributes = {
            "program_slot": zone.program_number,
            "holiday": zone.holiday,
        }
        if zone.mode == HeatingMode.OFF:
            self._attr_target_temperature = None
            self._attr_hvac_action = HVACAction.OFF
            return
        schedule = self.coordinator.data.schedules.get(self.zone_id)
        self._attr_target_temperature = zone.target_temperature(schedule, dt_util.now())
        self._attr_hvac_action = (
            HVACAction.HEATING if zone.heating_active else HVACAction.IDLE
        )

    def _target_or_current(self) -> float:
        """Setpoint to send with a mode change (the mode command always carries one)."""
        if self.target_temperature is not None:
            return self.target_temperature
        return self.zone.setting_temperature if self.zone else 20.0

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Change the setpoint, and the mode if one is given."""
        if (
            hvac_mode := kwargs.get(ATTR_HVAC_MODE)
        ) is not None and hvac_mode != self.hvac_mode:
            await self.async_set_hvac_mode(hvac_mode)
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        if self._mode == HeatingMode.OFF:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="zone_off",
                translation_placeholders={"zone": self._zone_name},
            )
        # In Auto this starts an Override until the next program slot, as on the thermostat.
        await self._control(
            self.coordinator.client.async_set_zone_temperature(
                self.device, self.zone_id, temperature, self._mode
            )
        )

    async def _set_mode(self, mode: HeatingMode) -> None:
        await self._control(
            self.coordinator.client.async_set_zone_mode(
                self.device, self.zone_id, mode, self._target_or_current()
            )
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Switch between Auto, Heat and Off."""
        await self._set_mode(NAVIEN_FROM_HVAC_MODE[hvac_mode])

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Start Boost or an Override, or end either one."""
        if preset_mode == PRESET_BOOST:
            mode = HeatingMode.BOOST
        elif preset_mode == PRESET_OVERRIDE:
            mode = HeatingMode.OVERRIDE
        elif self._mode == HeatingMode.BOOST:
            # Leaving boost returns to manual heating.
            mode = HeatingMode.ON
        elif self._mode == HeatingMode.OVERRIDE:
            # Cancelling an override returns to the schedule.
            mode = HeatingMode.AUTO
        else:
            return
        await self._set_mode(mode)

    async def async_turn_on(self) -> None:
        """Turn the zone on, following its schedule."""
        await self.async_set_hvac_mode(HVACMode.AUTO)

    async def async_turn_off(self) -> None:
        """Turn the zone off."""
        await self.async_set_hvac_mode(HVACMode.OFF)
