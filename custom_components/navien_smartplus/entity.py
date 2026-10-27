"""Base entities for Navien Smart Plus."""

from collections.abc import Awaitable
from typing import Any

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
)
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .api import Device, DeviceStatus, NavienError, ZoneStatus
from .const import DOMAIN, MANUFACTURER
from .coordinator import NavienCoordinator, NavienData, NavienUsageCoordinator


def device_info(device: Device, data: NavienData | None = None) -> DeviceInfo:
    """Device registry details for a module."""
    info = DeviceInfo(
        identifiers={(DOMAIN, device.device_id)},
        connections={(CONNECTION_NETWORK_MAC, device.mac)},
        manufacturer=MANUFACTURER,
        model="Smart Plus",
        model_id="BCM-41RW",
        name="Navien boiler",
    )
    if data is not None:
        if serial := data.features.boiler_serial:
            info["serial_number"] = serial
        if version := data.features.wifi_sw_version:
            info["sw_version"] = version
    return info


class NavienBaseEntity[CoordinatorT: DataUpdateCoordinator[Any]](
    CoordinatorEntity[CoordinatorT]
):
    """Sets its `_attr_` state from the coordinator data when added and on every update."""

    _attr_has_entity_name = True

    async def async_added_to_hass(self) -> None:
        """Set the initial state before the entity is first written."""
        await super().async_added_to_hass()
        self._update_attrs()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update the state from new coordinator data."""
        self._update_attrs()
        super()._handle_coordinator_update()

    def _update_attrs(self) -> None:
        """Set the entity's `_attr_` state from the coordinator data."""


class NavienEntity(NavienBaseEntity[NavienCoordinator]):
    """An entity of one BCM module, kept up to date by its coordinator."""

    def __init__(self, coordinator: NavienCoordinator, key: str) -> None:
        """Set up an entity of one module, unique by `key`."""
        super().__init__(coordinator)
        self.device = coordinator.device
        self._attr_unique_id = f"{self.device.device_id}_{key}"
        self._attr_device_info = device_info(self.device, coordinator.data)

    @property
    def status(self) -> DeviceStatus:
        """The module's latest status."""
        return self.coordinator.data.status

    async def _control(self, call: Awaitable[DeviceStatus | None]) -> None:
        """Run a control command and show its resulting status straight away."""
        try:
            result = await call
        except NavienError as err:
            raise HomeAssistantError(f"Navien command failed: {err}") from err
        self.coordinator.async_apply_status(result)


class NavienZoneEntity(NavienEntity):
    """An entity of one heating zone."""

    def __init__(self, coordinator: NavienCoordinator, key: str, zone_id: int) -> None:
        """Set up an entity of one zone, unique by `key` and zone."""
        super().__init__(coordinator, f"zone{zone_id}_{key}")
        self.zone_id = zone_id
        zone_names = {z.zone_id: z.name for z in self.device.zones}
        self._zone_name = zone_names.get(zone_id, f"Zone {zone_id}")

    @property
    def zone(self) -> ZoneStatus | None:
        """The zone's latest status, or None if the module no longer reports it."""
        return self.status.zones.get(self.zone_id)

    @property
    def available(self) -> bool:
        """Available while the coordinator is and the zone is reported."""
        return super().available and self.zone is not None


class NavienUsageEntity(NavienBaseEntity[NavienUsageCoordinator]):
    """An entity fed by the hourly usage coordinator."""

    def __init__(self, coordinator: NavienUsageCoordinator, key: str) -> None:
        """Set up an entity of one module's usage, unique by `key`."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device.device_id}_{key}"
        self._attr_device_info = device_info(coordinator.device)

    @property
    def available(self) -> bool:
        """Available once the first usage update has finished."""
        return super().available and self.coordinator.data is not None
