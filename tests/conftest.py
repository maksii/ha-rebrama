"""Common fixtures for the Rebrama tests."""

from __future__ import annotations

from collections.abc import Generator
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if sys.platform == "win32":
    # Home Assistant's test harness blocks all sockets, but on Windows asyncio's
    # event-loop self-pipe is backed by a loopback socket. Neutralize the block
    # for local Windows development only; Linux CI keeps the strict default.
    import pytest_socket

    pytest_socket.disable_socket = lambda *args, **kwargs: None

from homeassistant.const import CONF_ACCESS_TOKEN, CONF_PASSWORD
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rebrama.const import (
    CONF_FINGERPRINT,
    CONF_PHONE,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DOMAIN,
)
from custom_components.rebrama.models import AccessPoint, AuthTokens, Place, Profile


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Enable loading of the custom integration in every test."""
    yield


def build_places() -> list[Place]:
    """Return a representative set of places/access points."""
    front = AccessPoint(
        id="ap-1",
        name="Front Gate",
        is_online=True,
        can_share_access=True,
        place_id="place-1",
        place_name="Home",
    )
    garage = AccessPoint(
        id="ap-2",
        name="Garage",
        is_online=False,
        can_share_access=False,
        place_id="place-1",
        place_name="Home",
    )
    return [
        Place(
            id="place-1",
            name="Home",
            can_manage=True,
            is_owner=True,
            access_points={"ap-1": front, "ap-2": garage},
        )
    ]


@pytest.fixture
def sample_places() -> list[Place]:
    """Provide sample places."""
    return build_places()


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a configured Rebrama config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="user-1",
        title="380990000000",
        data={
            CONF_PHONE: "380990000000",
            CONF_PASSWORD: "secret",
            CONF_ACCESS_TOKEN: "acc",
            CONF_REFRESH_TOKEN: "ref",
            CONF_FINGERPRINT: "fp-1",
            CONF_USER_ID: "user-1",
        },
    )


@pytest.fixture
def mock_client(sample_places: list[Place]) -> MagicMock:
    """Return a fully-stubbed RebramaClient."""
    client = MagicMock()
    client.async_login = AsyncMock(return_value=AuthTokens("acc", "ref"))
    client.async_get_profile = AsyncMock(return_value=Profile("user-1", "380990000000"))
    client.async_get_settings = AsyncMock(return_value={"widgetUpdatePeriod": 60000})
    client.async_get_places = AsyncMock(return_value=sample_places)
    client.async_get_latest_open_log = AsyncMock(return_value=None)
    client.async_open = AsyncMock(return_value=True)
    client.async_list_temporary_accesses = AsyncMock(return_value=[])
    client.async_create_temporary_access = AsyncMock(
        return_value={"tempAccessLink": "https://rebrama.com/access/abc123"}
    )
    client.async_delete_temporary_access = AsyncMock(return_value=None)
    return client


@pytest.fixture
def flow_client(mock_client: MagicMock) -> Generator[MagicMock]:
    """Patch RebramaClient used by the config flow."""
    with patch(
        "custom_components.rebrama.config_flow.RebramaClient",
        return_value=mock_client,
    ):
        yield mock_client


@pytest.fixture
def patch_client(mock_client: MagicMock) -> Generator[MagicMock]:
    """Patch RebramaClient in both the integration and the config flow."""
    with (
        patch("custom_components.rebrama.RebramaClient", return_value=mock_client),
        patch(
            "custom_components.rebrama.config_flow.RebramaClient",
            return_value=mock_client,
        ),
    ):
        yield mock_client


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Patch async_setup_entry so config-flow tests don't run a real setup."""
    with patch(
        "custom_components.rebrama.async_setup_entry", return_value=True
    ) as mock:
        yield mock
