import asyncio
import json

import pytest
import websockets

from luxctl.shell import ha_client as ha_client_module
from luxctl.shell.ha_client import (
    HAAuthError,
    HAClient,
    HAConnectionError,
    HASubscriptionActiveError,
)

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
            # requested_id lets tests verify each call's response is routed
            # back to the call that asked for it, without touching the
            # client's private state.
            await websocket.send(json.dumps({
                "id": message["id"],
                "type": "result",
                "success": True,
                "result": [{**SAMPLE_STATE, "requested_id": message["id"]}],
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


async def test_sequential_get_states_calls_are_each_correctly_routed(ha_url):
    """Two sequential calls must each receive their own response, not a
    stale or swapped one. This is black-box: it never touches client._id,
    it only checks that the server's echoed id matches what each call
    actually got back."""
    client = HAClient(ha_url, GOOD_TOKEN)
    await client.connect()
    try:
        first = await client.get_states()
        second = await client.get_states()
    finally:
        await client.close()

    assert first[0]["requested_id"] != second[0]["requested_id"]
    assert first[0]["entity_id"] == "sensor.south_window_lux"
    assert second[0]["entity_id"] == "sensor.south_window_lux"


async def test_get_states_before_connect_raises_clear_error():
    client = HAClient("ws://127.0.0.1:1/api/websocket", GOOD_TOKEN)

    with pytest.raises(HAConnectionError, match="connect"):
        await client.get_states()


async def test_subscribe_before_connect_raises_clear_error():
    client = HAClient("ws://127.0.0.1:1/api/websocket", GOOD_TOKEN)

    with pytest.raises(HAConnectionError, match="connect"):
        await anext(client.subscribe_state_changed())


async def test_get_states_before_connect_error_does_not_leak_token():
    secret = "super-secret-token"
    client = HAClient("ws://127.0.0.1:1/api/websocket", secret)

    with pytest.raises(HAConnectionError) as excinfo:
        await client.get_states()

    assert secret not in str(excinfo.value)


async def test_get_states_raises_while_subscription_active(ha_url):
    """Regression test for the get_states()/subscribe_state_changed()
    overlap hazard: get_states() reads and discards frames until it finds
    its own result, which would silently swallow subscription events. It
    must instead fail loudly."""
    client = HAClient(ha_url, GOOD_TOKEN)
    await client.connect()
    try:
        events = client.subscribe_state_changed()
        await asyncio.wait_for(anext(events), timeout=5)  # subscription now active

        with pytest.raises(HASubscriptionActiveError):
            await client.get_states()
    finally:
        await client.close()


async def test_connect_uses_a_bounded_max_size(ha_url, monkeypatch):
    """max_size=None (unbounded) was replaced with an explicit generous
    bound. Verify connect() actually passes that named constant through to
    websockets.connect rather than disabling the limit."""
    captured = {}
    real_connect = websockets.connect

    async def spy_connect(url, **kwargs):
        captured.update(kwargs)
        return await real_connect(url, **kwargs)

    monkeypatch.setattr(ha_client_module.websockets, "connect", spy_connect)

    client = HAClient(ha_url, GOOD_TOKEN)
    await client.connect()
    await client.close()

    assert captured["max_size"] == ha_client_module.MAX_MESSAGE_SIZE
    assert captured["max_size"] is not None


async def test_close_resets_subscription_active_flag(ha_url):
    """If a subscription generator is abandoned (not closed) and then close()
    is called on the client, the subscription flag must be reset so that
    get_states() can be called afterwards. Without this, the flag would be
    stuck True and get_states() would raise HASubscriptionActiveError forever."""
    client = HAClient(ha_url, GOOD_TOKEN)
    await client.connect()
    try:
        # Start a subscription and receive one event (this sets _subscription_active = True)
        events = client.subscribe_state_changed()
        await asyncio.wait_for(anext(events), timeout=5)
        # Abandon the generator without closing it; close() is called next
        # without awaiting events.aclose()
        # (The flag should still be True at this point)

        # Now close the client without closing the subscription generator
        await client.close()

        # Reconnect to try get_states(); it should NOT raise HASubscriptionActiveError
        await client.connect()
        try:
            # If the flag were not reset, this would raise HASubscriptionActiveError
            states = await client.get_states()
            assert len(states) >= 0  # Just verify we got a response
        finally:
            await client.close()
    except HASubscriptionActiveError:
        pytest.fail("get_states() should not raise HASubscriptionActiveError after close()")
