"""Diagnostics for Navien Smart Plus."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import NavienConfigEntry
from .const import CONF_CLIENT_ID, CONF_DEVICE_MACS

TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_CLIENT_ID,
    CONF_DEVICE_MACS,
    "device_id",
    "mac",
    "boilerControllerSerialNumber",
    "regionCode",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: NavienConfigEntry) -> dict[str, Any]:
    runtime = entry.runtime_data
    devices = []
    for device_id, coordinator in runtime.coordinators.items():
        data = coordinator.data
        usage = runtime.usage[device_id].data if device_id in runtime.usage else None
        devices.append(
            {
                "device": asdict(coordinator.device),
                "features": data.features.raw if data else None,
                "status": data.status.raw if data else None,
                "schedules": {k: asdict(v) for k, v in data.schedules.items()} if data else None,
                "usage_totals": asdict(usage) if usage else None,
            }
        )
    return async_redact_data(
        {"entry": dict(entry.data), "mqtt_connected": runtime.client.connected, "devices": devices},
        TO_REDACT,
    )
