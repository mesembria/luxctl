"""Home Assistant WebSocket API client.

Moves bytes and parses envelopes. Interpretation of state values belongs in
luxctl.core.events, not here.
"""

import json
import logging
from collections.abc import AsyncIterator

import websockets

log = logging.getLogger(__name__)

# HA's get_states response on a large install can exceed the ~1 MiB default
# frame limit, so we can't leave that default in place. But max_size=None
# disables the limit entirely, which allows unbounded memory growth on a
# resource-constrained Pi if a frame is ever unexpectedly huge or corrupt.
# 16 MiB is a generous bound that comfortably covers real HA installs while
# still capping worst-case memory use.
MAX_MESSAGE_SIZE = 16 * 1024 * 1024


class HAAuthError(RuntimeError):
    """Raised when Home Assistant rejects the access token.

    This is fatal: retrying with the same bad token cannot succeed, so the
    caller is expected to stop rather than reconnect. Only genuine credential
    rejection belongs here -- see HAProtocolError for the retryable case.
    """


class HAProtocolError(RuntimeError):
    """Raised when Home Assistant says something unexpected during the
    handshake.

    Deliberately NOT a subclass of HAAuthError. A malformed or unexpected
    greeting is usually transient -- HA mid-restart, or a reverse proxy
    answering before HA is ready -- so it must reach the caller's ordinary
    retry-with-backoff path. Making it an HAAuthError subclass would send it
    to the fatal handler instead, killing a two-week run over a blip and
    reporting it as a credentials problem.
    """


class HAConnectionError(RuntimeError):
    """Raised when a method that needs an open socket is called before
    connect() has succeeded."""


class HASubscriptionActiveError(RuntimeError):
    """Raised by get_states() when a subscribe_state_changed() stream is
    active on the same connection.

    get_states() reads and discards frames from the socket until it finds
    its own matching result. If a subscription is also reading from that
    socket, get_states() would silently steal and drop state_changed
    events meant for the subscriber. There is exactly one socket and one
    reader; the two must not be interleaved.
    """


class HAClient:
    def __init__(self, url: str, token: str):
        self._url = url
        self._token = token
        self._ws = None
        self._id = 0
        self._subscription_active = False

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _require_connected(self) -> None:
        if self._ws is None:
            raise HAConnectionError("connect() must be called before this method")

    async def connect(self) -> None:
        """Open the socket and complete HA's auth handshake.

        If anything fails after the socket is open, it is closed before the
        exception propagates. Leaking it would matter: the caller reconnects
        on a timer for weeks, and an fd leaked per attempt exhausts the
        process limit long before anyone looks at the logs.
        """
        self._ws = await websockets.connect(self._url, max_size=MAX_MESSAGE_SIZE)

        try:
            greeting = json.loads(await self._ws.recv())
            if greeting.get("type") != "auth_required":
                raise HAProtocolError(f"unexpected greeting from HA: {greeting}")

            await self._ws.send(
                json.dumps({"type": "auth", "access_token": self._token})
            )

            reply = json.loads(await self._ws.recv())
            if reply.get("type") != "auth_ok":
                raise HAAuthError(reply.get("message", "authentication failed"))
        except BaseException:
            await self._ws.close()
            self._ws = None
            raise

        log.info("connected to Home Assistant %s", reply.get("ha_version", "?"))

    async def get_states(self) -> list[dict]:
        """Fetch the current state of every entity HA knows about."""
        self._require_connected()
        if self._subscription_active:
            raise HASubscriptionActiveError(
                "get_states() cannot be called while subscribe_state_changed() "
                "is active on this connection; it would silently discard "
                "state_changed events meant for the subscription"
            )

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
        self._require_connected()

        message_id = self._next_id()
        self._subscription_active = True
        try:
            await self._ws.send(json.dumps({
                "id": message_id,
                "type": "subscribe_events",
                "event_type": "state_changed",
            }))

            async for raw in self._ws:
                message = json.loads(raw)
                if message.get("type") == "event" and message.get("id") == message_id:
                    yield message["event"]["data"]
        finally:
            self._subscription_active = False

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        self._subscription_active = False
