from datetime import datetime, timedelta, timezone

import pytest

pd = pytest.importorskip("pandas")

from luxctl.core.types import SensorReading
from luxctl.shell.config import Config, SensorConfig
from luxctl.shell.recorder import Recorder

from analysis.correlate import coverage, load_readings

BASE = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)

CONFIG = Config(
    ha_url="ws://test/api/websocket",
    sensors=(
        SensorConfig("south_window", "sensor.south_window_lux", "window"),
        SensorConfig("living_room", "sensor.living_room_lux", "room"),
    ),
    lights=(),
    stale_after_s=600,
)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "analysis.db"
    recorder = Recorder(path)
    for minute in range(60):
        at = BASE + timedelta(minutes=minute)
        recorder.record_reading(SensorReading("south_window", 1000.0 + minute, at))
        recorder.record_reading(SensorReading("living_room", 100.0 + minute, at))
    recorder.close()
    return path


def test_loads_readings_into_a_column_per_sensor(db):
    frame = load_readings(db, CONFIG)

    assert set(frame.columns) == {"south_window", "living_room"}
    assert len(frame) == 60


def test_index_is_time_ordered(db):
    frame = load_readings(db, CONFIG)

    assert frame.index.is_monotonic_increasing


def test_perfectly_correlated_sensors_report_r_of_one(db):
    frame = load_readings(db, CONFIG)

    r = frame["south_window"].corr(frame["living_room"])

    assert r == pytest.approx(1.0)


def test_coverage_reports_full_when_no_gaps(db):
    frame = load_readings(db, CONFIG)

    result = coverage(frame, expected_interval_s=60)

    assert result.loc["south_window", "completeness"] == pytest.approx(1.0, abs=0.05)


def test_coverage_detects_a_missing_sensor_period(tmp_path):
    path = tmp_path / "gappy.db"
    recorder = Recorder(path)
    for minute in range(60):
        at = BASE + timedelta(minutes=minute)
        recorder.record_reading(SensorReading("south_window", 1000.0, at))
        if minute < 30:
            recorder.record_reading(SensorReading("living_room", 100.0, at))
    recorder.close()

    frame = load_readings(path, CONFIG)
    result = coverage(frame, expected_interval_s=60)

    assert result.loc["living_room", "completeness"] == pytest.approx(0.5, abs=0.05)
