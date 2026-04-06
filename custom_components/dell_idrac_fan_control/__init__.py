"""Dell iDRAC Fan Control integration for Home Assistant."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_TIMEOUT,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant

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
    DOMAIN,
    PLATFORMS,
)
from .coordinator import FanControlCoordinator, TelemetryCoordinator
from .ipmi import IpmiClient
from .redfish import RedfishClient

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Dell iDRAC Fan Control from a config entry."""
    cfg = {**entry.data, **entry.options}

    redfish = RedfishClient(
        host=cfg[CONF_HOST],
        port=cfg.get(CONF_PORT, DEFAULT_PORT),
        username=cfg[CONF_USERNAME],
        password=cfg[CONF_PASSWORD],
        base_path=cfg.get(CONF_BASE_PATH, DEFAULT_BASE_PATH),
        timeout=cfg.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
        allow_insecure_tls=cfg.get(CONF_ALLOW_INSECURE_TLS, DEFAULT_ALLOW_INSECURE_TLS),
    )
    ipmi = IpmiClient(
        host=cfg[CONF_HOST],
        ipmi_port=cfg.get(CONF_IPMI_PORT, DEFAULT_IPMI_PORT),
        username=cfg[CONF_USERNAME],
        password=cfg[CONF_PASSWORD],
        timeout=cfg.get(CONF_IPMI_TIMEOUT, DEFAULT_IPMI_TIMEOUT),
    )

    scan_interval = cfg.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    telemetry = TelemetryCoordinator(hass, redfish, scan_interval)
    fan_control = FanControlCoordinator(hass, ipmi, scan_interval)

    await telemetry.async_config_entry_first_refresh()
    # Fan control coordinator is resilient — never raises UpdateFailed
    await fan_control.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "redfish": redfish,
        "ipmi": ipmi,
        "telemetry": telemetry,
        "fan_control": fan_control,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["redfish"].close()
    return ok
