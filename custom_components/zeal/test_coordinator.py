"""ZEAL Coordinator logic tests - the combination matrix that would take
a long time to click through by hand in a real dev environment.

Tests the Coordinator's actual methods directly (white-box) against a real
(test) `hass` instance from pytest-homeassistant-custom-component, using a
zone/room shape matching the project's own dev_environment.yaml fixture
(Floor1/Floor2, RoomA/RoomB/RoomC) - so results here should predict what
you'd see clicking through the real dev environment by hand, just in
seconds instead of manually testing every combination.

Run with:
    pip install pytest-homeassistant-custom-component
    pytest tests/ -v
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zeal.const import (
    CONF_ZONES,
    DOMAIN,
    ROOM_ACTIVE,
    ROOM_ID,
    ROOM_NAME,
    ROOM_SENSORS,
    ROOM_TRVS,
    ZONE_ID,
    ZONE_NAME,
    ZONE_ROOMS,
    ZONE_SWITCH,
)
from custom_components.zeal.coordinator import ZealCoordinator
from homeassistant.helpers.storage import Store


class FakeThermostat:
    """Stand-in for a ZealRoomThermostat entity - only the attributes the
    Coordinator actually reads (target_temperature, hvac_mode, entity_id).
    Avoids needing the real climate platform running just to unit-test the
    Coordinator's own decision logic."""

    def __init__(self, target_temperature: float, hvac_mode: str = "heat", entity_id: str = "climate.fake"):
        self.target_temperature = target_temperature
        self.hvac_mode = hvac_mode
        self.entity_id = entity_id


def make_room(room_id: str, name: str, trvs: list[str], sensors: list[str], active: bool = True) -> dict:
    return {
        ROOM_ID: room_id,
        ROOM_NAME: name,
        ROOM_TRVS: trvs,
        ROOM_SENSORS: sensors,
        ROOM_ACTIVE: active,
    }


def make_zone(zone_id: str, name: str, switch: str, rooms: list[dict]) -> dict:
    return {
        ZONE_ID: zone_id,
        ZONE_NAME: name,
        ZONE_SWITCH: switch,
        "heat_source": "ashp",
        "reenable_delay": 300,
        ZONE_ROOMS: rooms,
    }


@pytest.fixture
def floor1_zone() -> dict:
    """Matches the project's own dev_environment.yaml fixture shape:
    one zone, three rooms, one TRV and one sensor each."""
    return make_zone(
        "floor1",
        "Floor1",
        "switch.floor1_pump",
        [
            make_room("floor1_rooma", "Floor1 RoomA", ["climate.floor1_rooma_thermostat"], ["sensor.floor1_rooma_temperature"]),
            make_room("floor1_roomb", "Floor1 RoomB", ["climate.floor1_roomb_thermostat"], ["sensor.floor1_roomb_temperature"]),
            make_room("floor1_roomc", "Floor1 RoomC", ["climate.floor1_roomc_thermostat"], ["sensor.floor1_roomc_temperature"]),
        ],
    )


@pytest.fixture
async def coordinator(hass, floor1_zone) -> ZealCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={CONF_ZONES: [floor1_zone]})
    entry.add_to_hass(hass)
    store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}")
    coord = ZealCoordinator(hass, entry, store)
    return coord


def set_sensor(hass, entity_id: str, value: str | float) -> None:
    hass.states.async_set(entity_id, str(value))


# ---------------------------------------------------------------------
# _room_temperature: sensor reading/averaging
# ---------------------------------------------------------------------

async def test_room_temperature_single_sensor(hass, coordinator, floor1_zone):
    room = floor1_zone[ZONE_ROOMS][0]
    set_sensor(hass, "sensor.floor1_rooma_temperature", 18.5)
    assert coordinator._room_temperature(room) == 18.5


async def test_room_temperature_averages_multiple_sensors(hass, coordinator):
    room = make_room(
        "r", "R",
        trvs=["climate.r_trv"],
        sensors=["sensor.r_1", "sensor.r_2"],
    )
    set_sensor(hass, "sensor.r_1", 18.0)
    set_sensor(hass, "sensor.r_2", 20.0)
    assert coordinator._room_temperature(room) == 19.0


