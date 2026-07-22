import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytest

from luxctl.core.types import SensorReading
from luxctl.shell import runner
from luxctl.shell.config import Config, LightConfig, SensorConfig
from luxctl.shell.recorder import Recorder
from luxctl.shell.ha_client import HAAuthError, HAProtocolError
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
    """A connection that actually stayed up for a while (not just one that
    authenticated) should reset the reconnect backoff, so a healthy
    connection followed by a blip reconnects promptly rather than waiting
    out a backoff grown from earlier, unrelated failures."""
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

    # The "succeed" client represents a connection that was healthy for a
    # long time before dropping: it authenticates once, at t=0, and the
    # cycle only ends (t=HEALTHY_CONNECTION_S + 1) well past the healthy
    # threshold. Every other cycle fails before ever authenticating, so
    # the clock is never consulted for them.
    clock = iter([0.0, runner.HEALTHY_CONNECTION_S + 1])
    monkeypatch.setattr(runner, "_now", lambda: next(clock))

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


class _FlappingClient:
    """Authenticates successfully every time, then the connection drops
    almost immediately -- the "HA is flapping" failure mode. Each cycle
    sets connected=True, which is exactly what the regression under test
    conflated with "healthy"."""

    async def connect(self):
        pass

    async def get_states(self):
        raise RuntimeError("socket dropped right after auth")

    async def subscribe_state_changed(self):
        return
        yield  # pragma: no cover - makes this an async generator

    async def close(self):
        pass


async def test_backoff_escalates_during_a_flapping_connection(recorder, monkeypatch):
    """Auth succeeding is not the same as the connection being healthy.

    If Home Assistant is flapping -- auth succeeds, then the socket drops a
    moment later, over and over -- resetting the backoff on every successful
    auth defeats exponential backoff entirely: it collapses into a fixed
    1-second reconnect hammer for the life of the run. The backoff must only
    reset when a connection actually stayed up for a while; a connection
    that dies moments after authenticating must let the backoff keep
    escalating toward the cap.
    """
    monkeypatch.setattr(runner, "HAClient", lambda url, token: _FlappingClient())

    async def fake_watchdog(config, recorder):
        await asyncio.Event().wait()

    monkeypatch.setattr(runner, "_watchdog", fake_watchdog)

    # The clock never advances between a cycle's connect and its failure,
    # so every connection "lasted" 0 seconds -- nowhere near the healthy
    # threshold. This is what makes the scenario a flap rather than a
    # long-healthy connection.
    monkeypatch.setattr(runner, "_now", lambda: 0.0)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 5:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await runner.record_forever(CONFIG, recorder, "token")

    # Every cycle authenticates successfully and then dies immediately, so
    # the backoff must keep escalating rather than being reset to 1 before
    # every sleep.
    assert sleep_calls[:5] == [1, 2, 4, 8, 16]


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


class _StreamingClient:
    """Stand-in for HAClient that seeds a fixed set of states and then
    delivers a fixed set of state_changed payloads before closing."""

    def __init__(self, states=(), events=()):
        self._states = list(states)
        self._events = list(events)

    async def connect(self):
        pass

    async def get_states(self):
        return self._states

    async def subscribe_state_changed(self):
        for event in self._events:
            yield event

    async def close(self):
        pass


def _ha_state(entity_id, value, attributes=None):
    return {
        "entity_id": entity_id,
        "state": value,
        "attributes": attributes or {},
        "last_updated": "2026-07-21T10:00:00+00:00",
    }


async def test_one_failing_entity_does_not_abort_the_recording_cycle(
    recorder, monkeypatch, caplog
):
    """A single entity that blows up while being recorded must not take the
    whole run down. Without isolation the exception unwinds the cycle, the
    caller reconnects, re-seeds the same state, and fails identically --
    an endless loop recording nothing at all, for every sensor."""
    good = _ha_state("sensor.living_room_lux", "200.0")
    bad = _ha_state("sensor.south_window_lux", "1234.5")

    real_route_state = runner.route_state

    def exploding_route_state(state, entity_id, config, rec):
        if entity_id == "sensor.south_window_lux":
            raise RuntimeError("disk is full")
        return real_route_state(state, entity_id, config, rec)

    monkeypatch.setattr(runner, "route_state", exploding_route_state)
    monkeypatch.setattr(
        runner, "HAClient", lambda url, token: _StreamingClient(states=[bad, good])
    )

    with caplog.at_level(logging.ERROR):
        await runner._record_once(CONFIG, recorder, "token")

    # The healthy sensor was still recorded despite its neighbour failing.
    assert len(recorder.readings_for("living_room")) == 1
    assert recorder.readings_for("south_window") == []
    # And the failure names the offending entity, not just "something broke".
    assert "sensor.south_window_lux" in caplog.text


