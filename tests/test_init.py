"""Tests for the Rebrama integration setup/teardown and dynamic devices."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rebrama.api import RebramaAuthError
from custom_components.rebrama.const import DOMAIN, account_device_id
from custom_components.rebrama.models import AccessPoint, Place


async def test_setup_and_unload(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The entry loads, creates devices/entities, and unloads cleanly."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED

    ent_reg = er.async_get(hass)
    button_id = ent_reg.async_get_entity_id("button", DOMAIN, "ap-1_open")
    assert button_id is not None
    assert hass.states.get(button_id) is not None

    # Account + place + access-point devices exist.
    dev_reg = dr.async_get(hass)
    assert dev_reg.async_get_device({(DOMAIN, account_device_id("user-1"))})
    assert dev_reg.async_get_device({(DOMAIN, "place_place-1")})
    assert dev_reg.async_get_device({(DOMAIN, "ap_ap-1")})

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
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    # ap-2 (Garage) is offline in the fixture.
    garage = ent_reg.async_get_entity_id("button", DOMAIN, "ap-2_open")
    assert hass.states.get(garage).state == "unavailable"


async def test_dynamic_and_stale_devices(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """New access points appear and removed ones are cleaned up on refresh."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

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

    # New entity added dynamically.
    assert ent_reg.async_get_entity_id("button", DOMAIN, "ap-9_open") is not None
    # Stale place/access-point devices removed.
    assert dev_reg.async_get_device({(DOMAIN, "ap_ap-1")}) is None
    assert dev_reg.async_get_device({(DOMAIN, "place_place-1")}) is None
    # New place device present.
    assert dev_reg.async_get_device({(DOMAIN, "place_place-9")}) is not None