async def test_room_temperature_ignores_unavailable_sensor(hass, coordinator):
    room = make_room("r", "R", ["climate.r_trv"], ["sensor.r_1", "sensor.r_2"])
    set_sensor(hass, "sensor.r_1", 20.0)
    hass.states.async_set("sensor.r_2", "unavailable")
    assert coordinator._room_temperature(room) == 20.0


async def test_room_temperature_all_unavailable_returns_none(hass, coordinator):
    room = make_room("r", "R", ["climate.r_trv"], ["sensor.r_1"])
    hass.states.async_set("sensor.r_1", "unavailable")
    assert coordinator._room_temperature(room) is None


async def test_room_temperature_no_sensors_configured_returns_none(hass, coordinator):
    room = make_room("r", "R", ["climate.r_trv"], [])
    assert coordinator._room_temperature(room) is None


# ---------------------------------------------------------------------
# _evaluate_zone: the core demand combination matrix
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "room_temp,target_temp,expect_demand",
    [
        (18.0, 20.0, True),   # room colder than target -> demanding
        (20.0, 18.0, False),  # room warmer than target -> satisfied
        (20.0, 20.0, False),  # exactly equal -> satisfied (>0 required, not >=)
        (19.99, 20.0, True),  # tiny genuine delta -> still demanding
    ],
)
async def test_single_room_demand_threshold(hass, coordinator, floor1_zone, room_temp, target_temp, expect_demand):
    room = floor1_zone[ZONE_ROOMS][0]
    coordinator.room_thermostats[room[ROOM_ID]] = FakeThermostat(target_temp)
    set_sensor(hass, "sensor.floor1_rooma_temperature", room_temp)
    # Make the other two rooms clearly satisfied so they can't influence the result
    for r in floor1_zone[ZONE_ROOMS][1:]:
        coordinator.room_thermostats[r[ROOM_ID]] = FakeThermostat(10.0)
        set_sensor(hass, r[ROOM_SENSORS][0], 20.0)

    needs_heat, demand_lines = coordinator._evaluate_zone(floor1_zone)
    assert needs_heat is expect_demand
    assert bool(demand_lines) is expect_demand


async def test_any_one_room_demanding_triggers_zone(hass, coordinator, floor1_zone):
    """Only RoomB demands - the zone should still show needs_heat=True."""
    rooms = floor1_zone[ZONE_ROOMS]
    coordinator.room_thermostats[rooms[0][ROOM_ID]] = FakeThermostat(15.0)
    coordinator.room_thermostats[rooms[1][ROOM_ID]] = FakeThermostat(25.0)  # demanding
    coordinator.room_thermostats[rooms[2][ROOM_ID]] = FakeThermostat(15.0)
    for r in rooms:
        set_sensor(hass, r[ROOM_SENSORS][0], 20.0)

    needs_heat, demand_lines = coordinator._evaluate_zone(floor1_zone)
    assert needs_heat is True
    assert len(demand_lines) == 1
    assert "Floor1 RoomB" in demand_lines[0]


async def test_all_rooms_satisfied_no_demand(hass, coordinator, floor1_zone):
    for r in floor1_zone[ZONE_ROOMS]:
        coordinator.room_thermostats[r[ROOM_ID]] = FakeThermostat(15.0)
        set_sensor(hass, r[ROOM_SENSORS][0], 20.0)

    needs_heat, demand_lines = coordinator._evaluate_zone(floor1_zone)
    assert needs_heat is False
    assert demand_lines == []


async def test_inactive_room_never_demands_regardless_of_temperature(hass, coordinator, floor1_zone):
    rooms = floor1_zone[ZONE_ROOMS]
    rooms[0][ROOM_ACTIVE] = False
    coordinator.room_thermostats[rooms[0][ROOM_ID]] = FakeThermostat(30.0)  # would clearly demand if active
    set_sensor(hass, rooms[0][ROOM_SENSORS][0], 5.0)  # freezing
    for r in rooms[1:]:
        coordinator.room_thermostats[r[ROOM_ID]] = FakeThermostat(10.0)
        set_sensor(hass, r[ROOM_SENSORS][0], 20.0)

    needs_heat, demand_lines = coordinator._evaluate_zone(floor1_zone)
    assert needs_heat is False


