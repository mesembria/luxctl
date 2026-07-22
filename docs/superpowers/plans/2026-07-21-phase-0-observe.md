# luxctl Phase 0 (Observe) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record lux and light-state history from the house into SQLite for two weeks, then determine whether window-mounted sensors predict room brightness well enough to make feedforward control sufficient.

**Architecture:** Home Assistant runs on the Raspberry Pi as a device driver layer only. A Python service (`luxctl`) connects to HA's WebSocket API, subscribes to `state_changed`, and appends every lux reading and light state to SQLite. Nothing commands any light in Phase 0 — there is no dispatcher and no strategy code. An offline analysis script reads the database and produces the correlation finding.

**Tech Stack:** Python 3.11+, `websockets`, stdlib `sqlite3` and `tomllib`, pytest + pytest-asyncio. Analysis uses pandas and matplotlib as an optional extra. ESPHome for sensor nodes.

## Global Constraints

- Python 3.11 or later (`tomllib` is stdlib from 3.11; Raspberry Pi OS bookworm ships 3.11).
- **Phase 0 writes no light commands.** No dispatcher, no strategies, no `light.turn_on` service calls. If a task seems to need one, the task is wrong.
- Shell/core split from the spec is binding: `luxctl/core/` contains no I/O — no sockets, no clocks, no database. All parsing is pure and takes its inputs as arguments.
- All timestamps stored as ISO 8601 strings in UTC.
- Brightness normalised to 0.0–1.0 on ingest (HA reports 0–255).
- The HA token is read from the `LUXCTL_HA_TOKEN` environment variable. It must never appear in a config file or a commit.
- Sensor IDs in `sensors.toml` are stable identifiers used as database keys. Renaming one orphans its history.

## Deviations from the spec, and why

Two, both deliberate:

1. **No tick loop for recording.** The spec's Phase 0 description says "`ha_client` + `recorder` + tick loop". Recording is event-driven instead — HA pushes `state_changed`, luxctl appends. Polling on a timer would resample data HA has already timestamped, losing fidelity for no gain. A periodic task does exist, but only as a staleness watchdog (Task 7).

2. **Light states are recorded, though Phase 0 controls nothing.** Without them, a room sensor's history is uninterpretable — an evening spike is indistinguishable between "sun came out" and "someone switched on a lamp". This also front-loads the data Phase 1's calibration needs.

## Task ownership

**Task 0 is human-only** — it installs Home Assistant on physical hardware and assembles sensor nodes. It cannot be delegated to an agent. Tasks 1–9 are software and are agent-executable.

---

### Task 0: Physical setup (HUMAN ONLY)

**Files:** none — this task is hardware and infrastructure.

**Interfaces:**
- Consumes: nothing.
- Produces: a reachable Home Assistant instance, a long-lived access token, and at least two lux entities visible in HA. Task 8 cannot run without these.

- [ ] **Step 1: Install Home Assistant OS on the Raspberry Pi**

Follow the official installer for the Pi. Confirm the web UI loads at `http://homeassistant.local:8123` and complete onboarding.

- [ ] **Step 2: Add the Hue integration**

Settings → Devices & Services → Add Integration → Philips Hue. Press the bridge link button when prompted.

Verify both existing Hue Motion Sensors appear, each exposing an illuminance entity. Note their entity IDs — they will look like `sensor.<name>_illuminance`.

- [ ] **Step 3: Create a long-lived access token**

Profile (bottom left) → Security tab → Long-lived access tokens → Create Token. Name it `luxctl`.

Copy it immediately; HA will not show it again. Store it somewhere safe — it goes into `LUXCTL_HA_TOKEN` in Task 8, never into a file in this repo.

- [ ] **Step 4: Bench-build one sensor node**

Plug together: QT Py ESP32-S2 → STEMMA QT cable → VEML7700 breakout. USB-C for power. No soldering.

- [ ] **Step 5: Find the STEMMA QT bus GPIO numbers**

Open Adafruit's QT Py ESP32-S2 pinout diagram. The board has two I²C buses; you need the SDA/SCL GPIO numbers for the **STEMMA QT connector**, not the breakout pads.

This is the one hardware fact the spec could not confirm. Write the numbers down before continuing.

- [ ] **Step 6: Flash ESPHome onto the node**

Create the node config, substituting the GPIO numbers from Step 5:

```yaml
esphome:
  name: south-window-lux
esp32:
  board: adafruit_qtpy_esp32s2
  framework:
    type: esp-idf

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password

api:
  encryption:
    key: !secret api_key
logger:
ota:
  - platform: esphome

i2c:
  sda: GPIO41   # REPLACE with the value from Step 5
  scl: GPIO40   # REPLACE with the value from Step 5

sensor:
  - platform: veml7700
    address: 0x10
    update_interval: 15s
    ambient_light:
      name: "South Window Lux"
      filters:
        - median:
            window_size: 5
```

Flash over USB for the first upload; subsequent updates go over the air.

If the sensor is not detected, the GPIO numbers from Step 5 are the first thing to re-check. This board does **not** need an I²C power-enable pin driven high — that applies to the Feather ESP32-S2, not the QT Py.

- [ ] **Step 7: Bench-verify the node reads plausibly**

Confirm the entity appears in HA, then check magnitude in four conditions. You are checking order of magnitude, not accuracy — a phone lux app is an adequate reference.

| Condition | Expected range |
|---|---|
| Dark room | 1–10 lx |
| Normally lit room | 100–300 lx |
| Beside a window, daylight | 1,000–5,000 lx |
| Outdoors, direct sun | 50,000–100,000 lx |

Plausible in all four means the node works. Implausible in only the dark room usually means a diffuser or enclosure is blocking light.

- [ ] **Step 8: Build and site the remaining nodes**

Assemble three more. Fit each with a white diffuser (PTFE cap, sanded acrylic, or half a ping-pong ball) so it reads overall sky brightness rather than whether the sun is momentarily in its field of view.

