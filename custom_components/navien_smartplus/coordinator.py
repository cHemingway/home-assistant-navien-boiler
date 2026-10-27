"""Data update coordinators for Navien Smart Plus."""

from collections.abc import Awaitable
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import VolumeConverter

from .api import (
    Device,
    DeviceFeatures,
    DeviceStatus,
    HeatingSchedule,
    NavienAuthError,
    NavienError,
    NavienSmartPlusClient,
    UsageRecord,
)
from .const import (
    DOMAIN,
    LOGGER,
    RETRY_AFTER,
    SCHEDULE_INTERVAL,
    STATUS_INTERVAL,
    USAGE_BACKFILL_MONTHS,
    USAGE_INTERVAL,
)

if TYPE_CHECKING:
    from . import NavienConfigEntry


@dataclass
class NavienData:
    """Everything the entities of one module need."""

    status: DeviceStatus
    features: DeviceFeatures
    schedules: dict[int, HeatingSchedule]


class NavienBaseCoordinator[T](DataUpdateCoordinator[T]):
    """Retries a failed update sooner than the poll interval, backing off towards it."""

    config_entry: NavienConfigEntry
    device: Device
    _failures = 0

    async def _async_update_data(self) -> T:
        try:
            return await self._async_fetch()
        except UpdateFailed as err:
            # Failures in a row; a pushed update in between also counts as a success.
            self._failures = self._failures + 1 if not self.last_update_success else 1
            if self._retry_soon():
                if TYPE_CHECKING:
                    assert self.update_interval is not None
                err.retry_after = min(
                    RETRY_AFTER * 2 ** (self._failures - 1),
                    self.update_interval.total_seconds(),
                )
            raise

    async def _async_fetch(self) -> T:
        """Fetch the data; errors from `_cloud` mark the update as failed."""
        raise NotImplementedError

    def _retry_soon(self) -> bool:
        """Whether a failed update is worth retrying before the next poll."""
        return True


class NavienCoordinator(NavienBaseCoordinator[NavienData]):
    """Polls status for one BCM module and applies pushed updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: NavienConfigEntry,
        client: NavienSmartPlusClient,
        device: Device,
    ) -> None:
        """Set up polling for one module."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {device.device_id}",
            update_interval=STATUS_INTERVAL,
        )
        self.client = client
        self.device = device
        self._schedules_at: datetime | None = None
        # Whether the module is connected to the cloud: the REST device list at first, then its connection events.
        self.module_online = device.is_connected

    async def _async_fetch(self) -> NavienData:
        """Poll status; read device info once and the schedule when it's due."""
        previous = self.data
        if previous:
            features = previous.features
        else:
            features = await _cloud(self.client.async_get_features(self.device))
        status = await _cloud(self.client.async_get_status(self.device))
        # It just answered, so it's online even if a connection event was missed.
        self.module_online = True
        schedules = previous.schedules if previous else {}
        if self._schedule_due(status):
            schedules = await _cloud(
                self.client.async_get_heating_schedules(self.device)
            )
            self._schedules_at = dt_util.utcnow()
        return NavienData(status=status, features=features, schedules=schedules)

    def _retry_soon(self) -> bool:
        """Not while the module has reported itself offline: it can't answer until it reports back."""
        return self.module_online is not False

    def _schedule_due(self, status: DeviceStatus) -> bool:
        """Refresh the schedule periodically, or as soon as a zone moves to another slot."""
        if self.data is None or self._schedules_at is None:
            return True
        if dt_util.utcnow() - self._schedules_at > SCHEDULE_INTERVAL:
            return True
        previous = {
            z.zone_id: z.program_number for z in self.data.status.zones.values()
        }
        return any(
            previous.get(z.zone_id) != z.program_number for z in status.zones.values()
        )

    @callback
    def async_handle_status(self, device: Device, status: DeviceStatus) -> None:
        """Status pushed over MQTT: replies to other clients of this account, such as the app."""
        if device.device_id == self.device.device_id:
            self.async_apply_status(status)

    @callback
    def async_handle_online(self, device: Device, online: bool) -> None:
        """The module reported going offline or coming back."""
        if device.device_id == self.device.device_id:
            self.module_online = online
            self.async_update_listeners()

    @callback
    def async_apply_status(self, status: DeviceStatus | None) -> None:
        """Show a newer status straight away (pushed, or returned by a control command)."""
        if status is not None and self.data is not None:
            self.async_set_updated_data(replace(self.data, status=status))


USAGE_STATISTICS = {
    # statistic suffix -> (UsageRecord attribute, name)
    "heating_gas": ("heating_usage", "heating gas"),
    "hot_water_gas": ("hot_water_usage", "hot water gas"),
    "total_gas": ("total_usage", "total gas"),
}


