from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from luxctl.core.types import LightState, SensorReading


def test_sensor_reading_holds_its_fields():
    at = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)
    reading = SensorReading(sensor_id="south_window", lux=1234.5, at=at)

    assert reading.sensor_id == "south_window"
    assert reading.lux == 1234.5
    assert reading.at == at


def test_sensor_reading_is_immutable():
    reading = SensorReading(
        sensor_id="south_window",
        lux=1234.5,
        at=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )

    with pytest.raises(FrozenInstanceError):
        reading.lux = 9999.0


def test_light_state_allows_null_brightness_when_off():
    state = LightState(
        light_id="living_room_main",
        is_on=False,
        brightness=None,
        at=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )

    assert state.is_on is False
    assert state.brightness is None


def test_light_state_holds_normalised_brightness():
    state = LightState(
        light_id="living_room_main",
        is_on=True,
        brightness=0.5,
        at=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )

    assert state.brightness == 0.5