Site them: **one south-facing window, one north-facing window, one or two room-centre**. The room-centre sensors must be in rooms the window sensors could plausibly predict — the whole analysis is a comparison between them.

Mount with tape. This is deliberately unlovely; placement is expected to change and enclosures come after it settles.

- [ ] **Step 9: Record the entity IDs**

List every lux entity ID and every light entity ID you want recorded. Task 8 puts them into `sensors.toml`.

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `luxctl/__init__.py`
- Create: `luxctl/core/__init__.py`
- Create: `luxctl/shell/__init__.py`
- Create: `tests/__init__.py`
- Test: `tests/test_scaffolding.py`

**Interfaces:**
- Consumes: nothing.
- Produces: an installed `luxctl` package importable as `import luxctl`, and a working `pytest` invocation. Every later task depends on both.

- [ ] **Step 1: Write the failing test**

Create `tests/test_scaffolding.py`:

```python
def test_package_is_importable():
    import luxctl

    assert luxctl.__name__ == "luxctl"


def test_core_and_shell_subpackages_exist():
    import luxctl.core
    import luxctl.shell

    assert luxctl.core.__name__ == "luxctl.core"
    assert luxctl.shell.__name__ == "luxctl.shell"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scaffolding.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'luxctl'` (or pytest itself is missing, which Step 3 also fixes).

- [ ] **Step 3: Create the package and project files**

Create `pyproject.toml`:

```toml
[project]
name = "luxctl"
version = "0.1.0"
description = "Sensor-driven lighting control"
requires-python = ">=3.11"
dependencies = [
    "websockets>=12.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]
analysis = [
    "pandas>=2.0",
    "matplotlib>=3.8",
]

[project.scripts]
luxctl = "luxctl.shell.runner:cli"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["luxctl*", "analysis*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

Create `.gitignore`:

```
__pycache__/
*.py[cod]
.venv/
venv/
*.egg-info/
.pytest_cache/

# Recorded data — never commit
*.db
*.db-wal
*.db-shm
data/

# Analysis output
analysis/out/

# Secrets
.env
secrets.yaml
```

Create four empty files: `luxctl/__init__.py`, `luxctl/core/__init__.py`, `luxctl/shell/__init__.py`, `tests/__init__.py`.

- [ ] **Step 4: Install the package**

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_scaffolding.py -v`
Expected: PASS — 2 passed.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore luxctl tests
git commit -m "chore: scaffold luxctl package"
```

---

### Task 2: Domain types

**Files:**
- Create: `luxctl/core/types.py`
- Test: `tests/core/__init__.py`, `tests/core/test_types.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `SensorReading(sensor_id: str, lux: float, at: datetime)` — frozen dataclass.
  - `LightState(light_id: str, is_on: bool, brightness: float | None, at: datetime)` — frozen dataclass. `brightness` is 0.0–1.0, and is `None` when the light is off or reports no brightness.

  Tasks 3, 4, 6, 7 and 8 all construct or consume these exact types.

**Note on the spec:** the spec's `LightState` carries `color_temp_k` and no timestamp. Phase 0's is a *recorded observation*, so it needs `at`; and colour temperature is explicitly out of scope for the project, so it is omitted rather than recorded unused.

- [ ] **Step 1: Write the failing test**

Create `tests/core/__init__.py` (empty) and `tests/core/test_types.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/core/test_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'luxctl.core.types'`.

- [ ] **Step 3: Write the implementation**

Create `luxctl/core/types.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/core/test_types.py -v`
Expected: PASS — 4 passed.

- [ ] **Step 5: Commit**

```bash
git add luxctl/core/types.py tests/core
git commit -m "feat: add SensorReading and LightState domain types"
```

---

### Task 3: Event parsing

**Files:**
- Create: `luxctl/core/events.py`
- Test: `tests/core/test_events.py`

**Interfaces:**
- Consumes: `SensorReading`, `LightState` from Task 2.
- Produces:
  - `parse_lux(state: dict, sensor_id: str) -> SensorReading | None`
  - `parse_light(state: dict, light_id: str) -> LightState | None`

  Both take a raw Home Assistant state object (the dict shape HA sends in `get_states` results and in `state_changed` events as `new_state`) and return `None` when the entity is unavailable or the value is unusable. Task 7 calls both.

This is the whole reason for the shell/core split: HA's wire format is messy, and every messy case is testable here without a socket.

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_events.py`:

```python
from datetime import datetime, timezone

from luxctl.core.events import parse_light, parse_lux


def lux_state(value, last_updated="2026-07-21T10:00:00.000000+00:00"):
    return {
        "entity_id": "sensor.south_window_lux",
        "state": value,
        "attributes": {"unit_of_measurement": "lx"},
        "last_updated": last_updated,
    }


def light_state(value, attributes=None,
                last_updated="2026-07-21T10:00:00.000000+00:00"):
    return {
        "entity_id": "light.living_room",
        "state": value,
        "attributes": attributes or {},
        "last_updated": last_updated,
    }


def test_parses_a_numeric_lux_reading():
    reading = parse_lux(lux_state("1234.5"), "south_window")

    assert reading is not None
    assert reading.sensor_id == "south_window"
    assert reading.lux == 1234.5
    assert reading.at == datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)


def test_parses_integer_valued_lux():
    reading = parse_lux(lux_state("42"), "south_window")

    assert reading is not None
    assert reading.lux == 42.0


def test_parses_zero_lux():
    reading = parse_lux(lux_state("0"), "south_window")

    assert reading is not None
    assert reading.lux == 0.0


def test_rejects_unavailable_sensor():
    assert parse_lux(lux_state("unavailable"), "south_window") is None


def test_rejects_unknown_sensor():
    assert parse_lux(lux_state("unknown"), "south_window") is None


def test_rejects_non_numeric_state():
    assert parse_lux(lux_state("bright-ish"), "south_window") is None


def test_rejects_negative_lux():
    assert parse_lux(lux_state("-5"), "south_window") is None


def test_rejects_missing_state_key():
    assert parse_lux({"last_updated": "2026-07-21T10:00:00+00:00"},
                     "south_window") is None


