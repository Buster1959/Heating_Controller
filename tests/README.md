# ZEAL automated tests

Unit tests against the Coordinator's actual demand logic — the
combination matrix (room satisfied/demanding, inactive rooms, thermostat
off, multiple sensors, unavailable entities, the all-TRVs-off pump
protection, the self-write recursion guard) that would take real time to
click through by hand in a live dev environment. Runs in well under a
second.

Verified working: 21/21 passing against `pytest-homeassistant-custom-component`
as of the 0.10.0 release, in a clean virtualenv.

## Running

```bash
python3 -m venv venv
venv/bin/pip install -r requirements_test.txt
venv/bin/python -m pytest tests/ -v
```

No real Home Assistant install needed — `pytest-homeassistant-custom-component`
provides a real (but isolated, in-memory) `hass` test instance, the same
framework Home Assistant Core itself uses to test its own integrations.

## What's covered

- `_room_temperature` — single sensor, averaging multiple sensors,
  ignoring unavailable ones, all-unavailable/no-sensors returning `None`.
- `_evaluate_zone` — the room-by-room demand threshold (`Setpoint −
  Temperature > 0`), any-one-room-demanding triggering the whole zone,
  inactive rooms never contributing, a thermostat in `hvac_mode: off`
  being skipped, and the fallback to highest-TRV-setpoint when a room's
  `ZealRoomThermostat` hasn't registered yet.
- `_zone_all_trvs_off` — the pump-protection override: fires only when
  every TRV is *confirmed* off, never on an unavailable/uncertain
  reading, and ignores inactive rooms' TRVs.
- The self-write loop guard — reproduces the exact incident where a
  `ZealRoomThermostat` ended up in its own room's TRV list, confirming
  propagation skips it rather than recursing.

## What's NOT covered (yet)

Config Flow / Options Flow, the `switch`/`sensor`/`climate` entity
platforms themselves (as opposed to the Coordinator logic they call
into), and anything from Milestone 4 onward (scheduling, away mode,
boost/cooling) since that code doesn't exist yet. Contributions extending
coverage welcome — this is meant to grow alongside the project, not stay
fixed at this snapshot.

## Adding a test for a new bug

If you find a bug the way several were found in this project's own
Decisions Log (the infinite-recursion incident, the all-TRVs-off gap,
etc.) — write a test that reproduces it *before* fixing the code, confirm
it fails, then fix and confirm it passes. Keeps the exact scenario that
bit someone once from silently regressing later.
