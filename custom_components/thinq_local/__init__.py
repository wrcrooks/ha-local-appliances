"""LG ThinQ Local: Home Assistant as the ThinQ cloud for LG appliances.

Nothing here polls and nothing leaves the LAN. The integration listens for the
appliances' own outbound connections and turns their status frames straight
into entity state - no add-on, no external MQTT broker.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntry, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .const import (
    CONF_BIND,
    CONF_HOSTNAME,
    CONF_HTTPS_ADVERTISE_PORT,
    CONF_HTTPS_PORT,
    CONF_MQTTS_PORT,
    DOMAIN,
    OPT_PROFILES,
)
from .core.cloud import CloudConfig, Session, ThinqCloud
from .core.devices import Appliance, profile_for

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]


class Hub:
    """Owns the listeners and one `Appliance` per LG device ever seen."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.signal_new = f"{DOMAIN}_{entry.entry_id}_new"
        self.appliances: dict[str, Appliance] = {}
        # device id -> {model_id, model_name, sw_version, device_type}; kept so
        # entities exist (unavailable) before an appliance reconnects
        self.known: dict[str, dict[str, Any]] = {}
        self._store: Store[dict[str, dict[str, Any]]] = Store(
            hass, 1, f"{DOMAIN}.{entry.entry_id}"
        )
        self.cloud = ThinqCloud(
            CloudConfig(
                # Under .storage so backups carry the CA: appliances pin it,
                # and losing it means provisioning every one of them again.
                data_dir=Path(hass.config.path(".storage", DOMAIN)),
                hostname=entry.data[CONF_HOSTNAME],
                bind=entry.data.get(CONF_BIND, ""),
                https_port=entry.data[CONF_HTTPS_PORT],
                mqtts_port=entry.data[CONF_MQTTS_PORT],
                https_advertise_port=entry.data.get(CONF_HTTPS_ADVERTISE_PORT),
            ),
            self._session_opened,
            self._session_closed,
        )

    async def async_start(self) -> None:
        self.known = await self._store.async_load() or {}
        for device_id, info in self.known.items():
            self._create(device_id, info)
        try:
            await self.cloud.start()
        except OSError as err:
            raise ConfigEntryNotReady(f"cannot listen for appliances: {err}") from err
        except (RuntimeError, ValueError) as err:
            raise ConfigEntryError(str(err)) from err

    async def async_stop(self) -> None:
        await self.cloud.stop()
        # flush now: a reload must find the appliances a delayed save still held
        await self._store.async_save(self.known)

    def _create(self, device_id: str, info: dict[str, Any]) -> Appliance:
        override = self.entry.options.get(OPT_PROFILES, {}).get(device_id)
        appliance = profile_for(info["model_id"], override)(device_id)
        self.appliances[device_id] = appliance
        return appliance

    def device_info(self, appliance: Appliance) -> DeviceInfo:
        info = self.known[appliance.id]
        name = info["model_name"] if appliance.KIND == "appliance" else appliance.KIND
        return DeviceInfo(
            identifiers={(DOMAIN, appliance.id)},
            manufacturer="LG",
            model=info["model_name"],
            model_id=info["model_id"],
            sw_version=info.get("sw_version"),
            name=f"LG {name}",
        )

    @callback
    def _session_opened(self, session: Session) -> None:
        info = {
            "model_id": session.model_id,
            "model_name": session.model_name,
            "sw_version": session.sw_version,
            "device_type": session.device_type,
        }
        previous = self.known.get(session.id)
        if previous != info:
            self.known[session.id] = info
            self._store.async_delay_save(lambda: self.known, 1)

        appliance = self.appliances.get(session.id)
        if appliance is None:
            appliance = self._create(session.id, info)
            if appliance.PROFILE == "generic":
                _LOGGER.warning(
                    "No decoder for ThinQ model %s (%s) yet: raw frames are "
                    "available from the device's diagnostics download",
                    session.model_id,
                    session.id,
                )
            async_dispatcher_send(self.hass, self.signal_new, appliance)
        elif previous and previous["model_id"] != info["model_id"]:
            # same module, different appliance behind it: rebuild the entities
            self.hass.config_entries.async_schedule_reload(self.entry.entry_id)
            return
        elif previous != info:
            registry = dr.async_get(self.hass)
            device = registry.async_get_device(identifiers={(DOMAIN, session.id)})
            if device:
                registry.async_update_device(device.id, sw_version=session.sw_version)

        appliance.attach(session)

    @callback
    def _session_closed(self, session: Session) -> None:
        appliance = self.appliances.get(session.id)
        if appliance:
            appliance.detach(session)

    @callback
    def async_forget(self, device_id: str) -> bool:
        appliance = self.appliances.get(device_id)
        if appliance and appliance.available:
            return False
        self.appliances.pop(device_id, None)
        if self.known.pop(device_id, None) is not None:
            self._store.async_delay_save(lambda: self.known, 1)
        return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hub = Hub(hass, entry)
    await hub.async_start()
    entry.runtime_data = hub

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Entities left behind by a profile change (e.g. generic -> a real decoder)
    registry = er.async_get(hass)
    wanted = {
        f"{appliance.id}_{field.key}"
        for appliance in hub.appliances.values()
        for field in appliance.FIELDS
    }
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id not in wanted:
            registry.async_remove(entity.entity_id)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
) -> bool:
    """Allow deleting an appliance that is no longer connected."""
    hub: Hub = entry.runtime_data
    return all(
        hub.async_forget(identifier)
        for domain, identifier in device.identifiers
        if domain == DOMAIN
    )
