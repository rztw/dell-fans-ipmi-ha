# Dell iDRAC Fan Control for Home Assistant

A Home Assistant custom integration that lets you **monitor** and **control** Dell server fans through iDRAC, using Redfish for telemetry and pure-Python IPMI RMCP+ for fan commands — no `ipmitool` binary required.

## Features

| Category | Details |
|----------|---------|
| **Telemetry (Redfish)** | System model, power state, BIOS version, service tag, CPU, memory, temperatures, fan RPM, power consumption, PSU voltages |
| **Fan Control (IPMI)** | Switch between automatic and manual fan mode; set a fixed fan speed percentage (0–100 %) |
| **Platforms** | `sensor`, `number` (fan speed slider), `select` (auto / manual mode) |
| **Config Flow** | UI-based setup with connection validation |
| **Options Flow** | Adjust scan interval, Redfish base path, TLS settings, timeouts after initial setup |
| **Localisation** | English, Traditional Chinese (zh-Hant) |

## Requirements

- Dell server with **iDRAC** (tested on iDRAC 7/8/9)
- Redfish API enabled (HTTPS, default port 443)
- IPMI over LAN enabled (UDP, default port 623)
- Home Assistant **2024.1** or later

## Installation

### HACS (recommended)

1. Open **HACS** in Home Assistant.
2. Click **Integrations** → **⋮** (top-right) → **Custom repositories**.
3. Paste the repository URL and select category **Integration**.
4. Search for **Dell iDRAC Fan Control** and install.
5. Restart Home Assistant.

### Manual

1. Copy the `custom_components/dell_idrac_fan_control` folder into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.

## Configuration

1. Go to **Settings** → **Devices & Services** → **Add Integration**.
2. Search for **Dell iDRAC Fan Control**.
3. Enter your iDRAC host/IP, credentials, and (optionally) the IPMI port.
4. The integration validates the connection via Redfish before creating the entry.

### Options

After setup, click **Configure** on the integration card to adjust:

| Option | Default | Description |
|--------|---------|-------------|
| Scan interval | 30 s | How often to poll Redfish and probe IPMI |
| Redfish base path | `/redfish/v1` | Override if your iDRAC uses a non-standard path |
| Allow insecure TLS | Yes | Skip certificate verification (common for self-signed iDRAC certs) |
| Redfish timeout | 8 s | HTTP request timeout |
| IPMI timeout | 5 s | UDP session timeout |

## Entities

### Sensors (Redfish telemetry)

- **Model** / **Service Tag** / **BIOS Version** / **CPU Model** / **Total Memory** — diagnostic
- **Power State** — On / Off / …
- **iDRAC Firmware** — diagnostic
- **Power Consumption** / **Average Power** / **Peak Power** — watts
- **\<Fan Name\>** — RPM per fan (dynamic, one entity per fan)
- **\<Temperature Name\>** — °C per sensor (dynamic, one entity per temperature reading)
- **\<PSU Name\>** — input voltage per power supply (dynamic)

### Controls (IPMI)

- **Fan Mode** (`select`) — switch between `auto` and `manual`
- **Fan Speed** (`number`) — slider 0–100 %; setting a value automatically switches to manual mode

## How It Works

```
Home Assistant
  ├── TelemetryCoordinator ──► Redfish HTTPS ──► iDRAC
  └── FanControlCoordinator ─► IPMI RMCP+ UDP ──► iDRAC
```

- **Redfish** reads system, manager, thermal, and power data via HTTPS Basic Auth.
- **IPMI** uses a pure-Python RMCP+ implementation (AES-128-CBC + HMAC-SHA256/SHA1) to send Dell OEM raw commands for fan control — no external binaries needed.

## License

[MIT](LICENSE)
