"""Tests for the client's MQTT side against a fake AWS IoT broker."""

import asyncio
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import aiomqtt
import pytest

from custom_components.navien_smartplus.api import (
    DeviceStatus,
    HeatingMode,
    HotWaterMode,
    NavienConnectionError,
    NavienError,
    NavienSmartPlusClient,
)
from custom_components.navien_smartplus.api.exceptions import NavienTimeoutError
from custom_components.navien_smartplus.api.models import Account, AuthInfo, Session
from custom_components.navien_smartplus.api.mqtt import NavienMqtt

from .conftest import fixture_devices, load_fixture
from .fake_mqtt import FakeBroker

MODULE = "cmd/3/nsp-001122334455"
REPLY = "cmd/3/2002/1001/cid"
AUTH = {
    "accessToken": "access-1",
    "refreshToken": "refresh-1",
    "accessKeyId": "AKIA1",
    "secretKey": "secret-1",
    "sessionToken": "session-1",
}


def status_response() -> dict[str, Any]:
    """The `response` of a captured status reply."""
    return load_fixture("reply_status.json")["data"]["response"]


def app_replies(topic: str, payload: dict[str, Any]) -> list[tuple[str, dict]]:
    """Answer each request the way the module does, from captured replies."""
    usage = load_fixture("usage.json")
    responses = {
        f"{MODULE}/st": status_response(),
        f"{MODULE}/st/start": load_fixture("reply_did.json")["data"]["response"],
        f"{MODULE}/st/heat-reserv-query/rd": load_fixture("reply_heat_schedule.json")[
            "response"
        ],
        f"{MODULE}/st/fuel-usage-daily-query/rd": {"usage": usage["daily_2026_10"]},
        f"{MODULE}/st/fuel-usage-monthly-query/rd": {"usage": usage["monthly_2026"]},
        f"{MODULE}/ctrl": status_response(),
    }
    replies = [(payload["responseTopic"], reply(payload, responses[topic]))]
    if topic.endswith("/st/start"):
        # st/start also answers with a status on res, under the same sessionID.
        replies.append((f"{REPLY}/res", reply(payload, status_response())))
    return replies


