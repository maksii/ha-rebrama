"""Tests for Rebrama account-level sensors."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.rebrama.const import DOMAIN
from custom_components.rebrama.models import Profile, TempAccess


def _temp_access(slug: str, start: datetime, end: datetime) -> TempAccess:
    return TempAccess(
        slug=slug,
        url=f"https://rebrama.com/access/{slug}",
        description=slug.title(),
        date_start=start,
        date_end=end,
        uses_number=1,
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _entity_id(hass: HomeAssistant, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


async def test_account_sensors(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Subscription, access-points-online and temp-access sensors expose data."""
    patch_client.async_get_profile.return_value = Profile(
        "user-1", "380990000000", datetime(2027, 1, 1, tzinfo=UTC)
    )
    patch_client.async_list_temporary_accesses.return_value = [
        _temp_access(
            "cleaner",
            datetime(2026, 6, 6, 12, tzinfo=UTC),
            # Far-future end so the "active" filter keeps it deterministically.
            datetime(2099, 1, 1, tzinfo=UTC),
        )
    ]
    await _setup(hass, mock_config_entry)

    sub = hass.states.get(_entity_id(hass, "user-1_subscription_expires"))
    assert sub.state == "2027-01-01T00:00:00+00:00"

    online = hass.states.get(_entity_id(hass, "user-1_access_points_online"))
    # Fixture: ap-1 online, ap-2 (Garage) offline.
    assert online.state == "1"
    assert online.attributes["total"] == 2
    assert online.attributes["offline"] == ["Garage"]

    temp = hass.states.get(_entity_id(hass, "user-1_temporary_accesses"))
    assert temp.state == "1"
    accesses = temp.attributes["accesses"]
    assert accesses[0]["description"] == "Cleaner"
    assert accesses[0]["url"] == "https://rebrama.com/access/cleaner"
    assert accesses[0]["max_uses"] == 1


async def test_temporary_access_sensor_excludes_expired(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Expired links are dropped from the count locally."""
    patch_client.async_list_temporary_accesses.return_value = [
        _temp_access(
            "old", datetime(2020, 1, 1, tzinfo=UTC), datetime(2020, 1, 2, tzinfo=UTC)
        ),
        _temp_access(
            "new", datetime(2099, 1, 1, tzinfo=UTC), datetime(2099, 1, 2, tzinfo=UTC)
        ),
    ]
    await _setup(hass, mock_config_entry)

    temp = hass.states.get(_entity_id(hass, "user-1_temporary_accesses"))
    assert temp.state == "1"
    assert [a["description"] for a in temp.attributes["accesses"]] == ["New"]


async def test_temporary_access_sensor_updates_when_link_expires(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The count drops the moment a link expires, without any new data."""
    now = datetime(2026, 6, 6, 12, tzinfo=UTC)
    freezer.move_to(now)
    patch_client.async_list_temporary_accesses.return_value = [
        _temp_access("short", now - timedelta(hours=1), now + timedelta(hours=1)),
        _temp_access("long", now - timedelta(hours=1), now + timedelta(days=1)),
    ]
    await _setup(hass, mock_config_entry)
    entity_id = _entity_id(hass, "user-1_temporary_accesses")
    assert hass.states.get(entity_id).state == "2"

    freezer.tick(timedelta(hours=1, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "1"
    assert [
        a["description"] for a in hass.states.get(entity_id).attributes["accesses"]
    ] == ["Long"]

    freezer.tick(timedelta(days=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "0"


async def test_subscription_unknown_when_absent(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """With no validUntil the subscription sensor is unknown."""
    await _setup(hass, mock_config_entry)
    sub = hass.states.get(_entity_id(hass, "user-1_subscription_expires"))
    assert sub.state == "unknown"


async def test_subscription_follows_renewal(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A renewed subscription shows up after the next poll (no reload needed)."""
    patch_client.async_get_profile.return_value = Profile(
        "user-1", "380990000000", datetime(2027, 1, 1, tzinfo=UTC)
    )
    await _setup(hass, mock_config_entry)
    entity_id = _entity_id(hass, "user-1_subscription_expires")
    assert hass.states.get(entity_id).state == "2027-01-01T00:00:00+00:00"

    patch_client.async_get_profile.return_value = Profile(
        "user-1", "380990000000", datetime(2028, 1, 1, tzinfo=UTC)
    )
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "2028-01-01T00:00:00+00:00"
