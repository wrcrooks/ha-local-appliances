"""Binary sensors."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import Hub
from .core.devices import Appliance, Field
from .entity import ThinqEntity, setup_platform


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    setup_platform(hass, entry, async_add_entities, "binary_sensor", ThinqBinarySensor)


class ThinqBinarySensor(ThinqEntity, BinarySensorEntity):
    def __init__(self, hub: Hub, appliance: Appliance, field: Field) -> None:
        super().__init__(hub, appliance, field)
        if field.device_class:
            self._attr_device_class = BinarySensorDeviceClass(field.device_class)

    @property
    def is_on(self) -> bool | None:
        return self._appliance.state.get(self._key)
