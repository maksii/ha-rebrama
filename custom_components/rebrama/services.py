"""Service actions for the Rebrama integration."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import logging

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .api import RebramaAuthError, RebramaError
from .const import (
    ATTR_ACCESS_POINTS,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_DESCRIPTION,
    ATTR_END,
    ATTR_LINK,
    ATTR_START,
    ATTR_USES,
    DOMAIN,
    SERVICE_CREATE_TEMPORARY_ACCESS,
    SERVICE_DELETE_TEMPORARY_ACCESS,
)
from .coordinator import RebramaConfigEntry

_LOGGER = logging.getLogger(__name__)

CREATE_TEMPORARY_ACCESS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ACCESS_POINTS): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Required(ATTR_START): cv.datetime,
        vol.Required(ATTR_END): cv.datetime,
        vol.Optional(ATTR_DESCRIPTION, default="Home Assistant"): cv.string,
        vol.Optional(ATTR_USES): vol.All(vol.Coerce(int), vol.Range(min=1)),
    }
)

DELETE_TEMPORARY_ACCESS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_LINK): cv.string,
    }
)


def _epoch(value: datetime) -> int:
    """Convert a (possibly naive) datetime to epoch seconds."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return int(value.timestamp())


def _slug(link: str) -> str:
    """Extract the trailing slug from a share link or return it unchanged."""
    return link.rstrip("/").rsplit("/", 1)[-1] if "/" in link else link


def _loaded_entry(hass: HomeAssistant, entry_id: str) -> RebramaConfigEntry:
    """Return a loaded Rebrama config entry or raise a validation error."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_not_found"
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_not_loaded"
        )
    return entry


async def _async_create_temporary_access(call: ServiceCall) -> ServiceResponse:
    """Create a time-bounded share link for the selected access points."""
    hass = call.hass
    ent_reg = er.async_get(hass)

    access_points: dict[str, set[str]] = defaultdict(set)
    for entity_id in call.data[ATTR_ACCESS_POINTS]:
        entry = ent_reg.async_get(entity_id)
        if (
            entry is None
            or entry.platform != DOMAIN
            or entry.domain != "button"
            or entry.config_entry_id is None
            or not entry.unique_id.endswith("_open")
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_access_point",
                translation_placeholders={"entity_id": entity_id},
            )
        access_point_id = entry.unique_id.removesuffix("_open")
        access_points[entry.config_entry_id].add(access_point_id)

    if len(access_points) != 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="single_account_required"
        )

    config_entry_id, ap_ids = next(iter(access_points.items()))
    start = _epoch(call.data[ATTR_START])
    end = _epoch(call.data[ATTR_END])
    if end <= start:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="invalid_time_range"
        )

    coordinator = _loaded_entry(hass, config_entry_id).runtime_data
    try:
        result = await coordinator.client.async_create_temporary_access(
            access_point_ids=sorted(ap_ids),
            date_start=start,
            date_end=end,
            description=call.data[ATTR_DESCRIPTION],
            uses_number=call.data.get(ATTR_USES),
        )
    except RebramaAuthError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from err
    except RebramaError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="service_failed",
            translation_placeholders={"error": str(err)},
        ) from err

    await coordinator.async_refresh_temp_accesses()
    url = result.get("tempAccessLink") or result.get("url") or ""
    return {"url": url, "link": _slug(url) if url else ""}


async def _async_delete_temporary_access(call: ServiceCall) -> None:
    """Delete a temporary access by its link/slug."""
    hass = call.hass
    coordinator = _loaded_entry(hass, call.data[ATTR_CONFIG_ENTRY_ID]).runtime_data
    try:
        await coordinator.client.async_delete_temporary_access(
            _slug(call.data[ATTR_LINK])
        )
    except RebramaAuthError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from err
    except RebramaError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="service_failed",
            translation_placeholders={"error": str(err)},
        ) from err

    await coordinator.async_refresh_temp_accesses()


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register Rebrama service actions (once, at integration setup)."""
    if hass.services.has_service(DOMAIN, SERVICE_CREATE_TEMPORARY_ACCESS):
        return

    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_TEMPORARY_ACCESS,
        _async_create_temporary_access,
        schema=CREATE_TEMPORARY_ACCESS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_TEMPORARY_ACCESS,
        _async_delete_temporary_access,
        schema=DELETE_TEMPORARY_ACCESS_SCHEMA,
    )
