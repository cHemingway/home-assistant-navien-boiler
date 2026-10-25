"""Constants for the Navien Smart Plus integration."""

from datetime import timedelta
import logging

DOMAIN = "navien_smartplus"
LOGGER = logging.getLogger(__package__)

CONF_CLIENT_ID = "client_id"
CONF_DEVICE_MACS = "device_macs"

MANUFACTURER = "Navien"

STATUS_INTERVAL = timedelta(seconds=60)
SCHEDULE_INTERVAL = timedelta(minutes=30)
USAGE_INTERVAL = timedelta(hours=1)

# Months of daily gas history imported into long-term statistics on first setup.
USAGE_BACKFILL_MONTHS = 13

PRESET_OVERRIDE = "override"
