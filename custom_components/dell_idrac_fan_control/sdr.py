"""Pure-Python parsers for IPMI SDR records and FRU inventory areas.

Only the record types needed for telemetry are decoded: full sensor records
(analog fans / temperatures / power) and FRU device locator records (used to
name power supplies). Everything here is side-effect free so it can be unit
tested without a BMC.
"""
from __future__ import annotations

import math
from typing import Any

# ---------------------------------------------------------------------------
# Record types
# ---------------------------------------------------------------------------

SDR_TYPE_FULL = 0x01
SDR_TYPE_COMPACT = 0x02
SDR_TYPE_EVENT_ONLY = 0x03
SDR_TYPE_ENTITY_ASSOC = 0x08
SDR_TYPE_FRU_LOCATOR = 0x11
SDR_TYPE_MC_LOCATOR = 0x12
SDR_TYPE_OEM = 0xC0

# ---------------------------------------------------------------------------
# Sensor types (IPMI 2.0 Table 42-3)
# ---------------------------------------------------------------------------

SENSOR_TYPE_TEMPERATURE = 0x01
SENSOR_TYPE_VOLTAGE = 0x02
SENSOR_TYPE_CURRENT = 0x03
SENSOR_TYPE_FAN = 0x04
SENSOR_TYPE_POWER_SUPPLY = 0x08
SENSOR_TYPE_POWER_UNIT = 0x09
SENSOR_TYPE_OTHER = 0x0B

# Entity IDs (IPMI 2.0 Table 43-13)
ENTITY_SYSTEM_BOARD = 0x07
ENTITY_POWER_SUPPLY = 0x0A

# ---------------------------------------------------------------------------
# Base units (IPMI 2.0 Table 43-15)
# ---------------------------------------------------------------------------

UNIT_UNSPECIFIED = 0
UNIT_CELSIUS = 1
UNIT_FAHRENHEIT = 2
UNIT_KELVIN = 3
UNIT_VOLTS = 4
UNIT_AMPS = 5
UNIT_WATTS = 6
UNIT_JOULES = 7
UNIT_RPM = 18
UNIT_HZ = 19
UNIT_PERCENT = 67

# Analog data format (unit flags bits 7:6)
ANALOG_UNSIGNED = 0
ANALOG_ONES_COMPLEMENT = 1
ANALOG_TWOS_COMPLEMENT = 2
ANALOG_NONE = 3

# Linearization (byte 23)
LIN_LINEAR = 0x00
LIN_LN = 0x01
LIN_LOG10 = 0x02
LIN_LOG2 = 0x03
LIN_E = 0x04
LIN_EXP10 = 0x05
LIN_EXP2 = 0x06
LIN_INVERSE = 0x07
LIN_SQUARE = 0x08
LIN_CUBE = 0x09
LIN_SQRT = 0x0A
LIN_CUBERT = 0x0B


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _signed(value: int, bits: int) -> int:
    """Sign-extend ``value`` from ``bits`` bits."""
    sign = 1 << (bits - 1)
    return value - (1 << bits) if value & sign else value


def _decode_id_string(raw: bytes, offset: int) -> str:
    """Decode an SDR/FRU type-length string field."""
    if offset >= len(raw):
        return ""
    code = raw[offset]
    length = code & 0x3F
    kind = (code >> 6) & 0x03
    data = raw[offset + 1 : offset + 1 + length]
    if not data:
        return ""
    # FRU areas pad short strings with NULs; drop them before decoding.
    data = data.rstrip(b"\x00")
    if kind == 0:
        # Unicode: UTF-16LE, but some BMCs emit plain ASCII.
        try:
            return data.decode("utf-16-le").strip("\x00 ").strip()
        except UnicodeDecodeError:
            return data.decode("latin-1", "replace").strip()
    if kind == 1:
        # BCD plus: one nibble per character.
        table = "0123456789 -.:,_"
        out = []
        for byte in data:
            for nibble in (byte & 0x0F, byte >> 4):
                out.append(table[nibble] if nibble < len(table) else "?")
        return "".join(out).strip()
    if kind == 2:
        # 6-bit ASCII, packed little-endian, 4 chars per 3 bytes.
        out = []
        for i in range(0, len(data) - 2, 3):
            b0, b1, b2 = data[i], data[i + 1], data[i + 2]
            out.append(chr((b0 & 0x3F) + 0x20))
            out.append(chr((((b0 >> 6) & 0x03) | ((b1 & 0x0F) << 2)) + 0x20))
            out.append(chr((((b1 >> 4) & 0x0F) | ((b2 & 0x03) << 4)) + 0x20))
            out.append(chr(((b2 >> 2) & 0x3F) + 0x20))
        return "".join(out).strip()
    return data.decode("latin-1", "replace").strip()


