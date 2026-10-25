"""Base entities for Navien Smart Plus."""

from __future__ import annotations

from collections.abc import Awaitable

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo, format_mac
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Device, DeviceStatus, NavienError, ZoneStatus
from .const import DOMAIN, MANUFACTURER
from .coordinator import NavienCoordinator, NavienData, NavienUsageCoordinator


def device_info(device: Device, data: NavienData | None = None) -> DeviceInfo:
    info = DeviceInfo(
        identifiers={(DOMAIN, device.device_id)},
        connections={(CONNECTION_NETWORK_MAC, format_mac(device.mac))},
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


class NavienEntity(CoordinatorEntity[NavienCoordinator]):
    """An entity of one BCM module, kept up to date by its coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: NavienCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self.device = coordinator.device
        self._attr_unique_id = f"{self.device.device_id}_{key}"
        self._attr_device_info = device_info(self.device, coordinator.data)

    @property
    def status(self) -> DeviceStatus:
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
        super().__init__(coordinator, f"zone{zone_id}_{key}")
        self.zone_id = zone_id
        zone_names = {z.zone_id: z.name for z in self.device.zones}
        self._zone_name = zone_names.get(zone_id, f"Zone {zone_id}")

    @property
    def zone(self) -> ZoneStatus | None:
        return self.status.zones.get(self.zone_id)

    @property
    def available(self) -> bool:
        return super().available and self.zone is not None


class NavienUsageEntity(CoordinatorEntity[NavienUsageCoordinator]):
    """An entity fed by the hourly usage coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: NavienUsageCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device.device_id}_{key}"
        self._attr_device_info = device_info(coordinator.device)

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None
