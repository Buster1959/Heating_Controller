"""Config Flow and Options Flow for ASHP Zone Control.

Milestone-1 scope:
  * ConfigFlow just names the integration instance. (One-time JSON import
    from ashp_rooms.json is deliberately NOT implemented here yet - it's
    listed in the project doc as a later step, and a stub that silently did
    nothing would be worse than no field at all.)
  * OptionsFlow is the actual "admin surface" this scaffold exists to
    exercise against a real HA instance. A Zone is a user-named group of
    Rooms (e.g. "Ground Floor") with its own heating actuator switch(es); a
    Room IS an HA Area assigned to exactly one Zone. Flow:
      1. init            -> zone menu: add / edit / remove a zone, or finish
      2. select_rooms     -> per zone: multi-select which Areas are this
                              zone's rooms (an Area picked here is removed
                              from any other zone it was previously in)
      3. zone_details     -> per zone: name it (e.g. "Ground Floor") and
                              pick its heating actuator switch(es) - single
                              or multiple
      4. room_entities    -> per room: auto-discovered TRVs and temperature
                              sensors already in that Area, tick which ones
                              are active for this room

No Store writes happen here in a way that reaches the Coordinator (there is
no Coordinator yet). Options are persisted to config_entry.options via the
standard async_create_entry() options-flow mechanism, which IS the correct
long-term home for this data - __init__.py already copies it into a Store
on next milestone. This file exists to answer one question: does the
zones/rooms/trvs/sensors schema survive being populated with real areas and
real entities from your own HA instance?
"""
from __future__ import annotations

import uuid
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
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
    ZONE_SWITCHES,
)


class AshpZoneControlConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of a single integration instance."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input["name"].lower().strip())
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input["name"],
                data={},
                options={CONF_ZONES: []},
            )

        schema = vol.Schema(
            {
                vol.Required("name", default="ASHP Zone Control"): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        # Do NOT pass config_entry in here and store it on self — current
        # HA core exposes self.config_entry as a read-only property set by
        # the framework after instantiation. Assigning it yourself raises
        # at runtime (surfaces to the user as a bare 500 on the options
        # dialog). Load from self.config_entry lazily instead, see
        # AshpZoneControlOptionsFlow._ensure_loaded().
        return AshpZoneControlOptionsFlow()


class AshpZoneControlOptionsFlow(OptionsFlow):
    """Options Flow: manage Zones, each a named group of Rooms (Areas) with
    their own heating actuator switch(es) and per-room active TRVs/sensors.
    """

    def __init__(self) -> None:
        # Working copy of zones for the duration of this flow session.
        # Populated on first step via _ensure_loaded(), not here — self.hass
        # and self.config_entry aren't guaranteed to be set until the
        # framework has finished instantiating the flow.
        self._loaded = False
        self._zones: list[dict[str, Any]] = []
        self._current_zone_id: str | None = None
        # Queue of room_ids (Area ids) still needing a pass through the
        # per-room TRV/sensor picker, for the zone currently being edited.
        self._room_queue: list[str] = []
        self._current_room_id: str | None = None

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._zones = [
                dict(z, rooms=[dict(r) for r in z.get(ZONE_ROOMS, [])])
                for z in self.config_entry.options.get(CONF_ZONES, [])
            ]
            self._loaded = True

    # ------------------------------------------------------------------
    # Step 1: zone menu — add a zone, edit one, remove one, or finish
    # ------------------------------------------------------------------
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        self._ensure_loaded()
        area_registry = ar.async_get(self.hass)

        if not area_registry.async_list_areas():
            return self.async_abort(reason="no_areas_defined")

        if user_input is not None:
            action = user_input["action"]
            if action == "add_zone":
                zone_id = str(uuid.uuid4())
                self._zones.append(
                    {
                        ZONE_ID: zone_id,
                        ZONE_NAME: f"Zone {len(self._zones) + 1}",
                        ZONE_ROOMS: [],
                        ZONE_SWITCHES: [],
                    }
                )
                self._current_zone_id = zone_id
                return await self.async_step_select_rooms()
            if action.startswith("edit_"):
                self._current_zone_id = action[len("edit_") :]
                return await self.async_step_select_rooms()
            if action.startswith("remove_"):
                zone_id = action[len("remove_") :]
                self._zones = [z for z in self._zones if z[ZONE_ID] != zone_id]
                return await self.async_step_init()
            # action == "done"
            return self._async_save()

        options = [
            {"value": "add_zone", "label": "+ Add a new zone"},
            *[
                {
                    "value": f"edit_{z[ZONE_ID]}",
                    "label": f"Edit: {z[ZONE_NAME]} ({len(z[ZONE_ROOMS])} room(s))",
                }
                for z in self._zones
            ],
            *[
                {"value": f"remove_{z[ZONE_ID]}", "label": f"Remove: {z[ZONE_NAME]}"}
                for z in self._zones
            ],
            {"value": "done", "label": "Done → Save"},
        ]

        zone_summary = (
            ", ".join(z[ZONE_NAME] for z in self._zones)
            if self._zones
            else "(none yet)"
        )

        schema = vol.Schema(
            {
                vol.Required("action", default="add_zone"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options, mode=selector.SelectSelectorMode.LIST
                    )
                )
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={"zone_summary": zone_summary},
        )

    # ------------------------------------------------------------------
    # Step 2: pick which Areas are this zone's rooms
    # ------------------------------------------------------------------
    async def async_step_select_rooms(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        zone = self._get_current_zone()
        area_registry = ar.async_get(self.hass)
        all_areas = list(area_registry.async_list_areas())
        existing_room_ids = {r[ROOM_ID] for r in zone[ZONE_ROOMS]}

        if user_input is not None:
            selected_area_ids = set(user_input.get("areas", []))
            area_by_id = {area.id: area for area in all_areas}

            # An Area can only belong to one zone - claiming it here steals
            # it from wherever else it was previously assigned.
            for other in self._zones:
                if other[ZONE_ID] == zone[ZONE_ID]:
                    continue
                other[ZONE_ROOMS] = [
                    r for r in other[ZONE_ROOMS] if r[ROOM_ID] not in selected_area_ids
                ]

            existing_rooms_by_id = {r[ROOM_ID]: r for r in zone[ZONE_ROOMS]}
            new_rooms: list[dict[str, Any]] = []
            for area_id in selected_area_ids:
                area = area_by_id.get(area_id)
                if area is None:
                    continue
                if area_id in existing_rooms_by_id:
                    room = existing_rooms_by_id[area_id]
                    room[ROOM_NAME] = area.name
                    new_rooms.append(room)
                else:
                    new_rooms.append(
                        {
                            ROOM_ID: area_id,
                            ROOM_NAME: area.name,
                            ROOM_TRVS: [],
                            ROOM_SENSORS: [],
                            ROOM_ACTIVE: True,
                        }
                    )
            zone[ZONE_ROOMS] = new_rooms

            return await self.async_step_zone_details()

        schema = vol.Schema(
            {
                vol.Optional(
                    "areas", default=list(existing_room_ids)
                ): selector.AreaSelector(selector.AreaSelectorConfig(multiple=True))
            }
        )

        return self.async_show_form(
            step_id="select_rooms",
            data_schema=schema,
            description_placeholders={"zone_name": zone[ZONE_NAME]},
        )

    # ------------------------------------------------------------------
    # Step 3: name the zone and pick its heating actuator switch(es)
    # ------------------------------------------------------------------
    async def async_step_zone_details(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        zone = self._get_current_zone()

        if user_input is not None:
            zone[ZONE_NAME] = user_input["name"]
            zone[ZONE_SWITCHES] = user_input.get("switches", [])
            self._room_queue = [r[ROOM_ID] for r in zone[ZONE_ROOMS]]
            return await self._async_step_next_room()

        room_summary = (
            ", ".join(r[ROOM_NAME] for r in zone[ZONE_ROOMS])
            if zone[ZONE_ROOMS]
            else "(none)"
        )

        schema = vol.Schema(
            {
                vol.Required("name", default=zone[ZONE_NAME]): str,
                vol.Optional(
                    "switches", default=zone.get(ZONE_SWITCHES, [])
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="switch", multiple=True)
                ),
            }
        )

        return self.async_show_form(
            step_id="zone_details",
            data_schema=schema,
            description_placeholders={
                "current_name": zone[ZONE_NAME],
                "room_summary": room_summary,
            },
        )

    async def _async_step_next_room(self) -> Any:
        """Pop the next room off the queue and hand it to the entity picker."""
        if not self._room_queue:
            self._current_zone_id = None
            return await self.async_step_init()

        self._current_room_id = self._room_queue.pop(0)
        return await self.async_step_room_entities()

    # ------------------------------------------------------------------
    # Step 4: choose which auto-discovered TRVs/sensors are active
    # ------------------------------------------------------------------
    async def async_step_room_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        zone = self._get_current_zone()
        room = self._get_current_room(zone)

        if user_input is not None:
            room[ROOM_TRVS] = user_input.get("trvs", [])
            room[ROOM_SENSORS] = user_input.get("sensors", [])
            room[ROOM_ACTIVE] = user_input.get("active", True)
            return await self._async_step_next_room()

        discovered_trvs = self._discover_area_entities(room[ROOM_ID], "climate")
        discovered_sensors = self._discover_area_entities(
            room[ROOM_ID], "sensor", device_class="temperature"
        )

        # Default to "everything discovered is active" the first time a
        # room is visited; on later visits, keep only the previous picks
        # that are still discoverable so removed/renamed entities don't
        # linger silently.
        previous_trvs = [e for e in room.get(ROOM_TRVS, []) if e in discovered_trvs]
        trv_default = previous_trvs if room.get(ROOM_TRVS) else discovered_trvs

        previous_sensors = [
            e for e in room.get(ROOM_SENSORS, []) if e in discovered_sensors
        ]
        sensor_default = (
            previous_sensors if room.get(ROOM_SENSORS) else discovered_sensors
        )

        trv_selector_config: dict[str, Any] = {"domain": "climate", "multiple": True}
        if discovered_trvs:
            trv_selector_config["include_entities"] = discovered_trvs

        sensor_selector_config: dict[str, Any] = {
            "domain": "sensor",
            "device_class": "temperature",
            "multiple": True,
        }
        if discovered_sensors:
            sensor_selector_config["include_entities"] = discovered_sensors

        schema = vol.Schema(
            {
                vol.Optional(
                    "active", default=room.get(ROOM_ACTIVE, True)
                ): selector.BooleanSelector(),
                vol.Optional("trvs", default=trv_default): selector.EntitySelector(
                    selector.EntitySelectorConfig(**trv_selector_config)
                ),
                vol.Optional(
                    "sensors", default=sensor_default
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(**sensor_selector_config)
                ),
            }
        )

        return self.async_show_form(
            step_id="room_entities",
            data_schema=schema,
            description_placeholders={
                "room_name": room[ROOM_NAME],
                "zone_name": zone[ZONE_NAME],
                "trv_count": str(len(discovered_trvs)),
                "sensor_count": str(len(discovered_sensors)),
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _get_current_zone(self) -> dict[str, Any]:
        for zone in self._zones:
            if zone[ZONE_ID] == self._current_zone_id:
                return zone
        raise KeyError(f"Unknown zone_id in flow state: {self._current_zone_id}")

    def _get_current_room(self, zone: dict[str, Any]) -> dict[str, Any]:
        for room in zone[ZONE_ROOMS]:
            if room[ROOM_ID] == self._current_room_id:
                return room
        raise KeyError(f"Unknown room_id in flow state: {self._current_room_id}")

    def _discover_area_entities(
        self, area_id: str, domain: str, device_class: str | None = None
    ) -> list[str]:
        """Entities in `area_id` matching domain/device_class.

        Covers both entities assigned to the Area directly and entities
        that inherit their Area from their device.
        """
        entity_registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)
        matches: list[str] = []

        for entry in entity_registry.entities.values():
            if entry.domain != domain:
                continue
            if entry.disabled_by is not None:
                continue
            if device_class is not None:
                entry_device_class = entry.device_class or entry.original_device_class
                if entry_device_class != device_class:
                    continue

            effective_area_id = entry.area_id
            if effective_area_id is None and entry.device_id:
                device = device_registry.async_get(entry.device_id)
                effective_area_id = device.area_id if device else None

            if effective_area_id == area_id:
                matches.append(entry.entity_id)

        return matches

    def _friendly(self, entity_id: str) -> str:
        """Friendly name + entity_id, for the save summary."""
        state = self.hass.states.get(entity_id)
        name = state.name if state else entity_id
        return f"{name} (`{entity_id}`)"

    def _build_summary_markdown(self) -> str:
        """Render Zone -> Room -> active TRVs/sensors as a Markdown list for
        the options-flow success dialog."""
        if not self._zones:
            return "_No zones configured._"

        lines: list[str] = []
        for zone in self._zones:
            switches = zone.get(ZONE_SWITCHES) or []
            switch_text = (
                ", ".join(self._friendly(e) for e in switches)
                if switches
                else "_none_"
            )
            lines.append(f"- **{zone[ZONE_NAME]}** — switch(es): {switch_text}")

            rooms = zone.get(ZONE_ROOMS) or []
            if not rooms:
                lines.append("  - _no rooms_")
                continue

            for room in rooms:
                active_tag = "" if room.get(ROOM_ACTIVE, True) else " _(inactive)_"
                lines.append(f"  - {room[ROOM_NAME]}{active_tag}")
                trvs = room.get(ROOM_TRVS) or []
                sensors = room.get(ROOM_SENSORS) or []
                if not trvs and not sensors:
                    lines.append("    - _no active TRVs or sensors_")
                    continue
                for trv in trvs:
                    lines.append(f"    - TRV: {self._friendly(trv)}")
                for sensor in sensors:
                    lines.append(f"    - Sensor: {self._friendly(sensor)}")

        return "\n".join(lines)

    @callback
    def _async_save(self) -> Any:
        return self.async_create_entry(
            title="",
            data={CONF_ZONES: self._zones},
            description_placeholders={"summary": self._build_summary_markdown()},
        )
