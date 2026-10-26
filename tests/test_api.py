"""Tests for the HA-free protocol client: parsing, schedule maths, signing and request shapes."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.navien_smartplus.api import (
    HeatingMode,
    HotWaterMode,
    NavienSmartPlusClient,
    UsageRecord,
)
from custom_components.navien_smartplus.api.models import Account, AuthInfo, Session
from custom_components.navien_smartplus.api.sigv4 import presign_mqtt_path

from .conftest import (
    fixture_devices,
    fixture_features,
    fixture_schedules,
    fixture_status,
    load_fixture,
)


def test_status_parsing() -> None:
    """Status replies parse into the expected values and units."""
    status = fixture_status()
    assert status.outside_temperature == 9.2
    assert status.burner_active is False
    assert status.dhw_mode == HotWaterMode.AUTO
    assert status.error_code == 0
    zone = status.zones[1]
    assert zone.mode == HeatingMode.OVERRIDE
    assert zone.current_temperature == 19.5
    assert zone.setting_temperature == 19.0
    assert zone.program_type == 1


def test_features_and_devices() -> None:
    """Device-info and device-list replies parse correctly."""
    features = fixture_features()
    assert features.supported("dhwUse")
    assert features.supported("fuelUsageUse")
    assert not features.supported("intelligentUse")
    assert (features.min_zone_temperature, features.max_zone_temperature) == (
        10.0,
        40.0,
    )
    device = fixture_devices()[0]
    assert (device.mac, device.additional_value, device.device_type) == (
        "001122334455",
        "abcd",
        3,
    )
    assert [z.name for z in device.zones] == ["Home"]


@pytest.mark.parametrize(
    ("when", "program", "expected"),
    [
        ("2026-10-25T20:05", 5, 16.0),  # Sunday 20:00 slot; the app showed 16 °C
        ("2026-10-27T20:05", 5, 18.0),  # Tuesday 20:00 slot
        ("2026-10-27T05:45", 1, 18.0),  # Tuesday 05:30 slot
        ("2026-10-27T10:00", 2, 17.0),  # Tuesday 09:30 slot
        (
            "2026-10-27T03:00",
            0,
            16.0,
        ),  # before Tuesday's first slot: Monday's last slot
    ],
)
def test_schedule_target(when: str, program: int, expected: float) -> None:
    """The Auto target comes from the right day and slot of the schedule."""
    schedule = fixture_schedules()[1]
    assert (
        schedule.scheduled_temperature(1, program, datetime.fromisoformat(when))
        == expected
    )


def test_auto_target_uses_schedule_not_setting() -> None:
    """In Auto the target comes from the schedule, otherwise from the setting."""
    zone = fixture_status().zones[1]
    zone.mode = HeatingMode.AUTO
    zone.setting_temperature = 19.5
    zone.program_number = 5
    now = datetime.fromisoformat("2026-10-25T20:05")
    assert zone.target_temperature(fixture_schedules()[1], now) == 16.0
    assert zone.target_temperature(None, now) == 19.5
    zone.mode = HeatingMode.OVERRIDE
    assert zone.target_temperature(fixture_schedules()[1], now) == 19.5


def test_usage_parsing() -> None:
    """Daily rows add up to the monthly totals, and monthly rows have no day."""
    from .conftest import fixture_daily_usage, fixture_monthly_usage

    daily = fixture_daily_usage(2026, 10)
    monthly = fixture_monthly_usage(2026)
    assert sum(r.heating_usage for r in daily) == monthly[9].heating_usage == 10
    assert sum(r.hot_water_usage for r in daily) == monthly[9].hot_water_usage == 17
    assert all(r.day is None for r in monthly)
    total = UsageRecord.year_total(2026, monthly)
    assert (total.heating_usage, total.hot_water_usage, total.total_usage) == (
        252,
        202,
        454,
    )


def test_sigv4_presign_is_stable() -> None:
    """Locks in the signer, which was checked against botocore's SigV4QueryAuth with these inputs."""
    path = presign_mqtt_path(
        "spuk-iot.naviensmartcontrol.com",
        "eu-west-2",
        "iotdevicegateway",
        "AKIDEXAMPLE",
        "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        "tok/en+=",
        now=datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC),
    )
    assert path == SIGNED_PATH


SIGNED_PATH = (
    "/mqtt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIDEXAMPLE%2F20261025%2Feu-west-2%2Fiotdevicegateway%2Faws4_request&X-Amz-Date=20261025T120000Z&X-Amz-SignedHeaders=host"
    "&X-Amz-Signature=b4e9e2fd190724108fe22deaa544197670ad53359059eae1c10fc7fa91d2f9cc&X-Amz-Security-Token=tok%2Fen%2B%3D"
)


