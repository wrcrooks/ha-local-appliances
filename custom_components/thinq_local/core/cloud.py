"""A LAN stand-in for the LG ThinQ2 cloud.

A provisioned Wi-Fi module only ever talks to "its cloud": an HTTPS API for
enrolment and an MQTT broker for everything after. This module is both, on
plain asyncio, and turns each connected module into a `Session` that the
appliance decoders read from and write to.

Protocol knowledge comes from the rethink project's reverse engineering
(https://github.com/anszom/rethink, GPL-2.0).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import ssl
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .http import HttpApi
from .mqtt import Broker, Client
from .pki import CertAuthority, is_plausible_hostname

_LOGGER = logging.getLogger(__name__)

# Asked for once, by an unprovisioned module looking for its region's servers.
BOOTSTRAP_HOST = "common.lgthinq.com"
MAX_SNI_NAMES = 16
CAPTURE_FRAMES = 300

# Not exported by the ssl module on every Python/OpenSSL combination.
_OP_LEGACY_SERVER_CONNECT = 0x4
_OP_ALLOW_UNSAFE_LEGACY_RENEGOTIATION = 0x40000


@dataclass(frozen=True)
class CloudConfig:
    data_dir: Path
    hostname: str  # must resolve through plain DNS; modems have no mDNS
    bind: str = ""
    https_port: int = 443
    mqtts_port: int = 8883
    # What modems are told to dial, when a port forward sits in between.
    https_advertise_port: int | None = None
    mqtts_advertise_port: int | None = None

    @property
    def api_url(self) -> str:
        return f"https://{self.hostname}:{self.https_advertise_port or self.https_port}"

    @property
    def mqtt_url(self) -> str:
        return f"ssl://{self.hostname}:{self.mqtts_advertise_port or self.mqtts_port}"


class Session:
    """One connected Wi-Fi module, from its deploy message until it drops."""

    def __init__(self, client: Client, deploy: dict[str, Any]):
        self._client = client
        self.id: str = deploy["did"]
        self.model_id: str = str(deploy.get("kind") or "")
        data = deploy.get("data") or {}
        self.app_info: dict[str, Any] = data.get("appInfo") or {}
        self.platform_info: dict[str, Any] = data.get("platformInfo") or {}
        self.model_name: str = str(self.app_info.get("modelName") or self.model_id)
        self.sw_version: str | None = (
            self.app_info.get("softVer") or self.app_info.get("appVersion") or None
        )
        self.device_type: str = str(self.app_info.get("DeviceType") or "")
        self.rssi: int | None = deploy.get("rssi")
        self.connected = True
        self.on_packet: Callable[[bytes], None] | None = None
        # (unix time, "rx"/"tx"/"ack", hex) - what a new decoder gets written from
        self.capture: deque[tuple[float, str, str]] = deque(maxlen=CAPTURE_FRAMES)

    @property
    def topic(self) -> str:
        return f"lime/devices/{self.id}"

    def send(self, cmd: str, kind: int, data: Any) -> None:
        if not self.connected:
            return
        message = {
            "cmd": cmd,
            "type": kind,
            "data": data,
            "did": self.id,
            "mid": int(time.time() * 1000),
        }
        self._client.publish(self.topic, json.dumps(message).encode())

    def send_packet(self, data: bytes) -> None:
        self.capture.append((time.time(), "tx", data.hex()))
        self.send("packet", 1, data.hex().upper())

    def send_ack(self, data: bytes) -> None:
        self.capture.append((time.time(), "ack", data.hex()))
        self.send("ack", 1, data.hex().upper())

    def _received(self, message: dict[str, Any]) -> None:
        if isinstance(message.get("rssi"), int):
            self.rssi = message["rssi"]
        if message.get("cmd") != "device_packet" or not isinstance(message.get("data"), str):
            return
        try:
            packet = bytes.fromhex(message["data"])
        except ValueError:
            return
        self.capture.append((time.time(), "rx", packet.hex()))
        if self.on_packet:
            try:
                self.on_packet(packet)
            except Exception:  # noqa: BLE001 - a decoder bug must not drop the link
                _LOGGER.exception("decoder for %s failed on %s", self.model_id, packet.hex())


class ThinqCloud:
    def __init__(
        self,
        config: CloudConfig,
        on_session: Callable[[Session], None],
        on_session_closed: Callable[[Session], None],
    ):
        self.config = config
        self._on_session = on_session
        self._on_session_closed = on_session_closed
        self._broker = Broker(self._on_publish, self._on_disconnect)
        self._servers: list[asyncio.base_events.Server] = []
        self._ca: CertAuthority | None = None
        self._contexts: dict[str, ssl.SSLContext] = {}
        self._minting: set[str] = set()
        self._deploys: dict[Client, dict[str, Any]] = {}
        self._sessions: dict[Client, Session] = {}
        self._by_id: dict[str, Client] = {}

    @property
    def sessions(self) -> list[Session]:
        return list(self._sessions.values())

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        default = await loop.run_in_executor(None, self._prepare_tls)
        assert self._ca is not None
        api = HttpApi(
            self.config.api_url,
            self.config.mqtt_url,
            self._ca.cert_pem,
            lambda csr: loop.run_in_executor(None, self._ca.sign_csr, csr),
        )
        host = self.config.bind or None
        try:
            self._servers.append(
                await asyncio.start_server(
                    api.handle, host, self.config.https_port, ssl=default
                )
            )
            self._servers.append(
                await asyncio.start_server(
                    self._broker.handle, host, self.config.mqtts_port, ssl=default
                )
            )
        except OSError:
            await self.stop()
            raise
        _LOGGER.info(
            "ThinQ local cloud listening: %s and %s",
            self.config.api_url,
            self.config.mqtt_url,
        )

    async def stop(self) -> None:
        for server in self._servers:
            server.close()
        self._broker.close_all()
        for server in self._servers:
            await server.wait_closed()
        self._servers.clear()

    # -- TLS ---------------------------------------------------------------

    def _prepare_tls(self) -> ssl.SSLContext:
        """Blocking: load (or create) the CA and the certificates we serve."""
        if not is_plausible_hostname(self.config.hostname):
            raise ValueError(f"{self.config.hostname!r} is not a DNS hostname")
        self._ca = CertAuthority.load_or_create(self.config.data_dir)
        for name in (self.config.hostname, BOOTSTRAP_HOST):
            self._contexts[name.lower()] = self._make_context(name)
        default = self._contexts[self.config.hostname.lower()]
        return default

    def _make_context(self, hostname: str) -> ssl.SSLContext:
        assert self._ca is not None
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        # Modem firmware is TLS 1.2 only and prefers legacy ECDHE-CBC suites
        # that current OpenSSL defaults refuse.
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        ctx.set_ciphers("ALL:@SECLEVEL=0")
        ctx.options |= _OP_LEGACY_SERVER_CONNECT | _OP_ALLOW_UNSAFE_LEGACY_RENEGOTIATION
        ctx.load_cert_chain(str(self._ca.server_cert_file(hostname)))
        ctx.sni_callback = self._sni
        return ctx

    def _sni(self, sslobj: ssl.SSLObject, name: str | None, _ctx: ssl.SSLContext) -> None:
        if not name:
            return
        name = name.lower()
        ctx = self._contexts.get(name)
        if ctx is not None:
            sslobj.context = ctx
            return
        # Unknown name: serve the default certificate now (the bootstrap request
        # accepts anything) and have a proper one ready for the retry.
        if (
            is_plausible_hostname(name)
            and name not in self._minting
            and len(self._contexts) + len(self._minting) < MAX_SNI_NAMES
        ):
            self._minting.add(name)
            asyncio.get_running_loop().create_task(self._mint(name))

    async def _mint(self, name: str) -> None:
        try:
            ctx = await asyncio.get_running_loop().run_in_executor(
                None, self._make_context, name
            )
            self._contexts[name] = ctx
            _LOGGER.info("issued a TLS certificate for %s", name)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("could not issue a certificate for %s: %s", name, err)
        finally:
            self._minting.discard(name)

    # -- MQTT / CLIP -------------------------------------------------------

    def _on_publish(self, client: Client, topic: str, payload: bytes) -> None:
        if "clip/" not in topic:
            return
        try:
            message = json.loads(payload.rstrip(b"\x00"))
        except ValueError:
            _LOGGER.debug("unparseable payload on %s: %s", topic, payload.hex())
            return
        if not isinstance(message, dict):
            return
        _LOGGER.debug("rx %s %s", topic, message)

        # Some firmware prefixes the topic (e.g. with an AWS rule path).
        topic = re.sub(r"^.*/clip", "clip", topic)
        cmd = message.get("cmd")
        did = message.get("did")

        deploy = self._deploys.get(client)
        if deploy and topic == f"clip/message/devices/{deploy['did']}":
            if cmd == "completeProvisioning_ack":
                pass
            elif cmd == "req_timesync":
                self._time_sync(client, deploy["did"])
            elif client in self._sessions:
                self._sessions[client]._received(message)  # noqa: SLF001

        if (
            isinstance(did, str)
            and topic == f"clip/provisioning/devices/{did}"
            and cmd in ("preDeploy", "deploy")
        ):
            self._deploys[client] = message
            self._publish(client, did, _deploy_response(did, cmd))
            if client not in self._sessions:
                self._open_session(client, message)

    def _open_session(self, client: Client, deploy: dict[str, Any]) -> None:
        did = deploy["did"]
        stale = self._by_id.get(did)
        if stale is not None and stale is not client:
            _LOGGER.debug("%s reconnected; dropping its old connection", did)
            self._on_disconnect(stale)
            stale.close()
        session = Session(client, deploy)
        self._sessions[client] = session
        self._by_id[did] = client
        _LOGGER.info(
            "appliance connected: %s model=%s type=%s fw=%s",
            did,
            session.model_id,
            session.device_type,
            session.sw_version,
        )
        self._on_session(session)

    def _on_disconnect(self, client: Client) -> None:
        self._deploys.pop(client, None)
        session = self._sessions.pop(client, None)
        if session is None:
            return
        if self._by_id.get(session.id) is client:
            del self._by_id[session.id]
        session.connected = False
        _LOGGER.info("appliance disconnected: %s", session.id)
        self._on_session_closed(session)

    def _publish(self, client: Client, did: str, message: dict[str, Any]) -> None:
        _LOGGER.debug("tx %s %s", did, message)
        client.publish(f"lime/devices/{did}", json.dumps(message).encode())

    def _time_sync(self, client: Client, did: str) -> None:
        now = datetime.now(timezone.utc)
        stamp = bytes(
            [
                now.year % 100,
                now.month - 1,  # zero-based, as the firmware expects
                now.day,
                now.hour,
                now.minute,
                now.second,
                (now.weekday() + 1) % 7,  # Sunday = 0
            ]
        )
        self._publish(
            client,
            did,
            {
                "did": did,
                "mid": int(time.time() * 1000),
                "cmd": "resp_timesync",
                "type": 1,
                "data": base64.b64encode(stamp).decode(),
            },
        )


def _deploy_response(did: str, provisioning_type: str) -> dict[str, Any]:
    return {
        "did": did,
        "mid": int(time.time() * 1000),
        "cmd": "completeProvisioning",
        "type": 0,
        "data": {
            "result": 0,
            "host": "message",
            "appInfo": {
                "host": "message",
                "publication": {
                    "message": f"clip/message/devices/{did}",
                    # The modem caches this path and reuses it on its next
                    # provisioning. Keeping the firmware default means the
                    # appliance still works if it is ever returned to LG's
                    # cloud; do not change it.
                    "provisioning": f"clip/provisioning/devices/{did}",
                },
            },
            "provisioningType": provisioning_type,
            "deployInterval": 600,
        },
    }
