"""AWS IoT MQTT-over-WebSocket connection used for status and control."""

import asyncio
from collections.abc import Callable, Iterable
import contextlib
import json
import logging
import ssl
import time
from typing import Any

import aiomqtt

from .const import AWS_REGION, IOT_ENDPOINT, IOT_SERVICE
from .exceptions import NavienConnectionError, NavienTimeoutError
from .models import AuthInfo
from .sigv4 import presign_mqtt_path

_LOGGER = logging.getLogger(__name__)

# (topic, decoded message, whether it answered one of our pending requests)
MessageCallback = Callable[[str, dict[str, Any], bool], None]


class NavienMqtt:
    """One MQTT session: subscribe, publish, and match replies to requests by sessionID."""

    def __init__(
        self,
        client_id: str,
        on_message: MessageCallback,
        on_disconnect: Callable[[Exception | None], None],
        ssl_context: ssl.SSLContext,
        host: str = IOT_ENDPOINT,
    ) -> None:
        """Set up the session; `connect` opens it."""
        self.client_id = client_id
        self._on_message = on_message
        self._on_disconnect = on_disconnect
        self._ssl_context = ssl_context
        self._host = host
        self._client: aiomqtt.Client | None = None
        self._task: asyncio.Task[None] | None = None
        self._connected = asyncio.Event()
        # sessionID -> (topics the reply may arrive on, future resolved with the reply)
        self._pending: dict[
            str, tuple[frozenset[str], asyncio.Future[dict[str, Any]]]
        ] = {}
        self._last_session = 0

    @property
    def connected(self) -> bool:
        """Whether the connection is up and subscribed."""
        return self._connected.is_set()

    def new_session_id(self) -> str:
        """Return a session ID that's unique within this connection."""
        session = max(int(time.time() * 1000), self._last_session + 1)
        self._last_session = session
        return str(session)

    async def connect(
        self, auth: AuthInfo, topics: list[str], timeout: float = 20
    ) -> None:
        """Connect with SigV4-signed credentials and subscribe to `topics`."""
        if self._task and not self._task.done():
            raise NavienConnectionError("Already connected")
        path = presign_mqtt_path(
            self._host,
            AWS_REGION,
            IOT_SERVICE,
            auth.access_key_id,
            auth.secret_key,
            auth.session_token,
        )
        self._task = asyncio.create_task(
            self._run(path, topics), name="navien_smartplus_mqtt"
        )
        connected = asyncio.create_task(self._connected.wait())
        done, _ = await asyncio.wait(
            {connected, self._task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if connected not in done:
            connected.cancel()
            if self._task in done and (err := self._task.exception()):
                raise NavienConnectionError(f"MQTT connect failed: {err}") from err
            await self.disconnect()
            raise NavienConnectionError("MQTT connect timed out")

    async def _run(self, path: str, topics: list[str]) -> None:
        error: Exception | None = None
        try:
            async with aiomqtt.Client(
                hostname=self._host,
                port=443,
                identifier=self.client_id,
                transport="websockets",
                websocket_path=path,
                tls_context=self._ssl_context,
                keepalive=60,
                clean_session=True,
            ) as client:
                self._client = client
                for topic in topics:
                    await client.subscribe(topic, qos=1)
                _LOGGER.debug("MQTT connected, subscribed to %s", topics)
                self._connected.set()
                async for message in client.messages:
                    self._handle(str(message.topic), message.payload)
        except aiomqtt.MqttError as err:
            error = err
            _LOGGER.debug("MQTT connection lost: %s", err)
            if not self._connected.is_set():
                raise
        finally:
            self._client = None
            was_connected = self._connected.is_set()
            self._connected.clear()
            for _, future in self._pending.values():
                if not future.done():
                    future.set_exception(NavienConnectionError("MQTT disconnected"))
            self._pending.clear()
            if was_connected:
                self._on_disconnect(error)

    def _handle(self, topic: str, payload: Any) -> None:
        try:
            data = json.loads(payload)
        except TypeError, ValueError:
            data = None
        if not isinstance(data, dict):
            _LOGGER.debug("Ignoring message that isn't a JSON object on %s", topic)
            return
        _LOGGER.debug("MQTT <- %s %s", topic, data)
        answered = False
        if (pending := self._pending.get(str(data.get("sessionID")))) is not None:
            reply_topics, future = pending
            if topic in reply_topics and not future.done():
                future.set_result(data)
                answered = True
        try:
            self._on_message(topic, data, answered)
        except Exception:
            # One message the listeners can't handle mustn't end the connection.
            _LOGGER.exception("Error handling MQTT message on %s", topic)

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        """Publish one JSON message."""
        if not self._client or not self.connected:
            raise NavienConnectionError("MQTT not connected")
        _LOGGER.debug("MQTT -> %s %s", topic, payload)
        await self._client.publish(
            topic, json.dumps(payload, separators=(",", ":")), qos=1
        )

    async def request(
        self,
        topic: str,
        payload: dict[str, Any],
        reply_topics: Iterable[str],
        timeout: float,
    ) -> dict[str, Any]:
        """Publish and wait for the first reply with the same sessionID on one of `reply_topics`.

        One request can produce several replies with the same sessionID (`st/start` answers on both
        `res/did` and `res`), so the topic is matched too.
        """
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        session = payload["sessionID"]
        self._pending[session] = (frozenset(reply_topics), future)
        try:
            await self.publish(topic, payload)
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as err:
            raise NavienTimeoutError(f"No reply to {topic}") from err
        finally:
            self._pending.pop(session, None)

    async def disconnect(self) -> None:
        """Close the connection."""
        if self._task and not self._task.done():
            self._task.cancel()
            # The task ends by being cancelled, or with the connection error that stopped it.
            with contextlib.suppress(asyncio.CancelledError, aiomqtt.MqttError):
                await self._task
        self._task = None
