# ASHP Zone Control — Project Definition

*Last updated: 15 August 2026, following the Options Flow build-out and a review of the original AppDaemon source (`ashp_controller.py`, `ashp_rooms.json`, `ashp_realtime_controller.py`).*

## 1. Purpose

Replace the current AppDaemon-based ASHP heating control (`ashp_controller.py`,
`heating_admin_api.py`, `ashp_rooms.json`) with a single, self-contained Home
Assistant **custom integration**, distributable via HACS, that lets a user
configure zones/rooms/TRVs/sensors and time-based setpoint schedules entirely
through the native HA UI — no external AppDaemon dependency, no standalone
webserver, no separate config-editing card to install.

## 2. Architecture decision

**One HACS entry: a native `custom_component` integration.**

Rejected alternatives and why:

| Option | Why not |
|---|---|
| AppDaemon app + custom Lovelace card + REST admin API | Three separate HACS artefacts to package/version; the admin API has no auth story of its own and needs one built from scratch |
| AppDaemon app alone, config via YAML | No way to safely edit config from a UI without either a bespoke webserver or hand-editing files on the host |
| Custom Lovelace card + generic backend | Still needs a backend of some kind; doesn't gain anything over a proper integration |

Building it as an integration means:

- **Auth is free.** Every write goes through HA's own service-call/websocket
  auth — no bearer tokens to invent, no `register_endpoint` exposed
  unauthenticated on the AppDaemon HTTP port.
- **Config storage is free.** Use `homeassistant.helpers.storage.Store`
  instead of hand-rolled JSON file I/O — HA handles atomic writes, versioning,
  and backup restore for you.
- **UI is (mostly) free.** Config Flow / Options Flow give native forms with
  `entity_selector`, `area_selector`, `device_selector` — exactly what's
  needed for "pick TRVs and sensors per room" without writing a custom card
  from scratch for that part.
- **One repo, one HACS category** (Integration) — simplest possible thing
  for a first-time contributor to install and for you to maintain.

## 3. Components

### 3.1 Backend (`custom_components/ashp_zone_control/`)

- **Config Flow** — initial setup: name the integration instance, optionally
  point at an existing `ashp_rooms.json` for one-time import (not yet built —
  see Milestones).
- **Options Flow** — the ongoing "admin" surface. **Built and working**, and
  more capable than originally scoped: a **Zone** is a user-named group of
  **Rooms** (e.g. "Ground Floor") with its own heating actuator switch(es);
  a **Room** *is* an HA Area assigned to exactly one Zone (an Area can only
  belong to one Zone at a time — assigning it to a new Zone removes it from
  wherever it was before). The flow:
  1. **Zone menu** — add / edit / remove a zone, or finish and save.
  2. **Pick rooms** — an `AreaSelector(multiple=True)` to choose which Areas
     belong to this zone.
  3. **Name & switches** — name the zone and pick its heating actuator
     switch(es) (single or multiple, e.g. Ground Floor Heating switch).
  4. **Per-room entities** — TRVs (`climate`) and temperature sensors
     (`sensor`, device_class `temperature`) are **auto-discovered** from the
     entity/device registry for that Area, pre-ticked, with a manual
     **active/inactive toggle per room** (see §5).
  - The success dialog renders a full Markdown summary of everything saved
    (Zone → switches → Room → active TRVs/sensors), using HA's built-in
    `create_entry` description mechanism (`options.create_entry.default` in
    `strings.json`, populated via `description_placeholders`).
- **Coordinator** (`DataUpdateCoordinator`) — **not yet built** (Milestone 2).
  Owns the control loop ported from `ashp_controller.py`: reads TRV
  setpoints vs sensor temps per active room, decides per-zone demand, drives
  the zone's switch entities.
- **Entities per zone (planned, Milestone 2):** a `switch` mirroring the
  zone's demand-driven control, a diagnostic `sensor` exposing current
  demand summary, and a **manual override `switch`** created by the
  integration itself (see §5 — Zone-level manual override).
- **Scheduling** — built in-house rather than depending on the HACS
  Scheduler Component, to honour the "single integration" goal: a stored
  list of `{zone_id, room_id|null, days, time, setpoint}` entries per config
  entry, applied via `homeassistant.helpers.event.async_track_time_change`.
  Still Milestone 4 — see §5 for how "away mode" now folds into this.

### 3.2 Frontend

