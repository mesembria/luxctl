"""Append-only persistence of observations to SQLite."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from luxctl.core.types import LightState, SensorReading

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id          INTEGER PRIMARY KEY,
    sensor_id   TEXT NOT NULL,
    lux         REAL NOT NULL,
    at          TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_readings_sensor_at
    ON readings(sensor_id, at);

CREATE TABLE IF NOT EXISTS light_states (
    id          INTEGER PRIMARY KEY,
    light_id    TEXT NOT NULL,
    is_on       INTEGER NOT NULL,
    brightness  REAL,
    at          TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_light_states_light_at
    ON light_states(light_id, at);
"""


def _utc_iso(value: datetime, *, field: str) -> str:
    """Normalise an observation timestamp to a UTC ISO 8601 string.

    All `at` values are stored as UTC text so that lexicographic order
    (used by ORDER BY at / MAX(at)) always matches chronological order.
    A naive datetime is rejected outright rather than assumed to be UTC:
    guessing a timezone on unattended, unrecoverable field data is exactly
    the kind of silent corruption this recorder must not produce.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            f"{field} must be a timezone-aware datetime; got naive datetime "
            f"{value!r}. Refusing to guess a timezone."
        )
    return value.astimezone(timezone.utc).isoformat()


class Recorder:
    """Appends observations to SQLite. Never updates, never deletes."""

    def __init__(self, path: Path):
        self._conn = sqlite3.connect(str(path), isolation_level=None)
        try:
            # WAL keeps writes cheap on SD cards and lets the analysis
            # script read the database while recording continues.
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
        except Exception:
            self._conn.close()
            raise

    def record_reading(self, reading: SensorReading) -> None:
        self._conn.execute(
            "INSERT INTO readings (sensor_id, lux, at, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            (
                reading.sensor_id,
                reading.lux,
                _utc_iso(reading.at, field="reading.at"),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    def record_light(self, state: LightState) -> None:
        self._conn.execute(
            "INSERT INTO light_states "
            "(light_id, is_on, brightness, at, recorded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                state.light_id,
                1 if state.is_on else 0,
                state.brightness,
                _utc_iso(state.at, field="state.at"),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    def readings_for(self, sensor_id: str) -> list[SensorReading]:
        rows = self._conn.execute(
            "SELECT sensor_id, lux, at FROM readings "
            "WHERE sensor_id = ? ORDER BY at",
            (sensor_id,),
        ).fetchall()
        return [
            SensorReading(sensor_id=r[0], lux=r[1], at=datetime.fromisoformat(r[2]))
            for r in rows
        ]

    def lights_for(self, light_id: str) -> list[LightState]:
        rows = self._conn.execute(
            "SELECT light_id, is_on, brightness, at FROM light_states "
            "WHERE light_id = ? ORDER BY at",
            (light_id,),
        ).fetchall()
        return [
            LightState(
                light_id=r[0],
                is_on=bool(r[1]),
                brightness=r[2],
                at=datetime.fromisoformat(r[3]),
            )
            for r in rows
        ]

    def last_reading_at(self, sensor_id: str) -> datetime | None:
        row = self._conn.execute(
            "SELECT MAX(at) FROM readings WHERE sensor_id = ?",
            (sensor_id,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return datetime.fromisoformat(row[0])

    def close(self) -> None:
        self._conn.close()
