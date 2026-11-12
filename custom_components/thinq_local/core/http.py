"""The four HTTPS endpoints a ThinQ2 modem calls while provisioning.

GET  /route                         -> where the API and MQTT servers live
GET  /route/certificate             -> names of the trust roots on offer
GET  /route/certificate?name=...    -> the trust root (our CA)
POST /device/<uuid>/certificate     -> sign the modem's CSR

A hand-rolled HTTP/1.1 responder is enough for that, and keeps the whole
server on plain asyncio streams with nothing to install.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable
from urllib.parse import parse_qs, urlsplit

_LOGGER = logging.getLogger(__name__)

MAX_HEADER = 16 * 1024
MAX_BODY = 64 * 1024
TIMEOUT = 30

OK = "0000"


class HttpApi:
    def __init__(
        self,
        api_url: str,
        mqtt_url: str,
        ca_pem: str,
        sign_csr: Callable[[str], Awaitable[str]],
    ):
        self._api_url = api_url
        self._mqtt_url = mqtt_url
        self._ca_pem = ca_pem
        self._sign_csr = sign_csr

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while await self._one_request(reader, writer):
                pass
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            asyncio.TimeoutError,
            ConnectionError,
            ValueError,
        ) as err:
            _LOGGER.debug("HTTP connection ended: %r", err)
        finally:
            writer.close()

    async def _one_request(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> bool:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), TIMEOUT)
        except asyncio.IncompleteReadError as err:
            if not err.partial:
                return False  # idle keep-alive connection closed by the peer
            raise
        if len(head) > MAX_HEADER:
            raise ValueError("oversized request head")

        lines = head.decode("latin-1").split("\r\n")
        method, target, version = lines[0].split(" ", 2)
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()

        length = int(headers.get("content-length", "0"))
        if length > MAX_BODY:
            raise ValueError("oversized request body")
        body = (
            await asyncio.wait_for(reader.readexactly(length), TIMEOUT) if length else b""
        )

        _LOGGER.debug("HTTPS %s %s (Host: %s)", method, target, headers.get("host"))
        status, content_type, payload = await self._route(method, target, body)

        keep_alive = (
            version == "HTTP/1.1" and headers.get("connection", "").lower() != "close"
        )
        writer.write(
            (
                f"HTTP/1.1 {status}\r\n"
                f"Content-Type: {content_type}\r\n"
                f"Content-Length: {len(payload)}\r\n"
                f"Connection: {'keep-alive' if keep_alive else 'close'}\r\n\r\n"
            ).encode()
            + payload
        )
        await writer.drain()
        return keep_alive

    async def _route(
        self, method: str, target: str, body: bytes
    ) -> tuple[str, str, bytes]:
        url = urlsplit(target)
        path = url.path.rstrip("/")

        if method == "GET" and path == "/route":
            return _json(
                {
                    "resultCode": OK,
                    "result": {"apiServer": self._api_url, "mqttServer": self._mqtt_url},
                }
            )

        if method == "GET" and path == "/route/certificate":
            if parse_qs(url.query).get("name"):
                return _json(
                    {"resultCode": OK, "result": {"certificatePem": self._ca_pem}}
                )
            return _json({"resultCode": OK, "result": ["common-server", "aws-iot"]})

        parts = path.split("/")
        if (
            method == "POST"
            and len(parts) == 4
            and parts[1] == "device"
            and parts[3] == "certificate"
        ):
            try:
                csr = str(json.loads(body)["csr"])
                pem = await self._sign_csr(csr)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("could not sign a certificate for %s: %s", parts[2], err)
                return _json({"resultCode": "9999", "result": {}}, "500 Internal Server Error")
            _LOGGER.info("signed a certificate for appliance %s", parts[2])
            return _json({"resultCode": OK, "result": {"certificatePem": pem}})

        # What the real service answers for everything else
        return "200 OK", "text/xml;charset=utf-8", b""


def _json(data: object, status: str = "200 OK") -> tuple[str, str, bytes]:
    return status, "application/json; charset=utf-8", json.dumps(data).encode()
