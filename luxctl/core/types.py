"""Domain types. Pure data — no I/O, no behaviour."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SensorReading:
    """A single illuminance measurement."""

    sensor_id: str
    lux: float
    at: datetime


@dataclass(frozen=True)
class LightState:
    """An observed light state.

    `brightness` is normalised to 0.0-1.0 and is None when the light is off
    or reports no brightness attribute.
    """

    light_id: str
    is_on: bool
    brightness: float | None
    at: datetime
