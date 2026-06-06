"""Tests for the Rebrama coordinator (intervals, resilience)."""

from __future__ import annotations

from datetime import timedelta
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
)
from custom_components.rebrama.models import AccessPoint, Place


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


async def test_interval_from_server_hint(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """widgetUpdatePeriod (ms) is converted to seconds and used."""
    patch_client.async_get_settings.return_value = {"widgetUpdatePeriod": 120000}
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.update_interval == timedelta(seconds=120)


async def test_interval_option_overrides(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """A user-set scan interval wins over the server hint."""
    patch_client.async_get_settings.return_value = {"widgetUpdatePeriod": 120000}
    entry = _entry(options={CONF_SCAN_INTERVAL: 45})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.update_interval == timedelta(seconds=45)


async def test_interval_default_when_settings_fail(
    hass: HomeAssistant, patch_client: MagicMock
) -> None:
    """If settings can't be fetched, fall back to the default interval."""
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
