"""Parsing of Home Assistant state objects into domain types.

Pure: every input arrives as an argument, nothing here touches the network,
the clock or the disk.
"""

import math
from datetime import datetime

from luxctl.core.types import LightState, SensorReading

# Values HA uses to mean "no usable reading".
UNUSABLE = {"unknown", "unavailable", "none", ""}


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
    behind. An unusable (missing, null, or non-numeric) brightness value on
    an on light is also recorded as brightness=None rather than raising.
    """
    raw = state.get("state")
    if _is_unusable(raw):
        return None

    is_on = str(raw).strip().lower() == "on"

    brightness = None
    if is_on:
        attributes = state.get("attributes") or {}
        value = attributes.get("brightness")
        if value is not None:
            try:
                brightness = float(value) / 255.0
            except (TypeError, ValueError):
                brightness = None

    at = _parse_timestamp(state.get("last_updated"))
    if at is None:
        return None

    return LightState(
        light_id=light_id,
        is_on=is_on,
        brightness=brightness,
        at=at,
    )


def _parse_timestamp(raw: object) -> datetime | None:
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
