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

---

## 正體中文版本

# Dell iDRAC Fan Control（Home Assistant）

這是一個 Home Assistant 自訂整合，透過 iDRAC 讓你可以**監控**與**控制** Dell 伺服器風扇。遙測資料使用 Redfish，風扇控制使用純 Python 的 IPMI RMCP+，不需要 `ipmitool` 可執行檔。

## 功能

| 類別 | 說明 |
|------|------|
| **遙測（Redfish）** | 系統型號、電源狀態、BIOS 版本、Service Tag、CPU、記憶體、溫度、風扇 RPM、功耗、PSU 電壓 |
| **風扇控制（IPMI）** | 切換自動/手動模式；設定固定風扇轉速百分比（0–100%） |
| **平台** | `sensor`、`number`（風扇轉速滑桿）、`select`（自動/手動模式） |
| **設定流程** | 支援 UI 設定，建立時會先驗證連線 |
| **選項流程** | 可在安裝後調整輪詢間隔、Redfish 路徑、TLS 設定與逾時 |
| **在地化** | 英文、正體中文（zh-Hant） |

## 需求

- 具備 **iDRAC** 的 Dell 伺服器（已在 iDRAC 7/8/9 驗證）
- 已啟用 Redfish API（HTTPS，預設連接埠 443）
- 已啟用 IPMI over LAN（UDP，預設連接埠 623）
- Home Assistant **2024.1** 或更新版本

## 安裝

### 透過 HACS（建議）

1. 在 Home Assistant 開啟 **HACS**。
2. 進入 **Integrations** → 右上角 **⋮** → **Custom repositories**。
3. 貼上此專案的 repository URL，類型選擇 **Integration**。
4. 搜尋 **Dell iDRAC Fan Control** 並安裝。
5. 重新啟動 Home Assistant。

### 手動安裝

1. 將 `custom_components/dell_idrac_fan_control` 複製到 Home Assistant 的 `config/custom_components/`。
2. 重新啟動 Home Assistant。

## 設定

1. 前往 **設定** → **裝置與服務** → **新增整合**。
2. 搜尋 **Dell iDRAC Fan Control**。
3. 輸入 iDRAC 主機/IP、帳號密碼（可選填 IPMI 連接埠）。
4. 整合會先以 Redfish 驗證連線，通過後才建立設定。

### 可調整選項

完成設定後，可在整合卡片按 **Configure** 調整：

| 選項 | 預設值 | 說明 |
|------|--------|------|
| 輪詢間隔 | 30 秒 | Redfish 輪詢與 IPMI 探測頻率 |
| Redfish base path | `/redfish/v1` | 若 iDRAC 使用非標準路徑可覆寫 |
| 允許不安全 TLS | 是 | 跳過憑證驗證（常見於自簽章憑證） |
| Redfish 逾時 | 8 秒 | HTTP 請求逾時 |
| IPMI 逾時 | 5 秒 | UDP Session 逾時 |

## 實體（Entities）

### 感測器（Redfish 遙測）

- **Model** / **Service Tag** / **BIOS Version** / **CPU Model** / **Total Memory**（診斷）
- **Power State**（電源狀態）
- **iDRAC Firmware**（診斷）
- **Power Consumption** / **Average Power** / **Peak Power**（瓦特）
- **\<Fan Name\>**：每個風扇一個 RPM 感測器（動態建立）
- **\<Temperature Name\>**：每個溫度點一個 °C 感測器（動態建立）
- **\<PSU Name\>**：每個電源供應器一個輸入電壓感測器（動態建立）

### 控制（IPMI）

- **Fan Mode**（`select`）：切換 `auto` / `manual`
- **Fan Speed**（`number`）：0–100% 滑桿；設定值時會自動切到手動模式

## 運作方式

```
Home Assistant
  ├── TelemetryCoordinator ──► Redfish HTTPS ──► iDRAC
  └── FanControlCoordinator ─► IPMI RMCP+ UDP ──► iDRAC
```

- **Redfish**：透過 HTTPS Basic Auth 讀取 system、manager、thermal、power 資料。
- **IPMI**：透過純 Python RMCP+（AES-128-CBC + HMAC-SHA256/SHA1）送出 Dell OEM 原生命令進行風扇控制，不需外部二進位工具。