def test_parses_light_on_with_brightness():
    state = parse_light(light_state("on", {"brightness": 255}), "living_room")

    assert state is not None
    assert state.is_on is True
    assert state.brightness == 1.0


def test_normalises_brightness_from_ha_255_scale():
    state = parse_light(light_state("on", {"brightness": 128}), "living_room")

    assert state is not None
    assert state.brightness == 128 / 255


def test_light_on_without_brightness_attribute_has_null_brightness():
    state = parse_light(light_state("on"), "living_room")

    assert state is not None
    assert state.is_on is True
    assert state.brightness is None


def test_parses_light_off():
    state = parse_light(light_state("off", {"brightness": 200}), "living_room")

    assert state is not None
    assert state.is_on is False
    assert state.brightness is None


def test_rejects_unavailable_light():
    assert parse_light(light_state("unavailable"), "living_room") is None
```

Note `test_parses_light_off`: HA sometimes leaves a stale `brightness` attribute on a light that is off. Recording it would corrupt Phase 1's calibration, so an off light always records `brightness=None`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/core/test_events.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'luxctl.core.events'`.

- [ ] **Step 3: Write the implementation**

Create `luxctl/core/events.py`:

```python
"""Parsing of Home Assistant state objects into domain types.

Pure: every input arrives as an argument, nothing here touches the network,
the clock or the disk.
"""

from datetime import datetime

from luxctl.core.types import LightState, SensorReading

# Values HA uses to mean "no usable reading".
UNUSABLE = {"unknown", "unavailable", "none", ""}


def _is_unusable(raw: object) -> bool:
    return raw is None or str(raw).strip().lower() in UNUSABLE


def parse_lux(state: dict, sensor_id: str) -> SensorReading | None:
    """Convert an HA state object into a SensorReading.

    Returns None when the entity is unavailable, non-numeric, or negative.
    """
    raw = state.get("state")
    if _is_unusable(raw):
        return None

    try:
        lux = float(raw)
    except (TypeError, ValueError):
        return None

    if lux < 0:
        return None

    return SensorReading(
        sensor_id=sensor_id,
        lux=lux,
        at=datetime.fromisoformat(state["last_updated"]),
    )


def parse_light(state: dict, light_id: str) -> LightState | None:
    """Convert an HA state object into a LightState.

    Returns None when the entity is unavailable. A light that is off always
    records brightness=None, because HA may leave a stale brightness
    attribute behind.
    """
    raw = state.get("state")
    if _is_unusable(raw):
        return None

    is_on = str(raw).strip().lower() == "on"

    brightness = None
    if is_on:
        value = state.get("attributes", {}).get("brightness")
        if value is not None:
            brightness = float(value) / 255.0

    return LightState(
        light_id=light_id,
        is_on=is_on,
        brightness=brightness,
        at=datetime.fromisoformat(state["last_updated"]),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/core/test_events.py -v`
Expected: PASS — 13 passed.

- [ ] **Step 5: Commit**

```bash
git add luxctl/core/events.py tests/core/test_events.py
git commit -m "feat: parse HA state objects into domain types"
```

---

### Task 4: Recorder

**Files:**
- Create: `luxctl/shell/recorder.py`
- Test: `tests/shell/__init__.py`, `tests/shell/test_recorder.py`

**Interfaces:**
- Consumes: `SensorReading`, `LightState` from Task 2.
- Produces: `Recorder` class with
  - `Recorder(path: Path)` — creates the schema if absent
  - `record_reading(reading: SensorReading) -> None`
  - `record_light(state: LightState) -> None`
  - `readings_for(sensor_id: str) -> list[SensorReading]` — ordered by `at`
  - `lights_for(light_id: str) -> list[LightState]` — ordered by `at`
  - `last_reading_at(sensor_id: str) -> datetime | None`
  - `close() -> None`

  Task 7 calls `record_reading` and `record_light`; Task 7's watchdog calls `last_reading_at`; Task 7's tests call `lights_for`; Task 9's analysis reads the database directly with SQL.

- [ ] **Step 1: Write the failing test**

Create `tests/shell/__init__.py` (empty) and `tests/shell/test_recorder.py`:

```python
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
```

`test_storage_is_append_only` matters: two readings with identical timestamps must both survive. A `UNIQUE` constraint or an upsert would silently discard data, and you would not notice until the analysis in Task 9.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/shell/test_recorder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'luxctl.shell.recorder'`.

- [ ] **Step 3: Write the implementation**

Create `luxctl/shell/recorder.py`:

```python
"""Append-only persistence of observations to SQLite."""

import sqlite3
from datetime import datetime
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