async def test_thermostat_hvac_off_skips_room_regardless_of_temperature(hass, coordinator, floor1_zone):
    rooms = floor1_zone[ZONE_ROOMS]
    coordinator.room_thermostats[rooms[0][ROOM_ID]] = FakeThermostat(30.0, hvac_mode="off")
    set_sensor(hass, rooms[0][ROOM_SENSORS][0], 5.0)
    for r in rooms[1:]:
        coordinator.room_thermostats[r[ROOM_ID]] = FakeThermostat(10.0)
        set_sensor(hass, r[ROOM_SENSORS][0], 20.0)

    needs_heat, demand_lines = coordinator._evaluate_zone(floor1_zone)
    assert needs_heat is False


async def test_missing_thermostat_falls_back_to_highest_trv_setpoint(hass, coordinator, floor1_zone):
    """If a room's ZealRoomThermostat hasn't registered yet (e.g. right
    after a restart), the old highest-TRV-setpoint default should be used
    rather than skipping the room."""
    rooms = floor1_zone[ZONE_ROOMS]
    # Deliberately do NOT register a thermostat for room A.
    hass.states.async_set(rooms[0][ROOM_TRVS][0], "heat", {"temperature": 25.0})
    set_sensor(hass, rooms[0][ROOM_SENSORS][0], 20.0)
    for r in rooms[1:]:
        coordinator.room_thermostats[r[ROOM_ID]] = FakeThermostat(10.0)
        set_sensor(hass, r[ROOM_SENSORS][0], 20.0)

    needs_heat, demand_lines = coordinator._evaluate_zone(floor1_zone)
    assert needs_heat is True  # 25.0 > 20.0 via fallback


# ---------------------------------------------------------------------
# _zone_all_trvs_off: pump-protection override
# ---------------------------------------------------------------------

async def test_all_trvs_off_forces_no_override_when_one_trv_is_heating(hass, coordinator, floor1_zone):
    for r in floor1_zone[ZONE_ROOMS]:
        hass.states.async_set(r[ROOM_TRVS][0], "heat")
    assert coordinator._zone_all_trvs_off(floor1_zone) is False


async def test_all_trvs_off_true_when_every_trv_confirmed_off(hass, coordinator, floor1_zone):
    for r in floor1_zone[ZONE_ROOMS]:
        hass.states.async_set(r[ROOM_TRVS][0], "off")
    assert coordinator._zone_all_trvs_off(floor1_zone) is True


async def test_all_trvs_off_conservative_when_one_unavailable(hass, coordinator, floor1_zone):
    """An unavailable TRV must NOT count as 'off' - the override should
    never fire on an uncertain reading, only a confirmed one."""
    rooms = floor1_zone[ZONE_ROOMS]
    hass.states.async_set(rooms[0][ROOM_TRVS][0], "off")
    hass.states.async_set(rooms[1][ROOM_TRVS][0], "unavailable")
    hass.states.async_set(rooms[2][ROOM_TRVS][0], "off")
    assert coordinator._zone_all_trvs_off(floor1_zone) is False


async def test_all_trvs_off_ignores_inactive_rooms(hass, coordinator, floor1_zone):
    """An inactive room's TRV shouldn't block the override - it's not
    part of the zone's active flow path either way."""
    rooms = floor1_zone[ZONE_ROOMS]
    rooms[0][ROOM_ACTIVE] = False
    hass.states.async_set(rooms[0][ROOM_TRVS][0], "heat")  # would block if it counted
    hass.states.async_set(rooms[1][ROOM_TRVS][0], "off")
    hass.states.async_set(rooms[2][ROOM_TRVS][0], "off")
    assert coordinator._zone_all_trvs_off(floor1_zone) is True


async def test_all_trvs_off_false_when_zone_has_no_trvs_at_all(hass, coordinator):
    zone = make_zone("empty", "Empty", "switch.x", [make_room("r", "R", [], ["sensor.r"])])
    assert coordinator._zone_all_trvs_off(zone) is False


# ---------------------------------------------------------------------
# Self-write loop guard: own_thermostat_entity_ids
# ---------------------------------------------------------------------

async def test_own_thermostat_entity_ids_reflects_registered_thermostats(hass, coordinator):
    coordinator.room_thermostats["r1"] = FakeThermostat(20.0, entity_id="climate.r1_zeal")
    coordinator.room_thermostats["r2"] = FakeThermostat(20.0, entity_id="climate.r2_zeal")
    assert coordinator.own_thermostat_entity_ids() == {"climate.r1_zeal", "climate.r2_zeal"}


