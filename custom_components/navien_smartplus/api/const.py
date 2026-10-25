"""Constants for the Navien Smart Plus (UK) cloud protocol. See docs/protocol.md."""

from __future__ import annotations

from enum import IntEnum

API_BASE_URL = "https://spuk.naviensmartcontrol.com"
IOT_ENDPOINT = "spuk-iot.naviensmartcontrol.com"
AWS_REGION = "eu-west-2"
IOT_SERVICE = "iotdevicegateway"

APP_VERSION = "2.00.10_20017"
USER_AGENT = f"SmartPlus UK/{APP_VERSION} (Home Assistant, Home Assistant, navien_smartplus, Android, 14, 34)"

PATH_SIGN_IN = "/api/v1.0/users/sign-in"
PATH_REFRESH_TOKEN = "/api/v1.0/users/token/refresh"
PATH_REFRESH_SESSION = "/api/v1.0/users/token/session/refresh"
PATH_DEVICES = "/api/v1.0/devices"

MQTT_PROTOCOL_VERSION = 1
MQTT_NSP = "nsp-"

# Booleans in status payloads are 2 = on, 1 = off.
STATE_ON = 2


class Command(IntEnum):
    """MQTT `request.command` values."""

    STATUS_START = 0x01000001
    STATUS_GET = 0x01000003
    STATUS_MONTHLY_USAGE = 0x01000004
    STATUS_DAILY_USAGE = 0x01000005
    STATUS_GET_INSIDE_RSV = 0x01000007
    CTRL_HEAT_INSIDE = 0x02000001
    CTRL_HOT_WATER = 0x02000002
    CTRL_HOLIDAY_OFF = 0x02000003
    CTRL_HOLIDAY_ON = 0x02000004
    CTRL_SET_INSIDE_TEMP = 0x02000005


class ControlMode:
    """MQTT `request.mode` strings used with control commands."""

    HEAT_MODE = "heat-mode"
    INSIDE_HEAT = "inside-heat"
    DHW_MODE = "dhw-mode"
    HOLIDAY_ON = "goout-on"
    HOLIDAY_OFF = "goout-off"


class HeatingMode(IntEnum):
    """Zone heating modes (`insideOperationMode`)."""

    AUTO = 0
    ON = 1
    OFF = 2
    BOOST = 3
    OVERRIDE = 4


class HotWaterMode(IntEnum):
    """Domestic hot water modes (`dhwOperationMode`)."""

    AUTO = 0
    ON = 1
    ONCE = 2
    OFF = 3
    NONE = 4
    INTELLIGENT = 5
    NAVICIRC = 6


ZONE_MASK = {1: 1, 2: 2, 3: 4}

MIN_ZONE_TEMP = 10.0
MAX_ZONE_TEMP = 40.0
