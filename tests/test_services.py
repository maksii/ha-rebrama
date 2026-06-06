"""Tests for the Rebrama service actions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rebrama.api import RebramaError
from custom_components.rebrama.const import (
    ATTR_ACCESS_POINTS,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_END,
    ATTR_LINK,
    ATTR_START,
    DOMAIN,
    SERVICE_CREATE_TEMPORARY_ACCESS,
    SERVICE_DELETE_TEMPORARY_ACCESS,
)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return er.async_get(hass).async_get_entity_id("button", DOMAIN, "ap-1_open")


async def test_create_temporary_access(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """create_temporary_access resolves the AP and returns the share link."""
    entity_id = await _setup(hass, mock_config_entry)
    start = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_CREATE_TEMPORARY_ACCESS,
        {
            ATTR_ACCESS_POINTS: [entity_id],
            ATTR_START: start.isoformat(),
            ATTR_END: end.isoformat(),
        },
        blocking=True,
        return_response=True,
    )
    assert response["link"] == "abc123"
    assert response["url"] == "https://rebrama.com/access/abc123"

    kwargs = patch_client.async_create_temporary_access.call_args.kwargs
    assert kwargs["access_point_ids"] == ["ap-1"]
    assert kwargs["date_start"] == int(start.timestamp())
    assert kwargs["date_end"] == int(end.timestamp())


async def test_create_temporary_access_bad_time_range(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An end before start is rejected."""
    entity_id = await _setup(hass, mock_config_entry)
    start = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CREATE_TEMPORARY_ACCESS,
            {
                ATTR_ACCESS_POINTS: [entity_id],
                ATTR_START: start.isoformat(),
                ATTR_END: (start - timedelta(hours=1)).isoformat(),
            },
            blocking=True,
            return_response=True,
        )


async def test_delete_temporary_access(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """delete_temporary_access extracts the slug and calls the client."""
    await _setup(hass, mock_config_entry)
    await hass.services.async_call(
        DOMAIN,
        SERVICE_DELETE_TEMPORARY_ACCESS,
        {
            ATTR_CONFIG_ENTRY_ID: mock_config_entry.entry_id,
            ATTR_LINK: "https://rebrama.com/access/xyz789",
        },
        blocking=True,
    )
    patch_client.async_delete_temporary_access.assert_awaited_once_with("xyz789")


async def test_create_invalid_access_point(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A non-Rebrama entity is rejected."""
    await _setup(hass, mock_config_entry)
    start = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CREATE_TEMPORARY_ACCESS,
            {
                ATTR_ACCESS_POINTS: ["button.not_a_rebrama_device"],
                ATTR_START: start.isoformat(),
                ATTR_END: (start + timedelta(hours=1)).isoformat(),
            },
            blocking=True,
            return_response=True,
        )


async def test_create_api_error(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An API error while creating temp access surfaces as a validation error."""
    entity_id = await _setup(hass, mock_config_entry)
    patch_client.async_create_temporary_access.side_effect = RebramaError("boom")
    start = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CREATE_TEMPORARY_ACCESS,
            {
                ATTR_ACCESS_POINTS: [entity_id],
                ATTR_START: start.isoformat(),
                ATTR_END: (start + timedelta(hours=1)).isoformat(),
            },
            blocking=True,
            return_response=True,
        )


async def test_delete_entry_not_found(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Deleting against an unknown config entry is rejected."""
    await _setup(hass, mock_config_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_TEMPORARY_ACCESS,
            {ATTR_CONFIG_ENTRY_ID: "does-not-exist", ATTR_LINK: "abc"},
            blocking=True,
        )
