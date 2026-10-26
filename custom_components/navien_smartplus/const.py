"""Constants for the Navien Smart Plus integration."""

from datetime import timedelta
import logging

DOMAIN = "navien_smartplus"
LOGGER = logging.getLogger(__package__)

CONF_DEVICE_MACS = "device_macs"

MANUFACTURER = "Navien"

STATUS_INTERVAL = timedelta(seconds=60)
SCHEDULE_INTERVAL = timedelta(minutes=30)
USAGE_INTERVAL = timedelta(hours=1)

# Months of daily gas history imported into long-term statistics on first setup.
USAGE_BACKFILL_MONTHS = 13

PRESET_OVERRIDE = "override"

# Boiler error codes that have a description, from Navien's boiler manuals (see docs/protocol.md, "Error codes").
# The status's `errorCode` is the number shown on the boiler as E plus three digits, e.g. 3 = E003.
ERROR_CODES = (
    1,
    3,
    4,
    12,
    16,
    30,
    31,
    46,
    47,
    56,
    60,
    109,
    110,
    127,
    144,
    157,
    205,
    218,
    291,
    302,
    352,
    353,
    407,
    421,
    434,
    441,
    445,
    480,
    515,
    517,
    593,
    594,
    598,
    615,
    740,
    782,
    783,
    787,
    788,
    792,
)
ERROR_STATE_NONE = "none"
ERROR_STATE_UNKNOWN = "unknown"
