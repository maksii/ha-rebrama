"""Tests for the Rebrama coordinator (intervals, resilience)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from homeassistant.const import CONF_ACCESS_TOKEN, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rebrama.api import RebramaConnectionError, RebramaError
from custom_components.rebrama.const import (
    CONF_FINGERPRINT,
    CONF_PHONE,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from custom_components.rebrama.models import AccessPoint, Place, Profile, TempAccess


def _entry(**kwargs) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="user-1",
        data={
            CONF_PHONE: "380990000000",
            CONF_PASSWORD: "secret",
            CONF_ACCESS_TOKEN: "acc",
            CONF_REFRESH_TOKEN: "ref",
            CONF_FINGERPRINT: "fp-1",
            CONF_USER_ID: "user-1",
        },
        **kwargs,
    )


async def test_interval_defaults_without_override(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """With no user override, the friendly default interval is used."""
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.update_interval == DEFAULT_SCAN_INTERVAL


async def test_interval_option_overrides(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """A user-set scan interval is used."""
    entry = _entry(options={CONF_SCAN_INTERVAL: 120})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.update_interval == timedelta(seconds=120)


async def test_interval_option_clamped(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """An out-of-range override is clamped to the supported minimum."""
    entry = _entry(options={CONF_SCAN_INTERVAL: 5})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.update_interval == timedelta(seconds=MIN_SCAN_INTERVAL)


async def test_interval_default_when_settings_fail(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """A settings-fetch failure does not break setup; the default is used."""
    patch_client.async_get_settings.side_effect = RebramaError("nope")
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.update_interval == DEFAULT_SCAN_INTERVAL


async def test_update_failed_marks_unavailable(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """A later API error marks the coordinator update as failed."""
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    patch_client.async_get_places.side_effect = RebramaConnectionError("down")
    await coordinator.async_refresh()
    assert coordinator.last_update_success is False


async def test_log_fetch_is_resilient(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """A failure fetching opening logs does not fail the whole update."""
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    patch_client.async_get_latest_open_log.side_effect = RebramaError("logs down")
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.data.logs["place-1"] is None


async def test_temp_accesses_fetched_once_not_per_poll(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """Temp accesses load once, carry forward, and refresh only on demand."""
    patch_client.async_list_temporary_accesses.return_value = [
        TempAccess(
            slug="s",
            url="https://rebrama.com/access/s",
            description="d",
            date_start=None,
            date_end=None,
            uses_number=None,
        )
    ]
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    assert len(coordinator.data.temp_accesses) == 1
    assert patch_client.async_list_temporary_accesses.call_count == 1

    # A normal poll does NOT re-fetch the list (it is carried forward).
    await coordinator.async_refresh()
    assert patch_client.async_list_temporary_accesses.call_count == 1
    assert len(coordinator.data.temp_accesses) == 1

    # An explicit refresh (after create/delete) does re-fetch.
    await coordinator.async_refresh_temp_accesses()
    assert patch_client.async_list_temporary_accesses.call_count == 2

    # And it is resilient: a failure keeps the last value.
    patch_client.async_list_temporary_accesses.side_effect = RebramaError("down")
    await coordinator.async_refresh_temp_accesses()
    assert coordinator.last_update_success is True
    assert len(coordinator.data.temp_accesses) == 1


async def test_profile_fetched_at_setup_not_per_poll(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """Subscription expiry is fetched once at setup, not on every poll."""
    valid_until = datetime(2027, 1, 1, tzinfo=UTC)
    patch_client.async_get_profile.return_value = Profile(
        "user-1", "380990000000", valid_until
    )
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    assert coordinator.profile.valid_until == valid_until
    assert patch_client.async_get_profile.call_count == 1

    # A normal poll does NOT re-fetch the profile.
    await coordinator.async_refresh()
    assert patch_client.async_get_profile.call_count == 1


async def test_non_manage_place_skips_logs(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """Places the user cannot manage skip the (forbidden) open-logs call."""
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
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    patch_client.async_get_latest_open_log.assert_not_called()
    assert entry.runtime_data.data.logs["place-x"] is None
    ent_reg = er.async_get(hass)
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, "place-x_last_opened") is None
