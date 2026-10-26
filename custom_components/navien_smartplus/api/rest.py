"""REST part of the Navien Smart Plus cloud API (account, tokens, device list)."""

import logging
from typing import Any

import aiohttp

from .const import (
    API_BASE_URL,
    PATH_DEVICES,
    PATH_REFRESH_SESSION,
    PATH_REFRESH_TOKEN,
    PATH_SIGN_IN,
    USER_AGENT,
)
from .exceptions import NavienApiError, NavienAuthError, NavienConnectionError
from .models import Account, AuthInfo, Device, Session

_LOGGER = logging.getLogger(__name__)

# `code` values from sign-in that mean the credentials were rejected.
_AUTH_FAIL_CODES = {400, 401, 502, 508, 512}


class NavienRestClient:
    """Thin async wrapper around the REST endpoints."""

    def __init__(
        self, session: aiohttp.ClientSession, base_url: str = API_BASE_URL
    ) -> None:
        """Use `session` for all requests."""
        self._session = session
        self._base_url = base_url

    async def _request(
        self,
        method: str,
        path: str,
        *,
        access_token: str | None = None,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": USER_AGENT,
        }
        if access_token:
            headers["Authorization"] = access_token
        try:
            async with self._session.request(
                method,
                self._base_url + path,
                headers=headers,
                json=json,
                params=params,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                try:
                    body = await resp.json(content_type=None)
                except ValueError as err:
                    raise NavienApiError(
                        f"Non-JSON response from {path} (HTTP {resp.status})"
                    ) from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise NavienConnectionError(f"{method} {path} failed: {err}") from err

        if not isinstance(body, dict):
            raise NavienApiError(
                f"Unexpected response from {path} (HTTP {resp.status})"
            )
        # The cloud's own result code. A gateway error page has none, and its HTTP
        # status mustn't be mistaken for one (502 from sign-in means bad credentials).
        code = body.get("code")
        if (resp.status if code is None else code) == 200:
            return body.get("data") or {}
        message = body.get("msg") or body.get("message") or "no message"
        if resp.status == 401 or code == 401:
            raise NavienAuthError(
                f"{path}: {message} (code {code}, HTTP {resp.status})"
            )
        raise NavienApiError(
            f"{path}: {message} (code {code}, HTTP {resp.status})", code
        )

    async def sign_in(self, email: str, password: str) -> Session:
        """Sign in with the app login."""
        try:
            data = await self._request(
                "POST", PATH_SIGN_IN, json={"email": email, "password": password}
            )
        except NavienApiError as err:
            if err.code in _AUTH_FAIL_CODES:
                raise NavienAuthError(str(err)) from err
            raise
        account = Account(
            email=email,
            user_seq=int(data.get("userSeq", 0)),
            family_seq=int(data.get("familySeq", 0)),
        )
        return Session(
            account=account, auth=AuthInfo.from_json(data.get("authInfo") or {})
        )

    async def refresh_access_token(self, previous: AuthInfo) -> AuthInfo:
        """Exchange the refresh token for a new access token."""
        data = await self._request(
            "POST", PATH_REFRESH_TOKEN, json={"refreshToken": previous.refresh_token}
        )
        if not data.get("accessToken"):
            raise NavienAuthError("Refresh returned no access token")
        return AuthInfo.from_json(data, previous=previous)

    async def refresh_session(
        self, access_token: str, email: str, previous: AuthInfo
    ) -> AuthInfo:
        """Get fresh AWS credentials for MQTT."""
        data = await self._request(
            "POST",
            PATH_REFRESH_SESSION,
            access_token=access_token,
            json={"email": email},
        )
        return AuthInfo.from_json(data, previous=previous)

    async def get_devices(self, access_token: str, account: Account) -> list[Device]:
        """List the account's Smart Plus modules."""
        data = await self._request(
            "GET",
            PATH_DEVICES,
            access_token=access_token,
            params={"userSeq": account.user_seq, "familySeq": account.family_seq},
        )
        return [Device.from_json(d) for d in data.get("devices") or []]
