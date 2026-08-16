# ZEAL — Zoned, Efficient, Adaptive, Learning heating control — Project Definition

*Last updated: 16 August 2026. Supersedes both the original ASHP-specific
definition doc and `PROJECT_MANDATE.md` — this is now the single canonical
source of truth. Codebase still lives under the `ashp_zone_control` domain
and filenames pending the ZEAL rename (see §1.1) — do not rename files or
the domain until that pass happens deliberately, to avoid an unreviewed
partial rename landing mid-flight.*

## 1. Purpose

Replace the previous AppDaemon-based ASHP heating control (`ashp_controller.py`,
`heating_admin_api.py`, `ashp_rooms.json`) with a single, self-contained Home
Assistant **custom integration**, distributable via HACS, that lets a user
configure zones/rooms/TRVs/sensors and time-based setpoint schedules entirely
through the native HA UI — no external AppDaemon dependency, no standalone
webserver, no separate config-editing card to install.

### 1.1 Project name: ZEAL

**Zoned, Efficient, Adaptive, Learning.** Chosen to reflect the project's
scope beyond a single heat source — zoned control, multi-fuel efficiency,
adaptive boost behaviour, and the learning/schedule-suggestion direction in
§9 — while remaining distinct from existing HACS projects in this space
(`zoned-heating`, `zonal-heating`, `multizone_generic_thermostat`,
`versatile_thermostat`). Checked against GitHub/HACS/HA community search on
2026-08-16 with no name clash found; worth a repeat check immediately before
HACS submission (§7 milestone 6) in case of newer entrants.
`jmcollin78/versatile_thermostat` is not a naming clash but is the closest
conceptual neighbour (multi-heat-source, adaptive, learning-capable,
actively maintained, 1,000+ stars) and worth reviewing for feature
differentiation ahead of a public release.

