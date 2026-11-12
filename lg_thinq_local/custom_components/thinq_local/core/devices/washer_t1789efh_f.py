"""LG top-load washer, ThinQ model id T1789EFH_F (sold as WT7300CW).

Record layout, code tables and commands are from rethink's driver for this
model, where each was checked against captures from a real machine. Offsets
are relative to the record's 0x19 marker.

Frames (inner[0] == 0x20):
  0xEC  previous record + current record (27 bytes each, current is last)
  0xEB  single record: reply to a status query / sent after reconnect
  0xE2  settings echo - deliberately ignored: it replays a stale snapshot
  0x31  identity, 0xD8 heartbeat - ignored
"""

from __future__ import annotations

from .aabb import AabbAppliance
from .base import Codes, Field

CLASS_BYTE = 0x20
RECORD_LEN = 27  # one lead byte + the 26-byte record that starts with 0x19

PHASE = 1
TIME_H, TIME_M = 2, 3  # live countdown
INITIAL_H, INITIAL_M = 4, 5  # the cycle's total estimate
COURSE = 6
SOIL, SPIN, TEMP, RINSE = 8, 9, 10, 11
RESERVE_H, RESERVE_M = 13, 14  # Delay Wash
FLAGS = 15
FLAG_CHILD_LOCK, FLAG_DOOR_LOCK, FLAG_TURBO_WASH = 0x01, 0x04, 0x40
OPT2 = 16
OPT2_EXTRA_RINSE, OPT2_WATER_PLUS, OPT2_STAIN_CARE, OPT2_SOAK = 0x01, 0x02, 0x08, 0x20
REMOTE_START = 17
SMART_COURSE_ACTIVE = 21
DOWNLOADED_COURSE = 24

PHASE_OFF, PHASE_PAUSED = 0x00, 0x02

STATUS = Codes(
    {
        "Off": 0x00,
        "Standby": 0x01,
        "Paused": 0x02,
        "Detecting": 0x03,
        "Soaking": 0x04,
        "Washing": 0x05,
        "Rinsing": 0x06,
        "Spinning": 0x07,
        "End": 0x08,
        "Delayed": 0x09,
        "Updating firmware": 0x0A,
        "Smart Diagnosis": 0x0B,
    }
)

COURSES = Codes(
    {
        "Not selected": 0x00,
        "Normal": 0x01,
        "Heavy Duty": 0x02,
        "Delicates": 0x04,
        "Waterproof": 0x05,
        "Speed Wash": 0x07,
        "Bedding": 0x08,
        "Tub Clean": 0x0C,
        "Rinse+Spin": 0x11,
        "Spin Only": 0x12,
        "Deep Wash": 0x19,
        "Pre Wash+Normal": 0x29,
    }
)

SMART_COURSES = Codes(
    {
        "Not selected": 0x00,
        "Small Load": 0x64,
        "Single Item": 0x65,
        "Wrinkle Stop/Gentle Wave": 0x67,
        "Rapid Clean": 0x68,
        "Sweat Stains": 0x69,
        "Swimwear": 0x6A,
        "Curtains": 0x6B,
        "Dress Shirts": 0x6D,
        "Collar Stains": 0x6E,
        "Pillow Covers": 0x6F,
        "Lingerie": 0x70,
        "Baby Wear": 0x71,
        "Children's Wear": 0x73,
        "Juice & Food Stains": 0x74,
        "Baby Bibs": 0x75,
        "Pet Hair and Odor": 0x76,
        "EconoWash": 0x77,
        "Collars and Cuffs": 0x78,
        "Delicate Dresses": 0x79,
        "Half Load Wash": 0x7A,
        "Full Load Wash": 0x7B,
        "Whites": 0x83,
    }
)

SOIL_LEVELS = Codes(
    {
        "Not selected": 0,
        "Light": 1,
        "Light-Normal": 2,
        "Normal": 3,
        "Normal-Heavy": 4,
        "Heavy": 5,
    }
)

SPIN_SPEEDS = Codes(
    {
        "Not selected": None,  # an option only; "off" is reported this way
        "No Spin": 0,
        "Low": 1,
        "Medium": 2,
        "High": 3,
        "Extra High": 4,
    }
)

TEMPERATURES = Codes(
    {
        "Not selected": 0,
        "Tap Cold": 1,
        "Cold": 2,
        "Semi Warm": 3,
        "Warm": 4,
        "Hot": 5,
        "Extra Hot": 6,
    }
)

# f0 26 | course soil spin temp rinse | 00 x7 | flags opt2 | verb
# The settings are authoritative: the washer runs what the packet says, so
# the current record is echoed back and Start runs what is dialled in.
CMD_START = (0xF0, 0x26)
VERB_START, VERB_RESUME = 0x03, 0x01
CMD_PAUSE = bytes([0xF0, 0x24, 0x04, 0x01, 0x00])
CMD_POWER_OFF = bytes([0xF0, 0x24, 0x01, 0x01, 0x00])


