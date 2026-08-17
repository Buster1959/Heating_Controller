"""Constants for the ZEAL HVAC System integration."""

DOMAIN = "zeal"

STORAGE_VERSION = 1
STORAGE_KEY_FMT = f"{DOMAIN}_{{entry_id}}"

# Keys used inside config_entry.options
CONF_ZONES = "zones"

# Zone dict keys. A Zone is a user-named group of Rooms (e.g. "Ground
# Floor") with its own heating actuator switch(es) - it is NOT tied 1:1 to
# a single HA Area any more.
ZONE_ID = "zone_id"
ZONE_NAME = "name"
ZONE_ROOMS = "rooms"
# A zone has exactly ONE heating actuator switch (pump/relay) - never more.
# Real installs confirm this: a shared single-pump house split into
# ground-floor/first-floor zones, a dual-pump house with one switch per
# zone, a hotel with one switch per level. Holds an entity_id, or None if
# not yet configured for this zone.
ZONE_SWITCH = "switch"
# Heat source powering this zone. Affects the heating profile (how the
# source ramps output and whether cycling on/off is harmful or normal) and
# is used to pick a sensible *suggested* re-enable delay - see
# HEAT_SOURCE_DEFAULT_REENABLE_DELAY below. Also the same field §9.2 of the
# project doc will reuse for flow-temp/preheat regression once that's
# built, so it's not solely a Milestone 2 concern.
ZONE_HEAT_SOURCE = "heat_source"
# User-editable re-enable delay for this zone, in seconds. Pre-filled from
# HEAT_SOURCE_DEFAULT_REENABLE_DELAY[heat_source] when the zone is first
# configured, but stored as its own explicit value from then on - a
# suggestion, not a locked-in consequence of the heat_source choice.
ZONE_REENABLE_DELAY = "reenable_delay"

HEAT_SOURCE_ASHP = "ashp"
HEAT_SOURCE_MODULATING_BOILER = "modulating_boiler"
HEAT_SOURCE_NON_CONDENSING_BOILER = "non_condensing_boiler"
HEAT_SOURCE_OTHER = "other"
HEAT_SOURCE_OPTIONS = [
    HEAT_SOURCE_ASHP,
    HEAT_SOURCE_MODULATING_BOILER,
    HEAT_SOURCE_NON_CONDENSING_BOILER,
    HEAT_SOURCE_OTHER,
]

# --- v2 (long-term goal, hidden for now): ASHP heating + cooling --------
# Reserved schema fields for a future capability where a zone's ASHP can
# also provide cooling (via a physical retrofit - special radiators/fan
# coils rated for cold water without condensation risk). Not exposed in
# the Options Flow yet and NOT acted on by the Coordinator yet - these
# exist purely so the schema doesn't need another breaking migration once
# the real feature is built. See project doc for the full design
# (capability flag, per-room cooling-capable flag, and the "actively close
# non-cooling-capable rooms' TRVs in cooling mode" logic it requires).
ZONE_ASHP_CAPABILITY = "ashp_capability"
ASHP_CAPABILITY_HEAT_ONLY = "heat_only"
ASHP_CAPABILITY_HEAT_AND_COOL = "heat_and_cool"
# Per-room: does this room have cooling-rated emitters installed? Only
# meaningful when its zone's heat_source is ASHP and ashp_capability is
# heat_and_cool. False for every room until the physical retrofit exists
# and a room is deliberately marked otherwise - a standard radiator must
# NOT be sent cold water.
ROOM_COOLING_CAPABLE = "cooling_capable"

# Room dict keys. A Room IS an HA Area assigned to a Zone - ROOM_ID is the
# HA Area's own id, so an Area can only ever belong to one Zone at a time.
ROOM_ID = "room_id"
ROOM_NAME = "name"
ROOM_TRVS = "trvs"
ROOM_SENSORS = "sensors"
# Manual "does this room take part in heating demand" toggle, ported from
# the `active` flag in the old ashp_rooms.json (e.g. an unoccupied guest
# room, or a room like Ensuite/Bathroom that was permanently disabled).
ROOM_ACTIVE = "active"

# --- Milestone 2: Coordinator ---------------------------------------------
# ashp_controller.py's own changelog: hysteresis was removed in favour of a
# re-enable delay (see PROJECT_MANDATE.md, Decisions Log). DEFAULT_REENABLE_DELAY
# is now only the fallback used for HEAT_SOURCE_OTHER and for any zone saved
# before the per-zone field existed - see ZONE_REENABLE_DELAY above for the
# per-zone, user-editable value actually used by the Coordinator.
DEFAULT_SCAN_INTERVAL = 60  # seconds - periodic fallback poll
DEFAULT_REENABLE_DELAY = 300  # seconds - min time OFF before allowed back ON

# Suggested re-enable delay per heat source, in seconds - a starting point
# reflecting how each source actually behaves, not a hard rule:
#   * ASHP: long, steady, low-temp runs suit it; short-cycling wears the
#     compressor and wastes energy on defrost cycles. Longest delay.
#   * Modulating (condensing) boiler: no compressor to protect, but
#     condensing efficiency still favours steadier running over rapid
#     on/off, so a moderate delay.
#   * Non-condensing boiler: fixed high flow temp, designed to cycle on
#     and off to satisfy demand - minimal harm from re-enabling quickly.
#   * Other/unknown: fall back to the original global default.
# See README "Heat sources and heating profiles" for the full explanation
# this table is based on.
HEAT_SOURCE_DEFAULT_REENABLE_DELAY = {
    HEAT_SOURCE_ASHP: 300,
    HEAT_SOURCE_MODULATING_BOILER: 120,
    HEAT_SOURCE_NON_CONDENSING_BOILER: 60,
    HEAT_SOURCE_OTHER: DEFAULT_REENABLE_DELAY,
}

# Keys inside the runtime Store payload (separate from CONF_ZONES, which is
# just the mirrored config). Store shape:
#   {CONF_ZONES: [...], RUNTIME_LAST_OFF: {zone_id: iso_timestamp}}
RUNTIME_LAST_OFF = "last_off_time"