**Rename status: repo done, domain/files/classes still pending.** The
GitHub repo is renamed to `Buster1959/ZEAL` (was
`Buster1959/Heating_Controller`) — `manifest.json`'s `documentation`/
`issue_tracker` URLs updated to match. Domain (`ashp_zone_control`),
manifest name ("ASHP Zone Control"), and class names
(`AshpZoneCoordinator`, `AshpZoneOverrideSwitch`, `AshpZoneDemandSensor`)
still reflect the old name — deliberately not yet touched. Agreed
approach: keep working against the current filenames/domain/class names as
they stand in GitHub for now; the remaining rename (files, domain string,
config entry migration for anyone who's already set up an instance)
happens as one deliberate pass, not gradually — a config entry's domain is
part of its stored identity in HA, so renaming the domain string requires a
migration path for existing installs, not just a find-and-replace.

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

- **Config Flow — built and done.** Names the integration instance only.
  A one-time `ashp_rooms.json` import step was originally planned but is
  now **explicitly out of scope, not deferred** — it would only ever be
  useful to the one existing install migrating off the old AppDaemon
  setup, not to anyone installing this from HACS. That migration is done
  by hand through the (already-built, already-tested) Options Flow
  instead — see §4.
- **Options Flow — built and working**, and more capable than originally
  scoped. A **Zone** is a user-named group of **Rooms** (e.g. "Ground
  Floor") with its own single heating actuator switch; a **Room** *is* an
  HA Area assigned to exactly one Zone (an Area can only belong to one Zone
  at a time — assigning it to a new Zone removes it from wherever it was
  before). The flow:
  1. **Zone menu** — add / edit / remove a zone, or finish and save.
  2. **Pick rooms** — an `AreaSelector(multiple=True)` to choose which
     Areas belong to this zone.
  3. **Name & switch** — name the zone and pick its single heating actuator
     switch.
  4. **Heat source** — pick ASHP / modulating (condensing) boiler /
     non-condensing boiler / other. Affects only the suggested value in
     the next step, not anything the demand-evaluation logic does — see
     Decisions Log (§5).
  5. **Re-enable delay** — pre-filled with the suggested delay for the
     heat source just picked, but a plain editable number from then on.
  6. **Per-room entities** — TRVs (`climate`) and temperature sensors
     (`sensor`, device_class `temperature`) are **auto-discovered** from
     the entity/device registry for that Area, pre-ticked, with a manual
     **active/inactive toggle per room** (see §5).
  - The success dialog renders a full Markdown summary of everything saved
    (Zone → switch → heat source → re-enable delay → Room → active
    TRVs/sensors), using HA's built-in `create_entry` description
    mechanism.
- **Coordinator** (`DataUpdateCoordinator`) — **built**, `py_compile`-clean,
  **not yet tested against live/dummy TRVs** (first real test is on the
  Proxmox rig). Ports the control loop from `ashp_controller.py`: reads TRV
  setpoints vs sensor temps per active room, decides per-zone demand,
  drives the zone's single switch entity, subject to a re-enable delay and
  the zone's manual override switch.
- **Zone → single switch, always.** Confirmed against real installs: a
  shared single-pump house split into ground-floor/first-floor zones, a
  dual-pump house with one switch per zone, a hotel with one switch per
  level. A zone never has more than one switch — the schema and Coordinator
  logic reflect this directly (`ZONE_SWITCH` holds one entity_id, not a
  list), rather than modelling a multi-switch-per-zone case that doesn't
  occur in practice.
- **Entities created per zone — built:** a diagnostic `sensor`
  (`AshpZoneDemandSensor`) exposing current demand summary, replacing the
  old `input_text.<floor>_demanding_rooms` helper; and a manual override
  `switch` (`AshpZoneOverrideSwitch`), replacing the old hand-created
  `input_boolean.<floor>_heating_override` helper — the integration creates
  it itself per zone, no manual HA helper setup required for new installs.
- **Multi-TRV/multi-sensor rooms — built.** The old schema was always
  exactly one TRV and one sensor per room; the new schema allows several of
  each. Room temperature = **average** of all active sensors in the room
  (reduces single-sensor noise); room setpoint = **highest** setpoint among
  the room's TRVs (any one TRV wanting it warmer makes the room
  "demanding"). Flagged in the Decisions Log (§5) as a reasonable default
  needing sign-off, not a verified requirement — **now signed off** per
  conversation.
- **Multi-TRV rooms — boost propagation (Milestone 4, not yet built).**
  Where a room has more than one TRV, an unexpected setpoint change on any
  one TRV (increase or decrease, treated symmetrically) will be interpreted
  as new demand for the whole room:
  - The new setpoint propagates to all other TRVs in the room.
  - The room enters a boost state for 2 hours from the time of the
    triggering change.
  - A further unexpected setpoint change on *any* TRV in the room while a
    boost is active resets the 2-hour window from that point, and updates
    which TRV is recorded as the trigger.
  - At boost expiry, the Coordinator does **not** revert to a pre-boost
    snapshot. It recomputes the room's setpoint from the active schedule as
    it stands at expiry time (the same lookup used on every normal
    Coordinator tick) and applies that to all TRVs in the room. This avoids
    silently un-applying a schedule change that should have taken effect
    during the boost window.
- **Self-write loop guard (Milestone 4, not yet built).** Every setpoint
  the Coordinator writes to a TRV (via boost propagation, schedule
  application, or boost-expiry revert) will be recorded in an in-memory map
  of last-written values per entity. The state listener that detects
  "unexpected" TRV changes checks incoming state against this map first; a
  change matching the last value the Coordinator itself wrote is not
  treated as demand. Global map, not per-room, since it needs to catch any
  Coordinator-initiated write regardless of trigger.
- **Anti-hunting: re-enable delay, not hysteresis — built.**
  `ashp_controller.py`'s own changelog: hysteresis was removed in favour of
  a 5-minute re-enable delay via a `last_off_time` dict, well before this
  rewrite started. The Coordinator ports the re-enable delay (a zone switch
  that just turned OFF won't turn back ON for `DEFAULT_REENABLE_DELAY`,
  default 300s), tracked per zone and persisted across restarts via
  `Store`. `hysteresis` and `open_threshold_delta` were both dead fields in
  the old `ashp_rooms.json` and are dropped from the schema entirely.
- **Scheduling — not yet built (Milestone 4).** Built in-house rather than
  depending on the HACS Scheduler Component, to honour the "single
  integration" goal: a stored list of `{zone_id, room_id|null, days, time,
  setpoint}` entries per config entry, applied via
  `homeassistant.helpers.event.async_track_time_change`. Away/holiday mode
  folds into this milestone rather than being built standalone, since both
  need the same new capability (writing a setpoint to a TRV via
  `climate.set_temperature`) — see §5.

### 3.2 Frontend

- A **bundled custom Lovelace card** (not yet built) for the parts Options
  Flow forms handle awkwardly — mainly the schedule grid (day × time ×
  setpoint) and an at-a-glance zone/room overview. Registered as a frontend
  resource by the integration itself, same pattern as Scheduler
  Component's own card.
- Everything else (picking entities, naming zones/rooms) uses native HA
  selectors inside Options Flow — **built**, and turned out to cover more
  than originally expected (area pickers, entity pickers scoped to a
  room's discovered entities, boolean toggles).
- **Brand icon — built and installed**, ahead of schedule. HA 2026.3+ lets
  custom integrations ship their own icon locally at
  `custom_components/ashp_zone_control/brand/icon.png` (+ `icon@2x.png`),
  served through HA's own `/api/brands/integration/` proxy — no submission
  to the external `home-assistant/brands` repo needed. Spec: square PNG,
  transparent, 256×256 (+512×512 hDPI), trimmed to the subject.

### 3.3 Data model (current, as implemented)

```text
zones: [
  {
    zone_id:         <uuid>,
    name:            <str>,              # e.g. "Ground Floor" — user-set, not tied to an Area name
    switch:          <entity_id> | null, # exactly ONE heating actuator switch for this zone — never more
    heat_source:     <enum>,             # "ashp" | "modulating_boiler" | "non_condensing_boiler" | "other"
    reenable_delay:  <int>,              # seconds; user-editable, pre-filled from heat_source's suggested default
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
where the Coordinator keeps its own runtime state (last-off timestamps for
the re-enable delay) without writing back into config options, which would
trigger the update listener and reload in a loop.

**Migration note (switch field, breaking):** the switch field changed from
`switches: [<entity_id>, ...]` to `switch: <entity_id> | null` on
2026-08-16 to match the confirmed single-switch-per-zone reality. Any zone
already configured through the Options Flow before this change was saved
under the old key and will need its switch reassigned once the new code is
dropped in — the old list key is not read by the new code.

**Migration note (heat_source/reenable_delay, non-breaking):** both fields
added 2026-08-16, pulled forward from the future §9.2 design rather than
left theoretical, once it became clear `reenable_delay` genuinely needs to
differ by heat source *today*, not just for the later thermal-learning
feature (the 5-minute default was tuned for an ASHP specifically — see
Decisions Log). Any zone saved before this change simply falls back to
`HEAT_SOURCE_ASHP`'s suggested delay via `.get()` defaults in the
Coordinator and Options Flow — no reassignment required, unlike the switch
field above.

Dropped from the original `ashp_rooms.json` schema: `hysteresis` (dead
code — see §5), `open_threshold_delta` (dead code, dropped). `area_id` at
the zone level is gone too — a zone can now span multiple Areas, so it no
longer maps 1:1 to one.

Planned additions (Milestone 4, boost mechanic — not yet in the schema):

```
# per room
boost_active: bool
boost_started_at: timestamp | null
boost_source_trv: entity_id | null

# Coordinator in-memory only, not persisted to Store
last_written_setpoint: { entity_id: float }
```

## 4. Migration path

| Current | Becomes |
|---|---|
| `ashp_rooms.json` | No automated import (out of scope — see §3.1). Zones/rooms/TRVs/sensors recreated by hand through the Options Flow, a one-time manual step for the single existing install. |
| `ashp_controller.py` control loop (`evaluate_floor`/`set_switch`) | Ported into the Coordinator — **built**, using the re-enable delay correction (§5) |
| `heating_admin_api.py` | Deleted — replaced by Options Flow + Store, no custom endpoint needed |
| `ashp_realtime_controller.py` (Vaillant holiday mode via weather forecast) | **Out of scope for v1** — stays as a standalone AppDaemon app for now; revisit folding in once the core integration is stable |
| Per-floor `input_boolean.<floor>_heating_override` | Replaced by an integration-created `switch` entity per zone — **built** |
| `ashp_rooms.json`'s `away_mode.temperature` | Redesigned as a calendar-driven override inside the Milestone 4 scheduler — see §5 |

## 5. Decisions Log

### Hysteresis — RESOLVED
**Finding:** `ashp_rooms.json` still has a `hysteresis: 0.5` field per
floor, but it's dead. `ashp_controller.py`'s own changelog: *"20th Sept
2025: Removed hysteresis, added 5-min re-enable delay via `last_off_time`
dict."* The shipped code never reads `hysteresis`.

**Decision:** Port the re-enable delay instead. **Built** — after a zone
switch turns OFF, it won't turn back ON until `reenable_delay` (default
300s) has elapsed, tracked independently per zone and persisted across
restarts via `Store` (the old code used an `input_number` helper).

### `open_threshold_delta` — RESOLVED
**Finding:** Present per-floor in `ashp_rooms.json` but never referenced
in either script.

**Decision:** Dropped from the schema entirely. Nothing was lost.

### `away_mode.temperature` — RESOLVED (redesigned)
**Finding:** A top-level `{"away_mode": {"temperature": 12}}` block in
`ashp_rooms.json`, never read by either script. Also: neither script writes
TRV setpoints at all — `ashp_controller.py` only *reads* each TRV's current
setpoint to compare against the room sensor.

**Decision:** Away/holiday mode is built as part of Milestone 4
(Scheduling), not standalone, because both need the same new capability:
writing a setpoint to a TRV (`climate.set_temperature`). Design: bind to a
native HA `calendar` entity (Google Calendar, Local Calendar, CalDAV —
whatever the user already has) rather than a custom holiday date-range
picker. When the calendar entity's state is `on`, the scheduler forces
every active room's setpoint to a single **global** away-setpoint (default
12°C), overriding the time-based schedule. Scope is global rather than
per-zone, since a holiday away-mode naturally applies to the whole house.

