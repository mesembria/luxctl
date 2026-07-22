from datetime import datetime, timedelta, timezone

import pytest

pd = pytest.importorskip("pandas")

from luxctl.core.types import SensorReading
from luxctl.shell.config import Config, SensorConfig
from luxctl.shell.recorder import Recorder

from analysis.correlate import coverage, correlations, load_readings, write_plots

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

# A sensor configured in sensors.toml but which never recorded a single
# reading — dead from day one, or a typo'd entity_id. It must still show up
# everywhere, at 0% completeness, rather than vanishing silently.
CONFIG_WITH_DEAD_SENSOR = Config(
    ha_url="ws://test/api/websocket",
    sensors=(
        SensorConfig("south_window", "sensor.south_window_lux", "window"),
        SensorConfig("living_room", "sensor.living_room_lux", "room"),
        SensorConfig("dead_sensor", "sensor.dead_lux", "window"),
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


def test_sensor_with_zero_readings_still_gets_a_column(db):
    frame = load_readings(db, CONFIG_WITH_DEAD_SENSOR)

    assert "dead_sensor" in frame.columns
    assert frame["dead_sensor"].isna().all()


def test_sensor_with_zero_readings_reports_zero_completeness(db):
    frame = load_readings(db, CONFIG_WITH_DEAD_SENSOR)

    result = coverage(frame, expected_interval_s=60)

    assert "dead_sensor" in result.index
    assert result.loc["dead_sensor", "completeness"] == pytest.approx(0.0)


def test_sensor_with_zero_readings_yields_nan_correlation_not_a_number(db):
    frame = load_readings(db, CONFIG_WITH_DEAD_SENSOR)

    result = correlations(frame, CONFIG_WITH_DEAD_SENSOR)

    assert "dead_sensor" in result.index
    assert pd.isna(result.loc["dead_sensor", "living_room"])


def test_write_plots_handles_zero_lux_readings_without_error(tmp_path):
    path = tmp_path / "dark.db"
    recorder = Recorder(path)
    for minute in range(10):
        at = BASE + timedelta(minutes=minute)
        # A genuinely dark room/window: legitimate 0-lux readings that a
        # plain log axis would silently drop.
        recorder.record_reading(SensorReading("south_window", 0.0, at))
        recorder.record_reading(SensorReading("living_room", 0.0, at))
    recorder.close()

    frame = load_readings(path, CONFIG)
    out_dir = tmp_path / "out"

    write_plots(frame, CONFIG, out_dir)

    assert (out_dir / "timeseries.png").exists()
    assert (out_dir / "scatter_south_window_vs_living_room.png").exists()
