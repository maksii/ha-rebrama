"""Diagnostics support for the Rebrama integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import CONF_FINGERPRINT, CONF_PHONE, CONF_REFRESH_TOKEN, CONF_USER_ID
from .coordinator import RebramaConfigEntry

TO_REDACT = {
    CONF_PASSWORD,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_FINGERPRINT,
    CONF_PHONE,
    CONF_USER_ID,
    "user_phone",
    "userPhone",
    "opened_by_phone",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: RebramaConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data

    places = [
        {
            "id": place.id,
            "name": place.name,
            "can_manage": place.can_manage,
            "is_owner": place.is_owner,
            "access_points": [
                {
                    "id": access_point.id,
                    "name": access_point.name,
                    "is_online": access_point.is_online,
                    "can_share_access": access_point.can_share_access,
                }
                for access_point in place.access_points.values()
            ],
            "last_opened": (
                log.created_at.isoformat()
                if (log := data.logs.get(place.id)) and log.created_at
                else None
            ),
        }
        for place in data.places.values()
    ]

    # Share links/slugs are secrets, so only the non-sensitive shape is included.
    temporary_accesses = [
        {
            "description": access.description,
            "valid_from": (
                access.date_start.isoformat() if access.date_start else None
            ),
            "valid_until": access.date_end.isoformat() if access.date_end else None,
            "max_uses": access.uses_number,
        }
        for access in data.temp_accesses
    ]

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "settings": coordinator.settings,
        "update_interval_seconds": (
            coordinator.update_interval.total_seconds()
            if coordinator.update_interval
            else None
        ),
        "subscription_valid_until": (
            coordinator.profile.valid_until.isoformat()
            if coordinator.profile.valid_until
            else None
        ),
        "temporary_accesses": temporary_accesses,
        "places": async_redact_data(places, TO_REDACT),
    }
