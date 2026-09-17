"""Tests for the Rebrama integration setup/teardown and dynamic devices."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntry
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.rebrama as integration
from custom_components.rebrama import async_remove_config_entry_device
from custom_components.rebrama.api import RebramaAuthError
from custom_components.rebrama.binary_sensor import RebramaConnectivitySensor
from custom_components.rebrama.const import (
    CONF_REFRESH_TOKEN,
    DOMAIN,
    account_device_id,
)
from custom_components.rebrama.models import AccessPoint, Place


def _device(
    hass: HomeAssistant, entry: MockConfigEntry, identifier: str
) -> DeviceEntry | None:
    return dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, identifier), entry.entry_id
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_and_unload(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The entry loads, creates chained devices/entities, and unloads cleanly."""
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    ent_reg = er.async_get(hass)
    button_id = ent_reg.async_get_entity_id("button", DOMAIN, "ap-1_open")
    assert button_id is not None
    assert hass.states.get(button_id) is not None

    account = _device(hass, mock_config_entry, account_device_id("user-1"))
    place = _device(hass, mock_config_entry, "place_place-1")
    door = _device(hass, mock_config_entry, "ap_ap-1")
    assert account is not None and place is not None and door is not None
    # Devices are chained door -> place -> account.
    assert place.via_device_id == account.id
    assert door.via_device_id == place.id

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_auth_failure_triggers_reauth(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An auth failure on first refresh puts the entry in error and starts reauth."""
    patch_client.async_get_places.side_effect = RebramaAuthError
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR

    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)


async def test_offline_access_point_button_unavailable(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An offline access point's open button is unavailable."""
    await _setup(hass, mock_config_entry)

    ent_reg = er.async_get(hass)
    # ap-2 (Garage) is offline in the fixture.
    garage = ent_reg.async_get_entity_id("button", DOMAIN, "ap-2_open")
    assert garage is not None
    assert hass.states.get(garage).state == "unavailable"


async def test_dynamic_and_stale_devices(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """New access points appear, removed ones go, returning ones come back."""
    await _setup(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data
    ent_reg = er.async_get(hass)
    original_places = patch_client.async_get_places.return_value

    # Replace the data with a brand-new place/access point and drop the old one.
    new_ap = AccessPoint(
        id="ap-9",
        name="Side Door",
        is_online=True,
        can_share_access=True,
        place_id="place-9",
        place_name="Office",
    )
    new_place = Place(
        id="place-9",
        name="Office",
        can_manage=True,
        is_owner=True,
        access_points={"ap-9": new_ap},
    )
    patch_client.async_get_places.return_value = [new_place]
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # New entity added dynamically, new place device present.
    assert ent_reg.async_get_entity_id("button", DOMAIN, "ap-9_open") is not None
    assert _device(hass, mock_config_entry, "place_place-9") is not None
    # Stale place/access-point devices (and their entities) removed.
    assert _device(hass, mock_config_entry, "ap_ap-1") is None
    assert _device(hass, mock_config_entry, "place_place-1") is None
    assert ent_reg.async_get_entity_id("button", DOMAIN, "ap-1_open") is None

    # Access to the original door is granted again: its entities come back
    # without a reload.
    patch_client.async_get_places.return_value = original_places
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    button_id = ent_reg.async_get_entity_id("button", DOMAIN, "ap-1_open")
    assert button_id is not None
    assert hass.states.get(button_id) is not None
    assert hass.states.get(button_id).state != "unavailable"
    assert _device(hass, mock_config_entry, "ap_ap-1") is not None
    assert _device(hass, mock_config_entry, "ap_ap-9") is None


async def test_remove_config_entry_device(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Only devices that no longer exist may be removed while the entry is loaded."""
    await _setup(hass, mock_config_entry)
    door = _device(hass, mock_config_entry, "ap_ap-1")
    assert door is not None
    assert not await async_remove_config_entry_device(hass, mock_config_entry, door)

    stale = dr.async_get(hass).async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "ap_gone")},
        name="Gone",
    )
    assert await async_remove_config_entry_device(hass, mock_config_entry, stale)

    # Once unloaded there is nothing to compare against: allow the removal
    # instead of crashing on the missing runtime data.
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert await async_remove_config_entry_device(hass, mock_config_entry, door)


async def test_rotated_tokens_are_persisted(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The client's token updater writes the new pair into the config entry."""
    await _setup(hass, mock_config_entry)
    updater = integration.RebramaClient.call_args.kwargs["token_updater"]
    await updater("acc2", "ref2")
    assert mock_config_entry.data[CONF_ACCESS_TOKEN] == "acc2"
    assert mock_config_entry.data[CONF_REFRESH_TOKEN] == "ref2"
    # Nothing else was touched.
    assert mock_config_entry.data[CONF_PASSWORD] == "secret"


def test_access_point_entity_without_place() -> None:
    """An entity whose whole place vanished reports no access point."""
    coordinator = MagicMock()
    coordinator.data.places = {}
    entity = RebramaConnectivitySensor(
        coordinator,
        AccessPoint(
            id="ap-1",
            name="Front Gate",
            is_online=True,
            can_share_access=True,
            place_id="place-1",
            place_name="Home",
        ),
    )
    assert entity.access_point is None
    assert entity.available is False
