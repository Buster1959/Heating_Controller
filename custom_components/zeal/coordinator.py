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
one sensor per room): a room can have *multiple* TRVs and/or sensors. There
is no precedent for how to combine them, so a decision had to be made here
rather than guessed at:
  * Room temperature = the **average** of all its active sensors' readings
    (reduces single-sensor noise; standard practice for multi-sensor rooms).
  * Room setpoint = the **highest** setpoint among its TRVs (if any one TRV
    in the room wants it warmer, the room counts as demanding heat).
This is a reasonable default, not a verified requirement - worth checking
against how you'd actually want a multi-TRV room to behave.
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
        self.hass.async_create_task(self.async_request_refresh())

    # ------------------------------------------------------------------
    # Core evaluation loop
    # ------------------------------------------------------------------
    async def _async_update_data(self) -> dict[str, ZoneStatus]:
        results: dict[str, ZoneStatus] = {}
        off_time_changed = False

        for zone in self.zones:
            zone_id = zone[ZONE_ID]
            zone_name = zone.get(ZONE_NAME, zone_id)

            if not zone.get(ZONE_SWITCH):
                # Nothing to control for this zone - skip entirely, same as
                # ashp_controller.py's `if "switch" not in floor: continue`.
                continue

            needs_heat, demand_lines = self._evaluate_zone(zone)
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
            if not room.get(ROOM_ACTIVE, True):
                continue

            room_name = room.get("name", room.get("room_id", "unknown room"))
            set_temp = self._room_setpoint(room)
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
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": entity_id}, blocking=True
                )
                self._last_on_time[zone_id] = now
        else:
            if state.state != "off":
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
