"""Redfish API client for Dell iDRAC."""
from __future__ import annotations

import ssl
from typing import Any

import aiohttp


class RedfishError(Exception):
    """Redfish API error."""


class RedfishClient:
    """Async client for Dell iDRAC Redfish API."""

    def __init__(
        self,
        host: str,
        port: int = 443,
        username: str = "root",
        password: str = "",
        base_path: str = "/redfish/v1",
        timeout: int = 8,
        allow_insecure_tls: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self._username = username
        self._password = password
        self._base_path = base_path.rstrip("/") or "/redfish/v1"
        self._timeout = timeout

        if allow_insecure_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self._ssl: ssl.SSLContext | None = ctx
        else:
            self._ssl = None

        self._session: aiohttp.ClientSession | None = None
        self._core_paths: dict[str, str] | None = None

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=self._ssl)
            self._session = aiohttp.ClientSession(
                base_url=f"https://{self.host}:{self.port}",
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
                auth=aiohttp.BasicAuth(self._username, self._password),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        self._core_paths = None

    # ------------------------------------------------------------------
    # Low-level request
    # ------------------------------------------------------------------

    async def _request_json(self, path: str, label: str) -> dict[str, Any]:
        normalized = path if path.startswith("/") else f"{self._base_path}/{path}"
        session = self._ensure_session()
        try:
            async with session.get(
                normalized, headers={"Accept": "application/json"}
            ) as resp:
                if resp.status == 401:
                    raise RedfishError(f"{label}: authentication failed (HTTP 401)")
                if resp.status < 200 or resp.status >= 300:
                    body = await resp.text()
                    raise RedfishError(
                        f"{label} failed (HTTP {resp.status}): {body[:240]}"
                    )
                return await resp.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise RedfishError(f"{label}: {exc}") from exc

    # ------------------------------------------------------------------
    # Path resolution (cached)
    # ------------------------------------------------------------------

    async def _get_core_paths(self) -> dict[str, str]:
        if self._core_paths is None:
            self._core_paths = await self._resolve_core_paths()
        return self._core_paths

    async def _resolve_core_paths(self) -> dict[str, str]:
        root = await self._request_json(self._base_path, "Service root")
        systems_path = _odata_id(root.get("Systems"))
        managers_path = _odata_id(root.get("Managers"))
        if not systems_path or not managers_path:
            raise RedfishError("Service root missing Systems or Managers collection")

        system_path = await self._first_member(
            systems_path, "Systems", "System.Embedded"
        )
        system = await self._request_json(system_path, "System resource")
        links = system.get("Links", {})
        chassis_list = links.get("Chassis", [])
        managed_by = links.get("ManagedBy", [])

        chassis_path = _odata_id(chassis_list[0]) if chassis_list else None
        manager_path = _odata_id(managed_by[0]) if managed_by else None
        if not manager_path:
            manager_path = await self._first_member(
                managers_path, "Managers", "iDRAC"
            )
        if not chassis_path:
            raise RedfishError("System does not expose a linked Chassis")

        return {
            "system": system_path,
            "manager": manager_path,
            "chassis": chassis_path,
        }

    async def _first_member(
        self, collection_path: str, label: str, preferred: str | None = None
    ) -> str:
        collection = await self._request_json(collection_path, label)
        members = collection.get("Members", [])
        target: str | None = None
        if preferred:
            for m in members:
                oid = _odata_id(m)
                if oid and preferred in oid:
                    target = oid
                    break
        if not target and members:
            target = _odata_id(members[0])
        if not target:
            raise RedfishError(f"{label} collection has no members")
        return target

    # ------------------------------------------------------------------
    # Public data methods
    # ------------------------------------------------------------------

    async def test_connection(self) -> dict[str, Any]:
        """Quick connectivity check — returns system summary."""
        return await self.get_system_summary()

    async def get_all_data(self) -> dict[str, Any]:
        """Fetch system, manager, thermal, and power data in one pass."""
        paths = await self._get_core_paths()
        system = await self._fetch_system(paths)
        manager = await self._fetch_manager(paths)
        thermal = await self._fetch_thermal(paths)
        power = await self._fetch_power(paths)
        return {
            "system": system,
            "manager": manager,
            "thermal": thermal,
            "power": power,
        }

    async def get_system_summary(self) -> dict[str, Any]:
        paths = await self._get_core_paths()
        return await self._fetch_system(paths)

    # ------------------------------------------------------------------
    # Internal fetch helpers
    # ------------------------------------------------------------------

    async def _fetch_system(self, paths: dict[str, str]) -> dict[str, Any]:
        s = await self._request_json(paths["system"], "System")
        proc = s.get("ProcessorSummary") or {}
        mem = s.get("MemorySummary") or {}
        return {
            "id": s.get("Id", ""),
            "name": s.get("Name", ""),
            "manufacturer": s.get("Manufacturer", ""),
            "model": s.get("Model", ""),
            "host_name": s.get("HostName", ""),
            "power_state": s.get("PowerState", ""),
            "bios_version": s.get("BiosVersion", ""),
            "serial_number": s.get("SerialNumber", ""),
            "service_tag": s.get("SKU") or None,
            "cpu_model": proc.get("Model", ""),
            "cpu_count": proc.get("Count"),
            "total_memory_gib": mem.get("TotalSystemMemoryGiB"),
            "status": _parse_status(s.get("Status")),
        }

    async def _fetch_manager(self, paths: dict[str, str]) -> dict[str, Any]:
        m = await self._request_json(paths["manager"], "Manager")
        shell = m.get("CommandShell") or {}
        return {
            "id": m.get("Id", ""),
            "name": m.get("Name", ""),
            "model": m.get("Model", ""),
            "firmware_version": m.get("FirmwareVersion", ""),
            "manager_type": m.get("ManagerType", ""),
            "status": _parse_status(m.get("Status")),
            "command_shell_enabled": shell.get("ServiceEnabled", False),
        }

    async def _fetch_thermal(self, paths: dict[str, str]) -> dict[str, Any]:
        chassis = await self._request_json(paths["chassis"], "Chassis")
        thermal_path = _odata_id(chassis.get("Thermal"))
        if not thermal_path:
            return {"fans": [], "temperatures": []}
        thermal = await self._request_json(thermal_path, "Thermal")
        fans = [
            _parse_fan(f) for f in thermal.get("Fans", []) if isinstance(f, dict)
        ]
        temps = [
            _parse_temperature(t)
            for t in thermal.get("Temperatures", [])
            if isinstance(t, dict)
        ]
        return {"fans": fans, "temperatures": temps}

    async def _fetch_power(self, paths: dict[str, str]) -> dict[str, Any]:
        chassis = await self._request_json(paths["chassis"], "Chassis")
        power_path = _odata_id(chassis.get("Power"))
        if not power_path:
            return {"power_control": None, "power_supplies": []}
        power = await self._request_json(power_path, "Power")
        pc_list = power.get("PowerControl", [])
        power_ctrl = _parse_power_control(pc_list[0]) if pc_list else None
        psus = [
            _parse_power_supply(p)
            for p in power.get("PowerSupplies", [])
            if isinstance(p, dict)
        ]
        return {"power_control": power_ctrl, "power_supplies": psus}


# ------------------------------------------------------------------
# Parsing helpers
# ------------------------------------------------------------------


def _odata_id(obj: Any) -> str | None:
    if isinstance(obj, dict):
        return obj.get("@odata.id")
    return None


def _parse_status(obj: Any) -> dict[str, str | None] | None:
    if not isinstance(obj, dict):
        return None
    return {
        "health": obj.get("Health"),
        "health_rollup": obj.get("HealthRollup"),
        "state": obj.get("State"),
    }


def _parse_fan(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r.get("MemberId") or r.get("@odata.id", ""),
        "name": r.get("FanName") or r.get("Name", ""),
        "reading_rpm": r.get("Reading"),
        "physical_context": r.get("PhysicalContext", ""),
        "status": _parse_status(r.get("Status")),
        "lower_threshold_non_critical": r.get("LowerThresholdNonCritical"),
    }


def _parse_temperature(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r.get("MemberId") or r.get("@odata.id", ""),
        "name": r.get("Name", ""),
        "reading_celsius": r.get("ReadingCelsius"),
        "physical_context": r.get("PhysicalContext", ""),
        "status": _parse_status(r.get("Status")),
        "upper_threshold_critical": r.get("UpperThresholdCritical"),
        "upper_threshold_non_critical": r.get("UpperThresholdNonCritical"),
    }


def _parse_power_control(obj: dict[str, Any]) -> dict[str, Any] | None:
    if not obj:
        return None
    metrics = obj.get("PowerMetrics") or {}
    return {
        "power_consumed_watts": obj.get("PowerConsumedWatts"),
        "power_capacity_watts": obj.get("PowerCapacityWatts"),
        "average_consumed_watts": metrics.get("AverageConsumedWatts"),
        "max_consumed_watts": metrics.get("MaxConsumedWatts"),
        "min_consumed_watts": metrics.get("MinConsumedWatts"),
    }


def _parse_power_supply(obj: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": obj.get("MemberId") or obj.get("@odata.id", ""),
        "name": obj.get("Name", ""),
        "model": obj.get("Model", ""),
        "firmware_version": obj.get("FirmwareVersion", ""),
        "line_input_voltage": obj.get("LineInputVoltage"),
        "power_supply_type": obj.get("PowerSupplyType", ""),
        "serial_number": obj.get("SerialNumber", ""),
        "status": _parse_status(obj.get("Status")),
    }