### Room-level active/inactive toggle — RESOLVED (implemented)
**Finding:** Every room in `ashp_rooms.json` has `"active": true/false`,
and `evaluate_floor()` skips any room where it's `false`. Manual toggle,
not tied to occupancy.

**Decision:** Added `active` (bool, default `true`) to the room schema and
the Options Flow's per-room entity-picker step. **Built.**

### Zone-level manual override — RESOLVED (implemented)
**Finding:** The old code checks `input_boolean.<floor>_heating_override`
before acting — if `on`, that floor's automatic control is skipped
entirely.

**Decision:** The Coordinator creates its own override `switch` entity per
zone rather than requiring a hand-created `input_boolean`. **Built** —
`AshpZoneOverrideSwitch` registers itself into
`coordinator.override_switches[zone_id]` on `async_added_to_hass()`, and
the Coordinator checks the live entity object's `.is_on` directly rather
than a `hass.states.get()` lookup against a guessed entity_id, avoiding
string-matching fragility between the switch platform and the coordinator.

### Zone → single switch, never multiple — RESOLVED (implemented)
**Finding:** The schema and Coordinator originally allowed a list of
switches per zone (`switches: []`), built as a defensive generalisation
without a confirmed real-world case for it.

**Decision:** Confirmed against real installs there is never more than one
switch per zone: a shared single-pump house split into ground/first-floor
zones, a dual-pump house with one switch per zone, a hotel with one switch
per level. Changed `switches: [...]` to `switch: <entity_id> | null`
throughout — `const.py`, the Options Flow's switch selector (now
single-select), and the Coordinator's switch-driving logic (one lookup, one
on/off call, no per-switch loop). Removes a foot-gun (accidentally
selecting two switches for one zone) and matches reality exactly rather
than modelling a case that can't occur. **Built** — see the migration note
in §3.3 for the config_entry.options key change this implies for any
already-configured zone.

