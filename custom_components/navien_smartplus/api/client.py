"""High-level client for the Navien Smart Plus (UK) cloud."""

import asyncio
from collections.abc import Callable
import logging
import ssl
import time
from typing import TYPE_CHECKING, Any
import uuid

import aiohttp

from .const import (
    MQTT_NSP,
    MQTT_PROTOCOL_VERSION,
    ZONE_MASK,
    Command,
    ControlMode,
    HeatingMode,
    HotWaterMode,
)
from .exceptions import NavienConnectionError, NavienError
from .models import (
    Account,
    AuthInfo,
    Device,
    DeviceFeatures,
    DeviceStatus,
    HeatingSchedule,
    Session,
    UsageRecord,
)
from .mqtt import NavienMqtt
from .rest import NavienRestClient

_LOGGER = logging.getLogger(__name__)

StatusListener = Callable[[Device, DeviceStatus], None]
ConnectionListener = Callable[[bool], None]
RawListener = Callable[[str, dict[str, Any]], None]

# Refresh tokens this long before they expire.
_REFRESH_MARGIN = 300
REQUEST_TIMEOUT = 40


class NavienSmartPlusClient:
    """Sign in, list devices, read status and send control commands."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        *,
        client_id: str | None = None,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        """Set up the client; nothing is sent until the first call."""
        self._rest = NavienRestClient(session)
        self._email = email
        self._password = password
        self._client_id = client_id or str(uuid.uuid4())
        self._ssl_context = ssl_context
        self.session: Session | None = None
        self.devices: list[Device] = []
        self._mqtt: NavienMqtt | None = None
        self._closed = False
        self._status_listeners: list[StatusListener] = []
        self._connection_listeners: list[ConnectionListener] = []
        self._raw_listeners: list[RawListener] = []
        self._lock = asyncio.Lock()
        self._request_locks: dict[str, asyncio.Lock] = {}

    @property
    def connected(self) -> bool:
        """Whether the MQTT connection is up."""
        return bool(self._mqtt and self._mqtt.connected)

    async def login(self) -> Account:
        """Sign in with the app login and return the account."""
        return (await self._sign_in()).account

    async def _sign_in(self) -> Session:
        self.session = await self._rest.sign_in(self._email, self._password)
        account = self.session.account
        _LOGGER.debug(
            "Signed in as userSeq=%s familySeq=%s", account.user_seq, account.family_seq
        )
        return self.session

    async def _valid_session(self) -> Session:
        """Sign in if needed, and refresh an access token that is about to expire."""
        session = self.session or await self._sign_in()
        auth = session.auth
        if time.time() < auth.access_token_expires_at - _REFRESH_MARGIN:
            return session
        if auth.refresh_token:
            try:
                auth.access_token = await self._rest.refresh_access_token(
                    auth.refresh_token
                )
            except NavienError:
                _LOGGER.debug("Token refresh failed, signing in again")
            else:
                auth.obtained_at = time.time()
                return session
        return await self._sign_in()

    async def _fresh_aws_credentials(self) -> AuthInfo:
        session = await self._valid_session()
        if time.time() < session.auth.aws_credentials_expire_at - _REFRESH_MARGIN:
            return session.auth
        try:
            session.auth = await self._rest.refresh_session(
                session.auth.access_token, session.account.email, session.auth
            )
        except NavienError:
            _LOGGER.debug("Session refresh failed, signing in again")
            session = await self._sign_in()
        return session.auth

    async def async_get_devices(self) -> list[Device]:
        """List the account's Smart Plus modules."""
        session = await self._valid_session()
        self.devices = await self._rest.get_devices(
            session.auth.access_token, session.account
        )
        return self.devices

    def _subscriptions(self, account: Account) -> list[str]:
        topics: list[str] = []
        for device in self.devices:
            t, m, f = device.device_type, MQTT_NSP + device.mac, account.family_seq
            topics += [
                f"cmd/{t}/{f}/+/+/res",
                f"cmd/{t}/{f}/+/+/res/did",
                f"cmd/{t}/{f}/+/+/res/end",
                f"cmd/{t}/{f}/+/+/res/heat-reserv/rd",
                f"cmd/{t}/{f}/+/+/res/fuel-usage-daily-query/rd",
                f"cmd/{t}/{f}/+/+/res/fuel-usage-monthly-query/rd",
                f"cmd/{t}/{m}/res",
                f"cmd/{t}/{m}/res/did",
                f"cmd/{t}/{m}/res/end",
                f"cmd/{t}/{m}/res/ctrl-fail",
                f"evt/{t}/{m}/st/connection",
            ]
        return topics

    async def async_connect(self) -> None:
        """Connect MQTT (signing in and listing devices first if needed)."""
        async with self._lock:
            if self._closed:
                raise NavienConnectionError("Client is closed")
            if self.connected:
                return
            if not self.devices:
                await self.async_get_devices()
            auth = await self._fresh_aws_credentials()
            if self._ssl_context is None:
                self._ssl_context = await asyncio.get_running_loop().run_in_executor(
                    None, ssl.create_default_context
                )
            self._mqtt = NavienMqtt(
                self._client_id,
                self._on_message,
                self._on_disconnect,
                self._ssl_context,
            )
            if TYPE_CHECKING:
                assert self.session is not None
            await self._mqtt.connect(auth, self._subscriptions(self.session.account))
        for listener in self._connection_listeners:
            listener(True)

    def _on_disconnect(self, error: Exception | None) -> None:
        if self._closed:
            return
        _LOGGER.debug("MQTT disconnected, reconnecting on the next request: %s", error)
        for listener in self._connection_listeners:
            listener(False)

    async def async_close(self) -> None:
        """Disconnect MQTT for good; the client won't reconnect after this."""
        self._closed = True
        if self._mqtt:
            await self._mqtt.disconnect()
            self._mqtt = None

    def add_status_listener(self, listener: StatusListener) -> Callable[[], None]:
        """Call `listener` with every status pushed for another client; returns a remover."""
        self._status_listeners.append(listener)
        return lambda: self._status_listeners.remove(listener)

    def add_connection_listener(
        self, listener: ConnectionListener
    ) -> Callable[[], None]:
        """Call `listener` when MQTT connects or drops; returns a remover."""
        self._connection_listeners.append(listener)
        return lambda: self._connection_listeners.remove(listener)

    def _device_for_mac(self, mac: str | None) -> Device | None:
        mac = (mac or "").lower()
        return next((d for d in self.devices if d.mac == mac), None)

    def add_raw_listener(self, listener: RawListener) -> Callable[[], None]:
        """Every decoded MQTT message, for debugging and captures."""
        self._raw_listeners.append(listener)
        return lambda: self._raw_listeners.remove(listener)

    def _on_message(self, topic: str, data: dict[str, Any], answered: bool) -> None:
        for raw_listener in list(self._raw_listeners):
            raw_listener(topic, data)
        if answered:
            # A reply to one of our own requests is handled by its caller.
            return
        response = data.get("response")
        if not isinstance(response, dict) or not isinstance(
            response.get("status"), dict
        ):
            return
        if (device := self._device_for_mac(response.get("macAddress"))) is None:
            return
        status = DeviceStatus.from_json(response["status"])
        for listener in list(self._status_listeners):
            listener(device, status)

    def _envelope(
        self,
        device: Device,
        request_suffix: str,
        response_suffix: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the request envelope; `requestTopic` is where it's published."""
        if TYPE_CHECKING:
            assert self.session is not None
            assert self._mqtt is not None
        account = self.session.account
        t = device.device_type
        request_topic = f"cmd/{t}/{MQTT_NSP}{device.mac}{request_suffix}"
        response_topic = f"cmd/{t}/{account.family_seq}/{account.user_seq}/{self._client_id}{response_suffix}"
        payload = {
            "protocolVersion": MQTT_PROTOCOL_VERSION,
            "clientID": self._client_id,
            "sessionID": self._mqtt.new_session_id(),
            "requestTopic": request_topic,
            "responseTopic": response_topic,
            "request": {
                "deviceType": t,
                "macAddress": device.mac,
                "additionalValue": device.additional_value,
                **request,
            },
        }
        return payload

    async def _request(
        self,
        device: Device,
        request_suffix: str,
        response_suffix: str,
        request: dict[str, Any],
        *,
        control: bool = False,
    ) -> dict[str, Any]:
        """Send one request and return the reply's `response` object."""
        if not self.connected:
            await self.async_connect()
        # Each module handles one request at a time and can take 20+ s when busy, so never overlap its requests.
        async with self._request_locks.setdefault(device.device_id, asyncio.Lock()):
            payload = self._envelope(device, request_suffix, response_suffix, request)
            reply_topics = [payload["responseTopic"]]
            if control:
                reply_topics.append(
                    f"cmd/{device.device_type}/{MQTT_NSP}{device.mac}/res/ctrl-fail"
                )
            if TYPE_CHECKING:
                assert self._mqtt is not None
            reply = await self._mqtt.request(
                payload["requestTopic"], payload, reply_topics, REQUEST_TIMEOUT
            )
        response = reply.get("response")
        if not isinstance(response, dict):
            return {}
        if "failCode" in response:
            raise NavienError(f"Command rejected (failCode {response['failCode']})")
        return response

    async def async_get_features(self, device: Device) -> DeviceFeatures:
        """Read the module's capabilities and software versions."""
        response = await self._request(
            device,
            "/st/start",
            "/res/did",
            {"command": Command.STATUS_START, "zoneUseSetting": ZONE_MASK[1]},
        )
        return DeviceFeatures(raw=response.get("feature") or {})

    async def async_get_status(self, device: Device, zone_id: int = 1) -> DeviceStatus:
        """Read the status of the boiler, hot water and all zones."""
        response = await self._request(
            device,
            "/st",
            "/res",
            {
                "command": Command.STATUS_GET,
                "zoneUseSetting": ZONE_MASK.get(zone_id, 1),
            },
        )
        if not isinstance(status := response.get("status"), dict):
            raise NavienConnectionError("Status reply had no status")
        return DeviceStatus.from_json(status)

    async def async_get_heating_schedules(
        self, device: Device
    ) -> dict[int, HeatingSchedule]:
        """Weekly heating schedule of every zone, keyed by zone id."""
        response = await self._request(
            device,
            "/st/heat-reserv-query/rd",
            "/res/heat-reserv/rd",
            {"command": Command.STATUS_GET_INSIDE_RSV, "zoneUseSetting": ZONE_MASK[1]},
        )
        schedules = [
            HeatingSchedule.from_json(z)
            for z in response.get("heatingReservation") or []
        ]
        return {s.zone_id: s for s in schedules}

    async def async_get_daily_usage(
        self, device: Device, year: int, month: int
    ) -> list[UsageRecord]:
        """One record per day of the month. Days not finished yet read 0."""
        response = await self._request(
            device,
            "/st/fuel-usage-daily-query/rd",
            "/res/fuel-usage-daily-query/rd",
            {"command": Command.STATUS_DAILY_USAGE, "year": year, "month": month},
        )
        return [UsageRecord.from_json(r) for r in response.get("usage") or []]

    async def async_get_monthly_usage(
        self, device: Device, year: int
    ) -> list[UsageRecord]:
        """One record per month of the year (monthly rows carry no `day`)."""
        response = await self._request(
            device,
            "/st/fuel-usage-monthly-query/rd",
            "/res/fuel-usage-monthly-query/rd",
            {"command": Command.STATUS_MONTHLY_USAGE, "year": year},
        )
        return [UsageRecord.from_json(r) for r in response.get("usage") or []]

    async def _control(
        self,
        device: Device,
        zone_id: int,
        command: Command,
        mode: str,
        params: list[int],
    ) -> DeviceStatus | None:
        response = await self._request(
            device,
            "/ctrl",
            "/res",
            {
                "command": command,
                "mode": mode,
                "zoneUseSetting": ZONE_MASK.get(zone_id, 1),
                "param": params,
                "paramStr": "",
            },
            control=True,
        )
        status = response.get("status")
        return DeviceStatus.from_json(status) if isinstance(status, dict) else None

    async def async_set_zone_mode(
        self, device: Device, zone_id: int, mode: HeatingMode, temperature: float
    ) -> DeviceStatus | None:
        """Set a zone's heating mode; the setpoint is sent with every mode except Auto."""
        params = (
            [int(mode)]
            if mode == HeatingMode.AUTO
            else [int(mode), round(temperature * 2)]
        )
        return await self._control(
            device, zone_id, Command.CTRL_HEAT_INSIDE, ControlMode.HEAT_MODE, params
        )

    async def async_set_zone_temperature(
        self,
        device: Device,
        zone_id: int,
        temperature: float,
        current_mode: HeatingMode | int,
    ) -> DeviceStatus | None:
        """Change the setpoint the way the app does for the zone's current mode."""
        if current_mode == HeatingMode.AUTO:
            # This starts an override until the next program slot.
            return await self.async_set_zone_mode(
                device, zone_id, HeatingMode.OVERRIDE, temperature
            )
        if current_mode == HeatingMode.BOOST:
            # This leaves boost for manual heating.
            return await self.async_set_zone_mode(
                device, zone_id, HeatingMode.ON, temperature
            )
        return await self._control(
            device,
            zone_id,
            Command.CTRL_SET_INSIDE_TEMP,
            ControlMode.INSIDE_HEAT,
            [round(temperature * 2)],
        )

    async def async_set_hot_water_mode(
        self, device: Device, mode: HotWaterMode
    ) -> DeviceStatus | None:
        """Set the hot water mode."""
        return await self._control(
            device, 1, Command.CTRL_HOT_WATER, ControlMode.DHW_MODE, [int(mode)]
        )

    async def async_set_holiday(
        self, device: Device, zone_id: int, enabled: bool
    ) -> DeviceStatus | None:
        """Turn holiday mode on or off for a zone."""
        if enabled:
            return await self._control(
                device, zone_id, Command.CTRL_HOLIDAY_ON, ControlMode.HOLIDAY_ON, [2]
            )
        return await self._control(
            device, zone_id, Command.CTRL_HOLIDAY_OFF, ControlMode.HOLIDAY_OFF, [1]
        )
