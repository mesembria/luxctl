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
