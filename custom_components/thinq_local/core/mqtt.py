"""A deliberately tiny MQTT 3.1.1 broker.

It exists only so ThinQ2 modems have something to connect to. Published
messages are handed to a callback instead of being relayed between clients,
and the only outbound traffic is what the cloud emulation sends to a modem's
own subscriptions. No retained messages, no sessions, QoS 0 downstream.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Callable

_LOGGER = logging.getLogger(__name__)

MAX_PACKET = 256 * 1024
IDLE_TIMEOUT = 300  # modems ping well inside this

CONNECT, CONNACK, PUBLISH, PUBACK, PUBREC, PUBREL, PUBCOMP = 1, 2, 3, 4, 5, 6, 7
SUBSCRIBE, SUBACK, UNSUBSCRIBE, UNSUBACK, PINGREQ, PINGRESP, DISCONNECT = (
    8, 9, 10, 11, 12, 13, 14,
)


def encode_length(n: int) -> bytes:
    out = bytearray()
    while True:
        digit = n % 128
        n //= 128
        out.append(digit | 0x80 if n else digit)
        if not n:
            return bytes(out)


def _packet(kind: int, flags: int, body: bytes = b"") -> bytes:
    return bytes([kind << 4 | flags]) + encode_length(len(body)) + body


def _string(data: bytes, pos: int) -> tuple[bytes, int]:
    end = pos + 2 + int.from_bytes(data[pos : pos + 2], "big")
    if end > len(data):
        raise ValueError("truncated MQTT string")
    return data[pos + 2 : end], end


def publish_packet(topic: str, payload: bytes) -> bytes:
    raw = topic.encode()
    return _packet(PUBLISH, 0, len(raw).to_bytes(2, "big") + raw + payload)


def _filter_regex(topic_filter: str) -> re.Pattern[str]:
    parts = []
    for level in topic_filter.split("/"):
        if level == "#":
            parts.append(".*")
            break
        parts.append("[^/]*" if level == "+" else re.escape(level))
    return re.compile("^" + "/".join(parts) + "$")


class Client:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.client_id = ""
        self.subscriptions: dict[str, re.Pattern[str]] = {}
        self.will: tuple[str, bytes] | None = None
        self.peer = writer.get_extra_info("peername")
        self.closed = False

    def publish(self, topic: str, payload: bytes) -> bool:
        """Deliver if this client subscribed to `topic`."""
        if self.closed or not any(p.match(topic) for p in self.subscriptions.values()):
            return False
        self.writer.write(publish_packet(topic, payload))
        return True

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.writer.close()


class Broker:
    def __init__(
        self,
        on_publish: Callable[[Client, str, bytes], None],
        on_disconnect: Callable[[Client], None],
    ):
        self._on_publish = on_publish
        self._on_disconnect = on_disconnect
        self.clients: set[Client] = set()

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        client = Client(reader, writer)
        self.clients.add(client)
        clean = False
        try:
            clean = await self._serve(client)
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.TimeoutError):
            pass
        except ValueError as err:
            _LOGGER.debug("MQTT protocol error from %s: %s", client.peer, err)
        except Exception:  # noqa: BLE001 - one bad client must not take the broker down
            _LOGGER.exception("MQTT client %s failed", client.peer)
        finally:
            self.clients.discard(client)
            client.close()
            if client.will and not clean:
                self._dispatch(client, *client.will)
            self._on_disconnect(client)

    def close_all(self) -> None:
        for client in list(self.clients):
            client.close()

    def _dispatch(self, client: Client, topic: str, payload: bytes) -> None:
        try:
            self._on_publish(client, topic, payload)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("handler failed for a message on %s", topic)

    async def _read_packet(self, client: Client) -> tuple[int, int, bytes]:
        header = await asyncio.wait_for(client.reader.readexactly(1), IDLE_TIMEOUT)
        length = shift = 0
        while True:
            byte = (await client.reader.readexactly(1))[0]
            length |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            shift += 7
            if shift > 21:
                raise ValueError("malformed remaining length")
        if length > MAX_PACKET:
            raise ValueError(f"packet of {length} bytes exceeds the limit")
        body = await client.reader.readexactly(length) if length else b""
        return header[0] >> 4, header[0] & 0x0F, body

    async def _serve(self, client: Client) -> bool:
        kind, _, body = await self._read_packet(client)
        if kind != CONNECT:
            raise ValueError("first packet is not CONNECT")
        self._connect(client, body)
        client.writer.write(_packet(CONNACK, 0, b"\x00\x00"))

        while True:
            kind, flags, body = await self._read_packet(client)
            if kind == PUBLISH:
                qos = (flags >> 1) & 3
                topic, pos = _string(body, 0)
                if qos:
                    packet_id = body[pos : pos + 2]
                    pos += 2
                    client.writer.write(_packet(PUBACK if qos == 1 else PUBREC, 0, packet_id))
                self._dispatch(client, topic.decode("utf-8", "replace"), body[pos:])
            elif kind == PUBREL:
                client.writer.write(_packet(PUBCOMP, 0, body[:2]))
            elif kind == SUBSCRIBE:
                pos, granted = 2, bytearray()
                while pos < len(body):
                    topic, pos = _string(body, pos)
                    pos += 1  # requested QoS; everything is granted at 0
                    text = topic.decode("utf-8", "replace")
                    client.subscriptions[text] = _filter_regex(text)
                    granted.append(0)
                client.writer.write(_packet(SUBACK, 0, body[:2] + bytes(granted)))
            elif kind == UNSUBSCRIBE:
                pos = 2
                while pos < len(body):
                    topic, pos = _string(body, pos)
                    client.subscriptions.pop(topic.decode("utf-8", "replace"), None)
                client.writer.write(_packet(UNSUBACK, 0, body[:2]))
            elif kind == PINGREQ:
                client.writer.write(_packet(PINGRESP, 0))
            elif kind == DISCONNECT:
                return True
            # PUBACK and friends for our QoS 0 publishes never arrive; ignore the rest
            await client.writer.drain()

    @staticmethod
    def _connect(client: Client, body: bytes) -> None:
        _proto, pos = _string(body, 0)
        pos += 1  # protocol level: 3.1 and 3.1.1 are both fine
        flags = body[pos]
        pos += 3  # flags + keepalive
        client_id, pos = _string(body, pos)
        client.client_id = client_id.decode("utf-8", "replace")
        if flags & 0x04:
            topic, pos = _string(body, pos)
            message, pos = _string(body, pos)
            client.will = (topic.decode("utf-8", "replace"), message)
