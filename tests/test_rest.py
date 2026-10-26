"""Tests for the REST client against a mocked Smart Plus cloud."""

import asyncio
from collections.abc import AsyncGenerator
import time
from typing import Any

import aiohttp
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.navien_smartplus.api import NavienSmartPlusClient
from custom_components.navien_smartplus.api.const import (
    API_BASE_URL,
    PATH_DEVICES,
    PATH_REFRESH_SESSION,
    PATH_REFRESH_TOKEN,
    PATH_SIGN_IN,
)
from custom_components.navien_smartplus.api.exceptions import (
    NavienApiError,
    NavienAuthError,
    NavienConnectionError,
)
from custom_components.navien_smartplus.api.models import Account, AuthInfo
from custom_components.navien_smartplus.api.rest import NavienRestClient

from .conftest import load_fixture

SIGN_IN_URL = API_BASE_URL + PATH_SIGN_IN
REFRESH_TOKEN_URL = API_BASE_URL + PATH_REFRESH_TOKEN
REFRESH_SESSION_URL = API_BASE_URL + PATH_REFRESH_SESSION
DEVICES_URL = API_BASE_URL + PATH_DEVICES

ACCOUNT = Account("user@example.com", 1001, 2002)
SIGN_IN_DATA = {
    "userSeq": 1001,
    "familySeq": 2002,
    "authInfo": {
        "accessToken": "access-1",
        "refreshToken": "refresh-1",
        "accessKeyId": "AKIA1",
        "secretKey": "secret-1",
        "sessionToken": "session-1",
        "authenticationExpiresIn": 3600,
        "authorizationExpiresIn": 3600,
    },
}


def ok(data: dict[str, Any]) -> dict[str, Any]:
    """Wrap `data` the way the cloud does."""
    return {"code": 200, "msg": "SUCCESS", "data": data}


def sent(mock: AiohttpClientMocker, url: str) -> list[tuple[Any, ...]]:
    """The (method, url, json, headers) of each request to `url`, ignoring the query."""
    return [call for call in mock.mock_calls if str(call[1]).split("?")[0] == url]


@pytest.fixture
async def http(
    aioclient_mock: AiohttpClientMocker,
) -> AsyncGenerator[aiohttp.ClientSession]:
    """A session whose requests are answered by `aioclient_mock`."""
    session = aioclient_mock.create_session(asyncio.get_running_loop())
    yield session
    await session.close()


async def test_sign_in(
    http: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker
) -> None:
    """Sign-in sends the app login and parses the account and tokens."""
    aioclient_mock.post(SIGN_IN_URL, json=ok(SIGN_IN_DATA))
    session = await NavienRestClient(http).sign_in("user@example.com", "pw")

    [(_, _, body, headers)] = sent(aioclient_mock, SIGN_IN_URL)
    assert body == {"email": "user@example.com", "password": "pw"}
    assert "Authorization" not in headers
    assert session.account == ACCOUNT
    assert session.auth.access_token == "access-1"
    assert session.auth.refresh_token == "refresh-1"
    assert session.auth.session_token == "session-1"


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (200, {"code": 401, "msg": "Unauthorized"}),
        (200, {"code": 512, "msg": "Password mismatch"}),
        (200, {"code": 400, "msg": "Bad request"}),
        (401, {"msg": "Unauthorized"}),
    ],
)
async def test_sign_in_rejected(
    http: aiohttp.ClientSession,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    body: dict[str, Any],
) -> None:
    """Codes that mean wrong credentials raise an auth error, so HA asks for reauth."""
    aioclient_mock.post(SIGN_IN_URL, status=status, json=body)
    with pytest.raises(NavienAuthError):
        await NavienRestClient(http).sign_in("user@example.com", "wrong")


async def test_api_error_keeps_code(
    http: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker
) -> None:
    """Other error codes raise an API error carrying the cloud's code and message."""
    aioclient_mock.post(SIGN_IN_URL, json={"code": 500, "msg": "Server busy"})
    with pytest.raises(NavienApiError, match="Server busy") as err:
        await NavienRestClient(http).sign_in("user@example.com", "pw")
    assert err.value.code == 500
    assert not isinstance(err.value, NavienAuthError)


@pytest.mark.parametrize(
    "reply",
    [
        {"text": "<html>Bad gateway</html>"},
        {"json": {"message": "Internal server error"}},
    ],
)
async def test_gateway_error_is_not_bad_password(
    http: aiohttp.ClientSession,
    aioclient_mock: AiohttpClientMocker,
    reply: dict[str, Any],
) -> None:
    """An HTTP 502 from a gateway isn't the cloud's 502 code for bad credentials."""
    aioclient_mock.post(SIGN_IN_URL, status=502, **reply)
    with pytest.raises(NavienApiError, match="HTTP 502") as err:
        await NavienRestClient(http).sign_in("user@example.com", "pw")
    assert err.value.code is None
    assert not isinstance(err.value, NavienAuthError)


