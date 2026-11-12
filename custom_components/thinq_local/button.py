"""Buttons (start / pause / power off, where a profile supports them)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import ThinqEntity, setup_platform


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    setup_platform(hass, entry, async_add_entities, "button", ThinqButton)


class ThinqButton(ThinqEntity, ButtonEntity):
    async def async_press(self) -> None:
        self._appliance.press(self._key)