async def test_subscribed_events_are_recorded(recorder, monkeypatch):
    """The seeding path was covered, but the subscribe->route wiring that
    actually runs for the whole fortnight was not."""
    event = {
        "entity_id": "sensor.south_window_lux",
        "old_state": None,
        "new_state": _ha_state("sensor.south_window_lux", "4321.0"),
    }
    monkeypatch.setattr(
        runner, "HAClient", lambda url, token: _StreamingClient(events=[event])
    )

    await runner._record_once(CONFIG, recorder, "token")

    readings = recorder.readings_for("south_window")
    assert len(readings) == 1
    assert readings[0].lux == 4321.0


async def test_a_stale_sensor_is_named_in_a_warning(recorder, monkeypatch, caplog):
    """The staleness warning is the designed defence against a node dying
    early in a two-week run. Assert the warning itself, not just that
    find_stale returns something."""
    recorder.record_reading(SensorReading("south_window", 1.0, BASE))

    async def one_shot_sleep(seconds):
        one_shot_sleep.calls += 1
        if one_shot_sleep.calls > 1:
            raise asyncio.CancelledError()

    one_shot_sleep.calls = 0
    monkeypatch.setattr(asyncio, "sleep", one_shot_sleep)
    monkeypatch.setattr(
        runner, "find_stale", lambda config, rec, now: ["south_window"]
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(asyncio.CancelledError):
            await runner._watchdog(CONFIG, recorder)

    assert "south_window" in caplog.text


async def test_a_genuine_auth_failure_is_fatal_and_does_not_retry(
    recorder, monkeypatch
):
    """Retrying a rejected token cannot succeed, and hammering HA with bad
    credentials is worse than stopping."""
    attempts = []

    class _AuthFailingClient:
        async def connect(self):
            attempts.append(1)
            raise HAAuthError("Invalid access token")

        async def close(self):
            pass

    monkeypatch.setattr(runner, "HAClient", lambda url, token: _AuthFailingClient())

    async def fake_watchdog(config, rec):
        await asyncio.Event().wait()

    monkeypatch.setattr(runner, "_watchdog", fake_watchdog)

    with pytest.raises(HAAuthError):
        await runner.record_forever(CONFIG, recorder, "bad-token")

    assert len(attempts) == 1


async def test_an_unexpected_greeting_is_retried_rather_than_fatal(
    recorder, monkeypatch
):
    """HA mid-restart, or a proxy answering early, is transient. Treating it
    as an auth failure would kill a fortnight's run over a blip and blame
    the credentials."""
    attempts = []

    class _OddGreetingClient:
        async def connect(self):
            attempts.append(1)
            raise HAProtocolError("unexpected greeting from HA: {'type': 'nope'}")

        async def close(self):
            pass

    monkeypatch.setattr(runner, "HAClient", lambda url, token: _OddGreetingClient())

    async def fake_watchdog(config, rec):
        await asyncio.Event().wait()

    monkeypatch.setattr(runner, "_watchdog", fake_watchdog)

    async def fake_sleep(seconds):
        if len(attempts) >= 3:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await runner.record_forever(CONFIG, recorder, "token")

    assert len(attempts) >= 3


def test_cli_exits_clearly_when_the_token_is_missing(monkeypatch, capsys, tmp_path):
    """A missing token must produce a readable message, not a traceback --
    this is the first thing a human hits bringing the service up by hand."""
    monkeypatch.delenv("LUXCTL_HA_TOKEN", raising=False)
    monkeypatch.setattr("sys.argv", ["luxctl", "--config", str(tmp_path / "x.toml")])

    with pytest.raises(SystemExit) as exit_info:
        runner.cli()

    assert "LUXCTL_HA_TOKEN" in str(exit_info.value)


def test_cli_reports_a_bad_config_without_a_traceback(monkeypatch, tmp_path):
    monkeypatch.setenv("LUXCTL_HA_TOKEN", "token")
    missing = tmp_path / "absent.toml"
    monkeypatch.setattr("sys.argv", ["luxctl", "--config", str(missing)])

    with pytest.raises(SystemExit) as exit_info:
        runner.cli()

    message = str(exit_info.value)
    assert "config error" in message
    assert "absent.toml" in message


def test_cli_never_emits_the_token_into_a_log_record(monkeypatch, tmp_path, caplog):
    """The token reaches cli() from the environment and is passed onward.
    It must not reach a log record on any startup path."""
    secret = "super-secret-token-value"
    monkeypatch.setenv("LUXCTL_HA_TOKEN", secret)
    monkeypatch.setattr("sys.argv", ["luxctl", "--config", str(tmp_path / "no.toml")])

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(SystemExit):
            runner.cli()

    assert secret not in caplog.text
