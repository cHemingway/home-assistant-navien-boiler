"""The Navien Smart Plus integration."""

from dataclasses import dataclass

from homeassistant.components.recorder import get_instance
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.ssl import get_default_context

from .api import NavienAuthError, NavienError, NavienSmartPlusClient
from .const import CONF_CLIENT_ID, CONF_DEVICE_MACS, DOMAIN
from .coordinator import (
    USAGE_STATISTICS,
    NavienCoordinator,
    NavienUsageCoordinator,
    usage_statistic_id,
    usage_store,
)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
]


@dataclass
class NavienRuntimeData:
    """Objects shared by the platforms of one config entry."""

    client: NavienSmartPlusClient
    coordinators: dict[str, NavienCoordinator]
    usage: dict[str, NavienUsageCoordinator]


type NavienConfigEntry = ConfigEntry[NavienRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: NavienConfigEntry) -> bool:
    """Set up Navien Smart Plus from a config entry."""
    client = NavienSmartPlusClient(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        client_id=entry.data[CONF_CLIENT_ID],
        ssl_context=get_default_context(),
    )
    # Home Assistant also runs this when setup fails part-way.
    entry.async_on_unload(client.async_close)
    try:
        await client.async_get_devices()
        await client.async_connect()
    except NavienAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except NavienError as err:
        raise ConfigEntryNotReady(str(err)) from err

    macs = {d.device_id: d.mac for d in client.devices}
    if entry.data.get(CONF_DEVICE_MACS) != macs:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_DEVICE_MACS: macs}
        )

    coordinators = {
        d.device_id: NavienCoordinator(hass, entry, client, d) for d in client.devices
    }
    for coordinator in coordinators.values():
        await coordinator.async_config_entry_first_refresh()
    usage = {
        device_id: NavienUsageCoordinator(hass, entry, client, coordinator.device)
        for device_id, coordinator in coordinators.items()
        if coordinator.data.features.has_fuel_usage
    }
    entry.runtime_data = NavienRuntimeData(
        client=client, coordinators=coordinators, usage=usage
    )

    for coordinator in coordinators.values():
        entry.async_on_unload(
            client.add_status_listener(coordinator.async_handle_status)
        )

    def _connection_changed(connected: bool) -> None:
        # Reconnect promptly instead of waiting for the next poll.
        if not connected:
            for coordinator in coordinators.values():
                hass.async_create_task(coordinator.async_request_refresh())

    entry.async_on_unload(client.add_connection_listener(_connection_changed))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Gas history can take a while on first setup (one request per month), so load it in the background.
    for usage_coordinator in usage.values():
        entry.async_create_background_task(
            hass,
            usage_coordinator.async_refresh(),
            f"{DOMAIN} usage {usage_coordinator.device.device_id}",
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NavienConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove imported gas statistics and the usage cache along with the entry."""
    devices = entry.data.get(CONF_DEVICE_MACS, {})
    statistic_ids = [
        usage_statistic_id(mac, suffix)
        for mac in devices.values()
        for suffix in USAGE_STATISTICS
    ]
    if statistic_ids:
        get_instance(hass).async_clear_statistics(statistic_ids)
    for device_id in devices:
        await usage_store(hass, device_id).async_remove()