class Washer(AabbAppliance):
    PROFILE = "T1789EFH_F"
    KIND = "Washer"
    FIELDS = (
        Field("power", "binary_sensor", "Power", "mdi:washing-machine"),
        Field("status", "sensor", "Status", "mdi:state-machine", "enum", options=STATUS.options),
        Field("remaining_time", "sensor", "Remaining time", "mdi:timer-outline", "duration", "min"),
        Field("initial_time", "sensor", "Initial time estimate", "mdi:clock-outline", "duration", "min", diagnostic=True),
        Field("course", "sensor", "Course", "mdi:pin-outline", "enum", options=COURSES.options),
        Field("smart_course", "sensor", "Smart course", "mdi:cloud-download-outline", "enum", options=SMART_COURSES.options),
        Field("downloaded_course", "sensor", "Downloaded cycle", "mdi:tray-arrow-down", "enum", options=SMART_COURSES.options, diagnostic=True),
        Field("delay_wash", "sensor", "Delay wash", "mdi:timer-sand", "duration", "min"),
        Field("soil", "sensor", "Soil level", "mdi:liquid-spot", "enum", options=SOIL_LEVELS.options),
        Field("spin", "sensor", "Spin speed", "mdi:rotate-3d-variant", "enum", options=SPIN_SPEEDS.options),
        Field("temp", "sensor", "Temperature", "mdi:thermometer", "enum", options=TEMPERATURES.options),
        Field("door_lock", "binary_sensor", "Door lock", device_class="lock"),
        Field("child_lock", "binary_sensor", "Child lock", "mdi:lock-alert", diagnostic=True),
        Field("remote_start", "binary_sensor", "Remote start", "mdi:cellphone-wireless", diagnostic=True),
        Field("turbo_wash", "binary_sensor", "Turbo Wash", "mdi:speedometer"),
        Field("extra_rinse", "binary_sensor", "Extra Rinse", "mdi:water-sync"),
        Field("water_plus", "binary_sensor", "Water Plus", "mdi:water-plus"),
        Field("stain_care", "binary_sensor", "Stain Care", "mdi:liquid-spot"),
        Field("soak", "binary_sensor", "Soak", "mdi:water-outline"),
        Field("start", "button", "Start", "mdi:play"),
        Field("pause", "button", "Pause", "mdi:pause"),
        Field("power_off", "button", "Power off", "mdi:power"),
    )

    def __init__(self, device_id: str):
        super().__init__(device_id)
        self._record: bytes | None = None

    def handle_frame(self, inner: bytes) -> None:
        lead = self.current_record(inner, CLASS_BYTE, RECORD_LEN)
        if lead is not None:
            self._decode(lead[1:])

    def _decode(self, rec: bytes) -> None:
        self._record = rec
        phase = rec[PHASE]
        off = phase == PHASE_OFF
        flags = rec[FLAGS]
        # Powered off the record keeps the last cycle's leftovers (the Turbo
        # Wash bit, the countdown); report them cleared instead.
        cycle_flags = 0 if off else flags
        opt2 = 0 if off else rec[OPT2]
        self.state.update(
            power=not off,
            status=STATUS.label(phase),
            remaining_time=0 if off else rec[TIME_H] * 60 + rec[TIME_M],
            initial_time=0 if off else rec[INITIAL_H] * 60 + rec[INITIAL_M],
            course=COURSES.label(rec[COURSE]),
            smart_course=SMART_COURSES.label(rec[SMART_COURSE_ACTIVE]),
            downloaded_course=SMART_COURSES.label(rec[DOWNLOADED_COURSE]),
            delay_wash=rec[RESERVE_H] * 60 + rec[RESERVE_M],
            soil=SOIL_LEVELS.label(rec[SOIL]),
            # "No Spin" and "machine off" both read 0
            spin="Not selected" if off else SPIN_SPEEDS.label(rec[SPIN]),
            temp=TEMPERATURES.label(rec[TEMP]),
            # Home Assistant's lock class: on = unlocked
            door_lock=not flags & FLAG_DOOR_LOCK,
            child_lock=bool(flags & FLAG_CHILD_LOCK),
            remote_start=bool(rec[REMOTE_START] & 0x01),
            turbo_wash=bool(cycle_flags & FLAG_TURBO_WASH),
            extra_rinse=bool(opt2 & OPT2_EXTRA_RINSE),
            water_plus=bool(opt2 & OPT2_WATER_PLUS),
            stain_care=bool(opt2 & OPT2_STAIN_CARE),
            soak=bool(opt2 & OPT2_SOAK),
        )

    def press(self, key: str) -> None:
        rec = self._record
        if rec is None:
            return
        if key == "pause":
            self.send(CMD_PAUSE)
        elif key == "power_off":
            self.send(CMD_POWER_OFF)
        elif key == "start":
            # Remote Start can only be armed at the panel; an unarmed start is
            # ignored by the washer itself, so nothing is re-checked here.
            verb = VERB_RESUME if rec[PHASE] == PHASE_PAUSED else VERB_START
            self.send(
                bytes(
                    [
                        *CMD_START,
                        rec[COURSE],
                        rec[SOIL],
                        rec[SPIN],
                        rec[TEMP],
                        rec[RINSE],
                        0, 0, 0, 0, 0, 0, 0,
                        # the app never sends the door lock bit back
                        rec[FLAGS] & ~FLAG_DOOR_LOCK & 0xFF,
                        rec[OPT2],  # verbatim, including the 0x10 bit
                        verb,
                    ]
                )
            )
