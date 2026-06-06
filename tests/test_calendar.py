"""Tests for the Rebrama temporary-access calendar."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rebrama.calendar import RebramaTemporaryAccessCalendar
from custom_components.rebrama.coordinator import RebramaCoordinator
from custom_components.rebrama.models import AccessPoint, Place, TempAccess


def _temp_access() -> TempAccess:
    return TempAccess(
        slug="abc",
        url="https://rebrama.com/access/abc",
        description="Cleaner",
        date_start=datetime(2026, 6, 6, 12, tzinfo=UTC),
        date_end=datetime(2026, 6, 6, 13, tzinfo=UTC),
        uses_number=1,
    )


async def _coordinator(
    hass: HomeAssistant, entry: MockConfigEntry
) -> RebramaCoordinator:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def test_get_events_maps_temp_accesses(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Active temp accesses are returned as calendar events."""
    patch_client.async_list_temporary_accesses.return_value = [_temp_access()]
    coordinator = await _coordinator(hass, mock_config_entry)
    calendar = RebramaTemporaryAccessCalendar(coordinator)

    events = await calendar.async_get_events(
        hass,
        datetime(2026, 6, 6, 0, tzinfo=UTC),
        datetime(2026, 6, 7, 0, tzinfo=UTC),
    )
    assert len(events) == 1
    assert events[0].summary == "Cleaner"
    assert events[0].uid == "abc"
    # Outside the window -> nothing.
    assert not await calendar.async_get_events(
        hass,
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 2, tzinfo=UTC),
    )


async def test_create_event_grants_shareable_aps(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Creating an event makes a temp access for all shareable access points."""
    coordinator = await _coordinator(hass, mock_config_entry)
    calendar = RebramaTemporaryAccessCalendar(coordinator)

    start = datetime(2026, 6, 6, 12, tzinfo=UTC)
    await calendar.async_create_event(
        dtstart=start, dtend=start + timedelta(hours=1), summary="Guest"
    )

    kwargs = patch_client.async_create_temporary_access.call_args.kwargs
    # Only ap-1 is shareable in the fixture (ap-2 can_share_access=False).
    assert kwargs["access_point_ids"] == ["ap-1"]
    assert kwargs["description"] == "Guest"
    assert kwargs["uses_number"] is None


async def test_create_event_rejects_bad_range(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An end not after the start is rejected."""
    coordinator = await _coordinator(hass, mock_config_entry)
    calendar = RebramaTemporaryAccessCalendar(coordinator)

    start = datetime(2026, 6, 6, 12, tzinfo=UTC)
    with pytest.raises(HomeAssistantError):
        await calendar.async_create_event(dtstart=start, dtend=start)
    patch_client.async_create_temporary_access.assert_not_called()


async def test_create_event_no_shareable_aps(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """With no shareable access points, creation is rejected."""
    access_point = AccessPoint(
        id="ap-x",
        name="Gate",
        is_online=True,
        can_share_access=False,
        place_id="place-x",
        place_name="Shared",
    )
    patch_client.async_get_places.return_value = [
        Place(
            id="place-x",
            name="Shared",
            can_manage=False,
            is_owner=False,
            access_points={"ap-x": access_point},
        )
    ]
    coordinator = await _coordinator(hass, mock_config_entry)
    calendar = RebramaTemporaryAccessCalendar(coordinator)

    start = datetime(2026, 6, 6, 12, tzinfo=UTC)
    with pytest.raises(HomeAssistantError):
        await calendar.async_create_event(
            dtstart=start, dtend=start + timedelta(hours=1)
        )
    patch_client.async_create_temporary_access.assert_not_called()


async def test_event_property_prefers_ongoing(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The current event is reported in preference to an upcoming one."""
    now = dt_util.utcnow()
    ongoing = TempAccess(
        slug="now",
        url="https://rebrama.com/access/now",
        description="Now",
        date_start=now - timedelta(hours=1),
        date_end=now + timedelta(hours=1),
        uses_number=None,
    )
    upcoming = TempAccess(
        slug="later",
        url="https://rebrama.com/access/later",
        description="Later",
        date_start=now + timedelta(days=1),
        date_end=now + timedelta(days=2),
        uses_number=None,
    )
    patch_client.async_list_temporary_accesses.return_value = [upcoming, ongoing]
    coordinator = await _coordinator(hass, mock_config_entry)
    calendar = RebramaTemporaryAccessCalendar(coordinator)

    assert calendar.event is not None
    assert calendar.event.summary == "Now"


async def test_delete_event_calls_client(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Deleting an event revokes the temp access by its slug (the event uid)."""
    coordinator = await _coordinator(hass, mock_config_entry)
    calendar = RebramaTemporaryAccessCalendar(coordinator)

    await calendar.async_delete_event("abc")
    patch_client.async_delete_temporary_access.assert_awaited_once_with("abc")
