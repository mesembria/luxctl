from datetime import datetime, timedelta, timezone

import pytest

from luxctl.core.types import SensorReading
from luxctl.shell.config import Config, LightConfig, SensorConfig
from luxctl.shell.recorder import Recorder
from luxctl.shell.runner import find_stale, route_state

BASE = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)

CONFIG = Config(
    ha_url="ws://test/api/websocket",
    sensors=(
        SensorConfig("south_window", "sensor.south_window_lux", "window"),
        SensorConfig("living_room", "sensor.living_room_lux", "room"),
    ),
    lights=(LightConfig("living_room_main", "light.hue_living_1"),),
    stale_after_s=600,
)


@pytest.fixture
def recorder(tmp_path):
    rec = Recorder(tmp_path / "runner.db")
    yield rec
    rec.close()


def lux_state(value):
    return {"state": value, "attributes": {},
            "last_updated": "2026-07-21T10:00:00+00:00"}


def test_records_a_configured_sensor(recorder):
    recorded = route_state(lux_state("1234.5"), "sensor.south_window_lux",
                           CONFIG, recorder)

    assert recorded is True
    assert recorder.readings_for("south_window")[0].lux == 1234.5


def test_ignores_an_unconfigured_entity(recorder):
    recorded = route_state(lux_state("999"), "sensor.some_other_thing",
                           CONFIG, recorder)

    assert recorded is False
    assert recorder.readings_for("south_window") == []


def test_records_a_configured_light(recorder):
    recorded = route_state(
        {"state": "on", "attributes": {"brightness": 255},
         "last_updated": "2026-07-21T10:00:00+00:00"},
        "light.hue_living_1", CONFIG, recorder,
    )

    assert recorded is True
    states = recorder.lights_for("living_room_main")
    assert len(states) == 1
    assert states[0].brightness == 1.0


def test_skips_an_unavailable_sensor(recorder):
    recorded = route_state(lux_state("unavailable"), "sensor.south_window_lux",
                           CONFIG, recorder)

    assert recorded is False
    assert recorder.readings_for("south_window") == []


def test_no_sensors_are_stale_when_all_reported_recently(recorder):
    recorder.record_reading(SensorReading("south_window", 1.0, BASE))
    recorder.record_reading(SensorReading("living_room", 1.0, BASE))

    assert find_stale(CONFIG, recorder, now=BASE + timedelta(seconds=60)) == []


def test_detects_a_sensor_that_stopped_reporting(recorder):
    recorder.record_reading(SensorReading("south_window", 1.0, BASE))
    recorder.record_reading(SensorReading("living_room", 1.0, BASE))

    stale = find_stale(CONFIG, recorder, now=BASE + timedelta(seconds=700))

    assert sorted(stale) == ["living_room", "south_window"]


def test_a_sensor_that_never_reported_is_stale(recorder):
    recorder.record_reading(SensorReading("south_window", 1.0, BASE))

    stale = find_stale(CONFIG, recorder, now=BASE + timedelta(seconds=60))

    assert stale == ["living_room"]
