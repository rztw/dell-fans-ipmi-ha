"""Select platform — fan control mode (auto / manual)."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_PORT, DOMAIN
from .coordinator import FanControlCoordinator

_LOGGER = logging.getLogger(__name__)

MODE_AUTO = "auto"
MODE_MANUAL = "manual"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the fan mode select entity."""
    coordinator: FanControlCoordinator = hass.data[DOMAIN][entry.entry_id]["fan_control"]
    async_add_entities([DellIdracFanModeSelect(coordinator, entry)])


class DellIdracFanModeSelect(
    CoordinatorEntity[FanControlCoordinator], SelectEntity
):
    """Select entity for switching between auto and manual fan control."""

    _attr_has_entity_name = True
    _attr_translation_key = "fan_mode"
    _attr_icon = "mdi:fan-auto"
    _attr_options = [MODE_AUTO, MODE_MANUAL]

    def __init__(
        self,
        coordinator: FanControlCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_fan_mode"

    @property
    def device_info(self) -> DeviceInfo:  # noqa: D102
        cfg = self._entry.data
        telemetry = self.hass.data[DOMAIN][self._entry.entry_id].get("telemetry")
        data = telemetry.data if telemetry else None
        sys = (data.get("system") or {}) if data else {}
        mgr = (data.get("manager") or {}) if data else {}
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=f"Dell iDRAC ({cfg[CONF_HOST]})",
            manufacturer="Dell",
            model=sys.get("model"),
            sw_version=mgr.get("firmware_version"),
            serial_number=sys.get("service_tag") or sys.get("serial_number"),
            configuration_url=f"https://{cfg[CONF_HOST]}:{cfg.get(CONF_PORT, DEFAULT_PORT)}",
        )

    @property
    def current_option(self) -> str | None:  # noqa: D102
        if self.coordinator.data is None:
            return None
        mode = self.coordinator.data.get("mode", "unknown")
        if mode in (MODE_AUTO, MODE_MANUAL):
            return mode
        return None

    async def async_select_option(self, option: str) -> None:
        """Handle user selecting a fan mode."""
        if option == MODE_AUTO:
            _LOGGER.info("Restoring Dell iDRAC automatic fan control")
            await self.coordinator.async_set_auto_mode()
        elif option == MODE_MANUAL:
            _LOGGER.info("Switching Dell iDRAC to manual fan mode")
            current_speed = (self.coordinator.data or {}).get("speed_percent")
            speed = current_speed if current_speed is not None else 30
            await self.coordinator.async_set_manual_speed(speed)