@pytest.mark.parametrize(
    "exception", [aiohttp.ClientConnectionError("refused"), TimeoutError()]
)
async def test_connection_errors(
    http: aiohttp.ClientSession,
    aioclient_mock: AiohttpClientMocker,
    exception: Exception,
) -> None:
    """Network failures and timeouts raise a connection error, so HA retries."""
    aioclient_mock.post(SIGN_IN_URL, exc=exception)
    with pytest.raises(NavienConnectionError):
        await NavienRestClient(http).sign_in("user@example.com", "pw")


async def test_get_devices(
    http: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker
) -> None:
    """The device list uses the raw access token and the account's sequence numbers."""
    aioclient_mock.get(DEVICES_URL, json=load_fixture("devices.json"))
    devices = await NavienRestClient(http).get_devices("access-1", ACCOUNT)

    [(_, url, _, headers)] = sent(aioclient_mock, DEVICES_URL)
    assert headers["Authorization"] == "access-1"
    assert dict(url.query) == {"userSeq": "1001", "familySeq": "2002"}
    assert [(d.mac, d.additional_value) for d in devices] == [("001122334455", "abcd")]


async def test_get_devices_empty(
    http: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker
) -> None:
    """An account with no modules gives an empty list."""
    aioclient_mock.get(DEVICES_URL, json=ok({"devices": None}))
    assert await NavienRestClient(http).get_devices("access-1", ACCOUNT) == []


async def test_refresh_access_token(
    http: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker
) -> None:
    """Token refresh sends the refresh token and returns the new access token."""
    aioclient_mock.post(REFRESH_TOKEN_URL, json=ok({"accessToken": "access-2"}))
    assert await NavienRestClient(http).refresh_access_token("refresh-1") == "access-2"

    [(_, _, body, headers)] = sent(aioclient_mock, REFRESH_TOKEN_URL)
    assert body == {"refreshToken": "refresh-1"}
    assert "Authorization" not in headers


async def test_refresh_without_token(
    http: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker
) -> None:
    """A refresh reply with no access token is treated as an auth failure."""
    aioclient_mock.post(REFRESH_TOKEN_URL, json=ok({}))
    with pytest.raises(NavienAuthError):
        await NavienRestClient(http).refresh_access_token("refresh-1")


async def test_refresh_session_keeps_tokens(
    http: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker
) -> None:
    """New AWS credentials keep the tokens the reply leaves out."""
    aioclient_mock.post(
        REFRESH_SESSION_URL,
        json=ok(
            {"accessKeyId": "AKIA2", "secretKey": "secret-2", "sessionToken": "s2"}
        ),
    )
    auth = await NavienRestClient(http).refresh_session(
        "access-1", "user@example.com", AuthInfo.from_json(SIGN_IN_DATA["authInfo"])
    )

    [(_, _, body, headers)] = sent(aioclient_mock, REFRESH_SESSION_URL)
    assert headers["Authorization"] == "access-1"
    assert body == {"email": "user@example.com"}
    assert (auth.access_key_id, auth.session_token) == ("AKIA2", "s2")
    assert (auth.access_token, auth.refresh_token) == ("access-1", "refresh-1")


async def test_client_refreshes_expiring_token(
    http: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker
) -> None:
    """An access token near expiry is refreshed instead of signing in again."""
    aioclient_mock.post(SIGN_IN_URL, json=ok(SIGN_IN_DATA))
    aioclient_mock.post(REFRESH_TOKEN_URL, json=ok({"accessToken": "access-2"}))
    aioclient_mock.get(DEVICES_URL, json=load_fixture("devices.json"))
    client = NavienSmartPlusClient(http, "user@example.com", "pw")

    await client.async_get_devices()
    client.session.auth.obtained_at = time.time() - 3500
    await client.async_get_devices()

    assert len(sent(aioclient_mock, SIGN_IN_URL)) == 1
    assert [h["Authorization"] for *_, h in sent(aioclient_mock, DEVICES_URL)] == [
        "access-1",
        "access-2",
    ]


async def test_client_signs_in_when_refresh_fails(
    http: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker
) -> None:
    """A rejected refresh token falls back to signing in with the password."""
    aioclient_mock.post(SIGN_IN_URL, json=ok(SIGN_IN_DATA))
    aioclient_mock.post(REFRESH_TOKEN_URL, json={"code": 401, "msg": "Expired"})
    aioclient_mock.get(DEVICES_URL, json=load_fixture("devices.json"))
    client = NavienSmartPlusClient(http, "user@example.com", "pw")

    await client.async_get_devices()
    client.session.auth.obtained_at = time.time() - 3500
    await client.async_get_devices()

    assert len(sent(aioclient_mock, REFRESH_TOKEN_URL)) == 1
    assert len(sent(aioclient_mock, SIGN_IN_URL)) == 2


async def test_client_password_rejected(
    http: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker
) -> None:
    """A changed password surfaces as an auth error from the client."""
    aioclient_mock.post(SIGN_IN_URL, json={"code": 512, "msg": "Password mismatch"})
    with pytest.raises(NavienAuthError):
        await NavienSmartPlusClient(http, "user@example.com", "old").async_get_devices()
