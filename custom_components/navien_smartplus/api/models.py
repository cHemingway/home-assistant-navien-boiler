"""Data models parsed from Navien Smart Plus REST and MQTT payloads."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import time
from typing import Any

from .const import (
    MAX_ZONE_TEMP,
    MIN_ZONE_TEMP,
    STATE_ON,
    ZONE_MASK,
    HeatingMode,
    HotWaterMode,
)


def _on(value: Any) -> bool:
    return value == STATE_ON


def _enum(enum_cls: type, value: Any) -> Any:
    try:
        return enum_cls(value)
    except ValueError:
        return value


@dataclass
class AuthInfo:
    """Tokens and temporary AWS credentials returned by sign-in."""

    access_token: str
    refresh_token: str | None
    access_key_id: str
    secret_key: str
    session_token: str
    # Unix times. The access token and the AWS credentials are renewed separately.
    access_token_expires_at: float = 0
    aws_credentials_expire_at: float = 0

    @classmethod
    def from_json(
        cls, data: dict[str, Any], previous: AuthInfo | None = None
    ) -> AuthInfo:
        """Parse an `authInfo` object, keeping the previous token or AWS credentials when it leaves them out."""
        auth = cls(
            access_token=data.get("accessToken", ""),
            refresh_token=data.get("refreshToken"),
            access_key_id=data.get("accessKeyId", ""),
            secret_key=data.get("secretKey", ""),
            session_token=data.get("sessionToken", ""),
            access_token_expires_at=_expiry(data.get("authenticationExpiresIn")),
            aws_credentials_expire_at=_expiry(data.get("authorizationExpiresIn")),
        )
        if previous:
            if not auth.access_token:
                auth.access_token = previous.access_token
                auth.access_token_expires_at = previous.access_token_expires_at
            if not auth.access_key_id:
                auth.access_key_id = previous.access_key_id
                auth.secret_key = previous.secret_key
                auth.session_token = previous.session_token
                auth.aws_credentials_expire_at = previous.aws_credentials_expire_at
            auth.refresh_token = auth.refresh_token or previous.refresh_token
        return auth


def _expiry(expires_in: Any) -> float:
    """When a credential issued now runs out; an hour when the reply doesn't say."""
    return time.time() + (int(expires_in or 0) or 3600)


@dataclass
class Account:
    """Signed-in user."""

    email: str
    user_seq: int
    family_seq: int


@dataclass
class Session:
    """A signed-in account with its tokens and temporary AWS credentials."""

    account: Account
    auth: AuthInfo


@dataclass
class ZoneInfo:
    """A thermostat zone as listed by the REST device list."""

    zone_id: int
    name: str


@dataclass
class Device:
    """A BCM-41RW/45RW communication module and its zones."""

    device_seq: int
    device_id: str
    device_type: int
    model_code: int
    is_connected: bool
    zones: list[ZoneInfo]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Device:
        """Parse one entry of the REST device list."""
        return cls(
            device_seq=int(data.get("deviceSeq", 0)),
            device_id=str(data.get("deviceId", "")).lower(),
            device_type=int(data.get("deviceType", 0)),
            model_code=int(data.get("modelCode", 0)),
            is_connected=bool(data.get("isConnected")),
            zones=[
                ZoneInfo(
                    zone_id=int(z.get("zoneId", i + 1)),
                    name=z.get("name") or f"Zone {i + 1}",
                )
                for i, z in enumerate(data.get("thermostats") or [])
            ],
        )

    @property
    def mac(self) -> str:
        """MAC part of the device id, used in MQTT topics."""
        return self.device_id[:12]

    @property
    def additional_value(self) -> str:
        """The 4 characters after the MAC in the device ID, sent as `additionalValue`."""
        return self.device_id[12:16]


