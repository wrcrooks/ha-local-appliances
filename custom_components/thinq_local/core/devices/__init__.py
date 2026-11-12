"""Appliance profiles, keyed by the ThinQ model id they were verified on."""

from __future__ import annotations

from .base import Appliance, Field
from .dryer_rv13u6am8w import Dryer
from .generic import Generic
from .washer_t1789efh_f import Washer

PROFILES: dict[str, type[Appliance]] = {
    cls.PROFILE: cls for cls in (Washer, Dryer, Generic)
}

# ThinQ model ids that are known to share a listed profile's record layout.
# Add an alias here only after comparing real frames (see README).
ALIASES: dict[str, str] = {}

AUTO = "auto"


def profile_for(model_id: str, override: str | None = None) -> type[Appliance]:
    if override and override != AUTO and override in PROFILES:
        return PROFILES[override]
    return PROFILES.get(ALIASES.get(model_id, model_id), Generic)


__all__ = ["ALIASES", "AUTO", "PROFILES", "Appliance", "Field", "profile_for"]
