"""Constants for Dell iDRAC Fan Control integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "dell_idrac_fan_control"

CONF_BASE_PATH = "base_path"
CONF_ALLOW_INSECURE_TLS = "allow_insecure_tls"
CONF_IPMI_PORT = "ipmi_port"
CONF_IPMI_TIMEOUT = "ipmi_timeout"

DEFAULT_PORT = 443
DEFAULT_USERNAME = "root"
DEFAULT_BASE_PATH = "/redfish/v1"
DEFAULT_TIMEOUT = 8
DEFAULT_ALLOW_INSECURE_TLS = True
DEFAULT_IPMI_PORT = 623
DEFAULT_IPMI_TIMEOUT = 5
DEFAULT_SCAN_INTERVAL = 30

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.NUMBER, Platform.SELECT]
