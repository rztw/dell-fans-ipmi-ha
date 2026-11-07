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
    CONF_CONNECTION_MODE,
    CONF_IPMI_PORT,
    CONF_IPMI_TIMEOUT,
    CONNECTION_MODES,
    DEFAULT_ALLOW_INSECURE_TLS,
    DEFAULT_BASE_PATH,
    DEFAULT_CONNECTION_MODE,
    DEFAULT_IPMI_PORT,
    DEFAULT_IPMI_TIMEOUT,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DEFAULT_USERNAME,
    DOMAIN,
    MODE_AUTO,
    MODE_IPMI,
    MODE_REDFISH,
)
from .ipmi import IpmiClient, IpmiError
from .ipmi_telemetry import IpmiTelemetryClient, IpmiTelemetryError
from .redfish import RedfishClient, RedfishError

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_CONNECTION_MODE, default=DEFAULT_CONNECTION_MODE): vol.In(
            CONNECTION_MODES
        ),
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
        vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_IPMI_PORT, default=DEFAULT_IPMI_PORT): vol.Coerce(int),
    }
)


async def _probe_redfish(user_input: dict[str, Any]) -> dict[str, Any] | None:
    client = RedfishClient(
        host=user_input[CONF_HOST],
        port=user_input.get(CONF_PORT, DEFAULT_PORT),
        username=user_input[CONF_USERNAME],
        password=user_input[CONF_PASSWORD],
    )
    try:
        return await client.test_connection()
    except RedfishError as exc:
        _LOGGER.debug("Redfish probe failed: %s", exc)
        return None
    finally:
        await client.close()


async def _probe_ipmi(user_input: dict[str, Any]) -> dict[str, Any] | None:
    ipmi = IpmiClient(
        host=user_input[CONF_HOST],
        ipmi_port=user_input.get(CONF_IPMI_PORT, DEFAULT_IPMI_PORT),
        username=user_input[CONF_USERNAME],
        password=user_input[CONF_PASSWORD],
        timeout=DEFAULT_IPMI_TIMEOUT,
    )
    telemetry = IpmiTelemetryClient(ipmi)
    try:
        return await telemetry.get_system_summary()
    except (IpmiError, IpmiTelemetryError) as exc:
        _LOGGER.debug("IPMI probe failed: %s", exc)
        return None


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
            mode = user_input.get(CONF_CONNECTION_MODE, DEFAULT_CONNECTION_MODE)
            info: dict[str, Any] | None = None
            detected = mode

            if mode in (MODE_AUTO, MODE_REDFISH):
                info = await _probe_redfish(user_input)
                if info is not None:
                    detected = MODE_REDFISH

            if info is None and mode in (MODE_AUTO, MODE_IPMI):
                info = await _probe_ipmi(user_input)
                if info is not None:
                    detected = MODE_IPMI

            if info is None:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_input[CONF_HOST])
                self._abort_if_unique_id_configured()
                data = {**user_input, CONF_CONNECTION_MODE: detected}
                title = f"{info.get('model') or 'Dell iDRAC'} ({user_input[CONF_HOST]})"
                return self.async_create_entry(title=title, data=data)

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
                    CONF_CONNECTION_MODE,
                    default=cur.get(CONF_CONNECTION_MODE, DEFAULT_CONNECTION_MODE),
                ): vol.In(CONNECTION_MODES),
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
