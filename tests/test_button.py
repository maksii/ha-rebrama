"""Tests for the Rebrama open button."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rebrama.api import RebramaError
from custom_components.rebrama.const import DOMAIN


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return er.async_get(hass).async_get_entity_id("button", DOMAIN, "ap-1_open")


async def test_press_opens_access_point(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Pressing the button calls the open endpoint with the AP id."""
    entity_id = await _setup(hass, mock_config_entry)
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    patch_client.async_open.assert_awaited_once_with("ap-1")


async def test_press_not_delivered_raises(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A not-delivered open surfaces as an error to the user."""
    patch_client.async_open.return_value = False
    entity_id = await _setup(hass, mock_config_entry)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
        )


async def test_press_api_error_raises(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An API error while opening surfaces as an error."""
    patch_client.async_open.side_effect = RebramaError("boom")
    entity_id = await _setup(hass, mock_config_entry)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
        )
