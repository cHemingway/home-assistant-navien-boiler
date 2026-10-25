"""Tests for the Navien Smart Plus config flow."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.navien_smartplus.api import NavienAuthError, NavienConnectionError
from custom_components.navien_smartplus.const import CONF_CLIENT_ID, DOMAIN

USER_INPUT = {CONF_EMAIL: " user@example.com ", CONF_PASSWORD: "secret"}


@pytest.fixture(autouse=True)
def no_setup() -> None:
    with patch("custom_components.navien_smartplus.async_setup_entry", return_value=True):
        yield


async def test_user_flow(hass: HomeAssistant, mock_client: MagicMock) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "user@example.com"
    assert result["result"].unique_id == "1001"
    assert result["data"][CONF_EMAIL] == "user@example.com"
    assert result["data"][CONF_PASSWORD] == "secret"
    assert len(result["data"][CONF_CLIENT_ID]) == 36


@pytest.mark.parametrize(
    ("side_effect", "devices", "error"),
    [
        (NavienAuthError("bad"), None, "invalid_auth"),
        (NavienConnectionError("down"), None, "cannot_connect"),
        (RuntimeError("boom"), None, "unknown"),
        (None, [], "no_devices"),
    ],
)
async def test_user_flow_errors(hass: HomeAssistant, mock_client: MagicMock, side_effect, devices, error) -> None:
    if side_effect is not None:
        mock_client.login.side_effect = side_effect
    if devices is not None:
        mock_client.async_get_devices.return_value = devices
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    # Recovers once the problem is gone.
    mock_client.login.side_effect = None
    mock_client.async_get_devices.return_value = mock_client.devices
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_already_configured(hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    mock_client.login.side_effect = NavienAuthError("bad")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "wrong"})
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client.login.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "new"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "new"