- A **bundled custom Lovelace card** (registered as a frontend resource by
  the integration itself, same pattern as Scheduler Component's own card)
  for the parts Options Flow forms handle awkwardly — mainly the schedule
  grid (day × time × setpoint) and an at-a-glance zone/room overview.
- Everything else (picking entities, naming zones/rooms) uses native HA
  selectors inside Options Flow — built, and turned out to cover more than
  originally expected (area pickers, entity pickers scoped to a room's
  discovered entities, boolean toggles).
- **Brand icon** — built and installed. HA 2026.3+ lets custom integrations
  ship their own icon locally at `custom_components/ashp_zone_control/brand/icon.png`
  (+ `icon@2x.png`), served through HA's own `/api/brands/integration/`
  proxy — no submission to the external `home-assistant/brands` repo needed
  (that repo now auto-closes PRs for custom integrations and points here
  instead). Spec: square PNG, transparent, 256×256 (+512×512 hDPI),
  trimmed to the subject.

### 3.3 Data model (current, as implemented)

```text
zones: [
  {
    zone_id:  <uuid>,
    name:     <str>,                 # e.g. "Ground Floor" — user-set, not tied to an Area name
    switches: [<entity_id>, ...],    # heating actuator switch(es) for this zone
    rooms: [
      {
        room_id:  <HA Area id>,      # a Room IS an Area; no separate id needed
        name:     <str>,             # kept in sync with the Area's name
        trvs:     [<entity_id>, ...],
        sensors:  [<entity_id>, ...],
        active:   <bool>,            # default true — see §5, ported from ashp_rooms.json
      },
      ...
    ],
  },
  ...
]
```

Stored via `config_entry.options` (the Options Flow's source of truth,
auto-persisted by HA, triggers an automatic reload on every save) **and**
mirrored into a `Store` on every `async_setup_entry` call. The split is
deliberate: `entry.options` stays "what the user configured"; `Store` is
where the Coordinator will keep its own runtime state (last switch state,
re-enable timers) without writing back into config options, which would
trigger the update listener and reload in a loop.

Dropped from the original `ashp_rooms.json` schema: `hysteresis` (dead code
— see §5), `open_threshold_delta` (dead code, dropped). `area_id` at the
zone level is gone too — a zone can now span multiple Areas, so it no
longer maps 1:1 to one.

## 4. Migration path

| Current | Becomes |
|---|---|
| `ashp_rooms.json` | One-time import in Config Flow (not yet built), then lives in `config_entry.options` / `Store` |
| `ashp_controller.py` control loop (`evaluate_floor`/`set_switch`) | To be ported into the Coordinator (Milestone 2) — see §5 for the hysteresis correction |
| `heating_admin_api.py` | Deleted — replaced by Options Flow + Store, no custom endpoint needed |
| `ashp_realtime_controller.py` (Vaillant holiday mode via weather forecast) | **Out of scope for v1** — stays as a standalone AppDaemon app for now; revisit folding in once the core integration is stable |
| Per-floor `input_boolean.<floor>_heating_override` | To be replaced by an integration-created `switch` entity per zone (Milestone 2) — see §5 |
| `ashp_rooms.json`'s `away_mode.temperature` | Redesigned as a calendar-driven override inside the Milestone 4 scheduler — see §5 |

## 5. Decisions Log

Resolving the "Open items blocking final schema" from the original doc,
after reading the actual source files (`ashp_controller.py`, `ashp_rooms.json`,
`ashp_realtime_controller.py`) rather than guessing at their behaviour.

### Hysteresis — RESOLVED
**Finding:** `ashp_rooms.json` still has a `hysteresis: 0.5` field per floor,
but it's dead. `ashp_controller.py`'s own changelog says: *"20th Sept 2025:
Removed hysteresis, added 5-min re-enable delay via `last_off_time` dict."*
The code that ships today never reads `hysteresis`.

**Decision:** The Coordinator (Milestone 2) will port the **re-enable
delay** mechanism instead — after a zone switch turns OFF, it won't turn
back ON until `reenable_delay` (default 300s) has elapsed, tracked
independently per zone and persisted across restarts (the old code used an
`input_number` helper; the new Coordinator will use the `Store`). This is
what's actually been protecting the pump from short-cycling in production,
not the unused hysteresis field.

### `open_threshold_delta` — RESOLVED
**Finding:** Present per-floor in `ashp_rooms.json` (`-2` on ground floor,
`0.5` on first floor) but never referenced in either script.

**Decision:** Dropped from the schema entirely. Nothing was lost — it
wasn't doing anything.

### `away_mode.temperature` — RESOLVED (redesigned)
**Finding:** A top-level `{"away_mode": {"temperature": 12}}` block in
`ashp_rooms.json`, never read by either script. Also: neither script writes
TRV setpoints at all — `ashp_controller.py` only *reads* each TRV's current
setpoint to compare against the room sensor.

**Decision:** Away/holiday mode will be built as part of Milestone 4
(Scheduling), not as a standalone feature now, because both need the same
new capability: writing a setpoint to a TRV (`climate.set_temperature`).
Design: bind to a native HA `calendar` entity (Google Calendar, a Local
Calendar, CalDAV — whatever the user already has) rather than building a
custom holiday date-range picker. When the calendar entity's state is `on`
(an event is in progress), the scheduler forces every active room's setpoint
to a single **global** away-setpoint (default 12°C), overriding whatever the
time-based schedule would otherwise set. Scope is global (one calendar, one
setpoint, whole house) rather than per-zone, since a holiday away-mode
naturally applies to the whole house.

### Room-level active/inactive toggle — RESOLVED (implemented)
**Finding:** Every room in `ashp_rooms.json` has `"active": true/false`,
and `evaluate_floor()` skips any room where it's `false` (Ensuite and
Bathroom are switched off this way today). This is a manual toggle, not
tied to any occupancy sensor.

