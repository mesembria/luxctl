"""Parsing of Home Assistant state objects into domain types.

Pure: every input arrives as an argument, nothing here touches the network,
the clock or the disk.
"""

import math
from datetime import datetime

from luxctl.core.types import LightState, SensorReading

# Values HA uses to mean "no usable reading".
UNUSABLE = {"unknown", "unavailable", "none", ""}

# Home Assistant reports light brightness on an 8-bit scale. Anything outside
# it (or non-finite) is not a brightness we can interpret.
HA_BRIGHTNESS_MAX = 255.0


def _is_unusable(raw: object) -> bool:
    return raw is None or str(raw).strip().lower() in UNUSABLE


def parse_lux(state: dict, sensor_id: str) -> SensorReading | None:
    """Convert an HA state object into a SensorReading.

    Returns None when the entity is unavailable, non-numeric, negative, or
    non-finite (NaN/inf), or when last_updated is missing or unparseable.
    """
    raw = state.get("state")
    if _is_unusable(raw):
        return None

    try:
        lux = float(raw)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(lux):
        return None

    if lux < 0:
        return None

    at = _parse_timestamp(state.get("last_updated"))
    if at is None:
        return None

    return SensorReading(
        sensor_id=sensor_id,
        lux=lux,
        at=at,
    )


def parse_light(state: dict, light_id: str) -> LightState | None:
    """Convert an HA state object into a LightState.

    Returns None when the entity is unavailable, or when last_updated is
    missing or unparseable. A light that is off always records
    brightness=None, because HA may leave a stale brightness attribute
    behind.

    An unusable brightness value on an on light is recorded as
    brightness=None -- the light is still on, we just do not know how
    bright. "Unusable" means missing, null, non-numeric, non-finite
    (NaN/inf), or outside HA's 0-255 scale. Non-finite values matter
    especially: SQLite stores NaN as NULL, so an unvalidated NaN would come
    back as brightness=None anyway but only after masquerading as a real
    reading in memory -- indistinguishable downstream from a light that was
    off. Rejecting it here makes "we don't know" the explicit answer.
    """
    raw = state.get("state")
    if _is_unusable(raw):
        return None

    is_on = str(raw).strip().lower() == "on"

    brightness = None
    if is_on:
        attributes = state.get("attributes") or {}
        brightness = _parse_brightness(attributes.get("brightness"))

    at = _parse_timestamp(state.get("last_updated"))
    if at is None:
        return None

    return LightState(
        light_id=light_id,
        is_on=is_on,
        brightness=brightness,
        at=at,
    )


def _parse_brightness(value: object) -> float | None:
    """Normalise an HA brightness attribute to 0.0-1.0, or None.

    Applies the same guards parse_lux applies to lux: a value that is
    missing, non-numeric, non-finite, or outside HA's 0-255 scale is not a
    brightness, and is reported as None rather than as a plausible-looking
    number.
    """
    if value is None:
        return None

    try:
        raw = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(raw):
        return None

    if raw < 0 or raw > HA_BRIGHTNESS_MAX:
        return None

    return raw / HA_BRIGHTNESS_MAX


def _parse_timestamp(raw: object) -> datetime | None:
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
