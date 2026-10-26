"""Setup, entities and services of the Navien Smart Plus integration (client mocked with real captured data)."""

from datetime import datetime
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.components.water_heater import (
    ATTR_OPERATION_MODE,
    DOMAIN as WATER_HEATER_DOMAIN,
    SERVICE_SET_OPERATION_MODE,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.navien_smartplus.api import (
    HeatingMode,
    HotWaterMode,
    NavienAuthError,
    NavienConnectionError,
)

from .conftest import fixture_status

CLIMATE = "climate.navien_boiler_home"


@pytest.fixture(autouse=True)
async def london_sunday_evening(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The moment the fixtures were captured: Sunday 25 October 2026, 20:05 in London (program slot 5)."""
    await hass.config.async_set_time_zone("Europe/London")
    freezer.move_to("2026-10-25 20:05:00+00:00")


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add the entry and wait for setup to finish."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_and_entities(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Setup creates entities matching the captured data, and unloading closes the client."""
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    climate = hass.states.get(CLIMATE)
    assert (
        climate.state == HVACMode.AUTO
    )  # Override counts as Auto with the "override" preset
    assert climate.attributes["current_temperature"] == 19.5
    assert (
        climate.attributes["temperature"] == 19.0
    )  # in Override the setting is the target
    assert climate.attributes["preset_mode"] == "override"
    assert climate.attributes["hvac_action"] == "idle"

    assert hass.states.get("sensor.navien_boiler_outdoor_temperature").state == "9.2"
    assert hass.states.get("water_heater.navien_boiler_hot_water").state == "auto"
    assert hass.states.get("binary_sensor.navien_boiler_burner").state == "off"
    assert hass.states.get("binary_sensor.navien_boiler_fault").state == "off"
    assert hass.states.get("switch.navien_boiler_holiday_mode").state == "off"

    # Year-to-date gas from the monthly history.
    assert hass.states.get("sensor.navien_boiler_heating_gas_this_year").state == "252"
    assert (
        hass.states.get("sensor.navien_boiler_hot_water_gas_this_year").state == "202"
    )
    assert hass.states.get("sensor.navien_boiler_gas_this_year").state == "454"

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    mock_client.async_close.assert_awaited()


async def test_auto_target_comes_from_schedule(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Live-verified: in Auto the setting field keeps a stale override value; the app shows the schedule's 16 °C."""
    status = fixture_status()
    status.zones[1].mode = HeatingMode.AUTO
    status.zones[1].setting_temperature = 19.5
    status.zones[1].program_number = 5
    mock_client.async_get_status.return_value = status
    await setup_entry(hass, config_entry)

    climate = hass.states.get(CLIMATE)
    assert climate.attributes["temperature"] == 16.0
    assert climate.attributes["preset_mode"] == "none"


async def test_climate_services(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Climate actions send the matching commands."""
    await setup_entry(hass, config_entry)
    device = mock_client.devices[0]

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: 20.5},
        blocking=True,
    )
    mock_client.async_set_zone_temperature.assert_awaited_once_with(
        device, 1, 20.5, HeatingMode.OVERRIDE
    )

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_HVAC_MODE: HVACMode.OFF},
        blocking=True,
    )
    mock_client.async_set_zone_mode.assert_awaited_with(
        device, 1, HeatingMode.OFF, 19.0
    )

    # Leaving the override preset returns to the schedule.
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_PRESET_MODE: "none"},
        blocking=True,
    )
    mock_client.async_set_zone_mode.assert_awaited_with(
        device, 1, HeatingMode.AUTO, 19.0
    )


async def test_control_result_updates_state(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """The status returned by a command is shown straight away."""
    await setup_entry(hass, config_entry)
    result = fixture_status()
    result.zones[1].setting_temperature = 21.0
    mock_client.async_set_zone_temperature.return_value = result
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: 21.0},
        blocking=True,
    )
    assert hass.states.get(CLIMATE).attributes["temperature"] == 21.0


async def test_set_temperature_when_off(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Setting a temperature on a zone that's off is refused."""
    status = fixture_status()
    status.zones[1].mode = HeatingMode.OFF
    mock_client.async_get_status.return_value = status
    await setup_entry(hass, config_entry)
    assert hass.states.get(CLIMATE).state == HVACMode.OFF
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: 20},
            blocking=True,
        )
    mock_client.async_set_zone_temperature.assert_not_awaited()


async def test_hot_water_service(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Setting the hot water mode sends the matching command."""
    await setup_entry(hass, config_entry)
    await hass.services.async_call(
        WATER_HEATER_DOMAIN,
        SERVICE_SET_OPERATION_MODE,
        {
            ATTR_ENTITY_ID: "water_heater.navien_boiler_hot_water",
            ATTR_OPERATION_MODE: "boost",
        },
        blocking=True,
    )
    mock_client.async_set_hot_water_mode.assert_awaited_once_with(
        mock_client.devices[0], HotWaterMode.ONCE
    )


async def test_gas_statistics_imported(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Daily gas use is imported as statistics at local midnight."""
    await setup_entry(hass, config_entry)
    await async_wait_recording_done(hass)

    statistic_id = "navien_smartplus:001122334455_total_gas"
    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        datetime.fromisoformat("2026-10-01T00:00:00+01:00"),
        None,
        {statistic_id},
        "hour",
        None,
        {"state", "sum"},
    )
    rows = stats[statistic_id]
    # One row per finished day (1-24 October), at local midnight; the running sum ends at the month's total.
    assert len(rows) == 24
    assert rows[-1]["sum"] == 27  # 10 m³ heating + 17 m³ hot water
    last_start = dt_util.as_local(dt_util.utc_from_timestamp(rows[-1]["start"]))
    assert last_start.isoformat() == "2026-10-24T00:00:00+01:00"  # local (BST) midnight


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (NavienAuthError("bad"), ConfigEntryState.SETUP_ERROR),
        (NavienConnectionError("down"), ConfigEntryState.SETUP_RETRY),
    ],
)
async def test_setup_errors(
    hass: HomeAssistant,
    mock_client: MagicMock,
    config_entry: MockConfigEntry,
    error: Exception,
    state: ConfigEntryState,
) -> None:
    """Bad credentials start reauthentication; connection problems retry setup."""
    mock_client.async_get_devices.side_effect = error
    await setup_entry(hass, config_entry)
    assert config_entry.state is state
    if isinstance(error, NavienAuthError):
        assert any(
            flow["context"]["source"] == "reauth"
            for flow in hass.config_entries.flow.async_progress()
        )


