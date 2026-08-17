"""Per-zone manual override switch for ZEAL HVAC System.

Replaces the old `input_boolean.<floor>_heating_override` helper - which had
to be hand-created by whoever set the system up - with a switch entity the
integration creates itself for every configured zone. When ON, the
Coordinator leaves that zone's heating switch(es) alone entirely (see
coordinator.py's `_async_apply_zone_switches`).

The entity registers itself into `coordinator.override_switches[zone_id]` on
add and removes itself on removal, so the Coordinator can check `.is_on`
directly rather than going through hass.states.get() with a guessed
entity_id.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, ZONE_ID, ZONE_NAME, ZONE_SWITCH
from .coordinator import ZealCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one override switch per configured zone."""
    coordinator: ZealCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = [
        ZealOverrideSwitch(coordinator, entry, zone)
        for zone in coordinator.zones
        if zone.get(ZONE_SWITCH)  # only for zones that actually have a switch to override
    ]
    async_add_entities(entities)


class ZealOverrideSwitch(SwitchEntity, RestoreEntity):
    """Manual "hands off this zone" switch. Defaults OFF (automatic control)."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:hand-back-right-off"
    _attr_should_poll = False

    def __init__(
        self, coordinator: ZealCoordinator, entry: ConfigEntry, zone: dict[str, Any]
    ) -> None:
        self._coordinator = coordinator
        self._zone_id = zone[ZONE_ID]
        zone_name = zone.get(ZONE_NAME, self._zone_id)

        self._attr_unique_id = f"{entry.entry_id}_{self._zone_id}_override"
        self._attr_name = "Manual override"
        self._attr_is_on = False
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{self._zone_id}")},
            name=zone_name,
            manufacturer="ZEAL HVAC System",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._attr_is_on = last_state.state == "on"
        self._coordinator.override_switches[self._zone_id] = self

    async def async_will_remove_from_hass(self) -> None:
        self._coordinator.override_switches.pop(self._zone_id, None)
        await super().async_will_remove_from_hass()

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()
        await self._coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()
        await self._coordinator.async_request_refresh()