def _client_with_fake_mqtt(reply: dict) -> tuple[NavienSmartPlusClient, AsyncMock]:
    client = NavienSmartPlusClient(
        MagicMock(), "user@example.com", "pw", client_id="cid"
    )
    client.session = Session(
        account=Account(email="user@example.com", user_seq=1001, family_seq=2002),
        auth=AuthInfo(
            access_token="t",
            refresh_token="r",
            access_key_id="a",
            secret_key="s",
            session_token="x",
        ),
    )
    client.devices = fixture_devices()
    mqtt = MagicMock()
    mqtt.connected = True
    mqtt.new_session_id = MagicMock(return_value="123")
    mqtt.request = AsyncMock(return_value=reply)
    client._mqtt = mqtt
    return client, mqtt.request


async def test_status_request_shape() -> None:
    """A status request is published to the module with the right payload."""
    client, request = _client_with_fake_mqtt(load_fixture("reply_status.json")["data"])
    status = await client.async_get_status(client.devices[0])
    assert status.zones[1].current_temperature == 19.5
    topic, payload, reply_topics, _ = request.call_args.args
    assert topic == "cmd/3/nsp-001122334455/st"
    assert reply_topics == ["cmd/3/2002/1001/cid/res"]
    assert payload["responseTopic"] == "cmd/3/2002/1001/cid/res"
    assert payload["request"] == {
        "deviceType": 3,
        "macAddress": "001122334455",
        "additionalValue": "abcd",
        "command": 0x01000003,
        "zoneUseSetting": 1,
    }


@pytest.mark.parametrize(
    ("mode", "temperature", "expected_command", "expected_mode", "expected_param"),
    [
        (
            HeatingMode.AUTO,
            19.5,
            0x02000001,
            "heat-mode",
            [4, 39],
        ),  # live-tested: Auto -> Override
        (HeatingMode.OVERRIDE, 20.0, 0x02000005, "inside-heat", [40]),
        (HeatingMode.ON, 21.5, 0x02000005, "inside-heat", [43]),
        (HeatingMode.BOOST, 22.0, 0x02000001, "heat-mode", [1, 44]),
    ],
)
async def test_set_temperature_follows_app(
    mode, temperature, expected_command, expected_mode, expected_param
) -> None:
    """Setpoint changes use the same command as the app in each mode."""
    client, request = _client_with_fake_mqtt(load_fixture("reply_status.json")["data"])
    await client.async_set_zone_temperature(client.devices[0], 1, temperature, mode)
    topic, payload, reply_topics, _ = request.call_args.args
    assert topic == "cmd/3/nsp-001122334455/ctrl"
    assert reply_topics == [
        "cmd/3/2002/1001/cid/res",
        "cmd/3/nsp-001122334455/res/ctrl-fail",
    ]
    assert payload["request"]["command"] == expected_command
    assert payload["request"]["mode"] == expected_mode
    assert payload["request"]["param"] == expected_param


async def test_cancel_override_sends_auto_only() -> None:
    """Cancelling an override sends Auto with no setpoint."""
    client, request = _client_with_fake_mqtt(load_fixture("reply_status.json")["data"])
    await client.async_set_zone_mode(client.devices[0], 1, HeatingMode.AUTO, 19.0)
    assert request.call_args.args[1]["request"]["param"] == [0]


async def test_control_failure_raises() -> None:
    """A ctrl-fail reply raises an error."""
    from custom_components.navien_smartplus.api import NavienError

    client, _ = _client_with_fake_mqtt({"response": {"failCode": 3}})
    with pytest.raises(NavienError):
        await client.async_set_hot_water_mode(client.devices[0], HotWaterMode.ON)


async def test_mqtt_reply_matching() -> None:
    """Replies match on sessionID and topic; only answered replies are flagged so they aren't broadcast again."""
    import asyncio
    import json

    from custom_components.navien_smartplus.api.mqtt import NavienMqtt

    seen: list[tuple[str, bool]] = []
    mqtt = NavienMqtt(
        "cid",
        lambda topic, data, answered: seen.append((topic, answered)),
        lambda err: None,
        MagicMock(),
    )
    future = asyncio.get_running_loop().create_future()
    mqtt._pending["123"] = (frozenset({"t/res/did"}), future)

    mqtt._handle(
        "t/res", json.dumps({"sessionID": "123"})
    )  # same session, other topic: st/start's status reply
    assert not future.done()
    mqtt._handle("t/res/did", json.dumps({"sessionID": "123", "response": {}}))
    assert future.result() == {"sessionID": "123", "response": {}}
    assert seen == [("t/res", False), ("t/res/did", True)]
