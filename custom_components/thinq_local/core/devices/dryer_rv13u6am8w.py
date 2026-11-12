"""LG 27-inch electric dryer, ThinQ model id RV13U6AM8W_D_US_WIFI (DLE7300WE).

Read-only, as in rethink's driver for this model (its support level there is
"preliminary"). Frames have inner[0] == 0x30; records are 29 bytes.
"""

from __future__ import annotations

from .aabb import AabbAppliance
from .base import Codes, Field

CLASS_BYTE = 0x30
RECORD_LEN = 29

PHASE = 2
REMAINING_MIN = 4
COURSE = 7
ERROR = 8
DRY_LEVEL = 9
TEMP = 10
DRUM = 17  # bit 0x01: drum/blower turning

STATES = Codes(
    {
        "Off": 0,
        "Standby": 1,
        "Drying": (2, 50),
        "Paused": 3,
        "End": 4,
        "Error": 5,
        "Smart Diagnosis": 8,
        "Cooling": 51,
        "Wrinkle care": 56,
        "Delayed": 100,
    }
)

COURSES = Codes(
    {
        "Off": 0x00,
        "Heavy Duty": 0x01,
        "Normal": 0x03,
        "Perm Press": 0x04,
        "Delicates": 0x05,
        "Bedding": 0x07,
        "Speed Dry": 0x10,
        "Air Dry": 0x11,
        "Time Dry": 0x12,
    }
)

DRY_LEVELS = Codes(
    {"Not selected": 0, "Damp": 1, "Less": 2, "Normal": 3, "More": 4, "Very": 5}
)

TEMPERATURES = Codes(
    {"Not selected": 0, "Ultra Low": 1, "Low": 2, "Medium": 3, "Mid High": 4, "High": 5}
)

ERRORS = Codes(
    {
        "OK": 0,
        "Temperature sensor error (tE1)": 1,
        "Temperature sensor error (tE2)": 2,
        "Temperature sensor error (tE5)": 5,
        "Temperature sensor error (tE6)": 6,
        "Unknown error (PS)": 13,
        "Unknown error (nP)": 19,
    }
)

_FINISHED = (STATES.code("Off"), STATES.code("End"))


class Dryer(AabbAppliance):
    PROFILE = "RV13U6AM8W_D_US_WIFI"
    KIND = "Dryer"
    FIELDS = (
        Field("power", "binary_sensor", "Power", "mdi:tumble-dryer", "running"),
        Field("status", "sensor", "Status", "mdi:state-machine", "enum", options=STATES.options),
        Field("remaining_time", "sensor", "Remaining time", "mdi:timer-outline", "duration", "min"),
        Field("course", "sensor", "Course", "mdi:tumble-dryer", "enum", options=COURSES.options),
        Field("temp", "sensor", "Temperature", "mdi:thermometer", "enum", options=TEMPERATURES.options),
        Field("dry_level", "sensor", "Dry level", "mdi:water-percent", "enum", options=DRY_LEVELS.options),
        Field("drum_running", "binary_sensor", "Drum running", "mdi:rotate-3d-variant"),
        Field("error", "binary_sensor", "Error", device_class="problem", diagnostic=True),
        Field("error_message", "sensor", "Error message", "mdi:alert-circle-outline", "enum", options=ERRORS.options, diagnostic=True),
    )

    def handle_frame(self, inner: bytes) -> None:
        rec = self.current_record(inner, CLASS_BYTE, RECORD_LEN)
        if rec is None:
            return
        phase = rec[PHASE]
        error = rec[ERROR]
        self.state.update(
            power=phase != 0,
            status=STATES.label(phase),
            # Off and End keep the last countdown value; nothing is left to run
            remaining_time=0 if phase in _FINISHED else rec[REMAINING_MIN],
            course=COURSES.label(rec[COURSE]),
            temp=TEMPERATURES.label(rec[TEMP]),
            dry_level=DRY_LEVELS.label(rec[DRY_LEVEL]),
            drum_running=bool(rec[DRUM] & 0x01),
            error=bool(error),
            error_message=ERRORS.label(error),
        )
