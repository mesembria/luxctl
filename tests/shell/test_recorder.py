from datetime import datetime, timedelta, timezone

import pytest

from luxctl.core.types import LightState, SensorReading
from luxctl.shell.recorder import Recorder

BASE = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def recorder(tmp_path):
    rec = Recorder(tmp_path / "test.db")
    yield rec
    rec.close()


def test_records_and_reads_back_a_reading(recorder):
    recorder.record_reading(SensorReading("south_window", 1234.5, BASE))

    readings = recorder.readings_for("south_window")

    assert len(readings) == 1
    assert readings[0].sensor_id == "south_window"
    assert readings[0].lux == 1234.5
    assert readings[0].at == BASE


def test_readings_come_back_in_time_order(recorder):
    recorder.record_reading(SensorReading("south_window", 3.0, BASE + timedelta(minutes=2)))
    recorder.record_reading(SensorReading("south_window", 1.0, BASE))
    recorder.record_reading(SensorReading("south_window", 2.0, BASE + timedelta(minutes=1)))

    readings = recorder.readings_for("south_window")

    assert [r.lux for r in readings] == [1.0, 2.0, 3.0]


def test_readings_are_isolated_by_sensor(recorder):
    recorder.record_reading(SensorReading("south_window", 1000.0, BASE))
    recorder.record_reading(SensorReading("living_room", 200.0, BASE))

    assert len(recorder.readings_for("south_window")) == 1
    assert recorder.readings_for("living_room")[0].lux == 200.0


def test_unknown_sensor_has_no_readings(recorder):
    assert recorder.readings_for("nonexistent") == []


def test_last_reading_at_returns_most_recent(recorder):
    recorder.record_reading(SensorReading("south_window", 1.0, BASE))
    recorder.record_reading(SensorReading("south_window", 2.0, BASE + timedelta(minutes=5)))

    assert recorder.last_reading_at("south_window") == BASE + timedelta(minutes=5)


def test_last_reading_at_is_none_for_silent_sensor(recorder):
    assert recorder.last_reading_at("nonexistent") is None


def test_records_light_state_with_brightness(recorder):
    recorder.record_light(LightState("living_room_main", True, 0.5, BASE))

    states = recorder.lights_for("living_room_main")

    assert len(states) == 1
    assert states[0].light_id == "living_room_main"
    assert states[0].is_on is True
    assert states[0].brightness == 0.5
    assert states[0].at == BASE


def test_records_light_state_when_off(recorder):
    recorder.record_light(LightState("living_room_main", False, None, BASE))

    states = recorder.lights_for("living_room_main")

    assert states[0].is_on is False
    assert states[0].brightness is None


def test_storage_is_append_only(recorder):
    recorder.record_reading(SensorReading("south_window", 1.0, BASE))
    recorder.record_reading(SensorReading("south_window", 2.0, BASE))

    assert len(recorder.readings_for("south_window")) == 2


def test_reopening_an_existing_database_preserves_data(tmp_path):
    path = tmp_path / "persist.db"

    first = Recorder(path)
    first.record_reading(SensorReading("south_window", 42.0, BASE))
    first.close()

    second = Recorder(path)
    try:
        assert len(second.readings_for("south_window")) == 1
    finally:
        second.close()
