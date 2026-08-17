"""Coordinator for ZEAL HVAC System.

Ports the control loop from the old `ashp_controller.py` (evaluate_floor /
set_switch) onto the new zone/room schema. Two deliberate corrections vs.
the original, both captured with their rationale in PROJECT_MANDATE.md:

  * Anti-hunting uses the **re-enable delay** (a zone switch that just
    turned OFF won't turn back ON for `DEFAULT_REENABLE_DELAY` seconds),
    not hysteresis - hysteresis was already dead code in the version of
    ashp_controller.py that was actually running.
  * The manual "hands-off this zone" override is now an integration-created
    `switch` entity per zone (see switch.py), not a hand-created
    `input_boolean` helper.

New in this schema vs. the original (which was always exactly one TRV and
one sensor per room): a room can have *multiple* TRVs and/or sensors.
  * Room temperature = the **average** of all its active sensors' readings
    (reduces single-sensor noise; standard practice for multi-sensor rooms).
  * Room setpoint = read from that room's ZealRoomThermostat entity (see
    climate.py) - a single per-room master the Coordinator treats as the
    room's actual source of truth. Physical TRVs are slaved to it: this
    Coordinator propagates the thermostat's target_temperature out to
    every TRV in the room whenever it changes, and conversely, detects an
    unexpected change on any *physical* TRV and both updates the
    thermostat to match and re-propagates to the room's other TRVs - so a
    manual adjustment on any one TRV becomes the room's setpoint
    everywhere, not just on the TRV someone happened to touch. This
    supersedes an earlier "highest setpoint among the room's TRVs" default
    and a separate planned-but-never-built 2-hour "boost" mechanic - both
    are obsolete now that there's a real per-room entity to be the
    setpoint authority instead of inferring one from N TRVs' raw states.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ZONES,
    DEFAULT_REENABLE_DELAY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ROOM_ACTIVE,
    ROOM_ID,
    ROOM_NAME,
    ROOM_SENSORS,
    ROOM_TRVS,
    RUNTIME_LAST_OFF,
    ZONE_ID,
    ZONE_NAME,
    ZONE_REENABLE_DELAY,
    ZONE_ROOMS,
    ZONE_SWITCH,
)

_LOGGER = logging.getLogger(__name__)

UNAVAILABLE_STATES = (None, "unavailable", "unknown")


@dataclass
class ZoneStatus:
    """Snapshot of one zone's most recent evaluation, for entities to read."""

    zone_id: str
    zone_name: str
    needs_heat: bool
    demand_lines: list[str] = field(default_factory=list)
    switches_ok: bool = True  # False if every configured switch was unavailable


