# ZEAL HVAC System — Test Plan

*For the current build: Config Flow + Options Flow (Milestones 1 & 3, done)
and the Coordinator control loop (Milestone 2, built, not yet run against
hardware). Written to be usable as-is by anyone testing this from a fresh
HA install — every step names the exact screen, tab, and field.*

## 0. Scope and known gaps — read first

- **No multi-fuel / heat-source selector exists yet.** `heat_source` and
  `flow_temp_source` are design-only fields for the future thermal-learning
  feature (project doc §9.2, Milestone 7+). There is nothing to test here
  today — the Coordinator doesn't care what's heating the room, it just
  compares a TRV setpoint to a sensor reading and drives a switch. If a
  step below implies a heat-source choice, that's a documentation bug —
  flag it.
- **No persistent "view my config" screen exists yet.** There's no
  dashboard/overview card (that's the bundled Lovelace card in Milestone 4,
  not built). Right now there are exactly two ways to see what's
  configured, both covered in §1 below — neither is a permanent, glanceable
  summary. Worth being upfront about this rather than a reader hunting for
  a screen that isn't there.
- **Scheduling, away mode, and the boost/propagation mechanic don't exist
  yet** (Milestone 4). Nothing in this test plan exercises them.

## 1. Where to see what you've configured

Two places, neither of them a dedicated dashboard:

**A. Reopen the Options Flow.**
`Settings → Devices & Services → ZEAL HVAC System
→ Configure`. Every screen pre-fills with your current saved values — the
zone menu lists each zone's name and room count, and stepping into a zone
shows its current rooms, switch, and per-room TRV/sensor picks. This is
the closest thing to a "current state" view today; it just requires
clicking through rather than a single glance.

**B. The save-summary dialog.**
After clicking through to **Done → Save** at the end of any Options Flow
session, HA shows a one-time Markdown summary: every zone, its switch,
every room, and every active TRV/sensor. It is **not persisted anywhere**
— if you close that dialog without reading it, the only way to see the
same information again is to reopen Configure (§A) or screenshot it next
time.

**C. Devices & Entities (partial — integration-created entities only).**
`Settings → Devices & Services → Entities`, filtered to this integration,
shows the two entities it creates *per zone*: the manual override switch
and the demand sensor. It does **not** show your own TRVs/sensors — those
remain listed under whichever integration actually owns them (Zigbee2MQTT,
deCONZ, etc.), not under ZEAL. Each zone also appears as its own **Device**
(`Settings → Devices & Services → Devices`, filtered to this integration)
containing those same two entities.

## 2. Build a fully virtual test rig (UI only, no YAML, no real hardware)

This gives you one dummy "room" — a TRV with an adjustable setpoint and a
temperature sensor with an adjustable reading — plus a dedicated dummy
switch to act as the zone's heating actuator. Confirmed buildable entirely
from Settings screens on current HA (2026.8.x); no `configuration.yaml`
editing required anywhere in this section.

### 2.1 Dummy room temperature sensor

1. **Settings → Devices & Services → Helpers tab → + Create Helper → Number.**
   - Name: `Test Room Temp` (entity becomes `number.test_room_temp`)
   - Minimum: `0`, Maximum: `35`, Step: `0.1`, Unit of measurement: `°C`
   - This is the slider you'll actually drag during testing.
2. **Helpers tab → + Create Helper → Template → Template a sensor.**
   - Name: `Test Room Sensor` (entity becomes `sensor.test_room_sensor`)
   - State template: `{{ states('number.test_room_temp') }}`
   - Device class: **Temperature**
   - Unit of measurement: `°C`
   - This is the entity ZEAL will actually read — it mirrors whatever the
     Number helper above is set to, live.

### 2.2 Dummy TRV (a real `climate` entity with an adjustable setpoint)

1. **Helpers tab → + Create Helper → Template → Template a switch** (this
   is a separate wizard from the sensor one, still under Template).
   - Name: `Test TRV Internal Heater` (entity becomes
     `switch.test_trv_internal_heater`)
   - Leave the **state template field blank.** Leaving it empty puts the
     switch in *optimistic* mode — it just remembers whatever you last set
     it to, with no dependency on anything else. This entity is never
     looked at directly during testing; Generic Thermostat below just
     needs *some* switch to nominally control.
2. **Settings → Devices & Services → Add Integration → search "Generic
   Thermostat".**
   - Name: `Test TRV`
   - Heater switch: `switch.test_trv_internal_heater` (from step 1)
   - Target sensor: `sensor.test_room_sensor` (from §2.1)
   - Leave other fields at their defaults.
   - This creates `climate.test_trv` — a real climate entity whose target
     temperature you can set from its thermostat card, exactly like a real
     TRV. ZEAL reads this entity's `temperature` attribute as the room's
     setpoint.

### 2.3 Dummy zone actuator switch

1. **Helpers tab → + Create Helper → Template → Template a switch.**
   - Name: `Test Zone Switch` (entity becomes `switch.test_zone_switch`)
   - Leave the state template blank (optimistic, as in §2.2 step 1).
   - **This is the switch you'll actually be watching during the test** —
     do not confuse it with the internal one from §2.2, which exists only
     to satisfy Generic Thermostat's setup requirements and has no bearing
     on the test itself.

### 2.4 Put the dummy TRV and sensor in an Area

