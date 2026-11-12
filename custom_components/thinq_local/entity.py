"""Base entity: one `Field` of one `Appliance`."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import Hub
from .core.devices import Appliance, Field


class ThinqEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: Hub, appliance: Appliance, field: Field) -> None:
        self._appliance = appliance
        self._key = field.key
        self._attr_unique_id = f"{appliance.id}_{field.key}"
        self._attr_name = field.name
        self._attr_icon = field.icon
        self._attr_device_info = hub.device_info(appliance)
        if field.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return self._appliance.available

    async def async_added_to_hass(self) -> None:
        # Appliances push; this is the only update path.
        self.async_on_remove(self._appliance.listen(self.async_write_ha_state))


def setup_platform(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    platform: str,
    factory: Callable[[Hub, Appliance, Field], ThinqEntity],
) -> None:
    """Add `platform` entities for known appliances and for ones yet to connect."""
    hub: Hub = entry.runtime_data

    @callback
    def add(appliance: Appliance) -> None:
        async_add_entities(
            factory(hub, appliance, field)
            for field in appliance.FIELDS
            if field.platform == platform
        )

    for appliance in hub.appliances.values():
        add(appliance)
    entry.async_on_unload(async_dispatcher_connect(hass, hub.signal_new, add))
