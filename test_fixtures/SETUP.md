# ZEAL dev/test fixture — setup guide

Companion to `dev_environment.yaml`. This gives anyone testing ZEAL —
including a future session, or another contributor — an identical,
guaranteed-consistent dummy rig, rather than hand-building one and risking
the kind of silent naming mismatch that cost a full debugging session on
18 Aug 2026 (see the wiki Decisions Log entry "'Pump never turns off'
incident").

## What this replaces

If you've been testing with an earlier hand-built rig (e.g. entities named
after real rooms like `kitchen_dummy_temperature`, `living_room`, etc.),
**remove those** before adopting this fixture rather than running both side
by side — having two similarly-purposed sets of dummy entities in the same
instance is exactly the setup that caused the incident this fixture exists
to prevent.

## 1. Install the YAML

Either:
- Paste the whole content of `dev_environment.yaml` into your
  `configuration.yaml`, or
- Keep it as its own file and merge it in via packages (e.g.
  `homeassistant: packages: !include_dir_named test_fixtures` pointing at
  the folder this file lives in).

Restart Home Assistant Core (not just a reload) to apply it.

## 2. Create the Areas (cannot be done via YAML)

ZEAL's Options Flow discovers TRVs/sensors **by HA Area**, and Areas
aren't YAML-configurable — this step has to happen once, by hand, in the
UI, regardless of how carefully the entity YAML itself is written.

`Settings → Areas & Zones → Add Area`, create exactly 6:

- `Floor1 RoomA`
- `Floor1 RoomB`
- `Floor1 RoomC`
- `Floor2 RoomA`
- `Floor2 RoomB`
- `Floor2 RoomC`

(Any naming is fine as long as you're consistent — these are just
suggested to match the entity names below.)

## 3. Assign entities to Areas

For each room, assign its **sensor** and its **climate (dummy TRV)**
entity to the matching Area — `Settings → Devices & Services → Entities`,
click the entity, the cog icon, set Area:

| Area | Sensor | Dummy TRV |
|---|---|---|
| Floor1 RoomA | `sensor.floor1_rooma_temperature` | `climate.floor1_rooma_thermostat` |
| Floor1 RoomB | `sensor.floor1_roomb_temperature` | `climate.floor1_roomb_thermostat` |
| Floor1 RoomC | `sensor.floor1_roomc_temperature` | `climate.floor1_roomc_thermostat` |
| Floor2 RoomA | `sensor.floor2_rooma_temperature` | `climate.floor2_rooma_thermostat` |
| Floor2 RoomB | `sensor.floor2_roomb_temperature` | `climate.floor2_roomb_thermostat` |
| Floor2 RoomC | `sensor.floor2_roomc_temperature` | `climate.floor2_roomc_thermostat` |

Do **not** assign the `_heater` `input_boolean`s to any Area — they're
internal plumbing for `generic_thermostat`, not something ZEAL should ever
see or offer as a pick.

## 4. Configure ZEAL

`Settings → Devices & Services → ZEAL HVAC System → Configure`:

- **Zone "Floor1"** → switch `switch.floor1_pump` ("Pump Floor 1") → rooms:
  Floor1 RoomA, Floor1 RoomB, Floor1 RoomC.
- **Zone "Floor2"** → switch `switch.floor2_pump` ("Pump Floor 2") → rooms:
  Floor2 RoomA, Floor2 RoomB, Floor2 RoomC.

Each room's TRV and sensor should auto-discover and pre-tick correctly at
this point, since they're already assigned to the matching Area.

**Name the ZEAL zones exactly "Floor1"/"Floor2"** (matching the switch's
own naming), not something else like "Zone 1" — confirmed from a real
deployment that a `ZealRoomThermostat`'s auto-generated `entity_id`
combines **both** the zone name and the room name (e.g. zone "Zone 1" +
room "Floor1 RoomA" produced `climate.zone_1_floor1_rooma_thermostat_zeal`
— the zone name shows up too, not just the room). Naming the zone to match
the switch keeps the resulting entity_ids predictable
(`climate.floor1_rooma_thermostat_zeal` rather than a zone-name prefix
that doesn't match anything else in the fixture).

## 5. Testing

Exactly **one** entity controls each room's simulated temperature — its
`input_number`. No other entity to accidentally desync it from:

- `input_number.floor1_rooma_temp` (and so on for the other 5 rooms)

Set it below the room's ZEAL Thermostat target → that zone's pump should
turn on. Set it above → pump should turn off (subject to the zone's
re-enable delay). `test_fixtures/dashboard.yaml` has a ready-made
dashboard with both the dummy TRVs and ZEAL's own thermostats side by
side for exactly this — import it via a new Dashboard's "Edit in YAML"
mode. **Double-check every entity_id if you copy/adapt it rather than use
it as-is**: an earlier draft had one Floor2 tile silently pointing at a
Floor1 entity (right zone-shaped label, wrong entity underneath) — caught
before it shipped, but exactly the kind of copy-paste mistake this whole
fixture exists to prevent, so it's worth a deliberate second look rather
than assuming a dashboard config is correct just because it renders.

