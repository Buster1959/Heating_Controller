"""Diagnostics support for ZEAL.

Answers "what is currently configured" directly from the integration's own
UI (Settings -> Devices & Services -> ZEAL HVAC System -> the "..." menu on
the integration card -> Download diagnostics) rather than requiring anyone
to reopen the Options Flow and click through every zone/room to piece it
together, or dig through debug logs. This was directly motivated by a real
debugging session where the lack of any such view made a config mistake
(a ZealRoomThermostat entity accidentally selected as its own room's TRV,
causing infinite propagation recursion - see the Decisions Log) much
harder to spot than it needed to be.

This is HA's standard, idiomatic mechanism for "let a user see what an
integration currently holds" - not a custom dashboard tab (Home Assistant
doesn't have a way to add a custom tab to an integration's Settings page),
and lighter-weight than building a whole bespoke read-only UI. The bundled
Lovelace card originally planned for Milestone 4 remains the fuller,
always-visible answer to the same underlying need; this is the "right now,
one click, structured JSON" version of it.
"""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, ROOM_ID, ROOM_NAME, ROOM_SENSORS, ROOM_TRVS, ZONE_ID, ZONE_NAME, ZONE_ROOMS, ZONE_SWITCH
from .coordinator import ZealCoordinator

# Anything that could plausibly be sensitive if this diagnostics dump got
# pasted into a public GitHub issue. None of ZEAL's config is actually
# sensitive (no credentials, no location data) - kept as an explicit empty
# set rather than skipping redaction entirely, so it's obvious this was a
# deliberate check, not an oversight, if the schema ever grows a field that
# would need it.
TO_REDACT: set[str] = set()


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: ZealCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    zones_snapshot: list[dict[str, Any]] = []
    for zone in coordinator.zones:
        zone_id = zone.get(ZONE_ID)
        switch_entity = zone.get(ZONE_SWITCH)
        switch_state = hass.states.get(switch_entity) if switch_entity else None

        rooms_snapshot: list[dict[str, Any]] = []
        for room in zone.get(ZONE_ROOMS, []):
            room_id = room.get(ROOM_ID)
            thermostat = coordinator.room_thermostats.get(room_id)

            trv_snapshot = []
            for trv in room.get(ROOM_TRVS, []) or []:
                state = hass.states.get(trv)
                trv_snapshot.append(
                    {
                        "entity_id": trv,
                        "state": state.state if state else "not_found",
                        "target_temperature": (
                            state.attributes.get("temperature") if state else None
                        ),
                        "is_zeal_own_entity": trv
                        in coordinator.own_thermostat_entity_ids(),
                    }
                )

            sensor_snapshot = []
            for sensor in room.get(ROOM_SENSORS, []) or []:
                state = hass.states.get(sensor)
                sensor_snapshot.append(
                    {
                        "entity_id": sensor,
                        "state": state.state if state else "not_found",
                    }
                )

            rooms_snapshot.append(
                {
                    "room_id": room_id,
                    "name": room.get(ROOM_NAME),
                    "active": room.get("active", True),
                    "trvs": trv_snapshot,
                    "sensors": sensor_snapshot,
                    "computed_room_temperature": coordinator.room_current_temperature(
                        room_id
                    ),
                    "thermostat": {
                        "entity_id": getattr(thermostat, "entity_id", None),
                        "target_temperature": getattr(
                            thermostat, "target_temperature", None
                        ),
                        "hvac_mode": getattr(thermostat, "hvac_mode", None),
                        "registered_with_coordinator": thermostat is not None,
                    }
                    if thermostat is not None
                    else {"registered_with_coordinator": False},
                }
            )

        zones_snapshot.append(
            {
                "zone_id": zone_id,
                "name": zone.get(ZONE_NAME),
                "heat_source": zone.get("heat_source"),
                "reenable_delay": zone.get("reenable_delay"),
                "switch": {
                    "entity_id": switch_entity,
                    "state": switch_state.state if switch_state else "not_found",
                },
                "override_active": getattr(
                    coordinator.override_switches.get(zone_id), "is_on", None
                ),
                "rooms": rooms_snapshot,
            }
        )

    return {
        "entry": {
            "title": entry.title,
            "version": entry.version,
        },
        "zones": zones_snapshot,
    }
