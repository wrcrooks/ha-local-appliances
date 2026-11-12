"""Appliance decoders: shared plumbing.

An `Appliance` outlives the connections of the module it describes, so its
entities stay put (as unavailable) while a machine is unplugged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, ClassVar, Iterable

from ..cloud import Session


class Codes:
    """Two-way map between an appliance's numeric codes and display labels.

    A label may own several codes; the first is the one a write would use.
    """

    def __init__(self, table: dict[str, int | Iterable[int] | None]):
        self.options: tuple[str, ...] = tuple(table)
        self._forward: dict[int, str] = {}
        self._inverse: dict[str, int] = {}
        for label, codes in table.items():
            if codes is None:
                continue
            listed = [codes] if isinstance(codes, int) else list(codes)
            if listed:
                self._inverse[label] = listed[0]
            for code in listed:
                self._forward[code] = label

    def label(self, code: int) -> str | None:
        return self._forward.get(code)

    def code(self, label: str) -> int | None:
        return self._inverse.get(label)


@dataclass(frozen=True)
class Field:
    """One Home Assistant entity an appliance profile exposes."""

    key: str
    platform: str  # "sensor" | "binary_sensor" | "button"
    name: str
    icon: str | None = None
    device_class: str | None = None
    unit: str | None = None
    options: tuple[str, ...] | None = None
    diagnostic: bool = False


class Appliance:
    PROFILE: ClassVar[str] = ""
    KIND: ClassVar[str] = "appliance"  # default device name: "LG <Kind>"
    FIELDS: ClassVar[tuple[Field, ...]] = ()

    def __init__(self, device_id: str):
        self.id = device_id
        self.session: Session | None = None
        self.state: dict[str, Any] = {}
        self._listeners: set[Callable[[], None]] = set()

    @property
    def available(self) -> bool:
        return self.session is not None and self.session.connected

    def listen(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(callback)
        return lambda: self._listeners.discard(callback)

    def notify(self) -> None:
        for callback in list(self._listeners):
            callback()

    def attach(self, session: Session) -> None:
        self.session = session
        session.on_packet = self._packet
        self.on_connect()
        self.notify()

    def detach(self, session: Session) -> None:
        if self.session is session:
            session.on_packet = None
            self.notify()

    def _packet(self, packet: bytes) -> None:
        before = dict(self.state)
        self.handle_packet(packet)
        if self.state != before:
            self.notify()

    # -- for profiles to override -----------------------------------------

    def on_connect(self) -> None:
        """Called on every (re)connection, e.g. to ask for a status snapshot."""

    def handle_packet(self, packet: bytes) -> None:
        raise NotImplementedError

    def press(self, key: str) -> None:
        """A button entity was pressed."""