@dataclass
class ZoneStatus:
    """Live state of one heating zone."""

    zone_id: int
    mode: HeatingMode | int
    heating_active: bool
    current_temperature: float
    # `insideTemperatureSetting`. Only meaningful outside AUTO: in AUTO it keeps the last override value,
    # and the real target comes from the schedule (see `target_temperature`).
    setting_temperature: float
    holiday: bool
    # `freezeProtectionTemperature`: what the zone holds while holiday mode is on.
    frost_temperature: float
    # Current slot of today's schedule (1-6), or 0 while still in yesterday's last slot.
    program_number: int
    # Schedule layout: 1 = 7-day, 2 = 5/2, 3 = 5/1/1.
    program_type: int
    raw: dict[str, Any]
    # Whether the zone's thermostat has a radio link to the module (from the status's `nrcConnected` bitmask).
    thermostat_connected: bool = False

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ZoneStatus:
        """Parse one entry of a status reply's `thermostats` list."""
        return cls(
            zone_id=int(data.get("zoneId", 1)),
            mode=_enum(HeatingMode, data.get("insideOperationMode")),
            heating_active=_on(data.get("insideOperationBusy")),
            current_temperature=data.get("insideTemperature", 0) / 2,
            setting_temperature=data.get("insideTemperatureSetting", 0) / 2,
            holiday=_on(data.get("holidayUseSetting")),
            frost_temperature=data.get("freezeProtectionTemperature", 0) / 2,
            program_number=int(data.get("heatingCurrentProgramNum") or 0),
            program_type=int(data.get("programReservationType") or 1),
            raw=data,
        )

    def target_temperature(
        self, schedule: HeatingSchedule | None, now: datetime
    ) -> float:
        """The target the app shows: the frost-protection temperature in holiday mode, otherwise the setpoint."""
        if self.holiday:
            return self.frost_temperature
        return self.setpoint(schedule, now)

    def setpoint(self, schedule: HeatingSchedule | None, now: datetime) -> float:
        """The zone's own setpoint, ignoring holiday mode: the schedule's current slot in AUTO, otherwise the setting."""
        if self.mode == HeatingMode.AUTO and schedule is not None:
            scheduled = schedule.scheduled_temperature(
                self.program_type, self.program_number, now
            )
            if scheduled is not None:
                return scheduled
        return self.setting_temperature


@dataclass
class ScheduleSlot:
    """One switching point of a heating schedule."""

    slot: int
    hour: int
    minute: int
    temperature: float
    enabled: bool


@dataclass
class HeatingSchedule:
    """A zone's heating schedule from `res/heat-reserv/rd`. `days` is keyed by the raw `day` (0 = Sunday … 6 = Saturday)."""

    zone_id: int
    days: dict[int, list[ScheduleSlot]]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> HeatingSchedule:
        """Parse one zone of a `heatingReservation` reply."""
        days: dict[int, list[ScheduleSlot]] = {}
        for row in data.get("reservation") or []:
            days.setdefault(int(row.get("day", 0)), []).append(
                ScheduleSlot(
                    slot=int(row.get("rsvNum", 0)),
                    hour=int(row.get("hour", 0)),
                    minute=int(row.get("min", 0)),
                    temperature=row.get("param", 0) / 2,
                    enabled=_on(row.get("enable")),
                )
            )
        for slots in days.values():
            slots.sort(key=lambda s: s.slot)
        return cls(zone_id=int(data.get("zoneId", 1)), days=days)

    @staticmethod
    def day_index(program_type: int, day: date) -> int:
        """Raw `day` whose slots apply on `day`, mirroring the app.

        The app re-indexes raw days to lists Monday = 0 … Sunday = 6 (ProgramViewModel.addHeatList), then picks
        a list by program type (MainViewModel.getWeekProgramListData). Both steps are reproduced here.
        """
        # Monday = 0 … Sunday = 6, the same as the app's list index.
        weekday = day.weekday()
        if program_type == 1:
            # 7-day: every day has its own list.
            app_list = weekday
        elif program_type == 2:
            # 5/2: weekdays read list 1, the weekend list 5.
            app_list = 5 if weekday >= 5 else 1
        elif program_type == 3:
            # 5/1/1: weekdays read list 1, Saturday list 5, Sunday list 6.
            app_list = {5: 5, 6: 6}.get(weekday, 1)
        else:
            app_list = 1
        return (app_list + 1) % 7

    def scheduled_temperature(
        self, program_type: int, program_number: int, now: datetime
    ) -> float | None:
        """Temperature of the slot the thermostat reports as current (slot 0 = yesterday's last slot)."""
        if program_number == 0:
            slots = self.days.get(
                self.day_index(program_type, (now - timedelta(days=1)).date()), []
            )
            return slots[-1].temperature if slots else None
        slots = self.days.get(self.day_index(program_type, now.date()), [])
        if 0 < program_number <= len(slots):
            return slots[program_number - 1].temperature
        return None