def reply(payload: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    """Wrap `response` as a reply to the request `payload`."""
    return {
        "protocolVersion": 1,
        "clientID": payload["clientID"],
        "sessionID": payload["sessionID"],
        "requestTopic": payload["requestTopic"],
        "response": response,
    }


async def settle() -> None:
    """Let the MQTT task handle queued messages."""
    for _ in range(10):
        await asyncio.sleep(0)


@pytest.fixture
def broker() -> Generator[FakeBroker]:
    """A fake AWS IoT broker that answers like the module."""
    broker = FakeBroker()
    broker.responder = app_replies
    with patch("aiomqtt.Client", broker.Client):
        yield broker


@pytest.fixture
async def client(broker: FakeBroker) -> NavienSmartPlusClient:
    """A signed-in client with the captured device list."""
    client = NavienSmartPlusClient(
        MagicMock(), "user@example.com", "pw", client_id="cid", ssl_context=MagicMock()
    )
    client.session = Session(
        Account("user@example.com", 1001, 2002), AuthInfo.from_json(AUTH)
    )
    client.devices = fixture_devices()
    yield client
    await client.async_close()


async def test_connect_signs_and_subscribes(
    client: NavienSmartPlusClient, broker: FakeBroker
) -> None:
    """The connection uses a SigV4-signed WebSocket and subscribes to every reply topic."""
    connections: list[bool] = []
    client.add_connection_listener(connections.append)
    await client.async_connect()
    await client.async_connect()

    assert len(broker.clients) == 1
    settings = broker.client.kwargs
    assert settings["hostname"] == "spuk-iot.naviensmartcontrol.com"
    assert (settings["port"], settings["transport"]) == (443, "websockets")
    assert settings["identifier"] == "cid"
    assert settings["websocket_path"].startswith(
        "/mqtt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIA1%2F"
    )
    assert settings["websocket_path"].endswith("&X-Amz-Security-Token=session-1")
    assert {
        "cmd/3/2002/+/+/res",
        "cmd/3/2002/+/+/res/did",
        "cmd/3/2002/+/+/res/fuel-usage-daily-query/rd",
        f"{MODULE}/res",
        f"{MODULE}/res/ctrl-fail",
        "evt/3/nsp-001122334455/st/connection",
    } <= set(broker.client.subscriptions)
    assert client.connected
    assert connections == [True]


async def test_reads(client: NavienSmartPlusClient, broker: FakeBroker) -> None:
    """Each read goes to its own topic and parses the module's reply."""
    device = client.devices[0]

    features = await client.async_get_features(device)
    status = await client.async_get_status(device)
    schedules = await client.async_get_heating_schedules(device)
    daily = await client.async_get_daily_usage(device, 2026, 10)
    monthly = await client.async_get_monthly_usage(device, 2026)

    assert [topic for topic, _ in broker.published] == [
        f"{MODULE}/st/start",
        f"{MODULE}/st",
        f"{MODULE}/st/heat-reserv-query/rd",
        f"{MODULE}/st/fuel-usage-daily-query/rd",
        f"{MODULE}/st/fuel-usage-monthly-query/rd",
    ]
    assert features.supported("dhwUse")
    assert status.zones[1].current_temperature == 19.5
    assert schedules[1].zone_id == 1
    assert (len(daily), daily[1].hot_water_usage) == (31, 1)
    assert (len(monthly), monthly[9].heating_usage) == (12, 10)
    request = broker.published[3][1]["request"]
    assert (request["year"], request["month"]) == (2026, 10)


@pytest.mark.parametrize(
    ("call", "command", "mode", "param"),
    [
        (lambda c, d: c.async_set_holiday(d, 1, True), 0x02000004, "goout-on", [2]),
        (lambda c, d: c.async_set_holiday(d, 1, False), 0x02000003, "goout-off", [1]),
        (
            lambda c, d: c.async_set_hot_water_mode(d, HotWaterMode.OFF),
            0x02000002,
            "dhw-mode",
            [3],
        ),
        (
            lambda c, d: c.async_set_zone_mode(d, 1, HeatingMode.OFF, 19.0),
            0x02000001,
            "heat-mode",
            [2, 38],
        ),
    ],
)
async def test_controls(
    client: NavienSmartPlusClient,
    broker: FakeBroker,
    call: Any,
    command: int,
    mode: str,
    param: list[int],
) -> None:
    """Controls publish the app's command to the module and return the new status."""
    status = await call(client, client.devices[0])

    [(topic, payload)] = broker.published
    assert topic == f"{MODULE}/ctrl"
    assert payload["request"]["command"] == command
    assert payload["request"]["mode"] == mode
    assert payload["request"]["param"] == param
    assert isinstance(status, DeviceStatus)


async def test_control_rejected(
    client: NavienSmartPlusClient, broker: FakeBroker
) -> None:
    """A reply on ctrl-fail raises an error."""
    broker.responder = lambda topic, payload: [
        (
            f"{MODULE}/res/ctrl-fail",
            {"sessionID": payload["sessionID"], "response": {"failCode": 3}},
        )
    ]
    with pytest.raises(NavienError, match="failCode 3"):
        await client.async_set_holiday(client.devices[0], 1, True)


async def test_pushes_reach_listeners(
    client: NavienSmartPlusClient, broker: FakeBroker
) -> None:
    """Unprompted status pushes and connection events reach the listeners; other messages don't."""
    statuses: list[DeviceStatus] = []
    online: list[bool] = []
    raw: list[str] = []
    client.add_status_listener(lambda device, status: statuses.append(status))
    client.add_online_listener(lambda device, state: online.append(state))
    client.add_raw_listener(lambda topic, data: raw.append(topic))
    await client.async_connect()

    broker.push(f"{MODULE}/res", {"sessionID": "999", "response": status_response()})
    broker.push(
        "evt/3/nsp-001122334455/st/connection",
        {"event": {"macAddress": "001122334455", "connection": {"status": 1}}},
    )
    other = {**status_response(), "macAddress": "665544332211"}
    broker.push("cmd/3/nsp-665544332211/res", {"sessionID": "1", "response": other})
    broker.push(f"{MODULE}/res", "not json")
    await settle()

    assert [s.zones[1].current_temperature for s in statuses] == [19.5]
    assert online == [False]
    assert len(raw) == 3


async def test_bad_message_keeps_the_connection(
    client: NavienSmartPlusClient, broker: FakeBroker
) -> None:
    """A message that isn't a JSON object, or that a listener fails on, is skipped without dropping MQTT."""
    statuses: list[DeviceStatus] = []

    def listener(device: Any, status: DeviceStatus) -> None:
        if not statuses:
            statuses.append(status)
            raise ValueError("listener bug")
        statuses.append(status)

    client.add_status_listener(listener)
    await client.async_connect()

    broker.push(f"{MODULE}/res", "[1, 2, 3]")
    broker.push(f"{MODULE}/res", {"sessionID": "998", "response": status_response()})
    broker.push(f"{MODULE}/res", {"sessionID": "999", "response": status_response()})
    await settle()

    assert len(statuses) == 2
    assert client.connected
    assert len(broker.clients) == 1


async def test_request_timeout(
    client: NavienSmartPlusClient, broker: FakeBroker
) -> None:
    """No reply raises a timeout and leaves no request pending."""
    broker.responder = lambda topic, payload: []
    with (
        patch("custom_components.navien_smartplus.api.client.REQUEST_TIMEOUT", 0.05),
        pytest.raises(NavienTimeoutError),
    ):
        await client.async_get_status(client.devices[0])
    assert not client._mqtt._pending


async def test_dropped_connection_fails_request_and_reconnects(
    client: NavienSmartPlusClient, broker: FakeBroker
) -> None:
    """A dropped connection fails the waiting request at once, and the next request reconnects."""
    connections: list[bool] = []
    client.add_connection_listener(connections.append)
    broker.responder = lambda topic, payload: []
    request = asyncio.create_task(client.async_get_status(client.devices[0]))
    await settle()

    broker.drop()
    with pytest.raises(NavienConnectionError) as err:
        await request
    assert not isinstance(err.value, NavienTimeoutError)
    assert not client.connected
    assert connections == [True, False]

    broker.responder = app_replies
    status = await client.async_get_status(client.devices[0])
    assert status.zones[1].current_temperature == 19.5
    assert len(broker.clients) == 2
    assert connections == [True, False, True]


async def test_connect_refused(
    client: NavienSmartPlusClient, broker: FakeBroker
) -> None:
    """A refused connection raises a connection error without reporting a disconnect."""
    connections: list[bool] = []
    client.add_connection_listener(connections.append)
    broker.connect_error = aiomqtt.MqttError("Not authorized")
    with pytest.raises(NavienConnectionError, match="Not authorized"):
        await client.async_connect()
    assert connections == []


async def test_connect_timeout(broker: FakeBroker) -> None:
    """A connection that never completes times out and is cleaned up."""
    broker.hang = True
    mqtt = NavienMqtt("cid", MagicMock(), MagicMock(), MagicMock())
    with pytest.raises(NavienConnectionError, match="timed out"):
        await mqtt.connect(AuthInfo.from_json(AUTH), ["t"], timeout=0.05)
    assert mqtt._task is None
    with pytest.raises(NavienConnectionError, match="not connected"):
        await mqtt.publish("t", {})


async def test_close_is_final(
    client: NavienSmartPlusClient, broker: FakeBroker
) -> None:
    """Closing disconnects without reporting a drop, and the client stays closed."""
    connections: list[bool] = []
    client.add_connection_listener(connections.append)
    await client.async_connect()

    await client.async_close()

    assert not client.connected
    assert connections == [True]
    with pytest.raises(NavienConnectionError, match="closed"):
        await client.async_get_status(client.devices[0])
