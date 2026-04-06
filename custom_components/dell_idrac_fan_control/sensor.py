"""Sensor platform for Dell iDRAC Fan Control."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_PORT, DOMAIN
from .coordinator import TelemetryCoordinator


# ---------------------------------------------------------------------------
# Entity descriptions for "static" sensors (always one per device)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class DellIdracSensorDescription(SensorEntityDescription):
    """Extended description with a value extractor."""

    value_fn: Callable[[dict[str, Any]], Any] = lambda d: None


def _pc(d: dict[str, Any]) -> dict[str, Any]:
    return (d.get("power") or {}).get("power_control") or {}


STATIC_SENSORS: tuple[DellIdracSensorDescription, ...] = (
    DellIdracSensorDescription(
        key="model",
        translation_key="model",
        icon="mdi:server",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.get("system") or {}).get("model"),
    ),
    DellIdracSensorDescription(
        key="power_state",
        translation_key="power_state",
        icon="mdi:power",
        value_fn=lambda d: (d.get("system") or {}).get("power_state"),
    ),
    DellIdracSensorDescription(
        key="bios_version",
        translation_key="bios_version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.get("system") or {}).get("bios_version"),
    ),
    DellIdracSensorDescription(
        key="service_tag",
        translation_key="service_tag",
        icon="mdi:tag",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.get("system") or {}).get("service_tag"),
    ),
    DellIdracSensorDescription(
        key="cpu_model",
        translation_key="cpu_model",
        icon="mdi:cpu-64-bit",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.get("system") or {}).get("cpu_model"),
    ),
    DellIdracSensorDescription(
        key="total_memory_gib",
        translation_key="total_memory_gib",
        icon="mdi:memory",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement="GiB",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: (d.get("system") or {}).get("total_memory_gib"),
    ),
    DellIdracSensorDescription(
        key="idrac_firmware",
        translation_key="idrac_firmware",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.get("manager") or {}).get("firmware_version"),
    ),
    DellIdracSensorDescription(
        key="power_consumed",
        translation_key="power_consumed",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda d: _pc(d).get("power_consumed_watts"),
    ),
    DellIdracSensorDescription(
        key="power_average",
        translation_key="power_average",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _pc(d).get("average_consumed_watts"),
    ),
    DellIdracSensorDescription(
        key="power_peak",
        translation_key="power_peak",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _pc(d).get("max_consumed_watts"),
    ),
)


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dell iDRAC sensor entities."""
    coordinator: TelemetryCoordinator = hass.data[DOMAIN][entry.entry_id]["telemetry"]
    entities: list[SensorEntity] = []

    for desc in STATIC_SENSORS:
        entities.append(DellIdracStaticSensor(coordinator, entry, desc))

    data = coordinator.data or {}
    thermal = data.get("thermal") or {}
    for fan in thermal.get("fans", []):
        entities.append(DellIdracFanSensor(coordinator, entry, fan))
    for temp in thermal.get("temperatures", []):
        entities.append(DellIdracTempSensor(coordinator, entry, temp))

    power = data.get("power") or {}
    for psu in power.get("power_supplies", []):
        entities.append(DellIdracPsuSensor(coordinator, entry, psu))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Helper — shared device info
# ---------------------------------------------------------------------------


def _device_info(entry: ConfigEntry, data: dict[str, Any] | None = None):
    from homeassistant.helpers.device_registry import DeviceInfo

    cfg = entry.data
    sys = (data.get("system") or {}) if data else {}
    mgr = (data.get("manager") or {}) if data else {}
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Dell iDRAC ({cfg[CONF_HOST]})",
        manufacturer="Dell",
        model=sys.get("model"),
        sw_version=mgr.get("firmware_version"),
        serial_number=sys.get("service_tag") or sys.get("serial_number"),
        configuration_url=f"https://{cfg[CONF_HOST]}:{cfg.get(CONF_PORT, DEFAULT_PORT)}",
    )


# ---------------------------------------------------------------------------
# Static sensor entity
# ---------------------------------------------------------------------------


class DellIdracStaticSensor(
    CoordinatorEntity[TelemetryCoordinator], SensorEntity
):
    """Sensor whose value comes from a fixed path inside coordinator data."""

    _attr_has_entity_name = True
    entity_description: DellIdracSensorDescription

    def __init__(
        self,
        coordinator: TelemetryCoordinator,
        entry: ConfigEntry,
        description: DellIdracSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._entry = entry

    @property
    def device_info(self):  # noqa: D102
        return _device_info(self._entry, self.coordinator.data)

    @property
    def native_value(self):  # noqa: D102
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)


# ---------------------------------------------------------------------------
# Dynamic sensor entities (fans / temperatures / PSUs)
# ---------------------------------------------------------------------------


class DellIdracFanSensor(
    CoordinatorEntity[TelemetryCoordinator], SensorEntity
):
    """RPM sensor for one fan."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:fan"
    _attr_native_unit_of_measurement = "RPM"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: TelemetryCoordinator,
        entry: ConfigEntry,
        fan: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._fan_id = fan["id"]
        self._attr_name = fan.get("name") or f"Fan {self._fan_id}"
        self._attr_unique_id = f"{entry.entry_id}_fan_{self._fan_id}"
        self._entry = entry

    @property
    def device_info(self):  # noqa: D102
        return _device_info(self._entry, self.coordinator.data)

    @property
    def native_value(self):  # noqa: D102
        for f in (self.coordinator.data or {}).get("thermal", {}).get("fans", []):
            if f["id"] == self._fan_id:
                return f.get("reading_rpm")
        return None


class DellIdracTempSensor(
    CoordinatorEntity[TelemetryCoordinator], SensorEntity
):
    """Temperature sensor for one reading."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: TelemetryCoordinator,
        entry: ConfigEntry,
        temp: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._temp_id = temp["id"]
        self._attr_name = temp.get("name") or f"Temp {self._temp_id}"
        self._attr_unique_id = f"{entry.entry_id}_temp_{self._temp_id}"
        self._entry = entry

    @property
    def device_info(self):  # noqa: D102
        return _device_info(self._entry, self.coordinator.data)

    @property
    def native_value(self):  # noqa: D102
        for t in (
            (self.coordinator.data or {}).get("thermal", {}).get("temperatures", [])
        ):
            if t["id"] == self._temp_id:
                return t.get("reading_celsius")
        return None


class DellIdracPsuSensor(
    CoordinatorEntity[TelemetryCoordinator], SensorEntity
):
    """Input-voltage sensor for one power supply."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT

    def __init__(
        self,
        coordinator: TelemetryCoordinator,
        entry: ConfigEntry,
        psu: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._psu_id = psu["id"]
        self._attr_name = psu.get("name") or f"PSU {self._psu_id}"
        self._attr_unique_id = f"{entry.entry_id}_psu_{self._psu_id}"
        self._entry = entry

    @property
    def device_info(self):  # noqa: D102
        return _device_info(self._entry, self.coordinator.data)

    @property
    def native_value(self):  # noqa: D102
        for p in (self.coordinator.data or {}).get("power", {}).get(
            "power_supplies", []
        ):
            if p["id"] == self._psu_id:
                return p.get("line_input_voltage")
        return None
