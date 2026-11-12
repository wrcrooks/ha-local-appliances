"""Diagnostics: everything needed to write a decoder for a new model."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import Hub

TO_REDACT = {"HomeApSsid"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    hub: Hub = entry.runtime_data
    devices = {}
    for device_id, appliance in hub.appliances.items():
        session = appliance.session
        devices[device_id] = {
            **hub.known.get(device_id, {}),
            "profile": appliance.PROFILE,
            "available": appliance.available,
            "state": appliance.state,
            "rssi": session.rssi if session else None,
            "app_info": async_redact_data(session.app_info, TO_REDACT) if session else None,
            "platform_info": session.platform_info if session else None,
            # [unix time, rx|tx|ack, hex] - most recent last
            "frames": [list(item) for item in session.capture] if session else [],
        }
    return {"config": dict(entry.data), "options": dict(entry.options), "devices": devices}