**Decision:** Added `active` (bool, default `true`) to the room schema and
to the Options Flow's per-room entity-picker step, before the Coordinator
exists — cheap to add now, expensive to retrofit once entities depend on
the schema. Matches the "guest room not occupied" use case directly: flip
the toggle off, the room (and its TRV/sensor) is skipped entirely and never
triggers its zone's heating switch.

### Zone-level manual override — NEW, DEFERRED to Milestone 2
**Finding:** The old code checks `input_boolean.<floor>_heating_override`
before acting — if it's `on`, that floor's automatic control is skipped
entirely ("hands-off"). This has no equivalent anywhere in the new schema.

**Decision:** Rather than requiring users to hand-create an `input_boolean`
helper (the old, manual-setup-required approach), the Coordinator will
**create its own override `switch` entity per zone** (e.g.
`switch.ground_floor_heating_override`) as part of its Milestone 2 entity
set. Works out of the box for new installs, no manual HA helper setup
required.

### Landing/Bathroom duplicate entity assignment — RESOLVED (moot)
**Finding:** In `ashp_rooms.json`, both "Bathroom" and "Landing" point at
the exact same TRV and sensor (`climate.bathroom_trv` /
`sensor.bathroom_trv_air_temperature`) — Landing has no entities of its
own. Both are currently `active: false`, so it's dormant, but wrong.

**Decision:** No code fix needed. Since Rooms are now HA Areas rather than
freeform JSON entries, assigning Landing to a zone in the new Options Flow
will prompt for Landing's *own* discovered TRV/sensor — the copy-paste bug
can't recur in the new model. (Still worth pointing an actual TRV/sensor at
the Landing Area in HA if/when it's switched active.)

### Options Flow architecture — implemented, changed from original plan
The original plan had the Options Flow simply toggle HA Areas on/off to
directly become Zones (1:1). In practice this didn't match how the house is
actually organised (a "Ground Floor" zone spanning several rooms/Areas), so
it was redesigned mid-build to the zone-first, area-scoped-rooms model
described in §3.1/§3.3. Also fixed along the way: the original per-Area
`BooleanSelector` fields (`zone_<area_id>`) had no static translation key
(the key is different per HA install), so HA fell back to showing the raw
field name instead of the Area's friendly name — replaced with a single
`AreaSelector(multiple=True)` field, which renders proper names natively.

### Dev note: translation/cache staleness during iteration
Editing `strings.json`/`translations/en.json` is **not** picked up by
reloading the config entry alone — HA (and the browser) cache a custom
integration's translations. Symptoms: `formatjs` `MISSING_VALUE` errors
referencing old placeholder names, or raw `"Options"` titles with no
context. Fix: bump `manifest.json`'s `version` on every strings change, do
a full HA Core restart (not just "Reload"), and hard-refresh the browser
tab (Ctrl+Shift+R) afterwards. This is also HA's own documented advice for
testing translation changes.

## 6. Testing plan

- New Proxmox HA host, dummy/old TRVs and spare temperature sensors —
  confirmed available now.
- Phase testing order: (1) Config Flow import of existing JSON, (2)
  Coordinator control loop against dummy TRVs with fast scan interval,
  (3) Options Flow zone/room editing round-trip, (4) schedule firing
  correctness across a simulated day, (5) HACS install-from-repo smoke test
  on a clean HA instance.

## 7. Milestones

1. ~~Skeleton integration: manifest, Config Flow (import-only), Store-backed
   data model, no control logic yet.~~ **Done**, except the one-time
   `ashp_rooms.json` import step (still pending — see Migration path).
2. Port Coordinator control loop from `ashp_controller.py` — **using the
   re-enable delay, not hysteresis** (§5); create the per-zone override
   switch (§5); validate against dummy TRVs on the Proxmox rig. **Not
   started.**
3. ~~Options Flow for zone/room/TRV/sensor management.~~ **Done** — built
   ahead of Milestone 2, and more capable than originally scoped (see
   §3.1/§3.3).
4. Scheduling: data model + `async_track_time_change` firing + bundled card
   for the schedule grid, **including the calendar-driven away-mode
   override** (§5). **Not started.**
5. Polish: diagnostics sensor, entity naming/icons, translations
   scaffolding (`strings.json`) for HACS/community readiness. Brand icon
   already done ahead of schedule (§3.2).
6. HACS submission prep: `hacs.json`, README, brands submission if desired
   (note: brand *images* no longer need a separate submission — see §3.2).

## 8. Explicitly out of scope for v1

- Folding in `ashp_realtime_controller.py` / Vaillant holiday-mode logic
  (the *forecast-driven* Vaillant `mypyllant` holiday service calls — not
  to be confused with the new calendar-driven away-mode in §5, which is a
  different, simpler feature: forcing TRV setpoints down, not calling the
  Vaillant cloud API).
- Any dependency on the external Scheduler Component.
- Multi-user permission tiers beyond what HA's own auth already provides.
