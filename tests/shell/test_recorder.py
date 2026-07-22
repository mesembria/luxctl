import sqlite3
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


# --- Finding 1: timestamps must be normalised to UTC so text order == time order ---


def test_readings_order_is_chronological_across_differing_utc_offsets(recorder):
    # 20:00 in UTC-05:00 is 01:00 UTC the *next* day.
    later_instant_negative_offset = datetime(
        2026, 7, 21, 20, 0, tzinfo=timezone(timedelta(hours=-5))
    )
    # 23:00 UTC the same day is chronologically earlier than the instant above,
    # but as raw text "...T20:00:00-05:00" sorts before "...T23:00:00+00:00"
    # because '-' < '2' lexicographically. If `at` isn't normalised to a
    # common UTC offset before storage, ORDER BY at returns this backwards.
    earlier_instant_utc = datetime(2026, 7, 21, 23, 0, tzinfo=timezone.utc)

    recorder.record_reading(SensorReading("south_window", 2.0, later_instant_negative_offset))
    recorder.record_reading(SensorReading("south_window", 1.0, earlier_instant_utc))

    readings = recorder.readings_for("south_window")

    assert [r.lux for r in readings] == [1.0, 2.0]
    assert recorder.last_reading_at("south_window") == later_instant_negative_offset


def test_naive_datetime_is_rejected_for_reading(recorder):
    naive = datetime(2026, 7, 21, 10, 0)

    with pytest.raises(ValueError):
        recorder.record_reading(SensorReading("south_window", 1.0, naive))


def test_naive_datetime_is_rejected_for_light(recorder):
    naive = datetime(2026, 7, 21, 10, 0)

    with pytest.raises(ValueError):
        recorder.record_light(LightState("living_room_main", True, 0.5, naive))


# --- Finding 2: recorded_at must be written in UTC, not local time ---


def test_recorded_at_is_stored_with_utc_offset(recorder):
    recorder.record_reading(SensorReading("south_window", 1.0, BASE))
    recorder.record_light(LightState("living_room_main", True, 0.5, BASE))

    reading_recorded_at = recorder._conn.execute(
        "SELECT recorded_at FROM readings"
    ).fetchone()[0]
    light_recorded_at = recorder._conn.execute(
        "SELECT recorded_at FROM light_states"
    ).fetchone()[0]

    assert reading_recorded_at.endswith("+00:00")
    assert light_recorded_at.endswith("+00:00")


# --- Finding 3: the connection must not leak if schema setup fails ---


def test_connection_is_closed_if_schema_setup_fails(tmp_path, monkeypatch):
    # sqlite3.Connection is a C type; its methods can't be monkeypatched
    # directly. Use a Connection subclass (via the `factory` kwarg that
    # sqlite3.connect already supports) to make executescript fail while
    # still exercising a real, capturable connection object.
    class FailingConnection(sqlite3.Connection):
        def executescript(self, script):
            raise sqlite3.OperationalError("simulated schema setup failure")

    captured = {}
    real_connect = sqlite3.connect

    def fake_connect(*args, **kwargs):
        kwargs["factory"] = FailingConnection
        conn = real_connect(*args, **kwargs)
        captured["conn"] = conn
        return conn

    monkeypatch.setattr(sqlite3, "connect", fake_connect)

    with pytest.raises(sqlite3.OperationalError):
        Recorder(tmp_path / "fail.db")

    assert "conn" in captured
    with pytest.raises(sqlite3.ProgrammingError):
        captured["conn"].execute("SELECT 1")