@dataclass
class DeviceStatus:
    """Live state of the boiler, hot water and all zones. Fields not parsed here are in `raw`."""

    burner_active: bool
    error_code: int
    outside_temperature: float | None
    dhw_mode: HotWaterMode | int
    dhw_active: bool
    zones: dict[int, ZoneStatus]
    raw: dict[str, Any]

    @classmethod
    def from_json(cls, status: dict[str, Any]) -> DeviceStatus:
        """Parse a status reply's `status` object."""
        zones = [ZoneStatus.from_json(z) for z in status.get("thermostats") or []]
        linked = int(status.get("nrcConnected") or 0)
        for zone in zones:
            zone.thermostat_connected = bool(linked & ZONE_MASK.get(zone.zone_id, 0))
        return cls(
            burner_active=_on(status.get("operationBusy")),
            error_code=int(status.get("errorCode") or 0),
            outside_temperature=(
                None
                if not status.get("regionCode")
                else status.get("outsideTemperature", 0) / 10
            ),
            dhw_mode=_enum(HotWaterMode, status.get("dhwOperationMode")),
            dhw_active=_on(status.get("dhwOperationBusy")),
            zones={z.zone_id: z for z in zones},
            raw=status,
        )


@dataclass
class UsageRecord:
    """Fuel use for one day, one month (`day` None) or one year (`month` 0, see `year_total`).

    Gas in m³, oil presumably litres; times in hours.
    """

    year: int
    month: int
    day: int | None
    heating_usage: int
    hot_water_usage: int
    heating_hours: int
    hot_water_hours: int

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> UsageRecord:
        """Parse one row of a daily or monthly usage reply."""
        return cls(
            year=int(data.get("year", 0)),
            month=int(data.get("month", 0)),
            day=int(data["day"]) if data.get("day") is not None else None,
            heating_usage=int(data.get("heatUsage") or 0),
            hot_water_usage=int(data.get("dhwUsage") or 0),
            heating_hours=int(data.get("heatTime") or 0),
            hot_water_hours=int(data.get("dhwTime") or 0),
        )

    @classmethod
    def year_total(cls, year: int, records: list[UsageRecord]) -> UsageRecord:
        """Add up `records` into one record for the year."""
        return cls(
            year=year,
            month=0,
            day=None,
            heating_usage=sum(r.heating_usage for r in records),
            hot_water_usage=sum(r.hot_water_usage for r in records),
            heating_hours=sum(r.heating_hours for r in records),
            hot_water_hours=sum(r.hot_water_hours for r in records),
        )

    @property
    def total_usage(self) -> int:
        """Gas for heating and hot water together."""
        return self.heating_usage + self.hot_water_usage

    @property
    def date(self) -> date:
        """The day of the record, or the first of its month for a monthly record."""
        return date(self.year, self.month, self.day or 1)


@dataclass
class DeviceFeatures:
    """Capabilities and versions from the `res/did` device-info response."""

    raw: dict[str, Any]

    def supported(self, key: str) -> bool:
        """Whether the module reports `key` as supported."""
        return _on(self.raw.get(key))

    @property
    def has_hot_water(self) -> bool:
        """Whether the boiler supplies hot water."""
        return self.supported("dhwUse")

    @property
    def has_holiday(self) -> bool:
        """Whether holiday mode is available."""
        return self.supported("holidayUse")

    @property
    def has_fuel_usage(self) -> bool:
        """Whether fuel usage history is available."""
        return self.supported("fuelUsageUse")

    @property
    def min_zone_temperature(self) -> float:
        """Lowest room setpoint, in °C."""
        value = self.raw.get("insideTemperatureMin")
        return value / 2 if value else MIN_ZONE_TEMP

    @property
    def max_zone_temperature(self) -> float:
        """Highest room setpoint, in °C."""
        value = self.raw.get("insideTemperatureMax")
        return value / 2 if value else MAX_ZONE_TEMP

    @property
    def boiler_serial(self) -> str | None:
        """The boiler controller's serial number, if reported."""
        return self.raw.get("boilerControllerSerialNumber") or None

    @property
    def wifi_sw_version(self) -> str | None:
        """The Wi-Fi module's software version, if reported."""
        version = self.raw.get("wifiSwVersion")
        return str(version) if version is not None else None
