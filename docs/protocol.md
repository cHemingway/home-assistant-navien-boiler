# Navien Smart Plus (UK) cloud protocol

How the Navien Smart Plus Android app (`com.navien.thermostat.gb` v2.00.10, build 20017) talks to Navien's UK
cloud, and through it to the boiler. Worked out from the decompiled app and checked against a live system; see the
[test log](#test-log) at the end for what has been confirmed.

Hardware: BCM-41RW / BCM-45RW wireless communication module, NRC-10R thermostats (868 MHz RF), VCU-10R wiring
centre, Navien LCB/NCB boiler on the 2-wire KIW bus.

Conventions:
- *(verify)* marks something inferred from the app's code or naming but not yet seen on a real system.
- In topics and payloads: `T` = `deviceType`, `M` = MAC part of `deviceId` (first 12 hex chars), `A` = the rest of
  `deviceId` (4 hex chars), `F` = `familySeq`, `U` = `userSeq`, `C` = MQTT client ID.

## Contents

1. [Overview](#overview)
2. [REST API](#rest-api)
3. [MQTT](#mqtt)
4. [Status](#status)
5. [Error codes](#error-codes)
6. [Device info](#device-info)
7. [Control](#control)
8. [Heating schedule](#heating-schedule)
9. [Auto target temperature](#auto-target-temperature)
10. [Fuel usage history](#fuel-usage-history)
11. [Local interfaces](#local-interfaces)
12. [Things deliberately not used](#things-deliberately-not-used)
13. [Background and related work](#background-and-related-work)
14. [Test log](#test-log)

## Overview

```
 App / HA ──HTTPS──▶ spuk.naviensmartcontrol.com      (login, device list, names, reports)
     │                       │ returns temporary AWS credentials
     └──MQTT over WSS (SigV4)──▶ spuk-iot.naviensmartcontrol.com  (AWS IoT Core, eu-west-2)
                                      ▲
 BCM-41RW module ─────────────────────┘ (persistent connection; the module is the "nsp-{mac}" thing)
```

REST is only used for the account, the device list and some metadata. All status, control, schedules and usage
history go over MQTT, as request/reply messages that the module answers.

## REST API

Base URL: `https://spuk.naviensmartcontrol.com` (dev: `spuk-dev.`, staging: `spuk-stg.`; don't use these).

Headers:
- `Content-Type: application/json;charset=UTF-8`
- `User-Agent: SmartPlus UK/2.00.10_20017 (<manufacturer>, <brand>, <model>, Android, <release>, <sdk>)`
- `Authorization: <accessToken>`: the raw token, **no** `Bearer ` prefix (authenticated calls only)

The app's HTTP client has no certificate pinning.

### Response envelope

```json
{"code": 200, "msg": "...", "data": { ... }}
```
Success is `code == 200`. On errors the HTTP status may be non-2xx, with the same envelope in the body.

Sign-in failure codes handled by the app:
- `400`, `502`, `503`, `512`: bad credentials or a generic error
- `508`: too many password attempts
- `403` with `data.serverStatus.statusCode` 1 or 2: server maintenance (`text` explains)

### Sign in

`POST /api/v1.0/users/sign-in`
```json
{"email": "user@example.com", "password": "plaintext"}
```
Response `data`:
- `userSeq` (int), `familySeq` (int), `userType` (str), plus profile fields
- `authInfo`:
  - `accessToken`, `refreshToken`, `idToken`
  - `authenticationExpiresIn`, `authorizationExpiresIn` (seconds)
  - `accessKeyId`, `secretKey`, `sessionToken`: **temporary AWS credentials for MQTT**

### Refresh the access token

`POST /api/v1.0/users/token/refresh` (no auth header)
```json
{"refreshToken": "<refreshToken>"}
```
Response `data`: `accessToken`, `authenticationExpiresIn`.

### Refresh the AWS (MQTT) credentials

`POST /api/v1.0/users/token/session/refresh` with `Authorization: <accessToken>`
```json
{"email": "user@example.com"}
```
Response `data`: a full `authInfo`-shaped object (`accessKeyId`, `secretKey`, `sessionToken`, …). The app calls
this when MQTT drops, then reconnects.

### Devices

`GET /api/v1.0/devices?userSeq={userSeq}&familySeq={familySeq}`
```json
{"userSeq": 1001, "familySeq": 2002, "devices": [
  {"deviceSeq": 3003, "deviceId": "001122334455abcd", "deviceType": 3, "modelCode": 0,
   "isConnected": 1, "thermostats": [{"zoneId": 1, "name": "Home"}]}
]}
```
- `deviceId` is the module's Wi-Fi MAC (12 hex chars, no separators) followed by 4 more hex chars. Both halves are
  used in MQTT (`macAddress` and `additionalValue`).
- `deviceType` is `3` for a Smart Plus module.

### Other endpoints

Not needed for Home Assistant:
- `GET /api/v1.0/devices/{deviceSeq}/hems/reports?offset&limit&year`: energy reports
- `PUT /api/v1.0/devices/{deviceSeq}/thermostats/{zoneId}/name`: rename a zone
- `GET /api/v1.0/softwares?swCode&swVersion`: firmware info (`description, swCode, swVersion, otaMode, …`). There's
  no download URL: OTA is triggered over MQTT and the module fetches the update itself.
- `POST /api/v1.0/users/{userSeq}/sign-out`

## MQTT

### Connection

- Endpoint `spuk-iot.naviensmartcontrol.com`, a custom domain for `acfrrl5cy64th-ats.iot.eu-west-2.amazonaws.com`
  (AWS IoT Core, region `eu-west-2`).
- **MQTT 3.1.1 over WebSocket** (`wss://…:443/mqtt`), authenticated with a **SigV4-presigned URL** (service
  `iotdevicegateway`) using the AWS credentials from sign-in. As usual for AWS IoT, `X-Amz-Security-Token` is
  appended after signing. The app does this with the AWS IoT Android SDK (`AWSIotMqttManager` +
  `BasicSessionCredentials`).
- Client ID: a random UUID per app install.
- QoS 1 for publish and subscribe.

### Topics

Requests go to the module's topic; replies come back on a per-client topic named in the request.

| Purpose | Publish to | Reply on |
|---|---|---|
| Device info | `cmd/T/nsp-M/st/start` | `cmd/T/F/U/C/res/did` (and a status on `…/res`) |
| Status | `cmd/T/nsp-M/st` | `cmd/T/F/U/C/res` |
| Control | `cmd/T/nsp-M/ctrl` | `cmd/T/F/U/C/res` |
| Heating schedule, read / write | `cmd/T/nsp-M/st/heat-reserv-query/rd` / `…/ctrl/heat-reserv/rd` | `cmd/T/F/U/C/res/heat-reserv/rd` |
| Hot water schedule, read / write | `cmd/T/nsp-M/st/dhw-reserv-query/rd` / `…/ctrl/dhw-reserv/rd` | `cmd/T/F/U/C/res/dhw-reserv/rd` |
| Daily fuel usage | `cmd/T/nsp-M/st/fuel-usage-daily-query/rd` | `cmd/T/F/U/C/res/fuel-usage-daily-query/rd` |
| Monthly fuel usage | `cmd/T/nsp-M/st/fuel-usage-monthly-query/rd` | `cmd/T/F/U/C/res/fuel-usage-monthly-query/rd` |

Subscriptions:
- The app subscribes to family-wide wildcards such as `cmd/T/F/+/+/res`, so it also receives replies to other
  family members' apps. That's how changes made elsewhere show up. Each reply topic needs its own wildcard
  subscription (`…/res`, `…/res/did`, `…/res/heat-reserv/rd`, `…/res/fuel-usage-daily-query/rd`, …).
- Device-addressed topics: `cmd/T/nsp-M/res`, `cmd/T/nsp-M/res/did`, `cmd/T/nsp-M/res/ctrl-fail`.
- Events: `evt/T/nsp-M/st/connection`, `evt/T/nsp-M/smart-diagnostic…`, `evt/T/mobile/app-connection`,
  `evt/T/mobile/disconnect-mqtt`. The connection event carries `event.connection.status`: `1` = the module went
  offline (the app clears its data and logs "offline"), `2` = it's back online (the app re-reads device info with
  `st/start`). It also carries `versionNumber` and `additionalValue`. That makes it the module's online signal *(not yet
  seen live)*.

### Request envelope

```json
{
  "protocolVersion": 1,
  "clientID": "<C>",
  "sessionID": "<epoch millis as string>",
  "requestTopic": "<topic published to>",
  "responseTopic": "<topic to reply on>",
  "request": {"command": 16777219, "deviceType": 3, "macAddress": "M", "additionalValue": "A", ...}
}
```
Every `request` carries `command`, `deviceType`, `macAddress` and `additionalValue`. The other fields depend on the
command and are described in the sections below.

### Replies

```json
{"protocolVersion": 1, "clientID": "nsp-M", "sessionID": "<same as request>", "requestTopic": "...", "response": {...}}
```
- Match a reply to its request by **`sessionID` and topic**. One request can produce several replies with the same
  `sessionID`: `st/start` is answered on both `…/res/did` (device info) and `…/res` (status).
- Control commands are answered on `…/res` with a **full status**, whose `status.command` is the control command.
- A rejected control command is answered on `cmd/T/nsp-M/res/ctrl-fail` with `response.failCode`.
- Replies can arrive late or twice, including replies addressed to other clients. Ignore any `sessionID` that isn't
  pending.
- **Unprompted status pushes**: whenever the zone or burner changes, including changes made on the thermostat
  itself and each 0.5 °C step in room temperature, the module publishes a full status on its own `cmd/T/nsp-M/res`.
  These carry `requestTopic` `…/st`, no `responseTopic`, and a `sessionID` that no client sent.
  Subscribing to that topic gives live updates without polling.

### Pacing

The module handles requests one at a time. Answers usually take 1–3 s, but after several back-to-back requests they
can take 20–25 s. Send one request at a time and allow about 40 s for a reply.

### Commands

| Name | `command` | Hex |
|---|---|---|
| STATUS_START (device info) | 16777217 | 0x01000001 |
| STATUS_GET | 16777219 | 0x01000003 |
| STATUS_MONTHLY_USAGE | 16777220 | 0x01000004 |
| STATUS_DAILY_USAGE | 16777221 | 0x01000005 |
| STATUS_GET_INSIDE_RSV (heating schedule) | 16777223 | 0x01000007 |
| STATUS_GET_HOT_WATER_RSV (hot water schedule) | 16777224 | 0x01000008 |
| CTRL_HEAT_INSIDE (`heat-mode`) | 33554433 | 0x02000001 |
| CTRL_HOT_WATER (`dhw-mode`) | 33554434 | 0x02000002 |
| CTRL_HOLIDAY_OFF (`goout-off`) | 33554435 | 0x02000003 |
| CTRL_HOLIDAY_ON (`goout-on`) | 33554436 | 0x02000004 |
| CTRL_SET_INSIDE_TEMP (`inside-heat`) | 33554437 | 0x02000005 |
| CTRL_HEAT_RESERV / CTRL_DHW_RESERV (write schedules) | 33554439 / 33554440 | |
| CTRL_SET_RSV_PROGRAM_MODE (`reservation-mode`) | 33554441 | |
| CTRL_PRE_HEATING (`pre-heating`) | 33554448 | |
| CTRL_SET_WHEATHER_USE (`outside-use`) | 33554450 | |
| CTRL_SET_FREZ_TEMP (`freeze-protection-temp`) | 33554451 | |

Do **not** send `CTRL_WIFI_RESET` (33554447), `CTRL_WIFI_RECONNECT` (33554446), `CTRL_COMMIT_OTA` (33554442) or
`CTRL_SET_HISTORY_RESET` (33554452).

## Status

### Request

Publish to `cmd/T/nsp-M/st`:
```json
"request": {"command": 16777219, "deviceType": 3, "macAddress": "M", "additionalValue": "A", "zoneUseSetting": 1}
```
`zoneUseSetting` is a zone bitmask: zone 1 → 1, zone 2 → 2, zone 3 → 4. The reply's `thermostats` list covers
every zone *(verify with more than one zone)*.

### When the app asks

The app has no status timer. It requests status only:
- when it connects, right after reading the device info (`st/start`);
- when the module's connection event says the module is back online;
- when an override ends, to fetch the zone's new state;
- after a control command, whose reply is a full status anyway.

The rest of the time it relies on the module's unprompted pushes on `cmd/T/nsp-M/res` (see [Replies](#replies)). The
integration does the same and polls only every 10 minutes, as a safety net for a missed push or a stalled connection.

### Reply

`response.status`. Fields seen from a gas combi with one zone (full example in
[`tests/fixtures/reply_status.json`](../tests/fixtures/reply_status.json)):
```
command, outsideTemperature, regionCode, errorCode, subErrorCode, operationBusy, dhwOperationBusy,
dhwOperationMode, outsideTempDisplayUse, outsideTempUse, nrcPairing, vcmPairing, nrcConnected, vcmConnected,
boilerConnected, intelligentCurrentProgramNum, dhwCurrentProgramNum, ecoUse, maintenanceDay, geofenceTransition,
thermostats: [{
  zoneId, insideOperationBusy, insideOperationMode, insideTemperature, insideTemperatureSetting,
  preHeatingUse, freezeZoneProtectionUse, heatingCurrentProgramNum, geofenceUseSetting, holidayUseSetting,
  preHeatingUseSetting, freezeProtectionTemperature, programReservationType
}]
```
The app's data class also allows `wifiRssi`, `faultStatus1/2`, `supplyTemperature`, `returnTemperature`,
`supplyReturnType`, `heatFlowRate`, `heatingTemperatureSetting`, `dhwUse`, `dhwTemperature`,
`dhwTemperatureSetting`, `dhwBoost`, `dhwInternalFlowRate`, `dhwUseSustained`, `fastDHWOperateDuration`,
`smartFastDHW`, `freezeProtectionUse`, `smartDiagnostic`, `specialFunctionStatus`, `didReload` and
`actualInsideTemperature`. The combi tested reports none of these, so no flow, return or hot water temperatures.
Their scaling is unknown.

### Value encoding

- **Booleans**: `2` = on/true, `1` = off/false, `0` = not present or not supported (e.g. `vcmPairing` without a
  wiring centre). `operationBusy` = 2 means the burner is running; `insideOperationBusy` = 2 means the zone is
  calling for heat.
- **Exceptions**:
  - `nrcConnected` and `nrcPairing` are **zone bitmasks** of connected/paired thermostats (1 = zone 1, 2 = zone 2,
    4 = zone 3; the app accepts 1–7).
  - `boilerConnected` is never read by the app. It reads `1` while the boiler is online. By analogy with
    `nrcConnected` and `vcmConnected` (0 here, with no wiring centre), it's probably "1 boiler linked over the KIW
    bus" *(unverified)*. The app works out connectivity from `nrcConnected` and the module's connection event
    instead.
- **Errors**: `errorCode` is the boiler's fault code, shown as `E` plus three digits, with `0` meaning no error. See
  [Error codes](#error-codes).
- **Temperatures**:
  - `insideTemperature`, `insideTemperatureSetting`, `freezeProtectionTemperature`: ÷2 (°C, 0.5 steps)
  - `outsideTemperature`: ÷10 (°C)
  - `regionCode` is a weather-location ID; when it's `0`, no outdoor temperature is available.
- **Modes**: `insideOperationMode` uses the `heat-mode` values and `dhwOperationMode` the `dhw-mode` values (see
  [Control](#control)).
- **Program**: `heatingCurrentProgramNum` is the current slot of today's schedule (1–6, or 0 = still in
  yesterday's last slot), and `programReservationType` is the schedule layout (see
  [Auto target temperature](#auto-target-temperature)).
- **In Auto, `insideTemperatureSetting` is stale**: it keeps the last override value. Work the target out from the
  schedule instead (see [Auto target temperature](#auto-target-temperature)).

## Error codes

The status's `errorCode` (and `subErrorCode`) are the boiler's own fault codes. The boiler's front panel shows
`errorCode` as `E` plus three digits, e.g. `3` → `E003`, and the app formats it the same way (`BoilerErrorUtil`).
`0` means no error. The app's main screen only says "Boiler Error"; its smart-diagnosis screen looks up a description
in a built-in table of 35 codes and appends the sub-code, as in `E515-23`.

The integration's table below follows Navien's boiler manuals rather than the app's wording, which is shorter and
sometimes less useful (e.g. the app calls E110 "Abnormal air pressure", while the manuals say "Exhaust blockage
(condensate blockage)"). Every code in the app's table appears in the manuals with the same number. No code means
different things in different manuals; the oil-fired LCB700 adds three codes of its own (E056, E144, E157). Each manual
also gives a reset type (Auto, Manual or Alarm). That varies between models (E001 is Manual on the NCB700 but
Manual/Auto on the NCB300), so the integration doesn't show it.

| Code | Description | Listed in |
|---|---|---|
| E001 | Overheating of heat exchanger | NCB700, NCB300, LCB700, Service |
| E003 | Ignition failure | NCB700, NCB300, LCB700, Service |
| E004 | False flame detection | NCB700, NCB300, LCB700, Service |
| E012 | Flame loss | NCB700, NCB300, LCB700, Service |
| E016 | Heat exchanger overheat | NCB700, NCB300, LCB700, Service |
| E030 | Abnormal exhaust temperature | NCB700, NCB300, LCB700, Service |
| E031 | Burner overheat | NCB700, NCB300, LCB700 |
| E046 | Abnormal operation: heat exchanger overheating sensor | NCB700, NCB300, LCB700, Service |
| E047 | Abnormal operation: exhaust thermostat | NCB700, NCB300, LCB700, Service |
| E056 | Abnormal operation: oil pump | LCB700 |
| E060 | Abnormal operation: dual venturi limit switch | NCB700, Service |
| E109 | Abnormal operation: fan | NCB700, NCB300, LCB700, Service |
| E110 | Exhaust blockage (condensate blockage) | NCB700, LCB700, Service |
| E127 | Abnormal operation: air pressure sensor (APS) | NCB700, LCB700, Service |
| E144 | Abnormal operation: burner temperature sensor | LCB700 |
| E157 | Abnormal operation: air pressure sensor (APS) hose | LCB700 |
| E205 | Heating supply thermistor open or short circuit | NCB700, NCB300, LCB700, Service |
| E218 | Heating return thermistor open or short circuit | NCB700, Service |
| E291 | Supply/return inversion limit | NCB700 |
| E302 | Low water pressure | NCB700, NCB300, LCB700, Service |
| E352 | High water pressure | NCB700, LCB700, Service |
| E353 | Abnormal operation: water pressure sensor | NCB700, LCB700, Service |
| E407 | Hot water outlet thermistor open or short circuit | NCB700, NCB300, LCB700, Service |
| E421 | Hot water inlet thermistor open or short circuit | NCB700, LCB700, Service |
| E434 | Abnormal operation: flow control valve | NCB700 |
| E441 | Hot water outlet 2 thermistor open or short circuit | NCB700, LCB700 |
| E445 | Abnormal operation: mixing valve | NCB700, LCB700 |
| E480 | Hot water tank thermistor open or short circuit | NCB700, LCB700, Service |
| E515 | Abnormal operation: relay feedback (PCB) | NCB700, NCB300, LCB700, Service |
| E517 | Abnormal operation: DIP switch setting (PCB) | NCB700, NCB300, LCB700, Service |
| E593 | Abnormal operation: touch IC (panel PCB) | NCB700, NCB300 |
| E594 | Abnormal operation: EEPROM (PCB) | NCB700, NCB300, LCB700, Service |
| E598 | Abnormal operation: panel real-time clock | NCB700, NCB300 |
| E615 | Abnormal operation: input and memory (MCU) | NCB700, NCB300, LCB700, Service |
| E740 | Abnormal operation: outdoor temperature sensor | NCB700, NCB300, LCB700, Service |
| E782 | Abnormal operation: main panel communication | NCB700, NCB300, LCB700, Service |
| E783 | Abnormal operation: OpenTherm remote control | NCB700, NCB300, LCB700, Service |
| E787 | Abnormal operation: reset device | NCB700, NCB300, LCB700, Service |
| E788 | Abnormal operation: gas type settings | NCB700, NCB300 |
| E792 | Abnormal operation: hot water circulation | NCB700 |

Sources, all from Navien UK's [manuals page](https://navien.co.uk/support/manuals-and-brochures/), checked 2026-10-26:
- **NCB700**: *NCB700 Combi Installation & Operation Manual* (doc. 20051189B, 2024-10-07), section 10.8 "Error Codes",
  p. 74.
  [PDF](https://navien.co.uk/wp-content/uploads/2025/10/20051189BNCB700_Combi_CE_GB_Installation__Operation_Manual_EN_241007-1.pdf)
- **NCB300**: *NCB300 Combi Installation & Operation Manual* (2026-01-02), section 10.8 "Error Codes", p. 55.
  [PDF](https://navien.co.uk/wp-content/uploads/2025/10/NCB300_Combi_CE_GB_Installation__Operation_Manual_EN_260102-1.pdf)
- **LCB700**: *LCB700 Combi Installation & Operation Manual* (doc. 20047769C, 2026-02-13), section 11.6 "Error Codes",
  p. 67. [PDF](https://navien.co.uk/wp-content/uploads/2025/10/20047769CLCB700En_combi_260213_B.pdf)
- **Service**: *NCB Condensing Combi/System Boiler Service Manual* (universal, 2016-09-02), sections 4.1 "Error Code
  Classification" and 4.2 "Error Code List and Actions", pp. 84–85. This covers the older NCB-CE range, which uses the
  same codes. The NCB-CE installation manual mentions low water pressure as "E02", apparently a shortened display form.
  [PDF](https://navien.co.uk/wp-content/uploads/2025/10/NCB_Service_Manual_Universal-Sep-2-2016.pdf)

Not yet confirmed on hardware: that a real fault reports in `errorCode` with these numbers (the system tested has only
ever reported `0`).

## Device info

Publish a status-shaped request with `"command": 16777217` to `cmd/T/nsp-M/st/start`. The module answers twice:
`response.feature` on `…/res/did`, plus a normal status on `…/res`. Example in
[`tests/fixtures/reply_did.json`](../tests/fixtures/reply_did.json).

`response.feature`:
- **Capability flags** (`2` = supported): `insideUse, dhwUse, dhwBoostUse, holidayUse, preHeatingUse,
  outsideTempUse, weeklyHeatingReservationUse, weeklyDHWReservationUse, fuelUsageUse, freezeProtectionUse,
  smartDiagnosticUse, intelligentUse, geofenceUse, maintenanceDayUse, …`. Hot water support (`dhwUse`) is only
  reported here, not in status.
- **Setting ranges** (when present): `insideTemperatureMin/Max` (room setpoint), `dhwTemperatureMin/Max` (hot
  water), `heatingWaterTemperatureMin/Max` (radiator flow), `freezeProtectionTempMin/Max`. These only describe the
  boiler's ranges; nothing sets them. The app declares them but never reads them, and the combi tested sends none.
  The app uses its own fixed range instead, and only for the room setpoint: 10–40 °C in 0.5 steps
  (`MainViewModel.onUpDownClick`).
- **Model and software**: `boilerModelTypeCode, boilerControllerSerialNumber, boilerControllerSwVersion,
  bcmModelCode, bcmSwVersion, nrcSwVersion, vcmSwVersion, wifiSwVersion, panelSwVersion, countryCode`.

## Control

Publish to `cmd/T/nsp-M/ctrl`:
```json
"request": {"command": 33554433, "deviceType": 3, "macAddress": "M", "additionalValue": "A",
            "mode": "heat-mode", "zoneUseSetting": 1, "param": [0, 38], "paramStr": ""}
```
The reply is a full status on `…/res`.

### Zone heating

`heat-mode` (33554433): `param = [mode, setpoint × 2]`.

| Mode | Value |
|---|---|
| AUTO (follow the schedule) | 0 |
| ON (manual) | 1 |
| OFF | 2 |
| BOOST | 3 |
| OVERRIDE (temporary setpoint in Auto, until the next slot) | 4 |

`inside-heat` (33554437): `param = [setpoint × 2]`. Range 10.0–40.0 °C in 0.5 steps.

Changing the setpoint, as the app does, depends on the current mode:

| Current mode | Send |
|---|---|
| AUTO | `heat-mode [4, t×2]`: starts an Override |
| BOOST | `heat-mode [1, t×2]`: leaves Boost for manual |
| ON or OVERRIDE | `inside-heat [t×2]` |
| OFF | nothing; the app ignores the change |

Other mode changes:
- Cancel an override ("stop override"): `heat-mode [0]`. The zone returns to the schedule.
- Boost: `heat-mode [3, t×2]`. It lasts **one hour** and heats to the zone's stored setting
  (`insideTemperatureSetting`), not the temperature sent. Ending it from the app sends `heat-mode [1, t×2]`, which
  leaves the zone in manual heating (ON).
- The app's mode menu sends `[mode, current setpoint × 2]` for every mode. **In Auto the setpoint is ignored.**

### Hot water

`dhw-mode` (33554434): `param = [mode]`, with `zoneUseSetting` forced to 1. The app has no command to set the hot
water temperature (only its mode, schedule and "fast hot water"), so that's set on the boiler itself.

| Mode | Value |
|---|---|
| AUTO (schedule) | 0 |
| ON | 1 |
| ONCE (one-off boost) *(verify)* | 2 |
| OFF | 3 |
| None (not available) | 4 |
| INTELLIGENT | 5 |
| NaviCirc | 6 |

### Holiday and other settings

- Holiday on: `goout-on` (33554436), `param = [2]`. Holiday off: `goout-off` (33554435), `param = [1]`.
  The reply's `command` is the holiday command, and the zone's `holidayUseSetting` changes. `insideOperationMode`
  doesn't, so the zone still reads as Auto. While on, the zone holds its frost-protection temperature
  (`freezeProtectionTemperature` ÷ 2, 4 °C on the system tested), which is what the app shows.
- Pre-heating: `pre-heating` (33554448), `param = [2|1]` *(verify)*.

## Heating schedule

Publish to `cmd/T/nsp-M/st/heat-reserv-query/rd`:
```json
"request": {"command": 16777223, "deviceType": 3, "macAddress": "M", "additionalValue": "A", "zoneUseSetting": 1}
```
The reply comes on `cmd/T/F/U/C/res/heat-reserv/rd`:
```json
"response": {"reservationUse": 2, "heatingReservation": [{"zoneId": 1, "reservation": [
  {"day": 0, "rsvNum": 1, "hour": 6, "min": 30, "enable": 2, "mode": 2, "param": 36}, ...]}]}
```
- 6 slots (`rsvNum` 1–6) per day for each of the 7 days. Each slot sets `param` ÷ 2 °C from `hour:min`.
- Raw `day`: **0 = Sunday, 1 = Monday … 6 = Saturday**. The app moves day 0 to the end, so its own lists run Monday
  (0) to Sunday (6) (`ProgramViewModel.addHeatList`).
- Writing the schedule uses `CTRL_HEAT_RESERV` on `…/ctrl/heat-reserv/rd` with the same slot shape *(not used)*.

## Auto target temperature

In Auto the status's `insideTemperatureSetting` can't be trusted, so the app works out the target itself
(`MainViewModel.settingAutoProgramData` and `getWeekProgramListData`):
1. Pick today's list (in the app's Monday-first numbering) using the zone's `programReservationType`:
   - `1` = 7-day: today's own list
   - `2` = 5/2: list 1 on weekdays, list 5 at the weekend
   - `3` = 5/1/1: list 1 on weekdays, list 5 on Saturday, list 6 on Sunday
2. Take slot `heatingCurrentProgramNum − 1` from that list. When `heatingCurrentProgramNum` is `0`, take the last
   slot of yesterday's list instead.

To convert the app's list number back to the raw `day` field: raw `day` = (list + 1) mod 7.

## Fuel usage history

| Query | Publish to | `request` extras | Reply on |
|---|---|---|---|
| Daily, for one month | `cmd/T/nsp-M/st/fuel-usage-daily-query/rd` | `"command": 16777221, "year": 2026, "month": 10` | `…/res/fuel-usage-daily-query/rd` |
| Monthly, for one year | `cmd/T/nsp-M/st/fuel-usage-monthly-query/rd` | `"command": 16777220, "year": 2026` | `…/res/fuel-usage-monthly-query/rd` |

`response.usage` lists every day of the month (future days zero) or every month of the year:
```json
{"year": 2026, "month": 10, "day": 24, "heatUsage": 2, "dhwUsage": 1, "heatTime": 1, "dhwTime": 0}
```
- `heatUsage` / `dhwUsage`: gas for heating / hot water, in whole **m³**. Oil boilers presumably report litres, since
  the app has a "Liter" unit *(verify)*.
- `heatTime` / `dhwTime`: burner time in whole **hours**.
- A day is filled in after it ends; today's row reads 0.
- Monthly rows equal the sum of that month's daily rows.
- At least the previous calendar year is available.

## Local interfaces

None of these offer control; the integration is cloud-only.
- **Module web page** (HTTP port 80, in normal station mode): the Wi-Fi chip's generic admin page (I&C Technology
  SoC, lwIP 1.4.1), with no login. It shows firmware version, MAC and network settings. It has no boiler functions
  but does have reboot, firmware-upload and account forms, so don't POST to it. Only port 80 is open among the
  1000 most common TCP ports.
- **Setup access point** (SSID `Navien_Smart+`, `http://192.168.43.1/process_data.json`): JSON
  `{"command_id": "kdone00N", "command_type": "validproduct|productid|scan|join", "request_value": "get|set", "data": ""}`.
  Wi-Fi provisioning only.
- **Bluetooth LE** (service `237339A3-B40D-4283-A629-F89BB838D85A`, write `…85C`, notify `…85B`): JSON commands
  `scan`, `connectServer`, `linkageFinish`, `createID`. Provisioning only.
- The module reaches AWS IoT over TLS, presumably with an X.509 device certificate, so redirecting it to a local
  server would need modified firmware. Navien doesn't publish the firmware; it's only delivered over the air.

## Things deliberately not used

The app also contains a legacy Amazon MQ broker URL with hard-coded shared credentials (`MqttHelper`,
`RepositoryURL.THERMOSTAT_MQTT`). Those credentials are not the user's own, so the integration must never use them.

## Background and related work

The Smart Plus backend is from the same family as Navien's US **NaviLink** cloud (`nlus.naviensmartcontrol.com`),
with UK hostnames (`spuk.` / `spuk-iot.`). The two share:
- a sign-in response that carries both the app tokens and temporary AWS credentials
  (`accessKeyId`, `secretKey`, `sessionToken`)
- an `Authorization` header holding the raw access token, with no `Bearer` prefix
- MQTT 3.1.1 over WebSocket to AWS IoT Core, signed with SigV4
- the same JSON envelope (`protocolVersion`, `clientID`, `sessionID`, `requestTopic`, `responseTopic`,
  `request{command, deviceType, macAddress, additionalValue, mode, param, paramStr}`)
- topics shaped like `cmd/{deviceType}/{prefix}-{mac}/st|ctrl`, with replies on
  `cmd/{deviceType}/{homeSeq|familySeq}/{userSeq}/{clientId}/res…`

The visible differences:

| | US NaviLink | UK Smart Plus |
|---|---|---|
| Device topic prefix | `navilink-` | `nsp-` |
| `protocolVersion` | 2 | 1 |
| Sign-in | `POST /api/v2.1/user/sign-in` with `userId` | `POST /api/v1.0/users/sign-in` with `email` |
| Command set | water heaters | room thermostat zones, boiler, hot water |

This document was written independently from the UK app's decompiled code. The open-source NaviLink projects below
were useful background: they confirmed the overall design and made the decompiled code much quicker to read. No
code was copied from them.
- [eman/nwp500-python](https://github.com/eman/nwp500-python) (MIT): Python library for the NWP500 heat pump water
  heater, with detailed [REST](https://nwp500-python.readthedocs.io/en/latest/reference/protocol/rest_api.html) and
  [MQTT](https://nwp500-python.readthedocs.io/en/latest/reference/protocol/mqtt_protocol.html) protocol docs.
- [nikshriv/hass_navien_water_heater](https://github.com/nikshriv/hass_navien_water_heater) and
  [nikshriv/navilink_api](https://github.com/nikshriv/navilink_api): Home Assistant integration and API
  exploration for NaviLink water heaters.
- [rudybrian/PyNavienSmartControl](https://github.com/rudybrian/PyNavienSmartControl) and
  [Matthew1471/Navien-API](https://github.com/Matthew1471/Navien-API): earlier work on Navien's cloud protocols.

## Test log

All tests used one system: a BCM-41RW module (Wi-Fi firmware 26.87.03), one NRC-10R zone and a gas combi. Raw
captures with tokens removed are kept locally in `research/captures/` (not committed). The anonymised copies in
`tests/fixtures/` come from them.

### 2026-10-25: read-only

- Sign-in, device list (`deviceType` 3, `deviceId` = MAC + 4 hex chars) and the SigV4 WebSocket connection all
  worked first time. Every subscription listed under [Topics](#topics) was accepted.
- Status and device info returned the lean field set shown under [Status](#status) and [Device info](#device-info).
  The scaling was checked: `insideTemperature` 39 → 19.5 °C, setting 38 → 19.0 °C, `outsideTemperature`
  92 → 9.2 °C. The `st/start` double reply was seen here.
- Heating schedule: 7 × 6 slots. Days 0 and 6 hold the weekend pattern, which fits 0 = Sunday.
- Fuel usage: daily rows for October 2026 summed to that month's monthly row (10 m³ heating, 17 m³ hot water).
  Monthly rows for 2025 and 2026 were available. The app's history screen confirmed m³, and its "hour" view the
  hours.
- After several back-to-back requests, replies arrived 20–25 s late, and replies to an earlier client ID were
  delivered again. When the module was idle, three requests in a row were each answered within ~1.5 s.

### 2026-10-25: control (with the owner's OK)

The zone started in Auto: an earlier override had ended at the 20:00 program slot.
1. `heat-mode [4, 39]` (Override at 19.5 °C): answered in ~3 s on `…/res` with a full status showing mode 4 and
   setting 39.
2. `heat-mode [0]` (cancel override): the zone went back to Auto (mode 0), but `insideTemperatureSetting` still read
   19.5 °C.
3. `heat-mode [0, 38]`: no change to the setting, so the setpoint is ignored in Auto.
4. Two minutes later the setting still read 19.5 °C, while the app showed the schedule's 16 °C for the 20:00
   Sunday slot. That led to the [Auto target temperature](#auto-target-temperature) logic. With it, the client
   reports 16 °C, matching the app.

### 2026-10-25: Home Assistant integration (local test instance)

- The config flow signed in, and every entity came up with values matching the app (Auto, target 16 °C).
- The usage backfill imported 389 days of daily gas statistics. The daily changes shown in Home Assistant matched
  the CLI.

### 2026-10-26: holiday and Boost from Home Assistant (with the owner's OK)

Done through the integration's own entities on a local test instance, reverting each change.
- Holiday switch on, then off: `holidayUseSetting` went 1 → 2 → 1, with the zone staying in Auto throughout. The app
  showed holiday at 4 °C, the zone's frost-protection temperature. The integration showed the schedule's 18 °C
  instead, so it now uses the frost-protection temperature while holiday is on.
- Boost preset: the zone went to mode 3 and called for heat. The app showed Boost for one hour at 19.5 °C, the
  stored setting, although 18 °C was sent. Clearing the preset gave manual heating (mode 1), and setting Auto
  returned the zone to the schedule (18 °C). The burner was still on in that first status and off by the next poll.
- Hot water modes were left untested at the owner's request.

### 2026-10-26: changes made on the thermostat (with the owner's OK)

A listener, read-only and also subscribed to wildcards on the module's topics, ran while the owner changed the
thermostat by hand. The zone started in Auto at 18 °C.
- 18:36:45, the setpoint turned down: a status arrived unprompted on `cmd/3/nsp-M/res` within seconds, showing
  Override (mode 4) at 16.5 °C.
- 18:38:36, the setpoint turned back up: Override at 18.0 °C.
- 18:39:03, the override cancelled: Auto (mode 0).
- Two more pushes followed when the room temperature changed (19.0 → 19.5 → 19.0 °C).
- All 9 messages in 10 minutes arrived on `cmd/T/nsp-M/res` and nothing on any other topic, so the integration's
  existing subscription already gets these updates.

### Not yet tested on hardware

- `dhw-mode` changes (including whether 2 = ONCE is a one-off boost)
- Setting a zone to Off
- Systems with more than one zone, a VCU-10R wiring centre, a system or regular boiler, or an oil (LCB700) boiler
- The module's connection event (going offline and back)
- A real boiler fault in `errorCode` (only `0` seen so far)
