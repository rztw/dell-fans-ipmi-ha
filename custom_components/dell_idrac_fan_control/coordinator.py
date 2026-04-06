"""Data update coordinators for Dell iDRAC Fan Control."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .ipmi import IpmiClient, IpmiError
from .redfish import RedfishClient, RedfishError

_LOGGER = logging.getLogger(__name__)


class TelemetryCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches Redfish system / manager / thermal / power data."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: RedfishClient,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Dell iDRAC Telemetry",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.client.get_all_data()
        except RedfishError as exc:
            raise UpdateFailed(str(exc)) from exc


class FanControlCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Probes IPMI connectivity and tracks fan-control state."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: IpmiClient,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Dell iDRAC Fan Control",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self._mode: str = "unknown"
        self._speed: int | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            dev = await self.client.get_device_id()
            return {
                "connection_ok": True,
                "device_info": dev,
                "mode": self._mode,
                "speed_percent": self._speed,
                "error": None,
            }
        except IpmiError as exc:
            _LOGGER.debug("IPMI probe failed: %s", exc)
            return {
                "connection_ok": False,
                "device_info": None,
                "mode": self._mode,
                "speed_percent": self._speed,
                "error": str(exc),
            }

    async def async_set_auto_mode(self) -> None:
        """Send Dell auto-fan command via IPMI."""
        await self.client.set_automatic_fan_mode()
        self._mode = "auto"
        self._speed = None
        await self.async_request_refresh()

    async def async_set_manual_speed(self, speed: int) -> None:
        """Send Dell manual-fan-speed command via IPMI."""
        await self.client.set_manual_fan_speed(speed)
        self._mode = "manual"
        self._speed = speed
        await self.async_request_refresh()