1. **Settings → Areas & Zones** — create an Area if you don't already have
   a spare one, e.g. `Test Room`.
2. For **both** `climate.test_trv` and `sensor.test_room_sensor`: open the
   entity's settings (gear icon on its more-info dialog, or via
   `Settings → Devices & Services → Entities`, click the entity, then the
   cog) and assign it to the `Test Room` Area. This is required — ZEAL's
   Options Flow auto-discovers TRVs/sensors *by Area*, so anything not
   assigned to an Area won't show up as a pick.

## 3. Configure a test zone in ZEAL

1. `Settings → Devices & Services → Add Integration → ZEAL` (or "ASHP Zone
   Control" if not yet renamed). Give the instance any name and finish.
2. On the new integration's card, click **Configure**.
3. **Zone menu** → **+ Add a new zone**.
4. **Pick rooms** → select the `Test Room` Area you created in §2.4.
5. **Name & switch** → name the zone e.g. `Test Zone`, and set its switch
   to `switch.test_zone_switch` (§2.3) — **not** the internal one from
   §2.2.
6. **Heat source** → pick anything (e.g. "Other / not sure" — doesn't
   affect the demand test itself). Confirm it lands you on the next screen
   with a pre-filled suggested delay matching whatever you picked (300s
   for ASHP/Other, 120s for modulating boiler, 60s for non-condensing).
7. **Re-enable delay** → accept the suggested value, or lower it (e.g. to
   `15`) to make §4 step 5's re-enable-delay check faster to observe
   without editing `const.py`.
8. **Per-room entities** → you should see `climate.test_trv` and
   `sensor.test_room_sensor` already pre-ticked as discovered. Leave
   "Room is active" on. Confirm.
9. **Done → Save.** Read the save-summary dialog (§1B) and confirm it
   shows: Test Zone → switch `switch.test_zone_switch` → heat source →
   re-enable delay → Test Room (active) → TRV `climate.test_trv` → Sensor
   `sensor.test_room_sensor`.

## 4. Run the actual demand test

1. Open `climate.test_trv`'s more-info dialog (thermostat card) and set
   its target temperature to, say, **20°C**.
2. Open `number.test_room_temp` and set it **above** 20°C — e.g. 22.
   - Expected: `sensor.test_room_sensor` follows to 22 within a second or
     two (it's a live template). The Coordinator is watching that sensor
     directly (`async_track_state_change_event`), so it should re-evaluate
     almost immediately rather than waiting for the 60-second poll.
   - Expected result: `switch.test_zone_switch` is (or goes/stays) **off**
     — room is warmer than the setpoint, no demand.
3. Set `number.test_room_temp` **below** 20°C — e.g. 18.
   - Expected: within a few seconds, `switch.test_zone_switch` turns **on**.
   - Check the zone's **Demand sensor** (`Settings → Devices & Services →
     Entities`, filter to this integration, or via the zone's Device page)
     — its state should read `Demand`, with an attribute listing something
     like `Test Room: Set 20.0°C, Room 18.0°C (Δ 2.0°C)`.
4. Set `number.test_room_temp` back **above** 20°C.
   - Expected: `switch.test_zone_switch` turns **off**, Demand sensor
     reads `No demand`.
5. **Re-enable delay check.** Immediately after step 4 (switch just went
   off), push `number.test_room_temp` back below 20°C again.
   - Expected: the switch does **not** turn back on immediately — it
     should stay off for however long you set in §3 step 7 (e.g. 15
     seconds if you lowered it for faster testing, or the full suggested
     default if you left it) before turning on, even though the room is
     genuinely cold. This is the anti-short-cycling behaviour ported from
     the old `ashp_controller.py`, not a bug. If you didn't lower the
     delay in §3, this step will take longer to observe — reopen
     **Configure** on this zone and lower the re-enable delay there rather
     than editing any code.
6. **Manual override check.** With the room cold (demand present, switch
   on), find the zone's **Manual override switch** (same Entities/Device
   screen as the Demand sensor) and turn it **on**.
   - Expected: `switch.test_zone_switch` is left exactly as it was — the
     Coordinator should stop acting on it entirely while the override is
     on, even as you move `number.test_room_temp` up and down. Turn the
     override back off afterward and confirm normal control resumes.

## 5. Cleanup

Once satisfied, remove the test zone via **Configure → remove Test Zone →
Done → Save**, then delete the four Helpers (`Test Room Temp`, `Test Room
Sensor`, `Test TRV Internal Heater`, `Test Zone Switch`) and the Generic
Thermostat integration instance (`Test TRV`) from their respective
Settings screens. None of this leaves anything behind in `configuration.yaml`
since it was all created via UI helpers/config flows.

## 6. Open items this test plan does not cover

- Multiple TRVs/sensors in one room (average sensor / highest-setpoint
  logic) — repeat §2 twice more with a second dummy TRV/sensor pair in the
  same Area, and verify the aggregation behaves as described in the
  project doc §3.1.
- Multiple zones sharing vs. not sharing hardware, per your actual house
  layout (single-pump-two-zones, dual-pump, hotel-per-level) — this test
  plan builds one isolated zone; a second pass with two zones and two
  distinct dummy switches would confirm they don't interfere with each
  other.
- Anything in Milestone 4 (scheduling, away mode, boost) — not built yet,
  nothing to test.
