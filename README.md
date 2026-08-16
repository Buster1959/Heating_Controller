<p align="center">
  <img src="custom_components/ashp_zone_control/brand/icon.png" width="96" height="96" alt="ASHP Zone Control icon">
</p>

<h1 align="center">ASHP Zone Control</h1>

<p align="center">
  Zone-based heating control for Home Assistant — configure zones, rooms, TRVs and
  temperature sensors entirely through the native HA UI. No AppDaemon, no separate
  webserver, no custom card required to get started.
</p>

> **📛 Renaming to ZEAL (Zoned, Efficient, Adaptive, Learning).** The GitHub repo is
> renamed ([`Buster1959/ZEAL`](https://github.com/Buster1959/ZEAL)) — the domain,
> filenames, and class names inside still say "ASHP Zone Control" / `ashp_zone_control`
> throughout. That remaining rename will land as one deliberate pass (with a
> config-entry migration path for anyone already set up), not gradually, to avoid a
> half-renamed integration breaking existing installs.

> **⚠️ Early development — the control loop exists but hasn't been validated against
> real or dummy hardware yet.** Configuration (Config Flow + Options Flow) and the
> Coordinator control loop are both built. Before trusting this with real heating
> equipment, test it against dummy/spare TRVs first — read "Before you trust this
> with real heating" below. Don't remove your existing heating automation until
> you've verified it against your own setup.

## What it does (today)

- Define **Zones** — a Zone is whatever grouping makes sense for your house (e.g.
  "Ground Floor", "First Floor"), not tied to a single Home Assistant Area.
- Assign **Rooms** to each zone — a Room is an HA Area (Kitchen, Lounge, Bedroom...).
  An Area can only belong to one zone at a time.
- Pick each zone's **heating actuator switch** — one per zone (a shared single-pump
  house might have two zones sharing conceptually different floors, a dual-pump
  house one switch per zone, a hotel one switch per level — but a zone always has
  exactly one switch).
- Pick each zone's **heat source** (ASHP, modulating/condensing boiler,
  non-condensing boiler, or other) — used to suggest a sensible starting
  **re-enable delay** for that zone (see
  [Heat sources and heating profiles](#heat-sources-and-heating-profiles)
  below), which you can then change to any value you want.
- TRVs and temperature sensors are **auto-discovered** per room from whatever's
  already assigned to that Area in Home Assistant, pre-selected, editable.
- Mark a room **active/inactive** — an unoccupied guest room, for example, can be
  excluded from heating demand without removing its configuration.
- A saved-configuration summary (zones → switch → heat source → re-enable delay
  → rooms → active TRVs/sensors) is shown after every save.
- A **Coordinator** evaluates every active room every 60 seconds (and instantly on
  any tracked TRV/sensor state change), turns each zone's heating switch on when
  any active room is colder than its TRV setpoint, and off when none are — with a
  **re-enable delay** (per zone, editable — suggested default depends on heat
  source) after switching off, so a zone can't rapidly cycle.
- Each zone gets a **Manual override switch** (created automatically) — turn it on
  to take that zone out of automatic control entirely.
- Each zone gets a **Demand sensor** showing `Demand` / `No demand`, with which
  rooms are asking for heat as an attribute — a quick way to see what the
  Coordinator is doing without digging through logs.

## What it doesn't do yet

- No scheduling (day/time/setpoint grid) yet — TRV setpoints are only ever *read*,
  never written by an automated timer.
- No "away mode" / holiday calendar integration yet.
- No dashboard card yet — all configuration happens through Settings → Devices &
  Services → ASHP Zone Control → Configure.

See [Roadmap](#roadmap) for what's planned and in what order.

## Before you trust this with real heating

The Coordinator hasn't been run against live hardware yet — it's `py_compile`-clean
but untested. A couple of behaviours worth checking against how you actually want
your system to work:

- **Rooms with more than one TRV or sensor.** The original setup was always exactly
  one TRV and one sensor per room. The new schema allows several of each: room
  temperature is the **average** of all its sensors, and room setpoint is the
  **highest** setpoint among its TRVs (any one TRV wanting it warmer counts as
  demand). This is confirmed as the intended behaviour, not a placeholder — see
  `coordinator.py`'s `_room_setpoint`/`_room_temperature` methods if you want the
  detail.
- **If you configured any zones before 16 Aug 2026,** the switch field changed
  from a list (`switches: [...]`, supporting multiple switches per zone) to a
  single value (`switch: <entity_id>`) once it was confirmed a zone only ever has
  one heating actuator switch in practice. The old list key isn't read by the new
  code — reopen **Configure** and reassign each zone's switch after updating.
  (The heat-source and re-enable-delay fields added the same day are **not**
  breaking — an existing zone just behaves as ASHP-with-300s-delay until you
  reopen Configure and change it.)
- **First run drives real switches.** The moment this integration is reloaded with
  zones configured, the Coordinator evaluates and acts — it doesn't wait for you to
  press a "start" button. Test with dummy/spare TRVs and switches before pointing it
  at your actual heating actuators.

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
   - **Name the zone and pick its heating switch** — rename it to something
     meaningful (e.g. "Ground Floor") and select the single switch entity it
     should control.
   - **Heat source** — pick the option that matches this zone's heating
     equipment. Not sure? See
     [Heat sources and heating profiles](#heat-sources-and-heating-profiles)
     below, or just pick "Other / not sure" — it's editable later and only
     affects the suggested value on the next screen.
   - **Re-enable delay** — pre-filled with a suggested value based on the
     heat source you just picked. Change it to whatever you want; it's your
     equipment, not a fixed rule.
   - **Per room** — review the TRVs and temperature sensors already discovered for
     that Area, untick any that shouldn't count, and toggle the room off entirely
     if it shouldn't take part in heating demand (e.g. a guest room while empty).
   - Repeat for as many zones as you have, then choose **Done → Save**.
3. Reopen **Configure** at any time to add, edit, or remove zones and rooms — every
   field is pre-filled with your current configuration.

## Heat sources and heating profiles

Every heat source — ASHP, gas boiler, oil boiler — delivers heat to your
radiators differently, and that difference matters for one specific
setting: how long a zone's switch stays off before it's allowed to turn
back on (the **re-enable delay**), which exists to stop a zone rapidly
flicking on and off.

**Air source heat pump (ASHP).** Runs at much lower flow temperatures than
a boiler (commonly 30–45°C) and modulates its output continuously rather
than switching fully on or off — it's designed for long, steady, gentle
runs. Short-cycling is genuinely harmful here: it stresses the compressor
and can force wasteful defrost cycles. **Suggested delay: 300 seconds (5
minutes).**

**Modulating / condensing boiler (gas or oil).** Adjusts its output to
roughly match how much heat the building is losing, instead of firing at
one fixed temperature. No compressor to protect, but a condensing boiler's
efficiency is *higher* at lower return temperatures — so it still runs
better satisfying demand steadily rather than being kicked on and off
rapidly. **Suggested delay: 120 seconds.**

**Older non-condensing boiler (gas or oil).** Fires at a fixed, higher
flow temperature (often 70–80°C) and has no modulation — it's built to
switch fully on, satisfy the call for heat, and switch fully off. This is
just how it's meant to work; there's little to protect by delaying a
restart. **Suggested delay: 60 seconds.**

**Other / not sure.** Falls back to the original 300-second default.

These are **starting points**, not settings baked into the heat-source
choice — the actual delay is a plain editable number, pre-filled once when
you pick a heat source and left entirely up to you from then on. If your
specific equipment's manual gives a different minimum cycle time, use
that instead.

### Requirements

- Home Assistant with at least one **Area** defined (Settings → Areas & Zones) for
  each room you want to configure.
- A `switch` entity per zone to act as the heating actuator (e.g. a smart relay or
  your ASHP's zone valve control) — exactly one per zone.
- `climate` entities (TRVs) and `sensor` entities (temperature, `device_class:
  temperature`) assigned to the relevant Areas, so they can be auto-discovered.

## Roadmap

| Milestone | Status |
|---|---|
| 1. Skeleton integration, Config Flow, Store-backed data model | Done |
| 2. Coordinator — the actual control loop (reads TRVs/sensors, drives switches, per-zone editable re-enable delay suggested from heat source to prevent short-cycling, per-zone manual override switch, single-switch-per-zone schema) | Built, untested against real/dummy hardware |
| 3. Options Flow for zone/room/TRV/sensor management | Done |
| 4. Scheduling (day/time/setpoint grid), calendar-driven away mode, multi-TRV boost/propagation on manual overrides | Not started |
| 5. Polish — diagnostics sensor, translations, entity icons | Diagnostics sensor and brand icon done; rest pending |
| 6. HACS store submission, including the full ZEAL rename (domain, files, repo) with a migration path for existing installs | Pending |
| 7. Adaptive schedule suggestions (learns from manual boost history, notifies rather than auto-applies) | Post-v1, planned |

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

[MIT](LICENSE) — use it, modify it, redistribute it, no strings attached.
