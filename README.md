# Navien Smart Plus for Home Assistant

An unofficial Home Assistant integration (installable with HACS) for UK Navien boilers controlled by the
**Navien Smart Plus** thermostat system and app (`com.navien.thermostat.gb`).

> **Status: early release.** Tested on one system: a gas combi, one zone, BCM-41RW. Status, setpoint control,
> the heating schedule and gas history are confirmed against it. Holiday mode and the hot water modes are built
> the way the app sends them but haven't been tried on real hardware yet.

## Installation

1. In HACS, open the menu → **Custom repositories**, add `https://github.com/cHemingway/home-assistant-navien-boiler`
   with type **Integration**.
2. Install **Navien Smart Plus** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Navien Smart Plus**, then sign in with the email and
   password you use in the Navien Smart Plus app.

To install by hand, copy `custom_components/navien_smartplus` into your Home Assistant `config/custom_components/`
folder and restart.

## What you get

One device per Smart Plus module, with:

| Entity | What it does |
|---|---|
| Climate, one per zone | Auto (follows the schedule), Heat (manual), Off. Changing the temperature in Auto starts an **Override** until the next program slot, as on the thermostat. Presets: Boost, Override. In Auto the target shown is the schedule's current temperature, as in the app. |
| Water heater "Hot water" | Hot water mode: Schedule, On, Boost (once), Off. The combi doesn't report water temperatures. |
| Switch "Holiday mode" | Holiday (away) mode per zone. |
| Binary sensors | Burner on, hot water running, fault. Diagnostic: Online (the module's link to Navien's cloud) and, per zone, Thermostat connection (its radio link to the module). |
| Sensors | Outdoor temperature (when weather is set up in the app), error code. |
| Gas sensors | Heating, hot water and total gas **this year** in m³, plus burner hours (disabled by default). |

Status is polled every minute. The integration also hears the cloud's replies to the Smart Plus app, so changes
made in the app usually show up straight away. Changes made on the thermostat itself appear at the next poll.

### Energy dashboard

The boiler reports gas use **per day, in whole m³** (about 11 kWh), and only once the day is over. So:
- On setup, the integration imports up to **13 months of daily history** as long-term statistics named
  **Navien heating gas**, **Navien hot water gas** and **Navien total gas**. Add **Navien total gas** under
  *Settings → Dashboards → Energy → Gas consumption* to see past days on the right dates.
- The "… gas this year" sensors also work in the Energy dashboard, but only from the day you add them, and they
  update with about a day's delay.

## Supported hardware

- BCM-41RW / BCM-45RW wireless communication module
- NRC-10R wireless thermostats (up to 3 zones), VCU-10R wiring centre
- Navien LCB700 (oil) and NCB-CE / NCB300 / NCB500 / NCB700 (gas) boilers

The US NaviLink products (NPE/NCB water heaters, NWP500) use a different cloud region and app. See the related
projects below.

## How it works

Navien has no public API, so this integration talks to the same cloud the Smart Plus app uses:
- **REST** (`spuk.naviensmartcontrol.com`) to sign in and list devices
- **MQTT over WebSocket to AWS IoT Core** (`spuk-iot.naviensmartcontrol.com`) for live status and control

The module only offers Wi-Fi setup locally, so there's no local-only option. See [docs/protocol.md](docs/protocol.md)
for the full protocol.

The Smart Plus backend is from the same family as Navien's US **NaviLink** cloud, with UK hostnames, a different
device prefix and its own command set for thermostat zones and boilers.

## Credits

The protocol was worked out from the UK Smart Plus app. The open-source NaviLink projects below documented the
shared design and were valuable background; no code was copied from them. Thanks to their authors:
- [eman/nwp500-python](https://github.com/eman/nwp500-python): NWP500 library with excellent REST and MQTT
  protocol documentation
- [nikshriv/hass_navien_water_heater](https://github.com/nikshriv/hass_navien_water_heater) and
  [nikshriv/navilink_api](https://github.com/nikshriv/navilink_api): Home Assistant integration and API work for
  NaviLink water heaters
- [rudybrian/PyNavienSmartControl](https://github.com/rudybrian/PyNavienSmartControl) and
  [Matthew1471/Navien-API](https://github.com/Matthew1471/Navien-API): earlier Navien cloud clients

## Disclaimer

This project isn't affiliated with or endorsed by Navien or KD Navien. It uses an undocumented cloud API that may
change or stop working at any time. Use it at your own risk.