async def test_propagate_room_setpoint_skips_self_referencing_entity(hass, coordinator, floor1_zone):
    """The exact incident this guards against: a room's TRV list somehow
    contains a ZealRoomThermostat's own entity_id - propagation must skip
    it rather than recurse."""
    room = floor1_zone[ZONE_ROOMS][0]
    real_trv = room[ROOM_TRVS][0]
    zeal_entity_id = "climate.floor1_rooma_thermostat_zeal"
    room[ROOM_TRVS].append(zeal_entity_id)  # simulate the bad config
    coordinator.room_thermostats[room[ROOM_ID]] = FakeThermostat(20.0, entity_id=zeal_entity_id)
    hass.states.async_set(real_trv, "heat", {"temperature": 18.0})
    hass.states.async_set(zeal_entity_id, "heat", {"temperature": 18.0})

    called_entity_ids: list[str] = []

    async def fake_set_temperature(call):
        called_entity_ids.append(call.data.get("entity_id"))

    hass.services.async_register("climate", "set_temperature", fake_set_temperature)

    await coordinator.async_propagate_room_setpoint(room[ROOM_ID], 21.0)

    # The real TRV got a set_temperature call...
    assert real_trv in called_entity_ids
    # ...but the self-referencing entity was never called - no recursive
    # service call, no infinite loop.
    assert zeal_entity_id not in called_entity_ids
    assert zeal_entity_id not in coordinator._last_written_setpoint


# ---------------------------------------------------------------------
# Setpoint safety clamp - directly motivated by wanting to guarantee a
# bug can never drive a real TRV to an absurd value unnoticed overnight.
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_value,expected_clamp",
    [
        (95.0, 30.0),   # absurdly hot -> clamped to max
        (-5.0, 5.0),    # absurdly cold -> clamped to min
        (0.0, 5.0),     # zero -> clamped to min
    ],
)
async def test_propagate_clamps_out_of_range_setpoint(hass, coordinator, floor1_zone, bad_value, expected_clamp):
    room = floor1_zone[ZONE_ROOMS][0]
    real_trv = room[ROOM_TRVS][0]
    hass.states.async_set(real_trv, "heat", {"temperature": 18.0})

    called_temps: list[float] = []

    async def fake_set_temperature(call):
        called_temps.append(call.data.get("temperature"))

    hass.services.async_register("climate", "set_temperature", fake_set_temperature)

    await coordinator.async_propagate_room_setpoint(room[ROOM_ID], bad_value)

    assert called_temps == [expected_clamp]  # never the raw bad_value


async def test_propagate_does_not_clamp_a_sane_value(hass, coordinator, floor1_zone):
    room = floor1_zone[ZONE_ROOMS][0]
    real_trv = room[ROOM_TRVS][0]
    hass.states.async_set(real_trv, "heat", {"temperature": 18.0})

    called_temps: list[float] = []

    async def fake_set_temperature(call):
        called_temps.append(call.data.get("temperature"))

    hass.services.async_register("climate", "set_temperature", fake_set_temperature)

    await coordinator.async_propagate_room_setpoint(room[ROOM_ID], 21.5)

    assert called_temps == [21.5]  # untouched - a sane value must pass through exactly


# ---------------------------------------------------------------------
# Sensor/TRV offline handling - debounced detection, notification,
# recovery. Directly motivated by real battery hardware failing during a
# live shadow-mode deployment.
# ---------------------------------------------------------------------

def _register_notification_capture(hass):
    """Register fake persistent_notification.create/dismiss handlers and
    return the lists of calls made to each, for assertions."""
    creates: list[dict] = []
    dismisses: list[dict] = []

    async def fake_create(call):
        creates.append(dict(call.data))

    async def fake_dismiss(call):
        dismisses.append(dict(call.data))

    hass.services.async_register("persistent_notification", "create", fake_create)
    hass.services.async_register("persistent_notification", "dismiss", fake_dismiss)
    return creates, dismisses


async def test_offline_no_notification_before_debounce_elapses(hass, coordinator, freezer):
    zone = make_zone("z", "Z", "switch.z", [make_room("r", "R", ["climate.r_trv"], ["sensor.r"])])
    coordinator.zones = [zone]
    hass.states.async_set("climate.r_trv", "heat", {"temperature": 20.0})
    creates, _ = _register_notification_capture(hass)

    hass.states.async_set("sensor.r", "unavailable")
    await coordinator._async_check_entity_health()  # first sighting, starts the clock

    freezer.tick(60)  # well under OFFLINE_DEBOUNCE_SECONDS (300)
    await coordinator._async_check_entity_health()

    assert creates == []  # too soon - must not fire yet


