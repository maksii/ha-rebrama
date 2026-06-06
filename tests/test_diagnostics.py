"""Tests for Rebrama diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.const import CONF_ACCESS_TOKEN, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rebrama.const import CONF_REFRESH_TOKEN
from custom_components.rebrama.diagnostics import async_get_config_entry_diagnostics


async def test_diagnostics_redacts_secrets(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Diagnostics include place data but redact secrets."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    data = diag["entry"]["data"]
    assert data[CONF_PASSWORD] == "**REDACTED**"
    assert data[CONF_ACCESS_TOKEN] == "**REDACTED**"
    assert data[CONF_REFRESH_TOKEN] == "**REDACTED**"

    places = diag["places"]
    assert places[0]["name"] == "Home"
    assert {ap["id"] for ap in places[0]["access_points"]} == {"ap-1", "ap-2"}