# ---------------------------------------------------------------------------
# Full sensor record
# ---------------------------------------------------------------------------


def parse_full_sensor(raw: bytes) -> dict[str, Any] | None:
    """Parse a full sensor record (type 0x01)."""
    if len(raw) < 48:
        return None

    unit_flags = raw[20]
    mtol = raw[24:26]
    bacc = raw[26:30]

    m = _signed(mtol[0] | ((mtol[1] & 0xC0) << 2), 10)
    tolerance = mtol[1] & 0x3F

    b = _signed(bacc[0] | ((bacc[1] & 0xC0) << 2), 10)
    b_exp = _signed(bacc[3] & 0x0F, 4)
    r_exp = _signed((bacc[3] & 0xF0) >> 4, 4)

    return {
        "record_id": raw[0] | (raw[1] << 8),
        "record_type": SDR_TYPE_FULL,
        "sensor_number": raw[7],
        "entity_id": raw[8],
        "entity_instance": raw[9] & 0x7F,
        "sensor_type": raw[12],
        "event_type": raw[13],
        "name": _decode_id_string(raw, 47),
        "unit_base": raw[21],
        "unit_modifier": raw[22],
        "unit_rate": (unit_flags >> 3) & 0x07,
        "unit_percent": bool(unit_flags & 0x01),
        "analog_format": (unit_flags >> 6) & 0x03,
        "linearization": raw[23] & 0x7F,
        "m": m,
        "b": b,
        "b_exp": b_exp,
        "r_exp": r_exp,
        "tolerance": tolerance,
        "nominal": raw[31],
        "normal_max": raw[32],
        "normal_min": raw[33],
        "sensor_max": raw[34],
        "sensor_min": raw[35],
        "upper_non_recoverable": raw[36],
        "upper_critical": raw[37],
        "upper_non_critical": raw[38],
        "lower_non_recoverable": raw[39],
        "lower_critical": raw[40],
        "lower_non_critical": raw[41],
        "positive_hysteresis": raw[42],
        "negative_hysteresis": raw[43],
    }


# ---------------------------------------------------------------------------
# FRU device locator record
# ---------------------------------------------------------------------------


def parse_fru_locator(raw: bytes) -> dict[str, Any] | None:
    """Parse a FRU device locator record (type 0x11)."""
    if len(raw) < 16:
        return None
    return {
        "record_id": raw[0] | (raw[1] << 8),
        "record_type": SDR_TYPE_FRU_LOCATOR,
        "entity_id": raw[12],
        "entity_instance": raw[13] & 0x7F,
        "name": _decode_id_string(raw, 15),
    }


def parse_sdr_record(raw: bytes) -> dict[str, Any] | None:
    """Dispatch on the SDR record type."""
    if len(raw) < 5:
        return None
    rtype = raw[3]
    if rtype == SDR_TYPE_FULL:
        return parse_full_sensor(raw)
    if rtype == SDR_TYPE_FRU_LOCATOR:
        return parse_fru_locator(raw)
    return None


# ---------------------------------------------------------------------------
# Reading conversion
# ---------------------------------------------------------------------------


def convert_reading(record: dict[str, Any], raw: bytes) -> float | None:
    """Convert a raw Get Sensor Reading payload to engineering units."""
    if not raw:
        return None
    return convert_value(record, raw[0])