class ZealCoordinator(DataUpdateCoordinator[dict[str, ZoneStatus]]):
    """Polls TRVs/sensors, evaluates demand per zone, drives zone switches."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store: Store) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.entry = entry
        self.store = store
        self.zones: list[dict[str, Any]] = list(entry.options.get(CONF_ZONES, []))

        # zone_id -> datetime of last OFF transition, restored from Store.
        self._last_off_time: dict[str, datetime] = {}
        # zone_id -> datetime of last ON transition (duration logging only).
        self._last_on_time: dict[str, datetime] = {}

        # Populated by ZealOverrideSwitch.async_added_to_hass() /
        # removed on async_will_remove_from_hass(). Checking the live entity
        # object in-process avoids a hass.states.get() round trip and any
        # guesswork about the override switch's entity_id.
        self.override_switches: dict[str, Any] = {}

        # Populated by ZealRoomThermostat.async_added_to_hass() /
        # removed on async_will_remove_from_hass() - same live-object
        # pattern as override_switches above. Each room's thermostat is
        # the room's actual setpoint authority (see climate.py); physical
        # TRVs are slaved to it, not the other way around.
        self.room_thermostats: dict[str, Any] = {}

        # entity_id (TRV) -> last temperature we ourselves wrote to it, via
        # async_propagate_room_setpoint(). Self-write loop guard: without
        # this, propagating a thermostat's setpoint to a TRV would trigger
        # the TRV's own state-change listener, which would read it back as
        # a new *external* change and re-propagate indefinitely.
        self._last_written_setpoint: dict[str, float] = {}

        self._unsub_state_listener: Callable[[], None] | None = None

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------
    async def async_setup(self) -> None:
        """Restore persisted runtime state and start listening for changes."""
        stored = await self.store.async_load() or {}
        for zone_id, iso_ts in stored.get(RUNTIME_LAST_OFF, {}).items():
            parsed = dt_util.parse_datetime(iso_ts)
            if parsed is not None:
                self._last_off_time[zone_id] = parsed

        self._register_state_listener()

        total_rooms = sum(len(z.get(ZONE_ROOMS, [])) for z in self.zones)
        _LOGGER.info(
            "ZEAL Coordinator started: %d zone(s), %d room(s) total, "
            "%d restored last-off timestamp(s)",
            len(self.zones),
            total_rooms,
            len(self._last_off_time),
        )

    @callback
    def async_teardown(self) -> None:
        """Cancel the state-change listener on unload."""
        if self._unsub_state_listener is not None:
            self._unsub_state_listener()
            self._unsub_state_listener = None

    def _register_state_listener(self) -> None:
        """Watch every active room's TRVs/sensors for near-instant response.

        Mirrors the old per-entity `listen_state` calls in
        ashp_controller.py, layered on top of the periodic poll rather than
        replacing it - either one alone would miss cases the other catches
        (a state change between polls vs. an entity that silently stops
        updating).
        """
        entity_ids: set[str] = set()
        for zone in self.zones:
            for room in zone.get(ZONE_ROOMS, []):
                if not room.get(ROOM_ACTIVE, True):
                    continue
                entity_ids.update(room.get(ROOM_TRVS, []) or [])
                entity_ids.update(room.get(ROOM_SENSORS, []) or [])

        if not entity_ids:
            return

        self._unsub_state_listener = async_track_state_change_event(
            self.hass, list(entity_ids), self._async_handle_tracked_state_change
        )

    @callback
    def _async_handle_tracked_state_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        entity_id = event.data["entity_id"]
        new_state = event.data["new_state"]
        old_state = event.data["old_state"]

        new_val = new_state.state if new_state else None
        old_val = old_state.state if old_state else None
        _LOGGER.debug("Tracked entity changed: %s (%s -> %s)", entity_id, old_val, new_val)

        # Only TRVs carry a "temperature" attribute we care about here;
        # sensor state changes fall through to the plain refresh below.
        if new_state is not None and old_state is not None:
            new_temp = new_state.attributes.get("temperature")
            old_temp = old_state.attributes.get("temperature")
            if new_temp is not None and new_temp != old_temp:
                room = self._find_room_for_trv(entity_id)
                if room is not None:
                    _LOGGER.debug(
                        "TRV setpoint change detected: %s (%s -> %s°C) in room %s",
                        entity_id,
                        old_temp,
                        new_temp,
                        room.get(ROOM_NAME, room.get(ROOM_ID)),
                    )
                    self.hass.async_create_task(
                        self._async_handle_external_trv_change(room, entity_id, new_temp)
                    )

        self.hass.async_create_task(self.async_request_refresh())

    # ------------------------------------------------------------------
    # Core evaluation loop
    # ------------------------------------------------------------------
    async def _async_update_data(self) -> dict[str, ZoneStatus]:
        results: dict[str, ZoneStatus] = {}
        off_time_changed = False
        _LOGGER.debug("Evaluation cycle starting (%d zone(s) configured)", len(self.zones))

        for zone in self.zones:
            zone_id = zone[ZONE_ID]
            zone_name = zone.get(ZONE_NAME, zone_id)

            if not zone.get(ZONE_SWITCH):
                # Nothing to control for this zone - skip entirely, same as
                # ashp_controller.py's `if "switch" not in floor: continue`.
                _LOGGER.debug("[%s] No switch configured, skipping zone entirely", zone_name)
                continue

            needs_heat, demand_lines = self._evaluate_zone(zone)
            _LOGGER.debug(
                "[%s] needs_heat=%s%s",
                zone_name,
                needs_heat,
                f" ({'; '.join(demand_lines)})" if demand_lines else "",
            )
            switches_ok, zone_off_changed = await self._async_apply_zone_switches(
                zone, needs_heat
            )
            if zone_off_changed:
                off_time_changed = True

            results[zone_id] = ZoneStatus(
                zone_id=zone_id,
                zone_name=zone_name,
                needs_heat=needs_heat,
                demand_lines=demand_lines,
                switches_ok=switches_ok,
            )

        if off_time_changed:
            await self._async_persist_runtime_state()

        _LOGGER.debug("Evaluation cycle complete")
        return results

    def _evaluate_zone(self, zone: dict[str, Any]) -> tuple[bool, list[str]]:
        """Return (needs_heat, demand_lines) for one zone.

        needs_heat is True if *any* active room in the zone is colder than
        its setpoint (see module docstring for how multi-TRV/sensor rooms
        are aggregated).
        """
        needs_heat = False
        demand_lines: list[str] = []

        for room in zone.get(ZONE_ROOMS, []):
            room_name = room.get("name", room.get("room_id", "unknown room"))

            if not room.get(ROOM_ACTIVE, True):
                _LOGGER.debug("  %s: inactive, skipping", room_name)
                continue

            room_id = room.get(ROOM_ID)
            thermostat = self.room_thermostats.get(room_id)

            if thermostat is not None:
                if getattr(thermostat, "hvac_mode", None) == "off":
                    _LOGGER.debug("  %s: thermostat is OFF, skipping", room_name)
                    continue
                set_temp = getattr(thermostat, "target_temperature", None)
            else:
                # Thermostat entity hasn't finished loading yet (e.g. right
                # after a restart, before platforms finish setup) - fall
                # back to the old highest-TRV-setpoint default rather than
                # skip the room entirely.
                set_temp = self._room_setpoint(room)
                _LOGGER.debug(
                    "  %s: thermostat not yet loaded, using fallback setpoint %s°C",
                    room_name,
                    set_temp,
                )

            room_temp = self._room_temperature(room)

            if set_temp is None or room_temp is None:
                _LOGGER.debug(
                    "[%s] %s has no usable TRV/sensor reading, skipping",
                    zone.get(ZONE_NAME),
                    room_name,
                )
                continue

            if room_temp < set_temp:
                needs_heat = True
                diff = round(set_temp - room_temp, 1)
                demand_lines.append(
                    f"{room_name}: Set {set_temp}°C, Room {room_temp}°C (Δ {diff}°C)"
                )
                _LOGGER.debug(
                    "  %s: Set %s°C, Room %s°C -> DEMANDING (Δ %s°C)",
                    room_name,
                    set_temp,
                    room_temp,
                    diff,
                )
            else:
                _LOGGER.debug(
                    "  %s: Set %s°C, Room %s°C -> satisfied",
                    room_name,
                    set_temp,
                    room_temp,
                )

        return needs_heat, demand_lines

    def _room_setpoint(self, room: dict[str, Any]) -> float | None:
        """Highest setpoint among the room's active TRVs, or None."""
        values: list[float] = []
        for trv in room.get(ROOM_TRVS, []) or []:
            state = self.hass.states.get(trv)
            if state is None or state.state in UNAVAILABLE_STATES:
                continue
            raw = state.attributes.get("temperature")
            if raw in UNAVAILABLE_STATES:
                continue
            try:
                values.append(float(raw))
            except (TypeError, ValueError):
                _LOGGER.warning("Could not read setpoint from %s: %r", trv, raw)
        return max(values) if values else None

    def _room_temperature(self, room: dict[str, Any]) -> float | None:
        """Average reading among the room's active temperature sensors, or None."""
        values: list[float] = []
        for sensor in room.get(ROOM_SENSORS, []) or []:
            state = self.hass.states.get(sensor)
            if state is None or state.state in UNAVAILABLE_STATES:
                continue
            try:
                values.append(float(state.state))
            except (TypeError, ValueError):
                _LOGGER.warning("Could not read temperature from %s: %r", sensor, state.state)
        if not values:
            return None
        return round(sum(values) / len(values), 2)

    def _find_room(self, room_id: str) -> dict[str, Any] | None:
        for zone in self.zones:
            for room in zone.get(ZONE_ROOMS, []):
                if room.get(ROOM_ID) == room_id:
                    return room
        return None

    def _find_room_for_trv(self, entity_id: str) -> dict[str, Any] | None:
        for zone in self.zones:
            for room in zone.get(ZONE_ROOMS, []):
                if entity_id in (room.get(ROOM_TRVS) or []):
                    return room
        return None

    def room_current_temperature(self, room_id: str) -> float | None:
        """Public wrapper for ZealRoomThermostat.current_temperature."""
        room = self._find_room(room_id)
        if room is None:
            return None
        return self._room_temperature(room)

    def own_thermostat_entity_ids(self) -> set[str]:
        """entity_id of every currently-loaded ZealRoomThermostat.

        Used as a hard guard against ever writing a setpoint to one of our
        own entities as if it were a physical TRV - see the incident this
        guards against in the Decisions Log. Belt-and-braces alongside the
        config_flow.py fix that stops such an entity being *selectable* in
        the first place: this catches it even for a config saved before
        that fix existed, without requiring the user to notice and fix
        their saved config first.
        """
        return {
            t.entity_id
            for t in self.room_thermostats.values()
            if getattr(t, "entity_id", None)
        }

    async def async_propagate_room_setpoint(self, room_id: str, temp: float) -> None:
        """Push a new setpoint to every TRV configured for this room.

        Called both when a user adjusts the room's ZealRoomThermostat
        directly, and when an unexpected change on any one physical TRV in
        the room is detected (see _async_handle_external_trv_change) - in
        both cases every TRV in the room should end up showing the same
        setpoint, since the thermostat is the room's single source of
        truth, not any individual TRV.
        """
        room = self._find_room(room_id)
        if room is None:
            _LOGGER.debug("Can't propagate setpoint - unknown room_id %s", room_id)
            return
        room_name = room.get(ROOM_NAME, room_id)
        own_entities = self.own_thermostat_entity_ids()
        trvs = [t for t in (room.get(ROOM_TRVS, []) or []) if t not in own_entities]
        skipped = (room.get(ROOM_TRVS, []) or [])
        skipped = [t for t in skipped if t in own_entities]
        if skipped:
            _LOGGER.error(
                "[%s] Room's TRV list includes ZEAL's own entity/entities %s - "
                "refusing to propagate to them (this would recurse infinitely). "
                "Reopen Configure for this room and remove them from the TRV "
                "list; they should no longer be offered as an option.",
                room_name,
                skipped,
            )
        _LOGGER.debug("[%s] Propagating %s°C to %d TRV(s)", room_name, temp, len(trvs))
        for trv in trvs:
            state = self.hass.states.get(trv)
            if state is None or state.state in UNAVAILABLE_STATES:
                _LOGGER.warning(
                    "[%s] Can't propagate setpoint to unavailable TRV %s", room_name, trv
                )
                continue
            self._last_written_setpoint[trv] = temp
            await self.hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": trv, "temperature": temp},
                blocking=True,
            )
            _LOGGER.debug("  -> %s set to %s°C", trv, temp)

    async def _async_handle_external_trv_change(
        self, room: dict[str, Any], entity_id: str, new_temp: Any
    ) -> None:
        """A physical TRV's setpoint changed and it wasn't us who wrote it.

        Update the room's thermostat to match (so it displays the real
        current setpoint) and propagate that value to every other TRV in
        the room, so a manual change on any one TRV becomes the room's new
        setpoint everywhere, not just on the TRV someone happened to touch.
        """
        try:
            new_temp = float(new_temp)
        except (TypeError, ValueError):
            _LOGGER.debug("Ignoring non-numeric TRV temperature: %r", new_temp)
            return

        last_written = self._last_written_setpoint.get(entity_id)
        if last_written is not None and abs(last_written - new_temp) < 0.01:
            # This matches what we ourselves just wrote to this TRV - not a
            # new manual change, just our own propagation being read back.
            _LOGGER.debug(
                "%s: change to %s°C matches our own last write, ignoring (loop guard)",
                entity_id,
                new_temp,
            )
            return

        room_id = room.get(ROOM_ID)
        room_name = room.get(ROOM_NAME, room_id)
        _LOGGER.debug(
            "%s: genuine manual change to %s°C, updating room %s and propagating",
            entity_id,
            new_temp,
            room_name,
        )
        thermostat = self.room_thermostats.get(room_id)
        if thermostat is not None:
            thermostat.apply_external_setpoint(new_temp)
        await self.async_propagate_room_setpoint(room_id, new_temp)

    # ------------------------------------------------------------------
    # Switch control
    # ------------------------------------------------------------------
    async def _async_apply_zone_switches(
        self, zone: dict[str, Any], needs_heat: bool
    ) -> tuple[bool, bool]:
        """Drive this zone's single heating actuator switch toward needs_heat.

        A zone has exactly one switch (pump/relay) - never more. Confirmed
        against real installs: a shared single-pump house split into
        ground-floor/first-floor zones, a dual-pump house with one switch
        per zone, a hotel with one switch per level.

        Returns (available, off_time_changed):
          * available is False if the configured switch is unavailable
            (nothing could be actuated), True otherwise (including the
            "nothing needed to change" case).
          * off_time_changed is True only if this call newly recorded an
            OFF transition for this zone - i.e. an actual state change
            happened just now, not merely that the zone has ever turned
            off at some point in the past. Callers use this to decide
            whether a Store write is actually warranted this cycle.
        """
        zone_id = zone[ZONE_ID]
        zone_name = zone.get(ZONE_NAME, zone_id)
        now = dt_util.utcnow()
        off_time_changed = False

        override = self.override_switches.get(zone_id)
        if override is not None and getattr(override, "is_on", False):
            _LOGGER.debug("[%s] Manual override active — skipping automatic control", zone_name)
            return True, False

        entity_id = zone.get(ZONE_SWITCH)
        state = self.hass.states.get(entity_id)
        if state is None or state.state in UNAVAILABLE_STATES:
            _LOGGER.warning("[%s] Switch %s is unavailable, skipping", zone_name, entity_id)
            return False, False

        blocked_by_delay = False
        if needs_heat:
            last_off = self._last_off_time.get(zone_id)
            if last_off is not None:
                reenable_delay = zone.get(ZONE_REENABLE_DELAY, DEFAULT_REENABLE_DELAY)
                elapsed = (now - last_off).total_seconds()
                if elapsed < reenable_delay:
                    blocked_by_delay = True
                    remaining = int(reenable_delay - elapsed)
                    _LOGGER.debug(
                        "[%s] Demand present but waiting %ss before re-enabling",
                        zone_name,
                        remaining,
                    )

        if needs_heat:
            if not blocked_by_delay and state.state != "on":
                _LOGGER.debug("[%s] Turning ON %s", zone_name, entity_id)
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": entity_id}, blocking=True
                )
                self._last_on_time[zone_id] = now
            elif state.state == "on":
                _LOGGER.debug("[%s] %s already ON, nothing to do", zone_name, entity_id)
        else:
            if state.state != "off":
                _LOGGER.debug("[%s] Turning OFF %s", zone_name, entity_id)
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": entity_id}, blocking=True
                )
                last_on = self._last_on_time.get(zone_id)
                if last_on is not None:
                    duration_mins = (now - last_on).total_seconds() / 60
                    _LOGGER.debug(
                        "[%s] %s ran for %.1f minutes", zone_name, entity_id, duration_mins
                    )
                self._last_off_time[zone_id] = now
                off_time_changed = True
            else:
                _LOGGER.debug("[%s] %s already OFF, nothing to do", zone_name, entity_id)

        return True, off_time_changed

    async def _async_persist_runtime_state(self) -> None:
        await self.store.async_save(
            {
                CONF_ZONES: self.zones,
                RUNTIME_LAST_OFF: {
                    zone_id: dt.isoformat() for zone_id, dt in self._last_off_time.items()
                },
            }
        )
