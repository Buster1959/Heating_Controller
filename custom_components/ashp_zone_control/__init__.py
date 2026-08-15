"""The ASHP Zone Control integration.

Skeleton stage (milestone 1): sets up a Store-backed config entry and wires
in the Options Flow for zone/room/TRV/sensor management. No coordinator or
control-loop logic yet — that lands in milestone 2.

entry.options is the Options Flow's source of truth (what the user
configured); the Store is a separate on-disk copy intended for the
Coordinator to build on in milestone 2 - e.g. adding its own runtime state
(last switch state, timers) without writing back into config options, which
would trigger the update listener below and reload the entry in a loop.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import CONF_ZONES, DOMAIN, STORAGE_KEY_FMT, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)

# No platforms yet — switch/sensor entities land in milestone 2.
PLATFORMS: list[str] = []


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ASHP Zone Control from a config entry."""
    store: Store = Store(
        hass, STORAGE_VERSION, STORAGE_KEY_FMT.format(entry_id=entry.entry_id)
    )
    # Mirror the current Options Flow data into the Store. This re-runs on
    # every reload (see _async_update_listener), so the Store is always in
    # sync with the latest saved zones/rooms/TRVs/sensors.
    await store.async_save({CONF_ZONES: entry.options.get(CONF_ZONES, [])})

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {"store": store}

    # Re-run setup whenever the Options Flow saves changes, so anything
    # reading hass.data picks up the new zones/rooms immediately.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    if PLATFORMS:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update by reloading the config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = True
    if PLATFORMS:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