def convert_value(record: dict[str, Any], value: int) -> float | None:
    """Convert a single raw sensor value to engineering units."""
    if record.get("analog_format") == ANALOG_NONE:
        return None

    fmt = record.get("analog_format", ANALOG_UNSIGNED)
    if fmt == ANALOG_ONES_COMPLEMENT:
        if value & 0x80:
            value += 1
        value = _signed(value & 0xFF, 8)
    elif fmt == ANALOG_TWOS_COMPLEMENT:
        value = _signed(value, 8)

    m = record.get("m", 1)
    b = record.get("b", 0)
    b_exp = record.get("b_exp", 0)
    r_exp = record.get("r_exp", 0)

    try:
        result = (m * value + b * (10.0**b_exp)) * (10.0**r_exp)
    except OverflowError:
        return None

    lin = record.get("linearization", LIN_LINEAR)
    try:
        if lin == LIN_LN:
            result = math.log(result) if result > 0 else None
        elif lin == LIN_LOG10:
            result = math.log10(result) if result > 0 else None
        elif lin == LIN_LOG2:
            result = math.log2(result) if result > 0 else None
        elif lin == LIN_E:
            result = math.exp(result)
        elif lin == LIN_EXP10:
            result = 10.0**result
        elif lin == LIN_EXP2:
            result = 2.0**result
        elif lin == LIN_INVERSE:
            result = 1.0 / result if result else None
        elif lin == LIN_SQUARE:
            result = result * result
        elif lin == LIN_CUBE:
            result = result * result * result
        elif lin == LIN_SQRT:
            result = math.sqrt(result) if result >= 0 else None
        elif lin == LIN_CUBERT:
            result = math.copysign(abs(result) ** (1.0 / 3.0), result)
    except (ValueError, OverflowError, ZeroDivisionError):
        return None

    if result is None:
        return None
    return round(result, 3)


def reading_available(raw: bytes) -> bool:
    """True when the sensor reports a usable reading.

    Byte 1 of the Get Sensor Reading response carries the state flags:
    bit 5 = reading unavailable, bit 6 = scanning enabled.
    """
    if len(raw) < 2:
        return False
    state = raw[1]
    if state & 0x20:
        return False
    if not (state & 0x40):
        return False
    return True


# ---------------------------------------------------------------------------
# FRU inventory area
# ---------------------------------------------------------------------------

# FRU fields are positional, not tagged: the Nth field in an area has a fixed
# meaning. The type/length byte only carries the encoding and the length.
_FRU_BOARD_FIELDS = (
    "board_manufacturer",
    "board_product",
    "board_serial",
    "board_part_number",
    "board_fru_file_id",
)

_FRU_PRODUCT_FIELDS = (
    "product_manufacturer",
    "product_name",
    "product_part_number",
    "product_version",
    "product_serial",
    "product_asset_tag",
    "product_fru_file_id",
)


def _parse_fru_area(
    raw: bytes, offset: int, fields: tuple[str, ...], has_mfg_date: bool
) -> dict[str, str]:
    out: dict[str, str] = {}
    if offset <= 0 or offset + 2 >= len(raw):
        return out
    area_len = raw[offset + 1] * 8
    end = min(offset + area_len, len(raw)) if area_len else len(raw)
    # Board areas carry a 3-byte manufacturing date before the fields.
    pos = offset + (6 if has_mfg_date else 3)
    index = 0
    while pos < end:
        code = raw[pos]
        length = code & 0x3F
        if length == 0:
            break
        # 0xC1 0x00 marks the end of the field list.
        if code == 0xC1 and pos + 1 < len(raw) and raw[pos + 1] == 0:
            break
        if index < len(fields):
            out[fields[index]] = _decode_id_string(raw, pos)
        index += 1
        pos += 1 + length
    return out


def parse_fru(raw: bytes) -> dict[str, str]:
    """Parse a FRU inventory area into a flat dict of strings."""
    if len(raw) < 8 or raw[0] != 0x01:
        return {}
    result: dict[str, str] = {}
    result.update(_parse_fru_area(raw, raw[3] * 8, _FRU_BOARD_FIELDS, True))
    result.update(_parse_fru_area(raw, raw[4] * 8, _FRU_PRODUCT_FIELDS, False))
    return {k: v for k, v in result.items() if v}