### Landing/Bathroom duplicate entity assignment — RESOLVED (moot)
**Finding:** In `ashp_rooms.json`, "Bathroom" and "Landing" pointed at the
exact same TRV and sensor — Landing had no entities of its own. Both were
`active: false`, so dormant but wrong.

**Decision:** No code fix needed. Since Rooms are now HA Areas rather than
freeform JSON entries, assigning Landing to a zone in the Options Flow
prompts for Landing's *own* discovered TRV/sensor — the copy-paste bug
can't recur in the new model.

### Options Flow architecture — implemented, changed from original plan
The original plan had the Options Flow simply toggle HA Areas on/off to
directly become Zones (1:1). In practice this didn't match how a house is
actually organised (a "Ground Floor" zone spanning several rooms/Areas), so
it was redesigned mid-build to the zone-first, area-scoped-rooms model in
§3.1/§3.3. Also fixed along the way: the original per-Area `BooleanSelector`
fields (`zone_<area_id>`) had no static translation key (different per HA
install), so HA fell back to raw field names instead of Area friendly
names — replaced with a single `AreaSelector(multiple=True)` field.

### Dev note: translation/cache staleness during iteration
Editing `strings.json`/`translations/en.json` is **not** picked up by
reloading the config entry alone — HA and the browser cache a custom
integration's translations. Symptoms: `formatjs` `MISSING_VALUE` errors, or
raw `"Options"` titles with no context. Fix: bump `manifest.json`'s
`version` on every strings change, do a full HA Core restart (not just
"Reload"), and hard-refresh the browser tab afterwards — this is also HA's
own documented advice for testing translation changes.

