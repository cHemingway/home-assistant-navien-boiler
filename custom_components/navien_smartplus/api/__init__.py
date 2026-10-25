"""Async client for the Navien Smart Plus (UK) cloud. Kept free of Home Assistant imports."""

from .client import NavienSmartPlusClient
from .const import HeatingMode, HotWaterMode
from .exceptions import (
    NavienApiError,
    NavienAuthError,
    NavienConnectionError,
    NavienError,
    NavienTimeoutError,
)
from .models import (
    Account,
    AuthInfo,
    Device,
    DeviceFeatures,
    DeviceStatus,
    HeatingSchedule,
    ScheduleSlot,
    UsageRecord,
    ZoneInfo,
    ZoneStatus,
)

__all__ = [
    "Account",
    "AuthInfo",
    "Device",
    "DeviceFeatures",
    "DeviceStatus",
    "HeatingMode",
    "HeatingSchedule",
    "HotWaterMode",
    "NavienApiError",
    "NavienAuthError",
    "NavienConnectionError",
    "NavienError",
    "NavienSmartPlusClient",
    "NavienTimeoutError",
    "ScheduleSlot",
    "UsageRecord",
    "ZoneInfo",
    "ZoneStatus",
]
