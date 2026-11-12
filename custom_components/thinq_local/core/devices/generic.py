"""Fallback for a model id no profile claims yet.

It keeps the appliance connected the way the cloud would (acks, and a status
query for laundry) and surfaces the raw frames, so a real decoder can be
written from what the machine actually sends - see README "Adding a model".
"""

from __future__ import annotations

from .aabb import AabbAppliance, unframe
from .base import Field

# ThinQ DeviceType codes that speak the laundry status-query dialect.
LAUNDRY_TYPES = {"201", "202", "221", "222"}
MAX_STATE_LEN = 255  # Home Assistant's limit for a state string


class Generic(AabbAppliance):
    PROFILE = "generic"
    KIND = "appliance"
    FIELDS = (
        Field("frames", "sensor", "Frames received", "mdi:counter", diagnostic=True),
        Field("frame_type", "sensor", "Last frame type", "mdi:format-list-numbered", diagnostic=True),
        Field("last_status_frame", "sensor", "Last status frame", "mdi:code-braces", diagnostic=True),
        Field("last_frame", "sensor", "Last frame", "mdi:code-braces", diagnostic=True),
    )

    def on_connect(self) -> None:
        if self.session and self.session.device_type in LAUNDRY_TYPES:
            super().on_connect()

    def handle_packet(self, packet: bytes) -> None:
        self.state["frames"] = self.state.get("frames", 0) + 1
        self.state["last_frame"] = packet.hex()[:MAX_STATE_LEN]
        if unframe(packet) is not None:
            super().handle_packet(packet)

    def handle_frame(self, inner: bytes) -> None:
        if len(inner) < 2:
            return
        self.state["frame_type"] = f"{inner[0]:02x} {inner[1]:02x} ({len(inner)} bytes)"
        if inner[1] in (0xEB, 0xEC):
            self.state["last_status_frame"] = inner.hex()[:MAX_STATE_LEN]
