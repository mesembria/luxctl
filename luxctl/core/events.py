"""Parsing of Home Assistant state objects into domain types.

Pure: every input arrives as an argument, nothing here touches the network,
the clock or the disk.
"""

from datetime import datetime

from luxctl.core.types import LightState, SensorReading

# Values HA uses to mean "no usable reading".
UNUSABLE = {"unknown", "unavailable", "none", ""}


def _is_unusable(raw: object) -> bool:
    return raw is None or str(raw).strip().lower() in UNUSABLE


def parse_lux(state: dict, sensor_id: str) -> SensorReading | None:
    """Convert an HA state object into a SensorReading.

    Returns None when the entity is unavailable, non-numeric, or negative.
    """
    raw = state.get("state")
    if _is_unusable(raw):
        return None

    try:
        lux = float(raw)
    except (TypeError, ValueError):
        return None

    if lux < 0:
        return None

    return SensorReading(
        sensor_id=sensor_id,
        lux=lux,
        at=datetime.fromisoformat(state["last_updated"]),
    )


def parse_light(state: dict, light_id: str) -> LightState | None:
    """Convert an HA state object into a LightState.

    Returns None when the entity is unavailable. A light that is off always
    records brightness=None, because HA may leave a stale brightness
    attribute behind.
    """
    raw = state.get("state")
    if _is_unusable(raw):
        return None

    is_on = str(raw).strip().lower() == "on"

    brightness = None
    if is_on:
        value = state.get("attributes", {}).get("brightness")
        if value is not None:
            brightness = float(value) / 255.0

    return LightState(
        light_id=light_id,
        is_on=is_on,
        brightness=brightness,
        at=datetime.fromisoformat(state["last_updated"]),
    )