async def test_diagnostics_redacts(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Diagnostics leave out credentials and identifiers."""
    from custom_components.navien_smartplus.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    await setup_entry(hass, config_entry)
    diag = await async_get_config_entry_diagnostics(hass, config_entry)
    text = str(diag)
    assert "secret" not in text
    assert "user@example.com" not in text
    assert "001122334455" not in text
    assert diag["devices"][0]["status"]["outsideTemperature"] == 92


async def test_daily_usage_only_fetched_when_totals_change(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Daily usage is only fetched again when the monthly totals change."""
    await setup_entry(hass, config_entry)
    usage = config_entry.runtime_data.usage[mock_client.devices[0].device_id]
    # First setup backfills 13 months of daily history; the current month then matches its monthly total.
    assert mock_client.async_get_daily_usage.await_count == 13

    await usage.async_refresh()
    assert (
        mock_client.async_get_daily_usage.await_count == 13
    )  # monthly totals unchanged: no daily requests

    monthly = await mock_client.async_get_monthly_usage(None, 2026)
    monthly[9].heating_usage += 1  # the module has filled in another day
    mock_client.async_get_monthly_usage.side_effect = None
    mock_client.async_get_monthly_usage.return_value = monthly
    await usage.async_refresh()
    assert mock_client.async_get_daily_usage.await_count == 14
    assert hass.states.get("sensor.navien_boiler_heating_gas_this_year").state == "253"


async def test_stop_closes_client_without_reconnecting(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Home Assistant's stop event closes the client, and a drop while stopping doesn't trigger a refresh."""
    await setup_entry(hass, config_entry)
    connection_changed = mock_client.add_connection_listener.call_args.args[0]
    polls = mock_client.async_get_status.await_count

    hass.set_state(CoreState.stopping)
    connection_changed(False)
    await hass.async_block_till_done()
    assert mock_client.async_get_status.await_count == polls

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    mock_client.async_close.assert_awaited()
