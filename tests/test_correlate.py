from datetime import datetime, timedelta, timezone

import pytest

pd = pytest.importorskip("pandas")

from luxctl.core.types import LightState, SensorReading
from luxctl.shell.config import Config, LightConfig, SensorConfig
from luxctl.shell.recorder import Recorder

from analysis.correlate import (
    artificial_light_hours,
    correlations,
    coverage,
    daylight_correlations,
    daylight_sample_counts,
    load_readings,
    write_plots,
)

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


LIGHTS_CONFIG = Config(
    ha_url="ws://test/api/websocket",
    sensors=(SensorConfig("south_window", "sensor.south_window_lux", "window"),),
    lights=(LightConfig("living_room_main", "light.hue_living_1"),),
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


def test_write_plots_uses_symlog_so_zero_lux_readings_render(tmp_path, monkeypatch):
    """The files existing is not enough — a plain log axis would still have
    produced them, silently dropping every 0-lux point. Assert the scale."""
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    from matplotlib.axes import Axes

    scales = []
    real_x, real_y = Axes.set_xscale, Axes.set_yscale

    def record_x(self, value, **kwargs):
        scales.append(("x", value, kwargs.get("linthresh")))
        return real_x(self, value, **kwargs)

    def record_y(self, value, **kwargs):
        scales.append(("y", value, kwargs.get("linthresh")))
        return real_y(self, value, **kwargs)

    monkeypatch.setattr(Axes, "set_xscale", record_x)
    monkeypatch.setattr(Axes, "set_yscale", record_y)

    path = tmp_path / "symlog.db"
    recorder = Recorder(path)
    for minute in range(10):
        at = BASE + timedelta(minutes=minute)
        recorder.record_reading(SensorReading("south_window", 0.0, at))
        recorder.record_reading(SensorReading("living_room", 0.0, at))
    recorder.close()

    write_plots(load_readings(path, CONFIG), CONFIG, tmp_path / "out")

    symlog = [(axis, linthresh) for axis, value, linthresh in scales
              if value == "symlog"]

    assert ("y", 1) in symlog, "timeseries and scatter y axes must be symlog"
    assert ("x", 1) in symlog, "scatter x axis must be symlog"
    assert not any(value == "log" for _, value, _ in scales), (
        "a plain log axis silently drops 0-lux readings"
    )


def _daylight_db(tmp_path, room_tracks_window):
    """Build a record with a realistic day/night cycle.

    At night both sensors read ~0, which is what confounds the all-hours
    correlation. During daylight the room either tracks the window or
    varies independently of it, per `room_tracks_window`.
    """
    path = tmp_path / "daynight.db"
    recorder = Recorder(path)

    for minute in range(4 * 24 * 60):  # four days
        at = BASE + timedelta(minutes=minute)
        hour = (minute // 60) % 24
        daytime = 8 <= hour < 18

        if not daytime:
            window = 0.0
            room = 0.0
        else:
            # Window swings widely with cloud cover.
            window = 200.0 + 1800.0 * ((minute % 97) / 97.0)
            if room_tracks_window:
                room = 20.0 + window * 0.1
            else:
                # Independent of the window: driven by a different cycle.
                room = 100.0 + 80.0 * ((minute % 53) / 53.0)

        recorder.record_reading(SensorReading("south_window", window, at))
        recorder.record_reading(SensorReading("living_room", room, at))

    recorder.close()
    return path


def test_all_hours_correlation_is_inflated_by_the_day_night_cycle(tmp_path):
    """The confound this exists to expose: a room whose daylight behaviour
    is independent of the window still correlates strongly across 24h,
    purely because both sensors go dark at night."""
    frame = load_readings(_daylight_db(tmp_path, room_tracks_window=False),
                          CONFIG)

    all_hours = correlations(frame, CONFIG).loc["south_window", "living_room"]

    assert all_hours > 0.6


def test_daylight_correlation_rejects_a_room_the_window_does_not_predict(tmp_path):
    """Same data, daylight hours only: the independent room is correctly
    revealed as unpredicted, where the all-hours figure called it strong."""
    frame = load_readings(_daylight_db(tmp_path, room_tracks_window=False),
                          CONFIG)

    all_hours = correlations(frame, CONFIG).loc["south_window", "living_room"]
    daylight = daylight_correlations(frame, CONFIG).loc["south_window",
                                                        "living_room"]

    assert abs(daylight) < 0.3
    assert daylight < all_hours - 0.4


def test_daylight_correlation_still_finds_a_room_the_window_does_predict(tmp_path):
    """The restriction must not simply destroy signal — a room that really
    does track its window must still show a high daylight r."""
    frame = load_readings(_daylight_db(tmp_path, room_tracks_window=True),
                          CONFIG)

    daylight = daylight_correlations(frame, CONFIG).loc["south_window",
                                                        "living_room"]

    assert daylight > 0.95


def test_daylight_correlation_is_nan_when_too_few_daylight_samples(tmp_path):
    """A confident-looking r from a handful of minutes is worse than a
    blank, so too-sparse pairs report NaN."""
    path = tmp_path / "sparse.db"
    recorder = Recorder(path)
    for minute in range(10):  # far below MIN_DAYLIGHT_SAMPLES
        at = BASE + timedelta(minutes=minute)
        recorder.record_reading(SensorReading("south_window", 5000.0, at))
        recorder.record_reading(SensorReading("living_room", 300.0, at))
    recorder.close()

    frame = load_readings(path, CONFIG)
    daylight = daylight_correlations(frame, CONFIG).loc["south_window",
                                                        "living_room"]

    assert pd.isna(daylight)
    counts = daylight_sample_counts(frame, CONFIG)
    assert counts.loc["south_window", "living_room"] == 10


def test_coverage_columns_name_their_resampling(tmp_path):
    """These are computed on 1-minute means, not raw readings; the names
    must not invite a reader to treat them as raw peaks."""
    path = tmp_path / "cov.db"
    recorder = Recorder(path)
    for minute in range(5):
        at = BASE + timedelta(minutes=minute)
        recorder.record_reading(SensorReading("south_window", 100.0, at))
        recorder.record_reading(SensorReading("living_room", 50.0, at))
    recorder.close()

    result = coverage(load_readings(path, CONFIG), expected_interval_s=60)

    assert set(result.columns) == {
        "minutes", "completeness", "min_lux_1min_mean", "max_lux_1min_mean",
    }


def test_artificial_light_hours_reports_time_on(tmp_path):
    """A room whose lamps ran for much of the record has a contaminated
    correlation; this is what makes that visible."""
    path = tmp_path / "lights.db"
    recorder = Recorder(path)
    recorder.record_light(LightState("living_room_main", True, 1.0, BASE))
    recorder.record_light(
        LightState("living_room_main", False, None, BASE + timedelta(hours=3))
    )
    recorder.record_light(
        LightState("living_room_main", True, 0.5, BASE + timedelta(hours=4))
    )
    recorder.record_light(
        LightState("living_room_main", False, None, BASE + timedelta(hours=5))
    )
    recorder.close()

    result = artificial_light_hours(path, LIGHTS_CONFIG)

    assert result.loc["living_room_main", "hours_on"] == 4.0
    assert result.loc["living_room_main", "recorded"] is True


def test_artificial_light_hours_flags_a_light_that_never_reported(tmp_path):
    path = tmp_path / "nolights.db"
    Recorder(path).close()

    result = artificial_light_hours(path, LIGHTS_CONFIG)

    assert result.loc["living_room_main", "recorded"] is False
    assert result.loc["living_room_main", "hours_on"] == 0.0
