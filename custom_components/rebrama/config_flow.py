"""Config flow for the Rebrama integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
import re
from typing import Any
from uuid import uuid4

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import (
    RebramaApiError,
    RebramaAuthError,
    RebramaClient,
    RebramaConnectionError,
    RebramaError,
)
from .const import (
    CONF_FINGERPRINT,
    CONF_PHONE,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DOMAIN,
    ERROR_PHONE_FORMAT,
    ERROR_PHONE_INVALID,
    ERROR_PHONE_MIN_LENGTH,
    ERROR_WRONG_CREDENTIALS,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    PHONE_MIN_DIGITS,
)
from .models import AuthTokens

_LOGGER = logging.getLogger(__name__)

_PHONE_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.TEL))
_PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PHONE): _PHONE_SELECTOR,
        vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR,
    }
)
STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR})


def normalize_phone(raw: str) -> str:
    """Reduce a user-entered phone number to digits only (no '+', spaces ...)."""
    return re.sub(r"\D", "", raw or "")


@dataclass
class _Validated:
    """Result of validating credentials against the API."""

    tokens: AuthTokens
    user_id: str
    phone: str
    fingerprint: str


class RebramaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Rebrama config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._reauth_phone: str | None = None

    async def _async_validate(
        self, phone: str, password: str, fingerprint: str | None = None
    ) -> tuple[_Validated | None, str | None]:
        """Validate credentials. Returns ``(result, error_key)``."""
        session = async_get_clientsession(self.hass)
        fingerprint = fingerprint or f"hass-{uuid4()}"
        client = RebramaClient(
            session, fingerprint=fingerprint, phone=phone, password=password
        )

        try:
            tokens = await client.async_login()
            profile = await client.async_get_profile()
        except RebramaApiError as err:
            # The server cannot distinguish an unregistered phone from a
            # malformed one (both return 1201); after normalization we treat it
            # as "no account for this number".
            if err.code in (ERROR_PHONE_MIN_LENGTH, ERROR_PHONE_FORMAT):
                return None, "invalid_phone"
            if err.code == ERROR_PHONE_INVALID:
                return None, "account_not_found"
            if err.code == ERROR_WRONG_CREDENTIALS:
                return None, "invalid_auth"
            _LOGGER.error("Unexpected Rebrama API error %s: %s", err.code, err.message)
            return None, "unknown"
        except RebramaAuthError:
            return None, "invalid_auth"
        except RebramaConnectionError:
            return None, "cannot_connect"
        except RebramaError:
            _LOGGER.exception("Unexpected error validating Rebrama credentials")
            return None, "unknown"

        return (
            _Validated(
                tokens=tokens,
                user_id=profile.user_id,
                phone=profile.phone or phone,
                fingerprint=fingerprint,
            ),
            None,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial user step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            phone = normalize_phone(user_input[CONF_PHONE])
            if len(phone) < PHONE_MIN_DIGITS:
                errors["base"] = "invalid_phone"
            else:
                validated, error = await self._async_validate(
                    phone, user_input[CONF_PASSWORD]
                )
                if error:
                    errors["base"] = error
                else:
                    assert validated is not None
                    await self.async_set_unique_id(validated.user_id)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=phone,
                        data={
                            CONF_PHONE: phone,
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                            CONF_ACCESS_TOKEN: validated.tokens.access,
                            CONF_REFRESH_TOKEN: validated.tokens.refresh,
                            CONF_FINGERPRINT: validated.fingerprint,
                            CONF_USER_ID: validated.user_id,
                        },
                    )
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after the stored credentials stopped working."""
        self._reauth_phone = entry_data[CONF_PHONE]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again and update the entry."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            assert self._reauth_phone is not None
            validated, error = await self._async_validate(
                self._reauth_phone,
                user_input[CONF_PASSWORD],
                reauth_entry.data.get(CONF_FINGERPRINT),
            )
            if error:
                errors["base"] = error
            else:
                assert validated is not None
                await self.async_set_unique_id(validated.user_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_ACCESS_TOKEN: validated.tokens.access,
                        CONF_REFRESH_TOKEN: validated.tokens.refresh,
                    },
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            description_placeholders={CONF_PHONE: self._reauth_phone or ""},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user change the phone/password of an existing account."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()
        if user_input is not None:
            phone = normalize_phone(user_input[CONF_PHONE])
            if len(phone) < PHONE_MIN_DIGITS:
                errors["base"] = "invalid_phone"
            else:
                validated, error = await self._async_validate(
                    phone,
                    user_input[CONF_PASSWORD],
                    reconfigure_entry.data.get(CONF_FINGERPRINT),
                )
                if error:
                    errors["base"] = error
                else:
                    assert validated is not None
                    await self.async_set_unique_id(validated.user_id)
                    self._abort_if_unique_id_mismatch(reason="wrong_account")
                    return self.async_update_reload_and_abort(
                        reconfigure_entry,
                        data_updates={
                            CONF_PHONE: phone,
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                            CONF_ACCESS_TOKEN: validated.tokens.access,
                            CONF_REFRESH_TOKEN: validated.tokens.refresh,
                        },
                    )

        default_phone = reconfigure_entry.data.get(CONF_PHONE, "")
        if user_input is not None:
            default_phone = user_input.get(CONF_PHONE, default_phone)
        schema = vol.Schema(
            {
                vol.Required(CONF_PHONE, default=default_phone): _PHONE_SELECTOR,
                vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR,
            }
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> RebramaOptionsFlow:
        """Create the options flow."""
        return RebramaOptionsFlow()


class RebramaOptionsFlow(OptionsFlowWithReload):
    """Handle Rebrama options (polling interval)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            data: dict[str, Any] = {}
            interval = user_input.get(CONF_SCAN_INTERVAL)
            if interval:
                data[CONF_SCAN_INTERVAL] = int(interval)
            return self.async_create_entry(data=data)

        schema = vol.Schema(
            {
                vol.Optional(CONF_SCAN_INTERVAL): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=5,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, self.config_entry.options
            ),
        )