class Recorder:
    """Appends observations to SQLite. Never updates, never deletes."""

    def __init__(self, path: Path):
        self._conn = sqlite3.connect(str(path), isolation_level=None)
        # WAL keeps writes cheap on SD cards and lets the analysis script
        # read the database while recording continues.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)

    def record_reading(self, reading: SensorReading) -> None:
        self._conn.execute(
            "INSERT INTO readings (sensor_id, lux, at, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            (
                reading.sensor_id,
                reading.lux,
                reading.at.isoformat(),
                datetime.now().astimezone().isoformat(),
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
                state.at.isoformat(),
                datetime.now().astimezone().isoformat(),
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/shell/test_recorder.py -v`
Expected: PASS — 10 passed.

- [ ] **Step 5: Commit**

```bash
git add luxctl/shell/recorder.py tests/shell
git commit -m "feat: add append-only SQLite recorder"
```

---

### Task 5: Configuration

**Files:**
- Create: `luxctl/shell/config.py`
- Create: `sensors.toml.example`
- Test: `tests/shell/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `SensorConfig(id: str, entity_id: str, placement: str)` — frozen dataclass; `placement` is `"window"` or `"room"`.
  - `LightConfig(id: str, entity_id: str)` — frozen dataclass.
  - `Config(ha_url: str, sensors: tuple[SensorConfig, ...], lights: tuple[LightConfig, ...], stale_after_s: int)` with methods `entity_to_sensor() -> dict[str, str]` and `entity_to_light() -> dict[str, str]`, each mapping HA entity ID to our stable ID.
  - `load_config(path: Path) -> Config`
  - `ConfigError(Exception)`

  Task 7 uses `entity_to_sensor` / `entity_to_light` to route incoming events. Task 9 uses `placement` to decide which sensors are predictors and which are targets.

- [ ] **Step 1: Write the failing test**

Create `tests/shell/test_config.py`:

```python
import pytest

from luxctl.shell.config import ConfigError, load_config

VALID = """
[ha]
url = "ws://homeassistant.local:8123/api/websocket"

[watchdog]
stale_after_s = 600

[[sensors]]
id = "south_window"
entity_id = "sensor.south_window_lux"
placement = "window"

[[sensors]]
id = "living_room"
entity_id = "sensor.living_room_lux"
placement = "room"

[[lights]]
id = "living_room_main"
entity_id = "light.hue_living_1"
"""


def write(tmp_path, text):
    path = tmp_path / "sensors.toml"
    path.write_text(text)
    return path


def test_loads_a_valid_config(tmp_path):
    config = load_config(write(tmp_path, VALID))

    assert config.ha_url == "ws://homeassistant.local:8123/api/websocket"
    assert config.stale_after_s == 600
    assert len(config.sensors) == 2
    assert len(config.lights) == 1


def test_maps_sensor_entity_ids_to_stable_ids(tmp_path):
    config = load_config(write(tmp_path, VALID))

    assert config.entity_to_sensor() == {
        "sensor.south_window_lux": "south_window",
        "sensor.living_room_lux": "living_room",
    }


def test_maps_light_entity_ids_to_stable_ids(tmp_path):
    config = load_config(write(tmp_path, VALID))

    assert config.entity_to_light() == {"light.hue_living_1": "living_room_main"}


def test_preserves_placement(tmp_path):
    config = load_config(write(tmp_path, VALID))
    placements = {s.id: s.placement for s in config.sensors}

    assert placements == {"south_window": "window", "living_room": "room"}


def test_lights_are_optional(tmp_path):
    text = VALID.split("[[lights]]")[0]
    config = load_config(write(tmp_path, text))

    assert config.lights == ()


def test_stale_after_defaults_when_absent(tmp_path):
    text = VALID.replace("[watchdog]\nstale_after_s = 600\n", "")
    config = load_config(write(tmp_path, text))

    assert config.stale_after_s == 600


def test_rejects_unknown_placement(tmp_path):
    text = VALID.replace('placement = "window"', 'placement = "ceiling"')

    with pytest.raises(ConfigError, match="placement"):
        load_config(write(tmp_path, text))


def test_rejects_duplicate_sensor_ids(tmp_path):
    text = VALID.replace('id = "living_room"', 'id = "south_window"')

    with pytest.raises(ConfigError, match="duplicate"):
        load_config(write(tmp_path, text))


def test_rejects_config_with_no_sensors(tmp_path):
    text = VALID.split("[[sensors]]")[0]

    with pytest.raises(ConfigError, match="at least one sensor"):
        load_config(write(tmp_path, text))


def test_rejects_missing_ha_url(tmp_path):
    text = VALID.replace('url = "ws://homeassistant.local:8123/api/websocket"', "")

    with pytest.raises(ConfigError, match="url"):
        load_config(write(tmp_path, text))


def test_rejects_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "absent.toml")
```

`test_rejects_duplicate_sensor_ids` earns its place: sensor IDs are database keys, and two sensors sharing one would interleave into a single unusable series.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/shell/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'luxctl.shell.config'`.

- [ ] **Step 3: Write the implementation**

Create `luxctl/shell/config.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/shell/test_config.py -v`
Expected: PASS — 11 passed.

- [ ] **Step 5: Create the example config**

Create `sensors.toml.example`:

```toml
# Copy to sensors.toml and fill in your entity IDs.
# The HA token comes from the LUXCTL_HA_TOKEN environment variable,
# never from this file.

[ha]
url = "ws://homeassistant.local:8123/api/websocket"

[watchdog]
# Warn if a sensor has not reported within this many seconds.
stale_after_s = 600

# placement is "window" (a daylight reference) or "room" (perceived
# brightness). The Phase 0 analysis correlates the former against the latter.

[[sensors]]
id = "south_window"
entity_id = "sensor.south_window_lux"
placement = "window"

[[sensors]]
id = "north_window"
entity_id = "sensor.north_window_lux"
placement = "window"

[[sensors]]
id = "living_room"
entity_id = "sensor.living_room_lux"
placement = "room"

# Lights are recorded, never commanded, in Phase 0. They are needed to
# explain room-sensor movements that were not caused by daylight.

[[lights]]
id = "living_room_main"
entity_id = "light.hue_living_1"
```

- [ ] **Step 6: Commit**

```bash
git add luxctl/shell/config.py tests/shell/test_config.py sensors.toml.example
git commit -m "feat: load and validate sensors.toml"
```

---

### Task 6: Home Assistant WebSocket client

**Files:**
- Create: `luxctl/shell/ha_client.py`
- Test: `tests/shell/test_ha_client.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `HAClient(url: str, token: str)` with async methods:
    - `connect() -> None` — opens the socket and authenticates; raises `HAAuthError` on failure
    - `get_states() -> list[dict]` — returns raw HA state objects
    - `subscribe_state_changed() -> AsyncIterator[dict]` — yields the `data` payload of each `state_changed` event, i.e. dicts with keys `entity_id`, `old_state`, `new_state`
    - `close() -> None`
  - `HAAuthError(RuntimeError)`

  Task 7 constructs an `HAClient` and consumes all four methods. The dicts yielded by `subscribe_state_changed` are fed to Task 3's parsers.

The client only moves bytes and parses envelopes. All interpretation of state values lives in `core/events.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/shell/test_ha_client.py`:

```python
import asyncio
import json

import pytest
import websockets

from luxctl.shell.ha_client import HAAuthError, HAClient

GOOD_TOKEN = "good-token"

SAMPLE_STATE = {
    "entity_id": "sensor.south_window_lux",
    "state": "1234.5",
    "attributes": {"unit_of_measurement": "lx"},
    "last_updated": "2026-07-21T10:00:00.000000+00:00",
}

SAMPLE_EVENT_DATA = {
    "entity_id": "sensor.south_window_lux",
    "old_state": SAMPLE_STATE,
    "new_state": {**SAMPLE_STATE, "state": "1300.0"},
}


async def fake_ha_handler(websocket):
    """Minimal stand-in for HA's WebSocket API."""
    await websocket.send(json.dumps({"type": "auth_required", "ha_version": "2026.7.0"}))

    auth = json.loads(await websocket.recv())
    if auth.get("access_token") != GOOD_TOKEN:
        await websocket.send(
            json.dumps({"type": "auth_invalid", "message": "Invalid access token"})
        )
        return
    await websocket.send(json.dumps({"type": "auth_ok", "ha_version": "2026.7.0"}))

    async for raw in websocket:
        message = json.loads(raw)
        if message["type"] == "get_states":
            await websocket.send(json.dumps({
                "id": message["id"],
                "type": "result",
                "success": True,
                "result": [SAMPLE_STATE],
            }))
        elif message["type"] == "subscribe_events":
            await websocket.send(json.dumps({
                "id": message["id"],
                "type": "result",
                "success": True,
                "result": None,
            }))
            await websocket.send(json.dumps({
                "id": message["id"],
                "type": "event",
                "event": {
                    "event_type": "state_changed",
                    "data": SAMPLE_EVENT_DATA,
                    "context": {"id": "abc123", "parent_id": None, "user_id": None},
                },
            }))


@pytest.fixture
async def ha_url():
    async with websockets.serve(fake_ha_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://127.0.0.1:{port}/api/websocket"


async def test_authenticates_with_a_valid_token(ha_url):
    client = HAClient(ha_url, GOOD_TOKEN)
    await client.connect()
    await client.close()


async def test_rejects_an_invalid_token(ha_url):
    client = HAClient(ha_url, "wrong-token")

    with pytest.raises(HAAuthError):
        await client.connect()


async def test_get_states_returns_raw_state_objects(ha_url):
    client = HAClient(ha_url, GOOD_TOKEN)
    await client.connect()
    try:
        states = await client.get_states()
    finally:
        await client.close()

    assert len(states) == 1
    assert states[0]["entity_id"] == "sensor.south_window_lux"
    assert states[0]["state"] == "1234.5"


async def test_subscribe_yields_state_changed_payloads(ha_url):
    client = HAClient(ha_url, GOOD_TOKEN)
    await client.connect()
    try:
        events = client.subscribe_state_changed()
        first = await asyncio.wait_for(anext(events), timeout=5)
    finally:
        await client.close()

    assert first["entity_id"] == "sensor.south_window_lux"
    assert first["new_state"]["state"] == "1300.0"


async def test_message_ids_increment(ha_url):
    client = HAClient(ha_url, GOOD_TOKEN)
    await client.connect()
    try:
        await client.get_states()
        await client.get_states()
    finally:
        await client.close()

    assert client._id == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/shell/test_ha_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'luxctl.shell.ha_client'`.

- [ ] **Step 3: Write the implementation**

Create `luxctl/shell/ha_client.py`:

```python
"""Home Assistant WebSocket API client.

Moves bytes and parses envelopes. Interpretation of state values belongs in
luxctl.core.events, not here.
"""

import json
import logging
from collections.abc import AsyncIterator

import websockets

log = logging.getLogger(__name__)


class HAAuthError(RuntimeError):
    """Raised when Home Assistant rejects the access token."""


class HAClient:
    def __init__(self, url: str, token: str):
        self._url = url
        self._token = token
        self._ws = None
        self._id = 0

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def connect(self) -> None:
        """Open the socket and complete HA's auth handshake."""
        self._ws = await websockets.connect(self._url, max_size=None)

        greeting = json.loads(await self._ws.recv())
        if greeting.get("type") != "auth_required":
            raise HAAuthError(f"unexpected greeting from HA: {greeting}")

        await self._ws.send(
            json.dumps({"type": "auth", "access_token": self._token})
        )

        reply = json.loads(await self._ws.recv())
        if reply.get("type") != "auth_ok":
            raise HAAuthError(reply.get("message", "authentication failed"))

        log.info("connected to Home Assistant %s", reply.get("ha_version", "?"))

    async def get_states(self) -> list[dict]:
        """Fetch the current state of every entity HA knows about."""
        message_id = self._next_id()
        await self._ws.send(json.dumps({"id": message_id, "type": "get_states"}))

        while True:
            message = json.loads(await self._ws.recv())
            if message.get("id") == message_id and message.get("type") == "result":
                if not message.get("success"):
                    raise RuntimeError(f"get_states failed: {message}")
                return message["result"]

    async def subscribe_state_changed(self) -> AsyncIterator[dict]:
        """Yield the data payload of every state_changed event.

        Each yielded dict has keys: entity_id, old_state, new_state.
        """
        message_id = self._next_id()
        await self._ws.send(json.dumps({
            "id": message_id,
            "type": "subscribe_events",
            "event_type": "state_changed",
        }))

        async for raw in self._ws:
            message = json.loads(raw)
            if message.get("type") == "event" and message.get("id") == message_id:
                yield message["event"]["data"]

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/shell/test_ha_client.py -v`
Expected: PASS — 5 passed.

- [ ] **Step 5: Commit**

```bash
git add luxctl/shell/ha_client.py tests/shell/test_ha_client.py
git commit -m "feat: add Home Assistant WebSocket client"
```

---

### Task 7: Runner and staleness watchdog

**Files:**
- Create: `luxctl/shell/runner.py`
- Test: `tests/shell/test_runner.py`

**Interfaces:**
- Consumes: `Config` (Task 5), `Recorder` (Task 4), `HAClient` (Task 6), `parse_lux` / `parse_light` (Task 3).
- Produces:
  - `route_state(state: dict, entity_id: str, config: Config, recorder: Recorder) -> bool` — records the state if the entity is configured; returns True if anything was recorded.
  - `find_stale(config: Config, recorder: Recorder, now: datetime) -> list[str]` — sensor IDs with no reading inside the staleness window. `now` is a parameter, not `datetime.now()`, so the function is testable.
  - `async record_forever(config, recorder, token) -> None` — connect, seed from `get_states`, then record events until cancelled. Reconnects with exponential backoff.
  - `cli() -> None` — console-script entry point declared in Task 1's `pyproject.toml`.

The watchdog exists because the worst Phase 0 outcome is collecting two weeks of data and discovering afterwards that a node died on day two.

- [ ] **Step 1: Write the failing test**

Create `tests/shell/test_runner.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/shell/test_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'luxctl.shell.runner'`.

- [ ] **Step 3: Write the implementation**

Create `luxctl/shell/runner.py`:

```python
"""Phase 0 entry point: connect, record, watch for silent sensors.

Records only. Phase 0 issues no light commands.
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from luxctl.core.events import parse_light, parse_lux
from luxctl.shell.config import Config, ConfigError, load_config
from luxctl.shell.ha_client import HAAuthError, HAClient
from luxctl.shell.recorder import Recorder

log = logging.getLogger("luxctl")

WATCHDOG_INTERVAL_S = 60
MAX_BACKOFF_S = 60


def route_state(state: dict, entity_id: str, config: Config,
                recorder: Recorder) -> bool:
    """Record a state object if its entity is configured.

    Returns True when something was written.
    """
    sensor_id = config.entity_to_sensor().get(entity_id)
    if sensor_id is not None:
        reading = parse_lux(state, sensor_id)
        if reading is None:
            return False
        recorder.record_reading(reading)
        return True

    light_id = config.entity_to_light().get(entity_id)
    if light_id is not None:
        light = parse_light(state, light_id)
        if light is None:
            return False
        recorder.record_light(light)
        return True

    return False


def find_stale(config: Config, recorder: Recorder,
               now: datetime) -> list[str]:
    """Return the IDs of sensors with no reading inside the stale window.

    `now` is passed in rather than read from the clock so this is testable.
    """
    cutoff = now - timedelta(seconds=config.stale_after_s)
    stale = []
    for sensor in config.sensors:
        last = recorder.last_reading_at(sensor.id)
        if last is None or last < cutoff:
            stale.append(sensor.id)
    return stale


async def _watchdog(config: Config, recorder: Recorder) -> None:
    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL_S)
        stale = find_stale(config, recorder, now=datetime.now(timezone.utc))
        if stale:
            log.warning(
                "no readings within %ss from: %s",
                config.stale_after_s,
                ", ".join(sorted(stale)),
            )


async def _record_once(config: Config, recorder: Recorder, token: str) -> None:
    """One connect-and-record cycle. Returns when the connection drops."""
    client = HAClient(config.ha_url, token)
    await client.connect()
    try:
        # Seed with current state so every sensor has a value immediately,
        # rather than waiting for its first change.
        seeded = 0
        for state in await client.get_states():
            if route_state(state, state["entity_id"], config, recorder):
                seeded += 1
        log.info("seeded %d entities from current state", seeded)

        async for data in client.subscribe_state_changed():
            new_state = data.get("new_state")
            if new_state is None:
                continue
            route_state(new_state, data["entity_id"], config, recorder)
    finally:
        await client.close()


async def record_forever(config: Config, recorder: Recorder,
                         token: str) -> None:
    """Record indefinitely, reconnecting with exponential backoff."""
    watchdog = asyncio.create_task(_watchdog(config, recorder))
    backoff = 1
    try:
        while True:
            try:
                await _record_once(config, recorder, token)
                log.warning("connection closed by Home Assistant")
            except HAAuthError:
                raise
            except Exception as error:
                log.warning("connection failed: %s", error)

            log.info("reconnecting in %ds", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_S)
    finally:
        watchdog.cancel()


def cli() -> None:
    parser = argparse.ArgumentParser(description="luxctl Phase 0 recorder")
    parser.add_argument("--config", type=Path, default=Path("sensors.toml"))
    parser.add_argument("--db", type=Path, default=Path("data/luxctl.db"))
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    token = os.environ.get("LUXCTL_HA_TOKEN")
    if not token:
        sys.exit("LUXCTL_HA_TOKEN is not set")

    try:
        config = load_config(args.config)
    except ConfigError as error:
        sys.exit(f"config error: {error}")

    args.db.parent.mkdir(parents=True, exist_ok=True)
    recorder = Recorder(args.db)

    log.info("recording %d sensors and %d lights to %s",
             len(config.sensors), len(config.lights), args.db)

    try:
        asyncio.run(record_forever(config, recorder, token))
    except KeyboardInterrupt:
        log.info("stopping")
    except HAAuthError as error:
        sys.exit(f"authentication failed: {error}")
    finally:
        recorder.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/shell/test_runner.py -v`
Expected: PASS — 7 passed.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -v`
Expected: PASS — 52 passed (2 + 4 + 13 + 10 + 11 + 5 + 7).

- [ ] **Step 6: Commit**

```bash
git add luxctl/shell/runner.py tests/shell/test_runner.py
git commit -m "feat: add recording runner with staleness watchdog"
```

---

### Task 8: Deploy to the Pi

**Files:**
- Create: `deploy/luxctl.service`
- Create: `README.md`
- Modify: none

**Interfaces:**
- Consumes: `cli()` from Task 7, `sensors.toml.example` from Task 5, the HA token from Task 0.
- Produces: a running systemd service writing to `data/luxctl.db`. Task 9 reads that database.

This task requires the Pi and the token from Task 0. An agent can write the files; a human must run steps 4–7.

- [ ] **Step 1: Write the systemd unit**

Create `deploy/luxctl.service`:

```ini
[Unit]
Description=luxctl Phase 0 recorder
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/luxctl
Environment="LUXCTL_HA_TOKEN=REPLACE_WITH_TOKEN"
ExecStart=/home/pi/luxctl/.venv/bin/luxctl --config sensors.toml --db data/luxctl.db
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`Restart=always` matters: Phase 0 runs unattended for two weeks, and a crash that silently stops recording costs the whole experiment.

- [ ] **Step 2: Write the README**

Create `README.md`:

````markdown
# luxctl

Sensor-driven lighting control. Currently in Phase 0 (observation only —
records data, commands nothing).

See `docs/superpowers/specs/2026-07-21-luxctl-design.md` for the design.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,analysis]"
cp sensors.toml.example sensors.toml
# edit sensors.toml with your HA entity IDs
export LUXCTL_HA_TOKEN="<long-lived token from HA>"
```

## Run

```bash
.venv/bin/luxctl --config sensors.toml --db data/luxctl.db
```

## Test

```bash
.venv/bin/python -m pytest -v
```

## Analyse

```bash
.venv/bin/python analysis/correlate.py --db data/luxctl.db --out analysis/out
```

## Phase 0 checklist

- [ ] Nodes sited, all sensors reporting
- [ ] Service running under systemd
- [ ] Data flowing (check `sqlite3 data/luxctl.db "SELECT COUNT(*) FROM readings"`)
- [ ] Two weeks elapsed
- [ ] Correlation analysis run and interpreted
````

- [ ] **Step 3: Commit the deploy files**

```bash
git add deploy/luxctl.service README.md
git commit -m "chore: add systemd unit and README"
```

- [ ] **Step 4: Install on the Pi (HUMAN)**

```bash
git clone <repo> /home/pi/luxctl
cd /home/pi/luxctl
python3 -m venv .venv
.venv/bin/pip install -e .
cp sensors.toml.example sensors.toml
```

Edit `sensors.toml` with the entity IDs recorded in Task 0 Step 9.

- [ ] **Step 5: Verify it runs in the foreground first (HUMAN)**

```bash
export LUXCTL_HA_TOKEN="<your token>"
.venv/bin/luxctl --config sensors.toml --db data/luxctl.db --verbose
```

Expected within a few seconds:

```
connected to Home Assistant 2026.7.x
seeded N entities from current state
```

`N` must equal your configured sensor and light count. **If N is lower, an entity ID in `sensors.toml` is wrong** — fix it now rather than discovering it in two weeks. Let it run a minute and confirm no errors, then Ctrl-C.

- [ ] **Step 6: Install the service (HUMAN)**

```bash
sudo cp deploy/luxctl.service /etc/systemd/system/
sudo nano /etc/systemd/system/luxctl.service   # paste the real token
sudo chmod 600 /etc/systemd/system/luxctl.service
sudo systemctl daemon-reload
sudo systemctl enable --now luxctl
systemctl status luxctl
```

Expected: `active (running)`.

- [ ] **Step 7: Verify data is accumulating (HUMAN)**

Wait five minutes, then:

```bash
sqlite3 data/luxctl.db \
  "SELECT sensor_id, COUNT(*), MIN(at), MAX(at) FROM readings GROUP BY sensor_id"
