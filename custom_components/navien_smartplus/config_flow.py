"""Config flow for Navien Smart Plus."""

from collections.abc import Mapping
from typing import Any
import uuid

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_CLIENT_ID, CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import NavienAuthError, NavienError, NavienSmartPlusClient
from .const import DOMAIN, LOGGER

PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)
USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
    }
)
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR})


class NoDevicesError(Exception):
    """The account has no Smart Plus devices."""


class NavienSmartPlusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Sign in with the Navien Smart Plus app account."""

    VERSION = 1

    async def _sign_in(self, email: str, password: str) -> str:
        """Sign in, check the account has devices, and return the account ID."""
        client = NavienSmartPlusClient(
            async_get_clientsession(self.hass), email, password
        )
        account = await client.login()
        if not await client.async_get_devices():
            raise NoDevicesError
        return str(account.user_seq)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the app login and create the entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            try:
                account_id = await self._sign_in(email, user_input[CONF_PASSWORD])
            except NavienAuthError:
                errors["base"] = "invalid_auth"
            except NavienError as err:
                LOGGER.debug("Cannot connect: %s", err)
                errors["base"] = "cannot_connect"
            except NoDevicesError:
                errors["base"] = "no_devices"
            except Exception:
                LOGGER.exception("Unexpected error signing in")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(account_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=email,
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_CLIENT_ID: str(uuid.uuid4()),
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication when the stored password stops working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the new password and update the entry."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                account_id = await self._sign_in(
                    entry.data[CONF_EMAIL], user_input[CONF_PASSWORD]
                )
            except NavienAuthError:
                errors["base"] = "invalid_auth"
            except NavienError as err:
                LOGGER.debug("Cannot connect: %s", err)
                errors["base"] = "cannot_connect"
            except NoDevicesError:
                errors["base"] = "no_devices"
            except Exception:
                LOGGER.exception("Unexpected error signing in")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(account_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )
