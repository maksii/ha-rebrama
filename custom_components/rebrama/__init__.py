"""The Rebrama integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntry, DeviceEntryType
from homeassistant.helpers.typing import ConfigType

from .api import RebramaClient
from .const import (
    CONF_FINGERPRINT,
    CONF_PHONE,
    CONF_REFRESH_TOKEN,
    CONFIGURATION_URL,
    DOMAIN,
    MANUFACTURER,
    MODEL_ACCOUNT,
    MODEL_PLACE,
    PLATFORMS,
    access_point_device_id,
    account_device_id,
    place_device_id,
)
from .coordinator import RebramaConfigEntry, RebramaCoordinator
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

# This integration can only be configured through the UI.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Rebrama integration (registers service actions)."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: RebramaConfigEntry) -> bool:
    """Set up Rebrama from a config entry."""

    async def _token_updater(access: str, refresh: str) -> None:
        """Persist a rotated token pair back to the config entry."""
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_ACCESS_TOKEN: access,
                CONF_REFRESH_TOKEN: refresh,
            },
        )

    client = RebramaClient(
        async_get_clientsession(hass),
        fingerprint=entry.data[CONF_FINGERPRINT],
        access=entry.data.get(CONF_ACCESS_TOKEN),
        refresh=entry.data.get(CONF_REFRESH_TOKEN),
        phone=entry.data.get(CONF_PHONE),
        password=entry.data.get(CONF_PASSWORD),
        token_updater=_token_updater,
    )

    coordinator = RebramaCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Devices are synced before the platforms load so entities can link to
    # their parent (place / account) device, and again after every poll.
    _sync_devices(hass, entry)
    entry.async_on_unload(
        coordinator.async_add_listener(lambda: _sync_devices(hass, entry))
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RebramaConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: RebramaConfigEntry, device: DeviceEntry
) -> bool:
    """Allow deleting devices that no longer exist in the account."""
    if entry.state is not ConfigEntryState.LOADED:
        # Nothing to compare against; the device is recreated on the next
        # load if it still exists.
        return True
    return not _is_current(_valid_identifiers(entry.runtime_data), device)


@callback
def _valid_identifiers(coordinator: RebramaCoordinator) -> set[str]:
    """Return all device-identifier suffixes that currently exist."""
    valid = {account_device_id(coordinator.user_id)}
    for place in coordinator.data.places.values():
        valid.add(place_device_id(place.id))
        for access_point in place.access_points.values():
            valid.add(access_point_device_id(access_point.id))
    return valid


@callback
def _is_current(valid: set[str], device: DeviceEntry) -> bool:
    """Return whether a registry device still exists in the account."""
    return any(
        domain == DOMAIN and identifier in valid
        for domain, identifier in device.identifiers
    )


@callback
def _sync_devices(hass: HomeAssistant, entry: RebramaConfigEntry) -> None:
    """Register account/place devices and remove ones that disappeared."""
    coordinator = entry.runtime_data
    dev_reg = dr.async_get(hass)

    account = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, account_device_id(coordinator.user_id))},
        name=f"{MANUFACTURER} ({entry.data[CONF_PHONE]})",
        manufacturer=MANUFACTURER,
        model=MODEL_ACCOUNT,
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=CONFIGURATION_URL,
    )

    for place in coordinator.data.places.values():
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, place_device_id(place.id))},
            name=place.name,
            manufacturer=MANUFACTURER,
            model=MODEL_PLACE,
            via_device_id=account.id,
            configuration_url=CONFIGURATION_URL,
        )

    valid = _valid_identifiers(coordinator)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if not _is_current(valid, device):
            _LOGGER.debug("Removing stale Rebrama device %s", device.name)
            dev_reg.async_remove_device(device.id)
