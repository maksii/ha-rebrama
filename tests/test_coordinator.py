"""Tests for the Rebrama coordinator (intervals, resilience, polling)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rebrama.api import (
    RebramaAuthError,
    RebramaConnectionError,
    RebramaError,
)
from custom_components.rebrama.const import (
    CONF_FINGERPRINT,
    CONF_PHONE,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from custom_components.rebrama.models import (
    AccessPoint,
    OpenLog,
    Place,
    Profile,
    TempAccess,
)


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


def _temp_access(slug: str = "s") -> TempAccess:
    return TempAccess(
        slug=slug,
        url=f"https://rebrama.com/access/{slug}",
        description="d",
        date_start=None,
        date_end=None,
        uses_number=None,
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_interval_defaults_without_override(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """With no user override, the friendly default interval is used."""
    entry = _entry()
    await _setup(hass, entry)
    assert entry.runtime_data.update_interval == DEFAULT_SCAN_INTERVAL


async def test_interval_option_overrides(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """A user-set scan interval is used."""
    entry = _entry(options={CONF_SCAN_INTERVAL: 120})
    await _setup(hass, entry)
    assert entry.runtime_data.update_interval == timedelta(seconds=120)


async def test_interval_option_clamped(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """An out-of-range override is clamped to the supported minimum."""
    entry = _entry(options={CONF_SCAN_INTERVAL: 5})
    await _setup(hass, entry)
    assert entry.runtime_data.update_interval == timedelta(seconds=MIN_SCAN_INTERVAL)


async def test_settings_failure_does_not_break_setup(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """A settings-fetch failure does not break setup; the settings stay empty."""
    patch_client.async_get_settings.side_effect = RebramaError("nope")
    entry = _entry()
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.settings == {}


async def test_settings_auth_failure_triggers_reauth(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """An auth failure while fetching settings starts reauth right away."""
    patch_client.async_get_settings.side_effect = RebramaAuthError("dead")
    entry = _entry()
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)
    # No doomed follow-up calls were made.
    patch_client.async_get_places.assert_not_called()


async def test_update_failed_marks_unavailable(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """A later API error marks the coordinator update as failed."""
    entry = _entry()
    await _setup(hass, entry)

    coordinator = entry.runtime_data
    patch_client.async_get_places.side_effect = RebramaConnectionError("down")
    await coordinator.async_refresh()
    assert coordinator.last_update_success is False


async def test_secondary_fetch_auth_error_triggers_reauth(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """An auth error on a best-effort fetch is not swallowed: it starts reauth."""
    entry = _entry()
    await _setup(hass, entry)

    coordinator = entry.runtime_data
    patch_client.async_get_profile.side_effect = RebramaAuthError("dead")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success is False
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)


async def test_log_fetch_is_resilient(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """A failure fetching opening logs keeps the last known log."""
    log = OpenLog(
        created_at=datetime(2026, 6, 6, 10, tzinfo=UTC),
        user_phone="380",
        user_info="Me",
        access_point_name="Gate",
        is_temp_access=False,
    )
    patch_client.async_get_latest_open_log.return_value = log
    entry = _entry()
    await _setup(hass, entry)

    coordinator = entry.runtime_data
    assert coordinator.data.logs["place-1"] is log

    patch_client.async_get_latest_open_log.side_effect = RebramaError("logs down")
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.data.logs["place-1"] is log


async def test_temp_accesses_polled_and_carried_forward(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """Share links are re-fetched on every poll; a failure keeps the last list."""
    patch_client.async_list_temporary_accesses.return_value = [_temp_access()]
    entry = _entry()
    await _setup(hass, entry)

    coordinator = entry.runtime_data
    assert [a.slug for a in coordinator.data.temp_accesses] == ["s"]
    assert patch_client.async_list_temporary_accesses.call_count == 1

    # A link created in the mobile app shows up on the next poll.
    patch_client.async_list_temporary_accesses.return_value = [
        _temp_access("s"),
        _temp_access("app"),
    ]
    await coordinator.async_refresh()
    assert patch_client.async_list_temporary_accesses.call_count == 2
    assert [a.slug for a in coordinator.data.temp_accesses] == ["s", "app"]

    # A failure of just this endpoint keeps the last value and the update succeeds.
    patch_client.async_list_temporary_accesses.side_effect = RebramaError("down")
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert [a.slug for a in coordinator.data.temp_accesses] == ["s", "app"]


async def test_profile_polled_and_carried_forward(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """Subscription expiry follows the profile on every poll."""
    first = datetime(2027, 1, 1, tzinfo=UTC)
    patch_client.async_get_profile.return_value = Profile(
        "user-1", "380990000000", first
    )
    entry = _entry()
    await _setup(hass, entry)

    coordinator = entry.runtime_data
    assert coordinator.data.profile.valid_until == first

    renewed = datetime(2028, 1, 1, tzinfo=UTC)
    patch_client.async_get_profile.return_value = Profile(
        "user-1", "380990000000", renewed
    )
    await coordinator.async_refresh()
    assert coordinator.data.profile.valid_until == renewed

    patch_client.async_get_profile.side_effect = RebramaError("down")
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.data.profile.valid_until == renewed


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
    await _setup(hass, entry)

    patch_client.async_get_latest_open_log.assert_not_called()
    assert entry.runtime_data.data.logs["place-x"] is None
    ent_reg = er.async_get(hass)
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, "place-x_last_opened") is None


async def test_create_temporary_access_guards(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """Direct callers get a clear error for an empty selection."""
    entry = _entry()
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    now = dt_util.utcnow()

    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_create_temporary_access(
            [], start=now, end=now + timedelta(hours=1), description="x"
        )
    assert err.value.translation_key == "no_shareable_access_points"
    patch_client.async_create_temporary_access.assert_not_called()


async def test_create_temporary_access_survives_list_refresh_failure(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """A failing list refresh after a successful create does not fail the action."""
    entry = _entry()
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    now = dt_util.utcnow()

    patch_client.async_list_temporary_accesses.side_effect = RebramaError("down")
    url = await coordinator.async_create_temporary_access(
        ["ap-1"], start=now, end=now + timedelta(hours=1), description="x"
    )
    assert url == "https://rebrama.com/access/abc123"
    assert coordinator.data.temp_accesses == []