async def test_offline_notification_fires_after_debounce_elapses(hass, coordinator, freezer):
    zone = make_zone("z", "Z", "switch.z", [make_room("r", "R", ["climate.r_trv"], ["sensor.r"])])
    coordinator.zones = [zone]
    hass.states.async_set("climate.r_trv", "heat", {"temperature": 20.0})
    creates, _ = _register_notification_capture(hass)

    hass.states.async_set("sensor.r", "unavailable")
    await coordinator._async_check_entity_health()  # starts the clock

    freezer.tick(301)  # just past OFFLINE_DEBOUNCE_SECONDS
    await coordinator._async_check_entity_health()

    assert len(creates) == 1
    assert creates[0]["notification_id"] == "zeal_offline_sensor_r"
    assert "sensor.r" in creates[0]["message"]


async def test_offline_notification_not_recreated_every_cycle(hass, coordinator, freezer):
    zone = make_zone("z", "Z", "switch.z", [make_room("r", "R", ["climate.r_trv"], ["sensor.r"])])
    coordinator.zones = [zone]
    hass.states.async_set("climate.r_trv", "heat", {"temperature": 20.0})
    creates, _ = _register_notification_capture(hass)

    hass.states.async_set("sensor.r", "unavailable")
    await coordinator._async_check_entity_health()
    freezer.tick(301)
    await coordinator._async_check_entity_health()
    freezer.tick(60)
    await coordinator._async_check_entity_health()  # still offline, another cycle

    assert len(creates) == 1  # not fired a second time while the fault persists


async def test_offline_recovery_dismisses_notification(hass, coordinator, freezer):
    zone = make_zone("z", "Z", "switch.z", [make_room("r", "R", ["climate.r_trv"], ["sensor.r"])])
    coordinator.zones = [zone]
    hass.states.async_set("climate.r_trv", "heat", {"temperature": 20.0})
    creates, dismisses = _register_notification_capture(hass)

    hass.states.async_set("sensor.r", "unavailable")
    await coordinator._async_check_entity_health()
    freezer.tick(301)
    await coordinator._async_check_entity_health()
    assert len(creates) == 1

    hass.states.async_set("sensor.r", "20.0")  # battery replaced, back online
    await coordinator._async_check_entity_health()

    assert len(dismisses) == 1
    assert dismisses[0]["notification_id"] == "zeal_offline_sensor_r"


async def test_offline_message_reflects_other_coverage_remaining(hass, coordinator, freezer):
    room = make_room("r", "R", ["climate.r_trv1", "climate.r_trv2"], ["sensor.r"])
    zone = make_zone("z", "Z", "switch.z", [room])
    coordinator.zones = [zone]
    hass.states.async_set("climate.r_trv1", "unavailable")
    hass.states.async_set("climate.r_trv2", "heat", {"temperature": 20.0})  # still usable
    hass.states.async_set("sensor.r", "20.0")
    creates, _ = _register_notification_capture(hass)

    await coordinator._async_check_entity_health()
    freezer.tick(301)
    await coordinator._async_check_entity_health()

    assert len(creates) == 1
    assert "still active" in creates[0]["message"]  # other TRV still covers this room


async def test_offline_message_reflects_no_coverage_remaining(hass, coordinator, freezer):
    room = make_room("r", "R", ["climate.r_trv1"], ["sensor.r"])
    zone = make_zone("z", "Z", "switch.z", [room])
    coordinator.zones = [zone]
    hass.states.async_set("climate.r_trv1", "unavailable")  # the only TRV in this room
    hass.states.async_set("sensor.r", "20.0")
    creates, _ = _register_notification_capture(hass)

    await coordinator._async_check_entity_health()
    freezer.tick(301)
    await coordinator._async_check_entity_health()

    assert len(creates) == 1
    assert "No usable" in creates[0]["message"]  # nothing left to cover this room


# ---------------------------------------------------------------------
# Staleness detection - the Zigbee failure mode: a device stops
# reporting entirely while its last known value still looks completely
# normal (never transitions to "unavailable"). Z-Wave reliably marks a
# dead node unavailable via its own health-check; Zigbee has no
# equivalent guarantee.
# ---------------------------------------------------------------------

