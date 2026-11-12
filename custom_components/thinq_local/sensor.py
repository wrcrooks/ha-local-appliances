"""Sensors."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import Hub
from .core.devices import Appliance, Field
from .entity import ThinqEntity, setup_platform

UNITS = {"min": UnitOfTime.MINUTES}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    setup_platform(hass, entry, async_add_entities, "sensor", ThinqSensor)


class ThinqSensor(ThinqEntity, SensorEntity):
    def __init__(self, hub: Hub, appliance: Appliance, field: Field) -> None:
        super().__init__(hub, appliance, field)
        if field.device_class:
            self._attr_device_class = SensorDeviceClass(field.device_class)
        if field.options:
            self._attr_options = list(field.options)
        if field.unit:
            self._attr_native_unit_of_measurement = UNITS.get(field.unit, field.unit)

    @property
    def native_value(self) -> StateType:
        return self._appliance.state.get(self._key)