### Heat source pulled forward from §9.2 into the core schema — RESOLVED (implemented)
**Finding:** `DEFAULT_REENABLE_DELAY` (5 minutes) was a single global
constant, copied straight from `ashp_controller.py` — which was tuned
specifically for an ASHP's compressor-protection needs. `heat_source` had
only been designed as a future field for §9.2's thermal-learning
regression, not something the core control loop used.

**Decision:** Different heat sources genuinely warrant different re-enable
delays *today*, not just for the future preheat model — an ASHP benefits
from long, steady runs (compressor wear, defrost-cycle efficiency); a
modulating/condensing boiler has no compressor to protect but still favours
steadier running for condensing efficiency; an older non-condensing boiler
is designed to cycle on/off and isn't meaningfully harmed by a short delay.
Added `heat_source` (enum: `ashp` / `modulating_boiler` /
`non_condensing_boiler` / `other`) and `reenable_delay` (int, seconds) as
per-zone fields, with a suggested-default lookup table
(`HEAT_SOURCE_DEFAULT_REENABLE_DELAY`) that pre-fills the delay field based
on the heat source picked, but the delay remains a plain user-editable
number from then on — a suggestion, not a locked consequence of the
heat_source choice. Two new Options Flow steps
(`zone_heat_source`, `zone_reenable_delay`) inserted between "name & switch"
and the per-room entity picker. **Built** and compile-clean.

Deliberately **not** built now: any actual selector for
"multi-fuel"/heat-source at the *level of what the Coordinator's control
logic does* beyond the re-enable delay — the demand evaluation itself
(TRV setpoint vs. sensor reading) remains completely heat-source-agnostic,
as it should. `flow_temp_source` and the thermal-learning regression that
would actually use `heat_source` for more than a delay lookup are still
squarely §9.2/Milestone 7, unbuilt. This addition is scoped tightly to what
directly improves Milestone 2's anti-short-cycling behaviour today, not a
backdoor implementation of §9.2.

Backward compatible, unlike the switch-field rename above: both new fields
are read with `.get(..., default)` throughout, so a zone saved before this
change simply behaves as if it were `heat_source: ashp` with the default
300s delay — no forced reconfiguration.

### Coordinator `off_time_changed` Store-write bug — RESOLVED (fixed)
**Finding:** `_async_update_data` checked `if zone_id in
self._last_off_time` to decide whether to persist runtime state to
`Store` — this is dict *membership*, not *change*. Once any zone had ever
turned off, this was `True` on every single poll forever, writing to
`Store` every scan interval (default 60s) indefinitely rather than only
when a zone's off-time actually changed on that pass.

**Decision:** `_async_apply_zone_switches` now returns `(available,
off_time_changed)`, where `off_time_changed` is only `True` if this
specific call newly recorded an OFF transition. `_async_update_data` only
persists to `Store` when at least one zone's off-time changed on this
pass. **Fixed** and verified compile-clean.

### Coordinator built (Milestone 2) — multi-TRV/sensor aggregation
**Finding/Decision:** Documented above in §3.1. Room temperature = average
of active sensors; room setpoint = highest TRV setpoint. Flagged as
needing sign-off — **now signed off**.