class NavienUsageCoordinator(NavienBaseCoordinator[UsageRecord]):
    """Hourly fuel use: the year-to-date total for sensors, and daily history imported as long-term statistics.

    The year's monthly totals cost one request per hour. Daily records are fetched only when a month's total no
    longer matches the cached days, which happens once a day when the module fills in the day that just ended.
    """

    def __init__(self, hass: HomeAssistant, status: NavienCoordinator) -> None:
        """Set up usage polling for the module that `status` polls."""
        device = status.device
        super().__init__(
            hass,
            LOGGER,
            config_entry=status.config_entry,
            name=f"{DOMAIN} usage {device.device_id}",
            update_interval=USAGE_INTERVAL,
        )
        self._status = status
        self.client = status.client
        self.device = device
        # Every finished day since the backfill started, kept on disk so statistics sums always start from the
        # same day.
        self._store = usage_store(hass, device.device_id)
        self._days: dict[date, UsageRecord] | None = None
        # Months the first backfill still has to fetch, and the earliest day changed since the cache was last
        # saved. Both survive a failed update, so the next one carries on where it stopped.
        self._backfill: list[tuple[int, int]] = []
        self._unsaved_since: date | None = None
        # The day last December was last checked, while January's first days have no total to compare it with.
        self._last_year_checked: date | None = None

    async def _async_fetch(self) -> UsageRecord:
        """Fetch this year's totals, and new daily history when the totals have moved on."""
        today = dt_util.now().date()
        if self._days is None:
            stored = await self._store.async_load() or {}
            self._days = {
                date.fromisoformat(k): UsageRecord(**v)
                for k, v in stored.get("days", {}).items()
            }
            if not self._days:
                self._backfill = _months_back(today, USAGE_BACKFILL_MONTHS)
                # The backfill includes last December.
                self._last_year_checked = today
        while self._backfill:
            await self._fetch_month(*self._backfill[0], today)
            self._backfill.pop(0)
        monthly = await _cloud(
            self.client.async_get_monthly_usage(self.device, today.year)
        )
        # Check last month too for the first few days, while its final days are still being filled in. This
        # year's totals don't cover last December, so in January its days are fetched once a day instead of
        # hourly; if that's before the module fills in 31 December, the day arrives a day late.
        for year, month in _months_back(today, 2 if today.day <= 3 else 1):
            last_year = year != today.year
            if self._cached_month_matches(year, month, monthly) or (
                last_year and self._last_year_checked == today
            ):
                continue
            await self._fetch_month(year, month, today)
            if last_year:
                self._last_year_checked = today

        # Saved and imported only once everything has been fetched, so a failure part-way writes nothing.
        if self._unsaved_since is not None:
            await self._store.async_save(
                {"days": {d.isoformat(): asdict(r) for d, r in self._days.items()}}
            )
            self._import_statistics(self._unsaved_since)
            self._unsaved_since = None
        return UsageRecord.year_total(today.year, monthly)

    def _retry_soon(self) -> bool:
        """Not while the module has reported itself offline."""
        return self._status.module_online is not False

    def _cached_month_matches(
        self, year: int, month: int, monthly: list[UsageRecord]
    ) -> bool:
        """Whether the cached days add up to the module's total for that month (only known for this year)."""
        if TYPE_CHECKING:
            assert self._days is not None
        total = next((r for r in monthly if (r.year, r.month) == (year, month)), None)
        if total is None:
            return False
        cached = UsageRecord.year_total(
            year,
            [r for d, r in self._days.items() if (d.year, d.month) == (year, month)],
        )
        return (cached.heating_usage, cached.hot_water_usage) == (
            total.heating_usage,
            total.hot_water_usage,
        )

    async def _fetch_month(self, year: int, month: int, today: date) -> None:
        """Fetch a month's finished days into the cache, noting the earliest day that changed."""
        if TYPE_CHECKING:
            assert self._days is not None
        for record in await _cloud(
            self.client.async_get_daily_usage(self.device, year, month)
        ):
            day = record.date
            if day >= today:
                # Unfinished days read 0 until they are over.
                continue
            if self._days.get(day) != record:
                self._days[day] = record
                self._unsaved_since = min(self._unsaved_since or day, day)

    def _import_statistics(self, since: date) -> None:
        """Write one statistic row per finished day from `since`, at local midnight, with sums from the first day."""
        if TYPE_CHECKING:
            assert self._days is not None
        days = sorted(self._days.items())
        starts = {
            day: dt_util.start_of_local_day(day) for day, _ in days if day >= since
        }
        for suffix, (attr, label) in USAGE_STATISTICS.items():
            running = 0.0
            rows: list[StatisticData] = []
            for day, record in days:
                value = float(getattr(record, attr))
                running += value
                if day in starts:
                    rows.append(
                        StatisticData(start=starts[day], state=value, sum=running)
                    )
            metadata = StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                has_sum=True,
                name=f"Navien {label}",
                source=DOMAIN,
                statistic_id=usage_statistic_id(self.device.mac, suffix),
                unit_class=VolumeConverter.UNIT_CLASS,
                unit_of_measurement=UnitOfVolume.CUBIC_METERS,
            )
            async_add_external_statistics(self.hass, metadata, rows)
        LOGGER.debug("Imported %s days of gas statistics from %s", len(starts), since)


async def _cloud[T](request: Awaitable[T]) -> T:
    """Run one cloud request, turning its errors into coordinator errors."""
    try:
        return await request
    except NavienAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except NavienError as err:
        raise UpdateFailed(str(err)) from err


def _months_back(today: date, count: int) -> list[tuple[int, int]]:
    """The last `count` (year, month) pairs ending with today's month, oldest first."""
    months = []
    year, month = today.year, today.month
    for _ in range(count):
        months.append((year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(months))


def usage_statistic_id(mac: str, suffix: str) -> str:
    """The long-term statistic ID for one gas series of a module."""
    return f"{DOMAIN}:{mac}_{suffix}"


def usage_store(hass: HomeAssistant, device_id: str) -> Store[dict[str, Any]]:
    """The store holding a module's cached daily usage."""
    return Store(hass, 1, f"{DOMAIN}.usage.{device_id}")
