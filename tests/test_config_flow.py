"""Tests for the Rebrama config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rebrama.api import (
    RebramaApiError,
    RebramaAuthError,
    RebramaConnectionError,
    RebramaError,
)
from custom_components.rebrama.const import (
    CONF_PHONE,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DOMAIN,
)
from custom_components.rebrama.models import AuthTokens, Profile


async def _submit_user(hass: HomeAssistant, phone: str, password: str):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PHONE: phone, CONF_PASSWORD: password}
    )


async def test_user_flow_success(
    hass: HomeAssistant, flow_client: MagicMock, mock_setup_entry: AsyncMock
) -> None:
    """A valid phone/password creates an entry with normalized phone + tokens."""
    result = await _submit_user(hass, "+380 00 000 00 00", "secret")
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "380990000000"
    assert result["data"][CONF_PHONE] == "380990000000"
    assert result["data"][CONF_ACCESS_TOKEN] == "acc"
    assert result["data"][CONF_REFRESH_TOKEN] == "ref"
    assert result["data"][CONF_USER_ID] == "user-1"
    assert result["result"].unique_id == "user-1"
    assert len(mock_setup_entry.mock_calls) == 1


async def test_user_flow_invalid_phone(
    hass: HomeAssistant, flow_client: MagicMock
) -> None:
    """A too-short phone number is rejected before any network call."""
    result = await _submit_user(hass, "12345", "secret")
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_phone"}
    flow_client.async_login.assert_not_called()


async def test_user_flow_invalid_auth(
    hass: HomeAssistant, flow_client: MagicMock
) -> None:
    """Wrong password (code 1203) surfaces as invalid_auth."""
    flow_client.async_login.side_effect = RebramaApiError(
        1203, "Wrong user credentials"
    )
    result = await _submit_user(hass, "380990000000", "wrong")
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_account_not_found(
    hass: HomeAssistant, flow_client: MagicMock
) -> None:
    """An unregistered phone (code 1201) is reported clearly."""
    flow_client.async_login.side_effect = RebramaApiError(
        1201, "Phone must be a valid phone number"
    )
    result = await _submit_user(hass, "380990000000", "secret")
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "account_not_found"}


async def test_user_flow_already_configured(
    hass: HomeAssistant,
    flow_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The same account cannot be added twice."""
    mock_config_entry.add_to_hass(hass)
    result = await _submit_user(hass, "380990000000", "secret")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_success(
    hass: HomeAssistant,
    flow_client: MagicMock,
    mock_setup_entry: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Reauth updates the stored password and tokens."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    flow_client.async_login.return_value = AuthTokens("acc2", "ref2")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "newpass"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_PASSWORD] == "newpass"
    assert mock_config_entry.data[CONF_ACCESS_TOKEN] == "acc2"


async def test_reauth_wrong_account(
    hass: HomeAssistant,
    flow_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Reauth into a different account is blocked."""
    mock_config_entry.add_to_hass(hass)
    flow_client.async_get_profile.return_value = Profile("other-user", "380990000000")
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "newpass"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"


async def test_reconfigure_success(
    hass: HomeAssistant,
    flow_client: MagicMock,
    mock_setup_entry: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Reconfigure updates credentials for the same account."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PHONE: "380990000000", CONF_PASSWORD: "newpass"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_PASSWORD] == "newpass"


async def test_options_flow_scan_interval(
    hass: HomeAssistant,
    patch_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The options flow stores a custom scan interval."""
    from homeassistant.const import CONF_SCAN_INTERVAL

    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 120}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options[CONF_SCAN_INTERVAL] == 120


async def test_user_flow_login_cannot_connect(
    hass: HomeAssistant, flow_client: MagicMock
) -> None:
    """A connection error during login surfaces as cannot_connect."""
    flow_client.async_login.side_effect = RebramaConnectionError
    result = await _submit_user(hass, "380990000000", "secret")
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_unknown_error(
    hass: HomeAssistant, flow_client: MagicMock
) -> None:
    """An unexpected error surfaces as unknown."""
    flow_client.async_login.side_effect = RebramaError("boom")
    result = await _submit_user(hass, "380990000000", "secret")
    assert result["errors"] == {"base": "unknown"}


async def test_user_flow_server_invalid_phone(
    hass: HomeAssistant, flow_client: MagicMock
) -> None:
    """A server-side phone-format error (1210) surfaces as invalid_phone."""
    flow_client.async_login.side_effect = RebramaApiError(1210, "bad phone")
    result = await _submit_user(hass, "380990000000", "secret")
    assert result["errors"] == {"base": "invalid_phone"}


async def test_user_flow_api_unknown_code(
    hass: HomeAssistant, flow_client: MagicMock
) -> None:
    """An unmapped API error code surfaces as unknown."""
    flow_client.async_login.side_effect = RebramaApiError(9999, "")
    result = await _submit_user(hass, "380990000000", "secret")
    assert result["errors"] == {"base": "unknown"}


async def test_reauth_invalid_auth(
    hass: HomeAssistant, flow_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A wrong password during reauth keeps the form open with an error."""
    mock_config_entry.add_to_hass(hass)
    flow_client.async_login.side_effect = RebramaAuthError
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reconfigure_invalid_phone(
    hass: HomeAssistant, flow_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Reconfigure rejects an invalid phone number."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PHONE: "123", CONF_PASSWORD: "x"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_phone"}


async def test_reconfigure_wrong_account(
    hass: HomeAssistant, flow_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Reconfigure into a different account is blocked."""
    mock_config_entry.add_to_hass(hass)
    flow_client.async_get_profile.return_value = Profile("other", "380990000000")
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PHONE: "380990000000", CONF_PASSWORD: "x"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
