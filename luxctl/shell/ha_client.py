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
