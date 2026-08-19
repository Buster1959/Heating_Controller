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
