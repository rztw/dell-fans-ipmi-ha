"""Number platform — manual fan speed control."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_PORT, DOMAIN
from .coordinator import FanControlCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the fan speed number entity."""
    coordinator: FanControlCoordinator = hass.data[DOMAIN][entry.entry_id]["fan_control"]
    async_add_entities([DellIdracFanSpeedNumber(coordinator, entry)])


class DellIdracFanSpeedNumber(
    CoordinatorEntity[FanControlCoordinator], NumberEntity
):
    """Number entity representing the manual fan speed (0-100 %)."""

    _attr_has_entity_name = True
    _attr_translation_key = "fan_speed"
    _attr_icon = "mdi:fan"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(
        self,
        coordinator: FanControlCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_fan_speed"

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
    def native_value(self) -> float | None:  # noqa: D102
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("speed_percent")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:  # noqa: D102
        d = self.coordinator.data or {}
        dev = d.get("device_info") or {}
        return {
            "ipmi_connection_ok": d.get("connection_ok"),
            "ipmi_device_id": dev.get("device_id"),
            "ipmi_firmware_version": dev.get("firmware_version"),
            "ipmi_manufacturer_id": dev.get("manufacturer_id"),
            "ipmi_product_id": dev.get("product_id"),
            "ipmi_error": d.get("error"),
        }

    async def async_set_native_value(self, value: float) -> None:
        """Set manual fan speed — also switches mode to manual."""
        speed = int(value)
        _LOGGER.info("Setting Dell iDRAC fan speed to %d%%", speed)
        await self.coordinator.async_set_manual_speed(speed)
