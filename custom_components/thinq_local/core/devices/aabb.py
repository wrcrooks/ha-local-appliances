"""The AA..BB framing spoken by LG laundry (and most non-HVAC) appliances.

    AA <total length> <inner ...> <checksum> BB

`inner` starts with a class byte (0x20 washer, 0x30 dryer, ...) followed by a
frame type. 0xEB carries one status record, 0xEC the previous record followed
by the current one.
"""

from __future__ import annotations

from .base import Appliance

FRAME_ENVELOPE = 0x0A
ENVELOPE_SEQ = 5
ENVELOPE_MIN_LEN = 13
LONG_FRAME = 0xFF  # AA FF ...: long framing with a 16-bit checksum

# f0 00 <type> 04 [<seq16>], sent under the MQTT `ack` command.
CLOUD_ACK = (0xF0, 0x00)
CLOUD_ACK_STATUS = 0x04
# What the real cloud leaves unacknowledged: the appliance's own acks, the
# c3 heartbeat, and bare eb/ec status records.
UNACKED_TYPES = frozenset({0x00, 0xC3, 0xEB, 0xEC})

# "Report your state": the read-only query US washers and dryers expect on
# every connect. Without it they send only identity and heartbeat frames.
STATUS_REQUEST = bytes.fromhex("F0ED1121010000001800")


def frame(inner: bytes) -> bytes:
    packet = bytearray([0xAA, len(inner) + 4]) + inner + b"\x00\x00"
    packet[-2] = (sum(packet) & 0xFF) ^ 0x55
    packet[-1] = 0xBB
    return bytes(packet)


def unframe(packet: bytes) -> bytes | None:
    if len(packet) >= 4 and packet[0] == 0xAA and packet[-1] == 0xBB:
        return packet[2:-2]
    return None


def ack_for(packet: bytes) -> bytes | None:
    """The acknowledgement the cloud would send for `packet`, if any.

    Unacknowledged, some appliances repeat every frame up to ten times,
    re-deploy, and never start streaming status records.
    """
    inner = unframe(packet)
    if inner is None or len(inner) < 2:
        return None
    kind = inner[1]
    if kind in UNACKED_TYPES:
        return None
    payload = [*CLOUD_ACK, kind, CLOUD_ACK_STATUS]
    if kind == FRAME_ENVELOPE:
        long_frame = packet[1] == LONG_FRAME
        if len(inner) - (1 if long_frame else 0) < ENVELOPE_MIN_LEN:
            return None
        payload += inner[ENVELOPE_SEQ : ENVELOPE_SEQ + 2]
    return frame(bytes(payload))


class AabbAppliance(Appliance):
    AUTO_ACK = True
    QUERY_ON_CONNECT = True

    def send(self, inner: bytes) -> None:
        if self.session:
            self.session.send_packet(frame(inner))

    def on_connect(self) -> None:
        if self.QUERY_ON_CONNECT:
            self.send(STATUS_REQUEST)

    def handle_packet(self, packet: bytes) -> None:
        inner = unframe(packet)
        if inner is None:
            return
        if self.AUTO_ACK and self.session:
            ack = ack_for(packet)
            if ack:
                self.session.send_ack(ack)
        self.handle_frame(inner)

    def handle_frame(self, inner: bytes) -> None:
        raise NotImplementedError

    @staticmethod
    def current_record(inner: bytes, class_byte: int, length: int) -> bytes | None:
        """The live status record of a plain EB/EC frame, or None.

        In an EC frame the *second* record is current: consecutive frames
        chain, each one's first record repeating its predecessor's second.
        """
        if len(inner) < 2 or inner[0] != class_byte:
            return None
        if inner[1] == 0xEB and len(inner) == 2 + length:
            return inner[2:]
        if inner[1] == 0xEC and len(inner) == 2 + 2 * length:
            return inner[2 + length :]
        return None
