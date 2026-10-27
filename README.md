# Navien Smart Plus for Home Assistant

An unofficial Home Assistant integration (installable with HACS) for UK Navien boilers controlled by the
**Navien Smart Plus** thermostat system and app (`com.navien.thermostat.gb`).

> **Status: early release.** Tested on one system: a gas combi with one zone and a BCM-41RW module. Confirmed on
> it: status, the heating schedule, gas history, setpoint changes and overrides, Boost, holiday mode, live updates
> from changes made on the thermostat, and reconnecting after the connection drops. Not tried on real hardware
> yet: changing the hot water mode (so that entity starts disabled), switching a zone to Off, and systems with
> more zones or other boilers. See the [test log](docs/protocol.md#test-log).

## Installation

You need Home Assistant 2026.3 or later and a Navien Smart Plus account in the UK; the integration uses Navien's UK
cloud.

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
| Water heater "Hot water" | Hot water mode: Schedule, On, Boost (once), Off. The combi doesn't report water temperatures. **Disabled by default**: changing the mode hasn't been tried on real hardware yet. Enable it under the device's entities to try it. |
| Switch "Holiday mode" | Holiday (away) mode per zone. While it's on, the zone holds its frost-protection temperature (4 °C on the tested system), and the climate entity shows that as its target, as the app does. |
| Binary sensors | Burner on, hot water running, fault. Diagnostic: Online (the module's link to Navien's cloud) and, per zone, Thermostat connection (its radio link to the module). |
| Sensors | Outdoor temperature (when weather is set up in the app). Diagnostic: Error code, shown as the boiler's code and description, e.g. "E302 Low water pressure", from Navien's manuals. |
| Gas sensors | Heating, hot water and total gas **this year** in m³, plus burner hours (disabled by default). |

Updates are pushed: the boiler's module reports every change within seconds, whether it's made in Home
Assistant, the Smart Plus app or on the thermostat, including each 0.5 °C change in room temperature. Status is
also polled every 10 minutes as a safety net. If the connection to Navien's cloud drops, the integration
reconnects straight away. If it can't reach the cloud, it tries again after a minute, then waits twice as long each
time, up to the usual 10 minutes. It doesn't retry early while the module reports itself offline.

### Energy dashboard

On setup, the integration imports up to **13 months of daily gas history** as long-term statistics named
**Navien heating gas**, **Navien hot water gas** and **Navien total gas**. Home Assistant doesn't add these to the
Energy dashboard by itself. To see them:

1. Go to **Settings → Dashboards → Energy**, open the **Gas** tab and select **Add gas source**.
2. Under *Gas consumption*, pick **Navien total gas** (listed under *Navien Smart Plus*), then **Save**.
3. Open **Energy** in the sidebar and choose a longer period, e.g. **This month** or **Last 12 months**, from the
   date picker at the bottom.

The Energy dashboard opens on **today**, which always shows 0 m³: the boiler reports gas use **per day, in whole
m³** (about 11 kWh), and only once the day is over. Yesterday and earlier days show up on their dates.

The "… gas this year" sensors can be used as the gas source instead, but they only count from the day you add
them. Pick one or the other, not both, or your gas will be counted twice.

**Already have a gas meter in Energy?** Don't add the boiler as well. Energy adds gas sources together, and the
meter already includes everything the boiler burns, so the boiler's gas would be counted twice. Keep the meter as
your gas source and show the boiler's share on a dashboard with a **Statistics graph** card instead: pick
**Navien heating gas** and **Navien hot water gas** (or **Navien total gas**), with the statistic *Change*, the
period *Month* or *Day*, and the chart type *Bar*. In YAML (the IDs end in your module's MAC address):

```yaml
type: statistics-graph
title: Boiler gas
entities:
  - navien_smartplus:001122334455_heating_gas
  - navien_smartplus:001122334455_hot_water_gas
stat_types:
  - change
chart_type: bar
period: month
days_to_show: 365
```

## Supported hardware

Built for the UK Smart Plus system:
- BCM-41RW / BCM-45RW wireless communication module
- NRC-10R wireless thermostats (up to 3 zones), VCU-10R wiring centre
- Navien LCB700 (oil) and NCB-CE / NCB300 / NCB500 / NCB700 (gas) boilers

Only a gas combi with one NRC-10R thermostat and a BCM-41RW has been tested. The rest use the same app and cloud,
so they should work, but more zones and the wiring centre are untested. Oil boilers probably report fuel in
litres, which the integration doesn't handle yet: it labels all fuel use as m³ of gas. Reports from other
systems are welcome as GitHub issues.

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

## To do

- **Move the cloud client into its own library.** Home Assistant core only accepts integrations whose protocol
  code lives in a separate Python library on PyPI ([library requirement](https://contributors.home-assistant.io/core/integrations/concepts/#library)).
  The client in `custom_components/navien_smartplus/api/` is already free of Home Assistant code, but it still
  needs its own repository and PyPI release. Until then, the integration is available through HACS only.
- Try the hot water modes and switching a zone to Off on real hardware, then enable the hot water entity by
  default.
- Report oil (LCB700) boiler fuel use in litres instead of m³.

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
