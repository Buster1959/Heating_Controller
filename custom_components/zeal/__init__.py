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
from homeassistant.helpers.storage import Store

from .const import CONF_ZONES, DOMAIN, STORAGE_KEY_FMT, STORAGE_VERSION
from .coordinator import ZealCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["switch", "sensor", "climate"]


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

    # First evaluation + switch pass happens after platforms are set up, so
    # the override switches (switch.py) have already registered themselves
    # with the coordinator and are respected on this very first run.
    await coordinator.async_config_entry_first_refresh()

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