```

Expected: one row per sensor, counts climbing. At a 15s update interval, expect roughly 20 rows per sensor after five minutes.

A sensor missing from this output is not reporting. Fix it before starting the two-week clock.

- [ ] **Step 8: Start the clock**

Note the date. Leave it running for two weeks. Check `journalctl -u luxctl -n 50` every few days for staleness warnings — that is what the watchdog is for.

---

### Task 9: Correlation analysis

**Files:**
- Create: `analysis/correlate.py`
- Test: `tests/test_correlate.py`

**Interfaces:**
- Consumes: the SQLite database written by Task 7, and `load_config` / `SensorConfig` from Task 5.
- Produces: `load_readings(db_path, config) -> DataFrame` and `coverage(frame, expected_interval_s) -> DataFrame`, plus a CLI that prints the correlation matrix and writes plots. This is the terminal deliverable of Phase 0.

- [ ] **Step 1: Write the failing test**

Create `tests/test_correlate.py`:

```python
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
```

`test_coverage_detects_a_missing_sensor_period` is the important one. A correlation computed over a period where one sensor was dead is meaningless, and pandas will happily return a confident number anyway.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_correlate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analysis.correlate'` (or the whole module skips if pandas is absent — install with `.venv/bin/pip install -e ".[analysis]"`).

