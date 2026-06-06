"""Tests for Rebrama account-level sensors."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rebrama.const import DOMAIN
from custom_components.rebrama.models import Profile, TempAccess


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
        TempAccess(
            slug="abc",
            url="https://rebrama.com/access/abc",
            description="Cleaner",
            date_start=datetime(2026, 6, 6, 12, tzinfo=UTC),
            # Far-future end so the "active" filter keeps it deterministically.
            date_end=datetime(2099, 1, 1, tzinfo=UTC),
            uses_number=1,
        )
    ]

    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    ent_reg = er.async_get(hass)

    sub_id = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, "user-1_subscription_expires"
    )
    assert hass.states.get(sub_id).state == "2027-01-01T00:00:00+00:00"

    online_id = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, "user-1_access_points_online"
    )
    online = hass.states.get(online_id)
    # Fixture: ap-1 online, ap-2 (Garage) offline.
    assert online.state == "1"
    assert online.attributes["total"] == 2
    assert online.attributes["offline"] == ["Garage"]

    temp_id = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, "user-1_temporary_accesses"
    )
    temp = hass.states.get(temp_id)
    assert temp.state == "1"
    accesses = temp.attributes["accesses"]
    assert accesses[0]["description"] == "Cleaner"
    assert accesses[0]["url"] == "https://rebrama.com/access/abc"
    assert accesses[0]["max_uses"] == 1


async def test_temporary_access_sensor_excludes_expired(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Expired links are dropped from the count locally (no re-fetch needed)."""
    patch_client.async_list_temporary_accesses.return_value = [
        TempAccess(
            slug="past",
            url="https://rebrama.com/access/past",
            description="Old",
            date_start=datetime(2020, 1, 1, tzinfo=UTC),
            date_end=datetime(2020, 1, 2, tzinfo=UTC),
            uses_number=None,
        ),
        TempAccess(
            slug="future",
            url="https://rebrama.com/access/future",
            description="New",
            date_start=datetime(2099, 1, 1, tzinfo=UTC),
            date_end=datetime(2099, 1, 2, tzinfo=UTC),
            uses_number=None,
        ),
    ]

    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    temp_id = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, "user-1_temporary_accesses"
    )
    temp = hass.states.get(temp_id)
    assert temp.state == "1"
    assert [a["description"] for a in temp.attributes["accesses"]] == ["New"]


async def test_subscription_unknown_when_absent(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """With no validUntil the subscription sensor is unknown."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    sub_id = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, "user-1_subscription_expires"
    )
    assert hass.states.get(sub_id).state == "unknown"
