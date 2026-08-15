<p align="center">
  <img src="custom_components/ashp_zone_control/brand/icon.png" width="96" height="96" alt="ASHP Zone Control icon">
</p>

<h1 align="center">ASHP Zone Control</h1>

<p align="center">
  Zone-based heating control for Home Assistant — configure zones, rooms, TRVs and
  temperature sensors entirely through the native HA UI. No AppDaemon, no separate
  webserver, no custom card required to get started.
</p>

> **⚠️ Early development — not yet functional for real heating control.**
> Configuration (Config Flow + Options Flow) is built and working. The control loop
> that actually reads TRVs/sensors and drives your heating switches — the part that
> matters for keeping your house warm — is **not built yet** (see [Roadmap](#roadmap)).
> Installing this today lets you build and inspect your zone/room configuration; it
> will not turn anything on or off. Don't remove your existing heating automation
> until Milestone 2 ships.

## What it does (today)

- Define **Zones** — a Zone is whatever grouping makes sense for your house (e.g.
  "Ground Floor", "First Floor"), not tied to a single Home Assistant Area.
- Assign **Rooms** to each zone — a Room is an HA Area (Kitchen, Lounge, Bedroom...).
  An Area can only belong to one zone at a time.
- Pick each zone's **heating actuator switch(es)** — single or multiple, e.g. a
  separate switch per floor.
- TRVs and temperature sensors are **auto-discovered** per room from whatever's
  already assigned to that Area in Home Assistant, pre-selected, editable.
- Mark a room **active/inactive** — an unoccupied guest room, for example, can be
  excluded from heating demand without removing its configuration.
- A saved-configuration summary (zones → switches → rooms → active TRVs/sensors) is
  shown after every save.

## What it doesn't do yet

- Nothing is actually controlled. No switches are turned on or off, no TRV setpoints
  are read or written by an automated loop yet.
- No scheduling (day/time/setpoint grid) yet.
- No "away mode" / holiday calendar integration yet.
- No dashboard card yet — all configuration happens through Settings → Devices &
  Services → ASHP Zone Control → Configure.

See [Roadmap](#roadmap) for what's planned and in what order.

## Installation

### HACS (custom repository)

This integration isn't in the default HACS store yet. Add it as a custom repository:

1. HACS → Integrations → ⋮ (top right) → **Custom repositories**.
2. Repository: this repo's URL. Category: **Integration**.
3. Find **ASHP Zone Control** in HACS and install it.
4. Restart Home Assistant.

### Manual

1. Copy `custom_components/ashp_zone_control` into your Home Assistant
   `config/custom_components/` directory.
2. Restart Home Assistant.

## Configuration

All configuration is done via the UI — no YAML.

1. **Settings → Devices & Services → Add Integration → ASHP Zone Control.**
   Give the integration instance a name (e.g. "ASHP Zone Control").
2. Open **Configure** on the integration card. You'll land on the zone menu:
   - **+ Add a new zone** — creates a zone (default name "Zone N").
   - **Pick rooms** — choose which HA Areas belong to this zone.
   - **Name the zone and pick its heating switch(es)** — rename it to something
     meaningful (e.g. "Ground Floor") and select the switch entity/entities it
     should control.
   - **Per room** — review the TRVs and temperature sensors already discovered for
     that Area, untick any that shouldn't count, and toggle the room off entirely
     if it shouldn't take part in heating demand (e.g. a guest room while empty).
   - Repeat for as many zones as you have, then choose **Done → Save**.
3. Reopen **Configure** at any time to add, edit, or remove zones and rooms — every
   field is pre-filled with your current configuration.

### Requirements

- Home Assistant with at least one **Area** defined (Settings → Areas & Zones) for
  each room you want to configure.
- A `switch` entity per zone to act as the heating actuator (e.g. a smart relay or
  your ASHP's zone valve control).
- `climate` entities (TRVs) and `sensor` entities (temperature, `device_class:
  temperature`) assigned to the relevant Areas, so they can be auto-discovered.

## Roadmap

| Milestone | Status |
|---|---|
| 1. Skeleton integration, Config Flow, Store-backed data model | Done |
| 2. Coordinator — the actual control loop (reads TRVs/sensors, drives switches, 5‑minute re-enable delay to prevent short-cycling, per-zone manual override switch) | Not started |
| 3. Options Flow for zone/room/TRV/sensor management | Done |
| 4. Scheduling (day/time/setpoint grid) + calendar-driven away mode | Not started |
| 5. Polish — diagnostics sensor, translations, entity icons | Brand icon done; rest pending |
| 6. HACS store submission | Pending |

## Troubleshooting

**Options Flow shows stale text, an old field name, or a `formatjs MISSING_VALUE`
error after an update.** Home Assistant caches a custom integration's translations.
A config-entry *reload* alone won't refresh `strings.json` changes — do a full HA
Core **restart**, then a hard refresh of the browser tab (Ctrl+Shift+R / Cmd+Shift+R)
to clear the cached translation bundle.

**An Area doesn't show up as a room option.** Make sure it's defined under
Settings → Areas & Zones, and that at least one entity (TRV, sensor, or anything
else) is assigned to it — an Area with nothing in it still works as a room, but
won't have anything to auto-discover.

## Contributing

Issues and pull requests welcome. This is an early-stage personal project moving
through the milestones above in order — see the pinned issues / project board for
current status.

## License

TBD.
