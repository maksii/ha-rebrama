"""Constants for the Rebrama integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "rebrama"

PLATFORMS: Final[list[Platform]] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
]

# Branding / device metadata
MANUFACTURER: Final = "Rebrama"
MODEL_ACCOUNT: Final = "Account"
MODEL_PLACE: Final = "Place"
MODEL_ACCESS_POINT: Final = "Access point"
ATTRIBUTION: Final = "Data provided by Rebrama"
CONFIGURATION_URL: Final = "https://rebrama.com"

# --- HTTP API ---
BASE_URL: Final = "https://rebrama.com"
# Mirrors the official app's versionCode; keeps the request shape identical to
# the mobile client so server-side bot filtering does not reject us.
APP_BUILD_NUMBER: Final = "16"
USER_AGENT: Final = "HomeAssistant-Rebrama"
REQUEST_TIMEOUT: Final = 30  # seconds
# Minimum number of digits in a valid (country-code prefixed) phone number.
PHONE_MIN_DIGITS: Final = 12

# --- Config entry keys (CONF_PASSWORD / CONF_ACCESS_TOKEN come from HA core) ---
CONF_PHONE: Final = "phone"
CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_FINGERPRINT: Final = "fingerprint"
CONF_USER_ID: Final = "user_id"

# --- API domain error codes ---
# The server returns HTTP 400 for *every* failure (there is no 401/403/404);
# the real signal is ``error.code`` in the response envelope.
ERROR_UNAUTHORIZED: Final = 1100  # bad/expired bearer or refresh -> refresh/reauth
ERROR_PHONE_INVALID: Final = 1201  # malformed OR unregistered phone (indistinguishable)
ERROR_WRONG_CREDENTIALS: Final = 1203  # correct phone, wrong password
ERROR_PHONE_MIN_LENGTH: Final = 1209
ERROR_PHONE_FORMAT: Final = 1210
ERROR_TEMP_ACCESS_NOT_FOUND: Final = 1501

# --- Polling ---
DEFAULT_SCAN_INTERVAL: Final = timedelta(seconds=60)
MIN_SCAN_INTERVAL: Final = 30
MAX_SCAN_INTERVAL: Final = 600

# --- Services ---
SERVICE_CREATE_TEMPORARY_ACCESS: Final = "create_temporary_access"
SERVICE_DELETE_TEMPORARY_ACCESS: Final = "delete_temporary_access"

ATTR_ACCESS_POINTS: Final = "access_points"
ATTR_START: Final = "start"
ATTR_END: Final = "end"
ATTR_DESCRIPTION: Final = "description"
ATTR_USES: Final = "uses"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_LINK: Final = "link"


def account_device_id(user_id: str) -> str:
    """Return the device-registry identifier suffix for an account hub."""
    return f"account_{user_id}"


def place_device_id(place_id: str) -> str:
    """Return the device-registry identifier suffix for a place."""
    return f"place_{place_id}"


def access_point_device_id(access_point_id: str) -> str:
    """Return the device-registry identifier suffix for an access point."""
    return f"ap_{access_point_id}"