- [ ] **Step 3: Write the implementation**

Create `analysis/__init__.py` (empty) and `analysis/correlate.py`:

```python
"""Phase 0 deliverable: do window sensors predict room brightness?

If they do, feedforward control is sufficient and the closed-loop machinery
in the spec is unnecessary.
"""

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

from luxctl.shell.config import Config, load_config

RESAMPLE = "1min"


def load_readings(db_path: Path, config: Config) -> pd.DataFrame:
    """Load all readings into a frame with one column per sensor.

    Resampled to one-minute means so sensors reporting at different times
    line up on a common index.
    """
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        raw = pd.read_sql_query(
            "SELECT sensor_id, lux, at FROM readings ORDER BY at",
            connection,
            parse_dates=["at"],
        )
    finally:
        connection.close()

    known = {sensor.id for sensor in config.sensors}
    raw = raw[raw["sensor_id"].isin(known)]

    wide = raw.pivot_table(index="at", columns="sensor_id", values="lux",
                           aggfunc="mean")
    wide = wide.resample(RESAMPLE).mean()
    wide.columns.name = None
    return wide


def coverage(frame: pd.DataFrame, expected_interval_s: int) -> pd.DataFrame:
    """Report how complete each sensor's series is.

    A correlation computed across a period where a sensor was dead is
    meaningless, so always read this before reading the correlations.
    """
    span = frame.index.max() - frame.index.min()
    expected = span.total_seconds() / expected_interval_s + 1

    return pd.DataFrame({
        "readings": frame.notna().sum(),
        "completeness": (frame.notna().sum() / expected).clip(upper=1.0),
        "min_lux": frame.min(),
        "max_lux": frame.max(),
    })


def correlations(frame: pd.DataFrame, config: Config) -> pd.DataFrame:
    """Pearson r for every window sensor against every room sensor."""
    windows = [s.id for s in config.sensors
               if s.placement == "window" and s.id in frame.columns]
    rooms = [s.id for s in config.sensors
             if s.placement == "room" and s.id in frame.columns]

    return pd.DataFrame(
        {room: {window: frame[window].corr(frame[room]) for window in windows}
         for room in rooms}
    )


def write_plots(frame: pd.DataFrame, config: Config, out_dir: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)

    axis = frame.plot(figsize=(14, 6), logy=True, linewidth=0.8)
    axis.set_ylabel("lux (log scale)")
    axis.set_title("Recorded illuminance")
    axis.figure.savefig(out_dir / "timeseries.png", dpi=120, bbox_inches="tight")
    plt.close(axis.figure)

    windows = [s.id for s in config.sensors
               if s.placement == "window" and s.id in frame.columns]
    rooms = [s.id for s in config.sensors
             if s.placement == "room" and s.id in frame.columns]

    for window in windows:
        for room in rooms:
            figure, axes = plt.subplots(figsize=(6, 6))
            axes.scatter(frame[window], frame[room], s=2, alpha=0.3)
            axes.set_xscale("log")
            axes.set_yscale("log")
            axes.set_xlabel(f"{window} (lux)")
            axes.set_ylabel(f"{room} (lux)")
            axes.set_title(f"r = {frame[window].corr(frame[room]):.3f}")
            figure.savefig(out_dir / f"scatter_{window}_vs_{room}.png",
                           dpi=120, bbox_inches="tight")
            plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 0 correlation analysis")
    parser.add_argument("--db", type=Path, default=Path("data/luxctl.db"))
    parser.add_argument("--config", type=Path, default=Path("sensors.toml"))
    parser.add_argument("--out", type=Path, default=Path("analysis/out"))
    args = parser.parse_args()

    config = load_config(args.config)
    frame = load_readings(args.db, config)

    print(f"\nSpan: {frame.index.min()} to {frame.index.max()}")
    print(f"Samples: {len(frame)} minutes\n")

    print("Coverage — read this first; low completeness invalidates the rest")
    print(coverage(frame, expected_interval_s=60).to_string(), "\n")

    print("Pearson r, window sensors (rows) vs room sensors (columns)")
    print(correlations(frame, config).to_string(), "\n")

    write_plots(frame, config, args.out)
    print(f"Plots written to {args.out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pip install -e ".[analysis]" && .venv/bin/python -m pytest tests/test_correlate.py -v`