**Not yet tested against real or dummy TRVs** — built and `py_compile`-
clean, but the sandbox this was built in can't run Home Assistant itself.
First real test is on the Proxmox rig.

## 6. Testing plan

- New Proxmox HA host, dummy/old TRVs and spare temperature sensors —
  confirmed available.
- Phase testing order: (1) Options Flow zone/room/TRV/sensor round-trip —
  manually recreate the current live setup as the first real-world config,
  (2) Coordinator control loop against dummy TRVs with fast scan interval,
  (3) schedule firing correctness across a simulated day (once Milestone 4
  lands), (4) HACS install-from-repo smoke test on a clean HA instance.
- **Note:** any zone configured through the Options Flow before the
  single-switch schema change (§5) will need its switch reassigned after
  the updated files are dropped in — the old `switches` list key won't be
  read by the new code.

## 7. Milestones

1. ~~Skeleton integration: manifest, Config Flow, Store-backed data model,
   no control logic yet.~~ **Done.** (The one-time `ashp_rooms.json` import
   originally planned here was dropped as out of scope — see §3.1/§4; it
   would only ever help the one existing install, not a public one.)
2. Port Coordinator control loop from `ashp_controller.py` — using the
   re-enable delay, not hysteresis (§5); create the per-zone override
   switch (§5); single-switch-per-zone schema (§5). **Built,
   `py_compile`-clean, Store-write bug fixed, not yet tested against
   live/dummy TRVs.**
3. ~~Options Flow for zone/room/TRV/sensor management.~~ **Done** — built
   ahead of Milestone 2, more capable than originally scoped.
4. Scheduling: data model + `async_track_time_change` firing + bundled card
   for the schedule grid, including the calendar-driven away-mode override
   (§5), and the multi-TRV boost/propagation mechanic (§3.1). **Not
   started.**
5. Polish: diagnostics sensor, entity naming/icons, translations
   scaffolding for HACS/community readiness. Diagnostics sensor and brand
   icon **done** ahead of schedule.
6. HACS submission prep: `hacs.json`, README, brands submission if desired
   (brand images no longer need a separate submission — see §3.2). Repeat
   the ZEAL naming clash-check (§1.1) immediately before this milestone.
   **Includes the remaining ZEAL rename** (domain, files, class names — repo
   already renamed to `Buster1959/ZEAL`) as one deliberate pass with a
   config-entry migration path for any existing install — see §1.1.
7. **Adaptive schedule suggestions** (post-v1, revisit after ≥8 weeks of
   production boost history exists to learn from): boost-history logging
   with capped retention, pattern detection (≥3 occurrences, same
   room/day-of-week/time-window), actionable notification via
   `notify.mobile_app_*`, Accept → Store write, Dismiss → cooldown
   suppression. See §9.1.

## 8. Explicitly out of scope for v1

- Folding in `ashp_realtime_controller.py` / Vaillant holiday-mode logic
  (the *forecast-driven* Vaillant `mypyllant` holiday service calls — not
  to be confused with the calendar-driven away-mode in §5, which is
  simpler: forcing TRV setpoints down, not calling the Vaillant cloud API).
- Any dependency on the external Scheduler Component.
- Multi-user permission tiers beyond what HA's own auth already provides.
- Thermal-lag preheat learning (§9.2) — no notification story here since
  this isn't a discrete accept/reject decision, it's a continuous
  background model; would need its own design pass if picked up later.

*(Auto-schedule suggestion is not out of scope — promoted to tracked
milestone 7 above, sequenced after v1 ships.)*

## 9. Future direction: adaptive schedule/temperature learning

Idea: similar to Nest's Auto-Schedule and Time-to-Temperature, use the
control history the Coordinator is already generating to reduce manual
intervention over time. Two genuinely different sub-features, kept
separate deliberately:

### 9.1 Auto-schedule suggestion, notification-driven (tracked — milestone 7)

Mechanism: HA's built-in `persistent_notification` / mobile app
notification (`notify.mobile_app_*`) rather than anything bespoke — free
UI, works in both the web frontend and companion app, no new permissions
model.

- Coordinator logs each boost event (room, day-of-week, time, resulting
  setpoint) to a rolling history — capped window (e.g. last 8 weeks)
  rather than unbounded, since `Store` isn't a time-series store.
