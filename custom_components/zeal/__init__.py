"""The ZEAL HVAC System integration.

Milestone 2: the Coordinator now owns the actual control loop (see
coordinator.py) - reads TRVs/sensors, decides per-zone demand, drives the
configured heating switches, subject to the re-enable delay and each zone's
manual override switch (switch.py). A diagnostic demand sensor is created
per zone too (sensor.py).

entry.options remains the Options Flow's source of truth (what the user
configured); the Store is a separate on-disk copy the Coordinator uses for
its own runtime state (last-off timestamps for the re-enable delay) without
writing back into config options, which would trigger the update listener
below and reload the entry in a loop.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import CONF_ZONES, DOMAIN, ROOM_ID, STORAGE_KEY_FMT, STORAGE_VERSION, ZONE_ID, ZONE_ROOMS
from .coordinator import ZealCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["switch", "sensor", "climate"]


async def _async_cleanup_orphaned_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove devices/entities for zones or rooms no longer in config.

    Home Assistant does not automatically remove entities or devices when
    a config entry's saved options shrink - every async_add_entities call
    only ever *adds*. Without this, renaming or removing a zone/room in
    Configure leaves the old device/entities behind permanently, silently
    accumulating "ghost" devices - found via a real incident where two old
    zone devices ("Ground Floor", "First Floor") persisted for good after
    the zones were rebuilt as "Zone 1"/"Zone 2".

    Two separate checks, since they catch different cases:
      * A whole ZONE removed -> its device (identified by zone_id) no
        longer matches any current zone -> remove the device, which
        cascades to remove every entity registered under it (the zone's
        override switch, demand sensor, and every room's thermostat, since
        all of a zone's room thermostats share that zone's device).
      * A ROOM removed from a zone that still exists -> the zone's device
        is still valid, so the check above won't catch it. Its
        ZealRoomThermostat's own unique_id embeds the room_id directly
        (f"{entry.entry_id}_{room_id}_thermostat"), checked independently.
    """
    zones = entry.options.get(CONF_ZONES, [])
    valid_zone_ids = {z[ZONE_ID] for z in zones}
    valid_room_ids = {r[ROOM_ID] for z in zones for r in z.get(ZONE_ROOMS, [])}

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_"

    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        for domain, identifier in device.identifiers:
            if domain != DOMAIN or not identifier.startswith(prefix):
                continue
            zone_id = identifier[len(prefix):]
            if zone_id not in valid_zone_ids:
                _LOGGER.info(
                    "Removing orphaned device for a zone no longer in config: %s (%s)",
                    device.name,
                    device.id,
                )
                device_registry.async_remove_device(device.id)

    suffix = "_thermostat"
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if not entity.unique_id or not entity.unique_id.endswith(suffix):
            continue
        if not entity.unique_id.startswith(prefix):
            continue
        room_id = entity.unique_id[len(prefix):-len(suffix)]
        if room_id not in valid_room_ids:
            _LOGGER.info(
                "Removing orphaned thermostat entity for a room no longer in config: %s",
                entity.entity_id,
            )
            entity_registry.async_remove(entity.entity_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ZEAL HVAC System from a config entry."""
    store: Store = Store(
        hass, STORAGE_VERSION, STORAGE_KEY_FMT.format(entry_id=entry.entry_id)
    )
    # Mirror the current Options Flow data into the Store. This re-runs on
    # every reload (see _async_update_listener), so the Store is always in
    # sync with the latest saved zones/rooms/TRVs/sensors.
    await store.async_save({CONF_ZONES: entry.options.get(CONF_ZONES, [])})

    coordinator = ZealCoordinator(hass, entry, store)
    await coordinator.async_setup()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {"store": store, "coordinator": coordinator}

    # Re-run setup whenever the Options Flow saves changes, so anything
    # reading hass.data picks up the new zones/rooms immediately.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Runs after platforms load (not before) so the currently-valid
    # entities have already been (re-)registered and won't be mistaken
    # for orphans - only genuinely stale devices/entities from a
    # zone/room that no longer exists in config get removed.
    await _async_cleanup_orphaned_entities(hass, entry)

    # First evaluation + switch pass happens after platforms are set up, so
    # the override switches (switch.py) have already registered themselves
    # with the coordinator and are respected on this very first run.
    await coordinator.async_config_entry_first_refresh()

    # Logged here, not inside coordinator.async_setup(), specifically
    # because it needs to run *after* platforms have loaded - the banner
    # reports each room's registered ZealRoomThermostat, which doesn't
    # exist yet at the point async_setup() runs (climate.py hasn't been
    # forwarded/set up that early). Logging it before that point would
    # make every room show "not yet registered" always, regardless of
    # whether anything was actually wrong - misleading noise, not signal.
    await coordinator.async_log_startup_banner()

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update by reloading the config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data is not None:
            data["coordinator"].async_teardown()
    return unload_ok
