"""Unit tests for the Rebrama API client (auth/refresh/retry logic)."""

from __future__ import annotations

import base64
from collections.abc import Callable
import json
import time
from typing import Any

import pytest

from custom_components.rebrama.api import (
    RebramaApiError,
    RebramaAuthError,
    RebramaClient,
    RebramaConnectionError,
)

Router = Callable[[str, str, Any, dict[str, str]], tuple[int, Any]]


class _FakeResponse:
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self._payload = payload

    async def json(self, content_type: Any = None) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeContext:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return self._response

    async def __aexit__(self, *args: object) -> bool:
        return False


class FakeSession:
    """Minimal stand-in for aiohttp.ClientSession.request."""

    def __init__(self, router: Router) -> None:
        self._router = router
        self.requests: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
        params: Any = None,
        timeout: Any = None,
    ) -> _FakeContext:
        headers = headers or {}
        self.requests.append(
            {"method": method, "url": url, "headers": headers, "json": json}
        )
        status, payload = self._router(method, url, json, headers)
        return _FakeContext(_FakeResponse(status, payload))


def make_jwt(exp: float) -> str:
    """Build a fake JWT carrying only an ``exp`` claim."""
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=")
    return f"header.{payload.decode()}.sig"


async def test_get_places_parses_envelope() -> None:
    """A normal authenticated GET returns parsed models."""

    def router(method, url, body, headers):
        assert headers["Authorization"] == "Bearer acc"
        assert headers["App-Build-Number"] == "16"
        return 200, {
            "data": [
                {
                    "id": "p1",
                    "name": "Home",
                    "canManage": True,
                    "isOwner": True,
                    "accessPoints": [
                        {
                            "id": "a1",
                            "name": "Gate",
                            "isOnline": True,
                            "canShareAccess": True,
                        }
                    ],
                }
            ]
        }

    client = RebramaClient(
        FakeSession(router), fingerprint="fp", access="acc", refresh="ref"
    )
    places = await client.async_get_places()
    assert len(places) == 1
    assert places[0].access_points["a1"].name == "Gate"
    assert places[0].access_points["a1"].is_online is True


async def test_reactive_refresh_on_auth_error() -> None:
    """A 400/1100 auth error triggers a refresh + single retry, persisting tokens."""
    updated: list[tuple[str, str]] = []

    def router(method, url, body, headers):
        if url.endswith("/api/places/user/devices"):
            if headers.get("Authorization") == "Bearer acc":
                return 400, {"error": {"code": 1100, "message": "Unauthorized"}}
            return 200, {"data": []}
        if url.endswith("/api/auth/refresh"):
            assert body == {"refresh": "ref", "access": "acc"}
            return 200, {"data": {"access": "newacc", "refresh": "newref"}}
        return 404, None

    async def updater(access: str, refresh: str) -> None:
        updated.append((access, refresh))

    client = RebramaClient(
        FakeSession(router),
        fingerprint="fp",
        access="acc",
        refresh="ref",
        token_updater=updater,
    )
    assert await client.async_get_places() == []
    assert client.access_token == "newacc"
    assert client.refresh_token == "newref"
    assert updated == [("newacc", "newref")]


async def test_refresh_falls_back_to_login() -> None:
    """If the refresh token is rejected, fall back to a password login."""

    def router(method, url, body, headers):
        if url.endswith("/api/places/user/devices"):
            if headers.get("Authorization") == "Bearer acc":
                return 400, {"error": {"code": 1100, "message": "Unauthorized"}}
            return 200, {"data": []}
        if url.endswith("/api/auth/refresh"):
            return 400, {"error": {"code": 1100, "message": "Unauthorized"}}
        if url.endswith("/api/auth/login"):
            assert body == {"phone": "380990000000", "password": "pw"}
            return 200, {"data": {"access": "loginacc", "refresh": "loginref"}}
        return 404, None

    client = RebramaClient(
        FakeSession(router),
        fingerprint="fp",
        access="acc",
        refresh="ref",
        phone="380990000000",
        password="pw",
    )
    await client.async_get_places()
    assert client.access_token == "loginacc"


async def test_refresh_without_credentials_raises_auth() -> None:
    """When refresh fails and no password is stored, raise an auth error."""

    def router(method, url, body, headers):
        if url.endswith("/api/places/user/devices"):
            return 400, {"error": {"code": 1100, "message": "Unauthorized"}}
        if url.endswith("/api/auth/refresh"):
            return 400, {"error": {"code": 1100, "message": "Unauthorized"}}
        return 404, None

    client = RebramaClient(
        FakeSession(router), fingerprint="fp", access="acc", refresh="ref"
    )
    with pytest.raises(RebramaAuthError):
        await client.async_get_places()


