"""Config and options flows."""

from __future__ import annotations

import asyncio
import ipaddress
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import (
    CONF_BIND,
    CONF_HOSTNAME,
    CONF_HTTPS_ADVERTISE_PORT,
    CONF_HTTPS_PORT,
    CONF_MQTTS_ADVERTISE_PORT,
    CONF_MQTTS_PORT,
    DEFAULT_HOSTNAME,
    DEFAULT_HTTPS_PORT,
    DEFAULT_MQTTS_PORT,
    DOMAIN,
    OPT_PROFILES,
)
from .core.devices import AUTO, PROFILES
from .core.pki import is_plausible_hostname

PORT = vol.All(vol.Coerce(int), vol.Range(min=1, max=65535))


def _valid_hostname(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
    except ValueError:
        # Wi-Fi modules resolve through plain DNS only: no mDNS
        return is_plausible_hostname(name) and not name.endswith(".local")
    return False


async def _can_listen(bind: str, *ports: int) -> str | None:
    """Try the ports now, so a clash shows in the form and not in the log."""
    for port in ports:
        try:
            server = await asyncio.start_server(lambda r, w: w.close(), bind or None, port)
        except OSError as err:
            return f"{port}: {err.strerror or err}"
        server.close()
        await server.wait_closed()
    return None


class ThinqLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_HOSTNAME: user_input[CONF_HOSTNAME].strip().lower(),
                CONF_HTTPS_PORT: user_input[CONF_HTTPS_PORT],
                CONF_MQTTS_PORT: user_input[CONF_MQTTS_PORT],
                CONF_BIND: user_input.get(CONF_BIND, "").strip(),
            }
            for key in (CONF_HTTPS_ADVERTISE_PORT, CONF_MQTTS_ADVERTISE_PORT):
                if user_input.get(key):
                    data[key] = user_input[key]
            if not _valid_hostname(data[CONF_HOSTNAME]):
                errors[CONF_HOSTNAME] = "invalid_hostname"
            elif problem := await _can_listen(
                data[CONF_BIND], data[CONF_HTTPS_PORT], data[CONF_MQTTS_PORT]
            ):
                errors["base"] = "port_in_use"
                placeholders["error"] = problem
            else:
                return self.async_create_entry(title="LG ThinQ Local", data=data)

        defaults = user_input or {}
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HOSTNAME,
                        default=defaults.get(CONF_HOSTNAME, DEFAULT_HOSTNAME),
                    ): str,
                    vol.Required(
                        CONF_HTTPS_PORT,
                        default=defaults.get(CONF_HTTPS_PORT, DEFAULT_HTTPS_PORT),
                    ): PORT,
                    vol.Optional(
                        CONF_HTTPS_ADVERTISE_PORT,
                        description={
                            "suggested_value": defaults.get(CONF_HTTPS_ADVERTISE_PORT)
                        },
                    ): PORT,
                    vol.Required(
                        CONF_MQTTS_PORT,
                        default=defaults.get(CONF_MQTTS_PORT, DEFAULT_MQTTS_PORT),
                    ): PORT,
                    vol.Optional(
                        CONF_MQTTS_ADVERTISE_PORT,
                        description={
                            "suggested_value": defaults.get(CONF_MQTTS_ADVERTISE_PORT)
                        },
                    ): PORT,
                    vol.Optional(CONF_BIND, default=defaults.get(CONF_BIND, "")): str,
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ProfileOptionsFlow()


class ProfileOptionsFlow(OptionsFlow):
    """Point an appliance at a decoder profile other than the automatic one."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        hub = getattr(self.config_entry, "runtime_data", None)
        known = hub.known if hub else {}
        if not known:
            return self.async_abort(reason="no_devices")

        if user_input is not None:
            profiles = dict(self.config_entry.options.get(OPT_PROFILES, {}))
            if user_input["profile"] == AUTO:
                profiles.pop(user_input["device"], None)
            else:
                profiles[user_input["device"]] = user_input["profile"]
            return self.async_create_entry(data={OPT_PROFILES: profiles})

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): vol.In(
                        {
                            device_id: f"{info['model_id']} ({device_id[:8]})"
                            for device_id, info in known.items()
                        }
                    ),
                    vol.Required("profile", default=AUTO): vol.In([AUTO, *PROFILES]),
                }
            ),
        )
