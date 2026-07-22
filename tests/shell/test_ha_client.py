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
