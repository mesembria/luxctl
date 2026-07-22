import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytest

from luxctl.core.types import SensorReading
from luxctl.shell import runner
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


class _ScriptedClient:
    """Stand-in for HAClient whose connect() either fails or succeeds, and
    which otherwise reports an empty, immediately-closed stream."""

    def __init__(self, behavior):
        self._behavior = behavior

    async def connect(self):
        if self._behavior == "fail_connect":
            raise RuntimeError("connect failed")

    async def get_states(self):
        return []

    async def subscribe_state_changed(self):
        return
        yield  # pragma: no cover - makes this an async generator

    async def close(self):
        pass


async def test_backoff_resets_after_a_successful_connection(recorder, monkeypatch):
    """A connection that actually succeeds should reset the reconnect
    backoff, so a healthy connection followed by a blip reconnects
    promptly rather than waiting out a backoff grown from earlier,
    unrelated failures."""
    behaviors = iter(["fail_connect", "fail_connect", "succeed", "fail_connect"])

    def fake_ha_client(url, token):
        return _ScriptedClient(next(behaviors))

    monkeypatch.setattr(runner, "HAClient", fake_ha_client)

    async def fake_watchdog(config, recorder):
        # Never resolves on its own; only cancellation ends it. Uses an
        # Event rather than asyncio.sleep so it doesn't interfere with the
        # sleep-call recording below.
        await asyncio.Event().wait()

    monkeypatch.setattr(runner, "_watchdog", fake_watchdog)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 4:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await runner.record_forever(CONFIG, recorder, "token")

    # 1st, 2nd failures double the backoff (1, 2). The 3rd attempt succeeds,
    # so the backoff before the 4th (failing) attempt is reset to 1, not
    # continued at 4.
    assert sleep_calls[:4] == [1, 2, 1, 2]


async def test_watchdog_survives_and_logs_a_raising_iteration(recorder, monkeypatch, caplog):
    """A watchdog iteration that raises must be logged loudly, and the
    watchdog loop must keep running afterward rather than dying silently."""
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 3:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    call_count = 0

    def fake_find_stale(config, recorder, now):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom-stale-check")
        return []

    monkeypatch.setattr(runner, "find_stale", fake_find_stale)

    with caplog.at_level(logging.ERROR, logger="luxctl"):
        with pytest.raises(asyncio.CancelledError):
            await runner._watchdog(CONFIG, recorder)

    # The loop survived the first, raising iteration and ran a second time.
    assert call_count == 2
    assert "boom-stale-check" in caplog.text


async def test_seeding_warns_about_a_configured_entity_ha_did_not_return(
    recorder, monkeypatch, caplog
):
    """If a configured entity_id is typo'd (or otherwise absent from HA's
    current state), seeding must name both the stable sensor id and the
    HA entity_id so the mismatch is easy to grep for."""

    class MissingEntityClient:
        def __init__(self, url, token):
            pass

        async def connect(self):
            pass

        async def get_states(self):
            # sensor.south_window_lux is conspicuously absent, as if
            # someone typo'd it in sensors.toml.
            return [
                {
                    "entity_id": "sensor.living_room_lux",
                    "state": "10",
                    "attributes": {},
                    "last_updated": "2026-07-21T10:00:00+00:00",
                },
            ]

        async def subscribe_state_changed(self):
            return
            yield  # pragma: no cover

        async def close(self):
            pass

    monkeypatch.setattr(runner, "HAClient", MissingEntityClient)

    with caplog.at_level(logging.WARNING, logger="luxctl"):
        await runner._record_once(CONFIG, recorder, "token")

    assert any(
        "south_window" in record.getMessage()
        and "sensor.south_window_lux" in record.getMessage()
        for record in caplog.records
    )
