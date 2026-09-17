"""Tests for the Rebrama service actions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rebrama.api import (
    RebramaApiError,
    RebramaAuthError,
    RebramaConnectionError,
)
from custom_components.rebrama.const import (
    ATTR_ACCESS_POINTS,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_END,
    ATTR_LINK,
    ATTR_START,
    ATTR_USES,
    CONF_PHONE,
    CONF_USER_ID,
    DOMAIN,
    SERVICE_CREATE_TEMPORARY_ACCESS,
    SERVICE_DELETE_TEMPORARY_ACCESS,
)
from custom_components.rebrama.models import AccessPoint, Place, Profile
from custom_components.rebrama.services import async_setup_services

NOW = datetime(2026, 6, 6, 10, tzinfo=UTC)
START = NOW + timedelta(hours=2)
END = START + timedelta(hours=1)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, unique_id: str) -> str:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id("button", DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


async def _create(hass: HomeAssistant, data: dict) -> dict:
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_CREATE_TEMPORARY_ACCESS,
        data,
        blocking=True,
        return_response=True,
    )


async def test_create_temporary_access(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """create_temporary_access resolves the AP and returns the share link."""
    freezer.move_to(NOW)
    entity_id = await _setup(hass, mock_config_entry, "ap-1_open")

    response = await _create(
        hass,
        {
            ATTR_ACCESS_POINTS: [entity_id],
            ATTR_START: START.isoformat(),
            ATTR_END: END.isoformat(),
            ATTR_USES: 2,
        },
    )
    assert response["link"] == "abc123"
    assert response["url"] == "https://rebrama.com/access/abc123"

    kwargs = patch_client.async_create_temporary_access.call_args.kwargs
    assert kwargs["access_point_ids"] == ["ap-1"]
    assert kwargs["date_start"] == int(START.timestamp())
    assert kwargs["date_end"] == int(END.timestamp())
    assert kwargs["description"] == "Home Assistant"
    assert kwargs["uses_number"] == 2


async def test_create_temporary_access_bad_time_range(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An end before start is rejected."""
    freezer.move_to(NOW)
    entity_id = await _setup(hass, mock_config_entry, "ap-1_open")

    with pytest.raises(ServiceValidationError):
        await _create(
            hass,
            {
                ATTR_ACCESS_POINTS: [entity_id],
                ATTR_START: END.isoformat(),
                ATTR_END: START.isoformat(),
            },
        )
    patch_client.async_create_temporary_access.assert_not_called()


async def test_create_temporary_access_not_shareable(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An access point that cannot be shared is refused before calling the API."""
    freezer.move_to(NOW)
    # ap-2 (Garage) has can_share_access=False in the fixture.
    entity_id = await _setup(hass, mock_config_entry, "ap-2_open")

    with pytest.raises(ServiceValidationError) as err:
        await _create(
            hass,
            {
                ATTR_ACCESS_POINTS: [entity_id],
                ATTR_START: START.isoformat(),
                ATTR_END: END.isoformat(),
            },
        )
    assert err.value.translation_key == "access_point_not_shareable"
    assert err.value.translation_placeholders == {"name": "Garage"}
    patch_client.async_create_temporary_access.assert_not_called()


async def test_delete_temporary_access(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """delete_temporary_access extracts the slug and calls the client."""
    await _setup(hass, mock_config_entry, "ap-1_open")
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
    await _setup(hass, mock_config_entry, "ap-1_open")
    with pytest.raises(ServiceValidationError):
        await _create(
            hass,
            {
                ATTR_ACCESS_POINTS: ["button.not_a_rebrama_device"],
                ATTR_START: START.isoformat(),
                ATTR_END: END.isoformat(),
            },
        )


async def test_create_errors(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Server rejections are validation errors; other failures are not."""
    freezer.move_to(NOW)
    entity_id = await _setup(hass, mock_config_entry, "ap-1_open")
    data = {
        ATTR_ACCESS_POINTS: [entity_id],
        ATTR_START: START.isoformat(),
        ATTR_END: END.isoformat(),
    }

    patch_client.async_create_temporary_access.side_effect = RebramaApiError(
        1504, "Start date less than current date"
    )
    with pytest.raises(ServiceValidationError) as err:
        await _create(hass, data)
    assert err.value.translation_key == "rejected"

    patch_client.async_create_temporary_access.side_effect = RebramaConnectionError(
        "boom"
    )
    with pytest.raises(HomeAssistantError) as err:
        await _create(hass, data)
    assert not isinstance(err.value, ServiceValidationError)
    assert err.value.translation_key == "service_failed"

    patch_client.async_create_temporary_access.side_effect = RebramaAuthError("dead")
    with pytest.raises(HomeAssistantError) as err:
        await _create(hass, data)
    assert err.value.translation_key == "auth_failed"
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)


async def test_delete_entry_not_found(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Deleting against an unknown config entry is rejected."""
    await _setup(hass, mock_config_entry, "ap-1_open")
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_TEMPORARY_ACCESS,
            {ATTR_CONFIG_ENTRY_ID: "does-not-exist", ATTR_LINK: "abc"},
            blocking=True,
        )


async def test_delete_entry_not_loaded(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An unloaded (but existing) account is reported as such."""
    await _setup(hass, mock_config_entry, "ap-1_open")
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DELETE_TEMPORARY_ACCESS,
            {ATTR_CONFIG_ENTRY_ID: mock_config_entry.entry_id, ATTR_LINK: "abc"},
            blocking=True,
        )
    assert err.value.translation_key == "entry_not_loaded"


async def test_create_across_accounts_is_rejected(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Access points from two Rebrama accounts cannot share one link."""
    freezer.move_to(NOW)
    first = await _setup(hass, mock_config_entry, "ap-1_open")

    other_ap = AccessPoint(
        id="ap-7",
        name="Office Door",
        is_online=True,
        can_share_access=True,
        place_id="place-7",
        place_name="Office",
    )
    patch_client.async_get_places.return_value = [
        Place(
            id="place-7",
            name="Office",
            can_manage=True,
            is_owner=True,
            access_points={"ap-7": other_ap},
        )
    ]
    patch_client.async_get_profile.return_value = Profile("user-2", "380990000002")
    other_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user-2",
        title="380990000002",
        data={
            **mock_config_entry.data,
            CONF_PHONE: "380990000002",
            CONF_USER_ID: "user-2",
        },
    )
    second = await _setup(hass, other_entry, "ap-7_open")

    with pytest.raises(ServiceValidationError) as err:
        await _create(
            hass,
            {
                ATTR_ACCESS_POINTS: [first, second],
                ATTR_START: START.isoformat(),
                ATTR_END: END.isoformat(),
            },
        )
    assert err.value.translation_key == "single_account_required"


async def test_services_registered_once(hass: HomeAssistant) -> None:
    """Registering twice is a no-op."""
    async_setup_services(hass)
    async_setup_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_CREATE_TEMPORARY_ACCESS)
    assert hass.services.has_service(DOMAIN, SERVICE_DELETE_TEMPORARY_ACCESS)
