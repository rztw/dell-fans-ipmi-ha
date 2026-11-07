"""IPMI-only telemetry for iDRAC6 / 11th-generation PowerEdge servers.

iDRAC6 has no Redfish endpoint at all, so everything here is derived from the
IPMI SDR repository and the FRU inventory area. The output is shaped exactly
like :meth:`RedfishClient.get_all_data` so the sensor platform does not need to
know which backend produced it.
"""
from __future__ import annotations

import logging
from typing import Any

from . import sdr
from .ipmi import IpmiClient, IpmiError

_LOGGER = logging.getLogger(__name__)


class IpmiTelemetryError(Exception):
    """IPMI telemetry error."""


class IpmiTelemetryClient:
    """Telemetry backend that speaks IPMI only (iDRAC6 and friends)."""

    def __init__(self, ipmi: IpmiClient) -> None:
        self._ipmi = ipmi
        self._device_id: dict[str, Any] = {}
        self._fru: dict[str, str] = {}
        self._records: list[dict[str, Any]] = []
        self._locator_names: dict[tuple[int, int], str] = {}
        self._loaded = False

    async def close(self) -> None:
        """Nothing to release; kept for API parity with RedfishClient."""

    # ------------------------------------------------------------------
    # Static inventory (cached)
    # ------------------------------------------------------------------

    async def _ensure_static(self) -> None:
        if self._loaded:
            return
        try:
            static = await self._ipmi.read_static_platform()
        except IpmiError as exc:
            raise IpmiTelemetryError(str(exc)) from exc

        self._device_id = static.get("device_id") or {}
        self._fru = sdr.parse_fru(static.get("fru") or b"")

        records: list[dict[str, Any]] = []
        locators: dict[tuple[int, int], str] = {}
        for raw in static.get("sdr_records") or []:
            record = sdr.parse_sdr_record(raw)
            if not record:
                continue
            if record["record_type"] == sdr.SDR_TYPE_FRU_LOCATOR:
                locators[(record["entity_id"], record["entity_instance"])] = record[
                    "name"
                ]
            elif record["record_type"] == sdr.SDR_TYPE_FULL:
                records.append(record)

        self._records = records
        self._locator_names = locators
        self._loaded = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_all_data(self) -> dict[str, Any]:
        """Return telemetry in the same shape as the Redfish client."""
        await self._ensure_static()

        numbers = [r["sensor_number"] for r in self._records]
        try:
            snapshot = await self._ipmi.read_sensor_snapshot(numbers)
        except IpmiError as exc:
            raise IpmiTelemetryError(str(exc)) from exc

        readings: dict[int, bytes] = snapshot.get("readings") or {}
        chassis = snapshot.get("chassis") or {}

        return {
            "system": self._build_system(chassis),
            "manager": self._build_manager(),
            "thermal": self._build_thermal(readings),
            "power": self._build_power(readings),
        }

    async def get_system_summary(self) -> dict[str, Any]:
        """Cheap connectivity check used by the config flow."""
        await self._ensure_static()
        return self._build_system({})

    # ------------------------------------------------------------------
    # Builders
    # ------------------------------------------------------------------

    def _build_system(self, chassis: dict[str, Any]) -> dict[str, Any]:
        power_on = chassis.get("power_on")
        if power_on is None:
            power_state = ""
        else:
            power_state = "On" if power_on else "Off"

        return {
            "id": "system",
            "name": self._fru.get("product_name", ""),
            "manufacturer": self._fru.get("board_manufacturer", "Dell"),
            "model": self._fru.get("board_product", ""),
            "host_name": self._fru.get("product_name", ""),
            "power_state": power_state,
            "bios_version": "",
            "serial_number": self._fru.get("board_serial", ""),
            "service_tag": self._fru.get("product_serial") or None,
            "cpu_model": "",
            "cpu_count": None,
            "total_memory_gib": None,
            "status": None,
        }

    def _build_manager(self) -> dict[str, Any]:
        return {
            "id": "bmc",
            "name": "iDRAC",
            "model": "iDRAC6",
            "firmware_version": self._device_id.get("firmware_version", ""),
            "manager_type": "BMC",
            "status": None,
            "command_shell_enabled": False,
        }

    def _build_thermal(self, readings: dict[int, bytes]) -> dict[str, Any]:
        fans: list[dict[str, Any]] = []
        temperatures: list[dict[str, Any]] = []

        for record in self._records:
            raw = readings.get(record["sensor_number"])
            if not raw or not sdr.reading_available(raw):
                continue
            value = sdr.convert_reading(record, raw)
            if value is None:
                continue

            if (
                record["sensor_type"] == sdr.SENSOR_TYPE_FAN
                and record["unit_base"] == sdr.UNIT_RPM
            ):
                fans.append(
                    {
                        "id": f"sdr{record['record_id']}",
                        "name": record["name"] or f"Fan {record['sensor_number']}",
                        "reading_rpm": value,
                        "physical_context": "",
                        "status": None,
                        "lower_threshold_non_critical": sdr.convert_value(
                            record, record["lower_non_critical"]
                        ),
                    }
                )
            elif (
                record["sensor_type"] == sdr.SENSOR_TYPE_TEMPERATURE
                and record["unit_base"] == sdr.UNIT_CELSIUS
            ):
                temperatures.append(
                    {
                        "id": f"sdr{record['record_id']}",
                        "name": record["name"] or f"Temp {record['sensor_number']}",
                        "reading_celsius": value,
                        "physical_context": "",
                        "status": None,
                        "upper_threshold_critical": sdr.convert_value(
                            record, record["upper_critical"]
                        ),
                        "upper_threshold_non_critical": sdr.convert_value(
                            record, record["upper_non_critical"]
                        ),
                    }
                )

        return {"fans": fans, "temperatures": temperatures}

    def _build_power(self, readings: dict[int, bytes]) -> dict[str, Any]:
        power_control: dict[str, Any] | None = None
        supplies: dict[int, dict[str, Any]] = {}

        for record in self._records:
            raw = readings.get(record["sensor_number"])
            value = (
                sdr.convert_reading(record, raw)
                if raw and sdr.reading_available(raw)
                else None
            )

            if record["unit_base"] == sdr.UNIT_WATTS and value is not None:
                power_control = {
                    "power_consumed_watts": value,
                    "power_capacity_watts": None,
                    "average_consumed_watts": None,
                    "max_consumed_watts": None,
                    "min_consumed_watts": None,
                }
                continue

            # 11G exposes PSU voltage/current as separate Voltage/Current
            # sensors on the Power Supply entity (0x0A), not as type 0x08.
            if record["entity_id"] != sdr.ENTITY_POWER_SUPPLY:
                continue

            instance = record["entity_instance"]
            supply = supplies.setdefault(
                instance,
                {
                    "id": f"psu{instance}",
                    "name": self._locator_names.get(
                        (record["entity_id"], instance), f"PSU {instance}"
                    ),
                    "model": "",
                    "firmware_version": "",
                    "line_input_voltage": None,
                    "power_supply_type": "",
                    "serial_number": "",
                    "status": None,
                },
            )
            if record["unit_base"] == sdr.UNIT_VOLTS and value is not None:
                supply["line_input_voltage"] = value

        return {
            "power_control": power_control,
            "power_supplies": [supplies[k] for k in sorted(supplies)],
        }
