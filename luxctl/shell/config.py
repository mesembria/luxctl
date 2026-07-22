"""Loading and validation of sensors.toml."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

VALID_PLACEMENTS = {"window", "room"}
DEFAULT_STALE_AFTER_S = 600


class ConfigError(Exception):
    """Raised when the configuration file is missing or invalid."""


@dataclass(frozen=True)
class SensorConfig:
    id: str
    entity_id: str
    placement: str


@dataclass(frozen=True)
class LightConfig:
    id: str
    entity_id: str


@dataclass(frozen=True)
class Config:
    ha_url: str
    sensors: tuple[SensorConfig, ...]
    lights: tuple[LightConfig, ...]
    stale_after_s: int

    def entity_to_sensor(self) -> dict[str, str]:
        return {s.entity_id: s.id for s in self.sensors}

    def entity_to_light(self) -> dict[str, str]:
        return {light.entity_id: light.id for light in self.lights}


def _require(mapping: dict, key: str, context: str) -> object:
    if key not in mapping or mapping[key] in (None, ""):
        raise ConfigError(f"{context}: missing required key '{key}'")
    return mapping[key]


def load_config(path: Path) -> Config:
    """Read and validate sensors.toml.

    Raises ConfigError with an actionable message on any problem.
    """
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")

    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    ha_url = _require(raw.get("ha", {}), "url", "[ha]")

    sensors = []
    seen: set[str] = set()
    for entry in raw.get("sensors", []):
        sensor_id = _require(entry, "id", "[[sensors]]")
        entity_id = _require(entry, "entity_id", f"sensor '{sensor_id}'")
        placement = _require(entry, "placement", f"sensor '{sensor_id}'")

        if placement not in VALID_PLACEMENTS:
            raise ConfigError(
                f"sensor '{sensor_id}': placement must be one of "
                f"{sorted(VALID_PLACEMENTS)}, got '{placement}'"
            )
        if sensor_id in seen:
            raise ConfigError(f"duplicate sensor id: '{sensor_id}'")
        seen.add(sensor_id)

        sensors.append(SensorConfig(str(sensor_id), str(entity_id), str(placement)))

    if not sensors:
        raise ConfigError("config must define at least one sensor")

    lights = []
    seen_lights: set[str] = set()
    for entry in raw.get("lights", []):
        light_id = _require(entry, "id", "[[lights]]")
        entity_id = _require(entry, "entity_id", f"light '{light_id}'")
        if light_id in seen_lights:
            raise ConfigError(f"duplicate light id: '{light_id}'")
        seen_lights.add(light_id)
        lights.append(LightConfig(str(light_id), str(entity_id)))

    return Config(
        ha_url=str(ha_url),
        sensors=tuple(sensors),
        lights=tuple(lights),
        stale_after_s=int(
            raw.get("watchdog", {}).get("stale_after_s", DEFAULT_STALE_AFTER_S)
        ),
    )