async def test_proactive_refresh_when_token_expired() -> None:
    """An expired access token is refreshed before the request is sent."""
    expired = make_jwt(time.time() - 10)

    def router(method, url, body, headers):
        if url.endswith("/api/auth/refresh"):
            return 200, {"data": {"access": "freshacc", "refresh": "freshref"}}
        if url.endswith("/api/users/me"):
            # The very first profile call must already use the fresh token.
            assert headers["Authorization"] == "Bearer freshacc"
            return 200, {"data": {"id": "u1", "phone": "380990000000"}}
        return 404, None

    session = FakeSession(router)
    client = RebramaClient(session, fingerprint="fp", access=expired, refresh="ref")
    profile = await client.async_get_profile()
    assert profile.user_id == "u1"
    # Refresh happened before the profile request.
    assert session.requests[0]["url"].endswith("/api/auth/refresh")


async def test_domain_error_envelope() -> None:
    """An HTTP 400 with an error envelope raises RebramaApiError."""

    def router(method, url, body, headers):
        return 400, {"error": {"code": 1234, "message": "Bad"}}

    client = RebramaClient(FakeSession(router), fingerprint="fp", access="acc")
    with pytest.raises(RebramaApiError) as err:
        await client.async_open("a1")
    assert err.value.code == 1234


async def test_server_error_is_connection_error() -> None:
    """5xx responses surface as connection errors (retryable)."""

    def router(method, url, body, headers):
        return 503, None

    client = RebramaClient(FakeSession(router), fingerprint="fp", access="acc")
    with pytest.raises(RebramaConnectionError):
        await client.async_get_settings()


async def test_open_returns_delivered_flag() -> None:
    """async_open reports the isDelivered flag."""

    def router(method, url, body, headers):
        assert body == {"accessPointId": "a1"}
        return 200, {"data": {"isDelivered": True}}

    client = RebramaClient(FakeSession(router), fingerprint="fp", access="acc")
    assert await client.async_open("a1") is True


async def test_settings_logs_and_temp_access() -> None:
    """Cover settings, opening logs and temp-access create/delete."""

    def router(method, url, body, headers):
        if url.endswith("/api/settings"):
            return 200, {"data": {"widgetUpdatePeriod": 60000}}
        if url.endswith("/open-logs"):
            return 200, {
                "data": {
                    "items": [
                        {
                            "createdAt": "2026-06-06T10:00:00+00:00",
                            "userPhone": "+380",
                            "userInfo": "Me",
                            "aceessPointName": "Gate",
                            "isTempAccess": False,
                        }
                    ]
                }
            }
        if url.endswith("/api/temp-accesses") and method == "POST":
            assert body["accessPointIds"] == ["a1"]
            return 200, {"data": {"tempAccessLink": "https://rebrama.com/access/xyz"}}
        if "/api/temp-accesses/" in url and method == "DELETE":
            return 200, {"data": None}
        return 404, None

    client = RebramaClient(FakeSession(router), fingerprint="fp", access="acc")

    settings = await client.async_get_settings()
    assert settings["widgetUpdatePeriod"] == 60000

    log = await client.async_get_latest_open_log("p1")
    assert log is not None
    assert log.access_point_name == "Gate"
    assert log.user_info == "Me"

    created = await client.async_create_temporary_access(["a1"], 1000, 2000, "d", 1)
    assert created["tempAccessLink"].endswith("xyz")

    await client.async_delete_temporary_access("xyz")


async def test_empty_open_log_returns_none() -> None:
    """No log items yields None."""

    def router(method, url, body, headers):
        return 200, {"data": {"items": []}}

    client = RebramaClient(FakeSession(router), fingerprint="fp", access="acc")
    assert await client.async_get_latest_open_log("p1") is None


async def test_list_temporary_accesses_parses_items() -> None:
    """The temp-access list is parsed into models with derived slugs."""

    def router(method, url, body, headers):
        assert url.endswith("/api/temp-accesses/user")
        return 200, {
            "data": [
                {
                    "url": "https://rebrama.com/access/abc123",
                    "description": "Cleaner",
                    "dateStart": 1000,
                    "dateEnd": 2000,
                    "usesNumber": 3,
                },
                {
                    # Bare slug + ms timestamps exercise the defensive paths.
                    "link": "xyz789",
                    "description": "",
                    "dateStart": 1000000,
                    "dateEnd": 2000000000000,
                    "usesNumber": None,
                },
            ]
        }

    client = RebramaClient(FakeSession(router), fingerprint="fp", access="acc")
    accesses = await client.async_list_temporary_accesses()

    assert [a.slug for a in accesses] == ["abc123", "xyz789"]
    assert accesses[0].url == "https://rebrama.com/access/abc123"
    assert accesses[0].uses_number == 3
    assert accesses[0].date_start is not None
    # A bare slug is rebuilt into a full share URL.
    assert accesses[1].url == "https://rebrama.com/access/xyz789"
    assert accesses[1].uses_number is None


async def test_list_temporary_accesses_empty() -> None:
    """A null/empty data payload yields an empty list."""

    def router(method, url, body, headers):
        return 200, {"data": None}

    client = RebramaClient(FakeSession(router), fingerprint="fp", access="acc")
    assert await client.async_list_temporary_accesses() == []
