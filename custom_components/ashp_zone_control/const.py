"""Constants for the ASHP Zone Control integration."""

DOMAIN = "ashp_zone_control"

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
ZONE_SWITCHES = "switches"

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