async def test_get_usable_state_returns_state_for_a_fresh_reading(hass, coordinator, freezer):
    hass.states.async_set("sensor.r", "20.0")
    assert coordinator._get_usable_state("sensor.r") is not None


async def test_get_usable_state_returns_none_for_a_stale_reading(hass, coordinator, freezer):
    hass.states.async_set("sensor.r", "20.0")  # last_reported = now
    freezer.tick(3601)  # just past STALE_THRESHOLD_SECONDS (3600) - never updated again
    assert coordinator._get_usable_state("sensor.r") is None


async def test_get_usable_state_does_not_false_positive_on_a_stable_reading_within_threshold(
    hass, coordinator, freezer
):
    """A sensor sitting at an unchanging, genuinely healthy reading must
    not be treated as stale just because the value hasn't moved - this
    is exactly why last_reported (not last_updated/last_changed) is
    used. Staying under the threshold, this must still count as fresh."""
    hass.states.async_set("sensor.r", "20.0")
    freezer.tick(1800)  # well under the 3600s threshold
    assert coordinator._get_usable_state("sensor.r") is not None


async def test_room_temperature_excludes_a_stale_sensor(hass, coordinator, freezer):
    """The core scenario: a Zigbee sensor stops reporting but its last
    value (20.0, looking completely normal) is still sitting in the
    state machine. Without staleness detection, this would silently
    keep influencing real heating decisions forever."""
    room = make_room("r", "R", ["climate.r_trv"], ["sensor.r"])
    hass.states.async_set("sensor.r", "20.0")

    freezer.tick(3601)  # sensor never reports again

    assert coordinator._room_temperature(room) is None  # correctly distrusted, not "20.0"


async def test_room_temperature_averages_only_fresh_sensors_when_one_is_stale(hass, coordinator, freezer):
    room = make_room("r", "R", ["climate.r_trv"], ["sensor.r1", "sensor.r2"])
    hass.states.async_set("sensor.r1", "18.0")

    freezer.tick(3601)  # r1 goes silent from here on

    hass.states.async_set("sensor.r2", "22.0")  # r2 keeps reporting - fresh right now

    assert coordinator._room_temperature(room) == 22.0  # only the fresh one counted


async def test_zone_all_trvs_off_conservative_on_a_stale_trv(hass, coordinator, freezer):
    """A TRV that stopped reporting, even if its last known state was
    literally "off", must not count toward the confirmed-off override -
    we can no longer be sure that's still true."""
    room = make_room("r", "R", ["climate.r_trv"], ["sensor.r"])
    zone = make_zone("z", "Z", "switch.z", [room])
    hass.states.async_set("climate.r_trv", "off")

    freezer.tick(3601)  # never reports again, even though last state was "off"

    assert coordinator._zone_all_trvs_off(zone) is False


async def test_offline_health_check_detects_a_stale_entity_not_just_unavailable(hass, coordinator, freezer):
    """Proves the notification system catches the Zigbee scenario too,
    not just outright unavailable/unknown - a stale entity crosses the
    same debounce-then-notify path. Only the sensor goes stale here; the
    TRV is deliberately kept fresh throughout, to isolate exactly one
    notification rather than two."""
    room = make_room("r", "R", ["climate.r_trv"], ["sensor.r"])
    zone = make_zone("z", "Z", "switch.z", [room])
    coordinator.zones = [zone]
    hass.states.async_set("climate.r_trv", "heat", {"temperature": 20.0})
    hass.states.async_set("sensor.r", "20.0")  # never reports again from here
    creates, _ = _register_notification_capture(hass)

    freezer.tick(3601)  # sensor now stale (> STALE_THRESHOLD_SECONDS); TRV would be too...
    hass.states.async_set("climate.r_trv", "heat", {"temperature": 20.0})  # ...but keeps reporting
    await coordinator._async_check_entity_health()  # first sighting of the stale sensor

    freezer.tick(301)  # past OFFLINE_DEBOUNCE_SECONDS too
    hass.states.async_set("climate.r_trv", "heat", {"temperature": 20.0})  # still fresh
    await coordinator._async_check_entity_health()

    assert len(creates) == 1
    assert "sensor.r" in creates[0]["message"]
