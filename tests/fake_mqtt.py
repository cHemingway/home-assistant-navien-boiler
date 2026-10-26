"""A stand-in for AWS IoT, used in place of `aiomqtt.Client`.

aiomqtt ships no test double, and the real connection is MQTT over TLS WebSockets on port 443 with
SigV4-signed URLs, which a local broker can't easily accept. This fake keeps the parts our code relies on:
`async with Client(...)`, `subscribe`, `publish` and `async for message in client.messages`.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
import json
from types import SimpleNamespace
from typing import Any

import aiomqtt

# Given a request's topic and payload, the (topic, message) replies to send back.
Responder = Callable[[str, dict[str, Any]], list[tuple[str, dict[str, Any]]]]


class FakeBroker:
    """Records connections, subscriptions and publishes, and answers requests with `responder`."""

    def __init__(self) -> None:
        """Start with no connections and no replies."""
        self.clients: list[FakeClient] = []
        self.published: list[tuple[str, dict[str, Any]]] = []
        self.responder: Responder = lambda topic, payload: []
        # Raised by the next connection attempt, e.g. `aiomqtt.MqttError("refused")`.
        self.connect_error: Exception | None = None
        # When set, connection attempts never complete.
        self.hang = False

    def Client(self, **kwargs: Any) -> FakeClient:
        """Replace `aiomqtt.Client`."""
        client = FakeClient(self, kwargs)
        self.clients.append(client)
        return client

    @property
    def client(self) -> FakeClient:
        """The latest connection."""
        return self.clients[-1]

    def push(self, topic: str, message: dict[str, Any] | str) -> None:
        """Send a message to the client, as the module does for unprompted status pushes."""
        self.client.deliver(topic, message)

    def drop(self, error: str = "Connection lost") -> None:
        """End the connection, as a network failure would."""
        self.client.deliver_error(aiomqtt.MqttError(error))


class FakeClient:
    """One connection to the `FakeBroker`."""

    def __init__(self, broker: FakeBroker, kwargs: dict[str, Any]) -> None:
        """Keep the connection settings for tests to inspect."""
        self.broker = broker
        self.kwargs = kwargs
        self.subscriptions: list[str] = []
        self._inbox: asyncio.Queue[SimpleNamespace | Exception] = asyncio.Queue()

    async def __aenter__(self) -> FakeClient:
        """Connect, or fail the way the broker is set up to."""
        if self.broker.hang:
            await asyncio.Event().wait()
        if self.broker.connect_error:
            raise self.broker.connect_error
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Disconnect."""

    async def subscribe(self, topic: str, qos: int = 0) -> None:
        """Record a subscription."""
        self.subscriptions.append(topic)

    async def publish(self, topic: str, payload: str, qos: int = 0) -> None:
        """Record a publish and queue the broker's replies to it."""
        message = json.loads(payload)
        self.broker.published.append((topic, message))
        for reply_topic, reply in self.broker.responder(topic, message):
            self.deliver(reply_topic, reply)

    def deliver(self, topic: str, message: dict[str, Any] | str) -> None:
        """Queue a message for `messages`."""
        payload = message if isinstance(message, str) else json.dumps(message)
        self._inbox.put_nowait(SimpleNamespace(topic=topic, payload=payload.encode()))

    def deliver_error(self, error: Exception) -> None:
        """Make `messages` raise `error`."""
        self._inbox.put_nowait(error)

    @property
    async def messages(self) -> AsyncIterator[SimpleNamespace]:
        """Incoming messages, until the connection fails."""
        while True:
            item = await self._inbox.get()
            if isinstance(item, Exception):
                raise item
            yield item