Expected: PASS — 5 passed.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -v`
Expected: PASS — 57 passed.

- [ ] **Step 6: Commit**

```bash
git add analysis tests/test_correlate.py
git commit -m "feat: add Phase 0 window-vs-room correlation analysis"
```

- [ ] **Step 7: Run it against real data (HUMAN, after two weeks)**

```bash
.venv/bin/python analysis/correlate.py --db data/luxctl.db --out analysis/out
```

**Reading the result.** Check completeness first — anything below ~0.9 means a sensor had outages and its correlations are suspect.

Then, for each window/room pair:

| Pearson r | Reading |
|---|---|
| > 0.8 | Window sensors predict this room well. Feedforward is sufficient; skip the closed-loop machinery for it. |
| 0.5 – 0.8 | Partial. Feedforward gets you most of the way; the room may need its own sensor. |
| < 0.5 | Window sensors do not predict this room. It needs a local sensor, and the feedback question is live. |

Also look at the scatter plots. A high r with an obviously curved scatter means the relationship is real but non-linear — still usable, but a linear model will fit badly.

**This result determines Phase 1's scope.** If every room correlates well, the calibration sweep only needs to cover rooms that will run closed-loop — possibly none.

---

## Implementation note (added after execution)

The code blocks in the tasks above are the plan's *starting* code. Every task
went through review, and several were hardened beyond what is written here —
non-finite lux rejection, UTC normalisation of stored timestamps, a guard
against concurrent `get_states()`, duration-based reconnect backoff, per-entity
seeding warnings, and NaN-backfilled columns for sensors that never reported.

**The committed code is authoritative, not these blocks.** Read the source and
its tests rather than transcribing from here.

## Self-review

**Spec coverage.** Phase 0's spec requirements map as follows: HA as device-driver layer → Task 0; ESPHome nodes with two-stage bring-up → Task 0 Steps 4–8; `ha_client` → Task 6; `recorder` → Task 4; the shell/core split → enforced by the Task 2/3 vs 4/5/6/7 division; ~15s resolution to SQLite → Task 0 Step 6 and Task 4; window-vs-room correlation → Task 9. The tick loop is intentionally reduced to a watchdog, documented in "Deviations" above. Nothing in the spec's Phase 0 is unimplemented.

**Out of scope, correctly.** No dispatcher, no strategies, no safety ladder, no calibration — those are Phases 1–3. `LightCommand` is not defined anywhere in this plan, which is correct: Phase 0 issues none.

**Type consistency.** `SensorReading(sensor_id, lux, at)` and `LightState(light_id, is_on, brightness, at)` are used identically in Tasks 2, 3, 4, 7 and 9. `Recorder` method names (`record_reading`, `record_light`, `readings_for`, `lights_for`, `last_reading_at`, `close`) match between Task 4's definition and Tasks 7 and 9's use. `Config` fields and its two mapping methods match between Tasks 5, 7 and 9. `HAClient`'s four methods match between Tasks 6 and 7.

**Placeholder scan.** No TBDs, no "handle edge cases", no "similar to Task N". Every code step contains complete runnable code. Two values are deliberately left for a human to fill in and are marked as such at the point of use: the STEMMA QT GPIO numbers (Task 0 Step 5, unavailable from the datasheet) and the HA token in the systemd unit (Task 8 Step 1, a secret that must not be committed).
