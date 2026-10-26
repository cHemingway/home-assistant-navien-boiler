"""Fixtures for Navien Smart Plus tests. Data comes from anonymised captures of a real system (tests/fixtures)."""

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_CLIENT_ID, CONF_EMAIL, CONF_PASSWORD
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    load_json_object_fixture,
)

from custom_components.navien_smartplus.api import (
    Account,
    Device,
    DeviceFeatures,
    DeviceStatus,
    HeatingSchedule,
    UsageRecord,
)
from custom_components.navien_smartplus.const import DOMAIN


def load_fixture(name: str) -> Any:
    """A JSON fixture from tests/fixtures."""
    return load_json_object_fixture(name)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    recorder_mock: Any, enable_custom_integrations: None
) -> None:
    """Load custom_components/ in every test.

    The integration depends on recorder (gas statistics), and the recorder fixture must come before `hass`,
    so it's requested first here.
    """


def fixture_devices() -> list[Device]:
    """Devices from the captured REST device list."""
    return [
        Device.from_json(d) for d in load_fixture("devices.json")["data"]["devices"]
    ]


def fixture_status() -> DeviceStatus:
    """Status from a captured status reply."""
    return DeviceStatus.from_json(
        load_fixture("reply_status.json")["data"]["response"]["status"]
    )


def fixture_features() -> DeviceFeatures:
    """Features from a captured device-info reply."""
    return DeviceFeatures(load_fixture("reply_did.json")["data"]["response"]["feature"])


def fixture_schedules() -> dict[int, HeatingSchedule]:
    """Heating schedules from a captured schedule reply."""
    zones = load_fixture("reply_heat_schedule.json")["response"]["heatingReservation"]
    return {z["zoneId"]: HeatingSchedule.from_json(z) for z in zones}


def fixture_daily_usage(year: int, month: int) -> list[UsageRecord]:
    """Daily usage for a month; only October 2026 was captured."""
    if (year, month) != (2026, 10):
        return []
    return [
        UsageRecord.from_json(r) for r in load_fixture("usage.json")["daily_2026_10"]
    ]


def fixture_monthly_usage(year: int) -> list[UsageRecord]:
    """Monthly usage for a year; only 2026 was captured."""
    if year != 2026:
        return []
    return [
        UsageRecord.from_json(r) for r in load_fixture("usage.json")["monthly_2026"]
    ]


@pytest.fixture
def mock_client() -> Generator[MagicMock]:
    """A NavienSmartPlusClient double used by both the integration and the config flow."""
    client = MagicMock()
    client.devices = fixture_devices()
    client.connected = True
    client.login = AsyncMock(
        return_value=Account(email="user@example.com", user_seq=1001, family_seq=2002)
    )
    client.async_get_devices = AsyncMock(return_value=client.devices)
    client.async_connect = AsyncMock()
    client.async_close = AsyncMock()
    client.async_get_features = AsyncMock(return_value=fixture_features())
    client.async_get_status = AsyncMock(return_value=fixture_status())
    client.async_get_heating_schedules = AsyncMock(return_value=fixture_schedules())
    client.async_get_daily_usage = AsyncMock(
        side_effect=lambda device, year, month: fixture_daily_usage(year, month)
    )
    client.async_get_monthly_usage = AsyncMock(
        side_effect=lambda device, year: fixture_monthly_usage(year)
    )
    client.async_set_zone_temperature = AsyncMock(return_value=None)
    client.async_set_zone_mode = AsyncMock(return_value=None)
    client.async_set_hot_water_mode = AsyncMock(return_value=None)
    client.async_set_holiday = AsyncMock(return_value=None)
    client.add_status_listener = MagicMock(return_value=lambda: None)
    client.add_connection_listener = MagicMock(return_value=lambda: None)
    client.add_online_listener = MagicMock(return_value=lambda: None)
    with (
        patch(
            "custom_components.navien_smartplus.NavienSmartPlusClient",
            return_value=client,
        ),
        patch(
            "custom_components.navien_smartplus.config_flow.NavienSmartPlusClient",
            return_value=client,
        ),
    ):
        yield client


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A config entry for the test account."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="user@example.com",
        unique_id="1001",
        data={
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "secret",
            CONF_CLIENT_ID: "00000000-0000-0000-0000-000000000000",
        },
    )