- When the same room shows a manual boost within a similar time window on
  the same day-of-week on ≥3 occasions, fire a notification: *"Living Room
  is often boosted to 21°C around 6pm on weekdays — add this to the
  schedule?"* with actionable Accept/Dismiss buttons.
- Accept writes the entry into that room's `schedule: []` via the same
  Store write path Options Flow uses.
- Dismiss suppresses that specific suggestion for a cooldown period rather
  than re-prompting every time the pattern repeats.

Firmly suggest-then-confirm — nothing is written to the schedule without
the explicit Accept tap.

### 9.2 Thermal-lag preheat (not tracked — revisit only after 9.1 ships and proves out)

Learn each room's heating rate so the Coordinator starts heating *before*
a scheduled setpoint time and hits target *at* that time, rather than
starting cold at the scheduled moment. More novel and more valuable than
9.1, and also more complex — no discrete accept/reject UX fits this the
way it does 9.1, since it's a continuous background adjustment.

**Outdoor temp must be a first-class input, not an afterthought.** ASHPs
run much lower flow temperatures than a gas combi, so a room's achievable
heat-up rate is heavily gated by outdoor temp — on a cold day the system
can push less delta-T, so preheat needs to start earlier.

**Flow temperature, where available, is a better input than outdoor temp
alone.** Outdoor temp only matters as a proxy for how much delta-T the
heat source can push on its weather-compensation curve — actual flow
temperature is the more proximate driver. Where a Modbus connection or a
flow-temp sensor is available (e.g. Vaillant Arotherm via Modbus), the
regression should prefer flow temp over outdoor temp. Outdoor temp remains
the fallback for installs without that telemetry.

**Multi-fuel: heat source and available telemetry must be
per-installation config, not assumed.** Since this ships as open source,
not every install is an ASHP, and not every ASHP install has Modbus/flow-
temp access.

**Reconciliation needed before this milestone starts:** a per-zone
`heat_source` field already exists and is built (§3.3, §5's "Heat source
pulled forward" entry) — but it categorises by *heating profile*
(`ashp` / `modulating_boiler` / `non_condensing_boiler` / `other`, chosen
because that's what the re-enable delay actually depends on), not by
*fuel type* as originally sketched here (`ashp, gas, oil, pellet, other`).
The two aren't the same axis — a modern condensing oil boiler and a
condensing gas boiler behave alike (both `modulating_boiler`) despite
different fuel, while an old and a new gas boiler behave very differently
despite identical fuel. Before building the regression, decide whether
§9.2 reuses the existing profile-based enum as-is (likely the right call —
it's closer to what actually predicts thermal behaviour) or needs its own
separate fuel-type field alongside it. Whichever is chosen, the two
independent settings needed are:

```
heat_source: <the existing per-zone enum, reused> — or a new fuel-type field, TBD above
flow_temp_source: enum [none, entity_sensor, modbus] (+ associated entity/connection config)
```

The regression uses whichever inputs are actually available: flow temp if
`flow_temp_source` is configured (regardless of heat source); otherwise
outdoor temp where the heat source has a meaningful outdoor correlation
(ASHP, weather-compensated boilers); otherwise a plain time-to-target model
with no external variable (e.g. a pellet boiler with neither telemetry).

**Candidate approach: feedforward regression, not a live PID loop.** A
general PID loop is a poor fit: room heating with an ASHP is slow (hours,
not minutes), and the heat pump's own controller typically already runs an
internal outdoor-temp weather-compensation curve on flow temperature.
Layering a second, independently-tuned PID on top risks oscillation and
windup that's hard to diagnose from HA. Preferred approach: a learned
regression, refit periodically from rolling history:

```
time_to_target ≈ f(current_temp, target_temp, flow_temp | outdoor_temp | none)
```
— the third input selected per-install based on `heat_source` and
`flow_temp_source` above. Closer to how Nest's actual thermal model works
(a learned regression, not a PID loop against outdoor temp), and fails
more gracefully — worst case is preheat starting a bit early or late,
rather than oscillating. If a true PID is pursued later instead, outdoor
temp would need to enter as a feedforward/scheduling term added to the PID
output, not as a raw input into the loop itself.
