"""Config flow for Dell iDRAC Fan Control."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_TIMEOUT,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_ALLOW_INSECURE_TLS,
    CONF_BASE_PATH,
    CONF_IPMI_PORT,
    CONF_IPMI_TIMEOUT,
    DEFAULT_ALLOW_INSECURE_TLS,
    DEFAULT_BASE_PATH,
    DEFAULT_IPMI_PORT,
    DEFAULT_IPMI_TIMEOUT,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DEFAULT_USERNAME,
    DOMAIN,
)
from .redfish import RedfishClient, RedfishError

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
        vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_IPMI_PORT, default=DEFAULT_IPMI_PORT): vol.Coerce(int),
    }
)


class DellIdracConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Dell iDRAC Fan Control."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DellIdracOptionsFlow:
        """Return the options flow handler."""
        return DellIdracOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial user step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client = RedfishClient(
                host=user_input[CONF_HOST],
                port=user_input.get(CONF_PORT, DEFAULT_PORT),
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
            )
            try:
                info = await client.test_connection()
            except RedfishError as exc:
                _LOGGER.debug("Connection test failed: %s", exc)
                msg = str(exc)
                if "401" in msg:
                    errors["base"] = "invalid_auth"
                else:
                    errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during connection test")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user_input[CONF_HOST])
                self._abort_if_unique_id_configured()
                title = f"{info.get('model', 'Dell iDRAC')} ({user_input[CONF_HOST]})"
                return self.async_create_entry(title=title, data=user_input)
            finally:
                await client.close()

        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors
        )


class DellIdracOptionsFlow(OptionsFlow):
    """Handle options for Dell iDRAC Fan Control."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        cur = {**self.config_entry.data, **self.config_entry.options}

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=cur.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=10, max=300)),
                vol.Optional(
                    CONF_BASE_PATH,
                    default=cur.get(CONF_BASE_PATH, DEFAULT_BASE_PATH),
                ): str,
                vol.Optional(
                    CONF_ALLOW_INSECURE_TLS,
                    default=cur.get(CONF_ALLOW_INSECURE_TLS, DEFAULT_ALLOW_INSECURE_TLS),
                ): bool,
                vol.Optional(
                    CONF_TIMEOUT,
                    default=cur.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                ): vol.All(vol.Coerce(int), vol.Range(min=3, max=60)),
                vol.Optional(
                    CONF_IPMI_TIMEOUT,
                    default=cur.get(CONF_IPMI_TIMEOUT, DEFAULT_IPMI_TIMEOUT),
                ): vol.All(vol.Coerce(int), vol.Range(min=3, max=60)),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
