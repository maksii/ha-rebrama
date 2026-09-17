"""Unit tests for the Rebrama API client (auth/refresh/retry logic)."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
import json
import logging
import time
from typing import Any

import aiohttp
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
        await asyncio.sleep(0)  # let concurrent requests interleave like real I/O
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


async def test_list_temporary_accesses_fills_dates_from_details() -> None:
    """A list item without dates gets them from ``{slug}/info``; failures keep it."""

    def router(method, url, body, headers):
        if url.endswith("/api/temp-accesses/user"):
            return 200, {
                "data": [
                    {
                        "url": "https://x/access/full",
                        "dateStart": 1000,
                        "dateEnd": 2000,
                    },
                    {"url": "https://x/access/bare"},
                    {"url": "https://x/access/gone"},
                ]
            }
        if url.endswith("/bare/info"):
            return 200, {"data": {"link": "bare", "dateStart": 3000, "dateEnd": 4000}}
        return 400, {"error": {"code": 1501, "message": "Temp access not found"}}

    session = FakeSession(router)
    client = RebramaClient(session, fingerprint="fp", access="acc")
    accesses = await client.async_list_temporary_accesses()

    ends = [a.date_end and int(a.date_end.timestamp()) for a in accesses]
    assert ends == [2000, 4000, None]
    assert accesses[1].url == "https://x/access/bare"
    # Only the two undated links cost a detail call.
    assert sum("/info" in r["url"] for r in session.requests) == 2


async def test_list_temporary_accesses_empty() -> None:
    """A null/empty data payload yields an empty list."""

    def router(method, url, body, headers):
        return 200, {"data": None}

    client = RebramaClient(FakeSession(router), fingerprint="fp", access="acc")
    assert await client.async_list_temporary_accesses() == []


async def test_refresh_network_error_is_not_auth_failure() -> None:
    """A network error during refresh is a connection error, not a re-login."""

    def router(method, url, body, headers):
        if url.endswith("/api/places/user/devices"):
            return 400, {"error": {"code": 1100, "message": "Unauthorized"}}
        if url.endswith("/api/auth/refresh"):
            raise aiohttp.ClientConnectionError("dns")
        raise AssertionError(f"unexpected call {url}")

    client = RebramaClient(
        FakeSession(router),
        fingerprint="fp",
        access="acc",
        refresh="ref",
        phone="380990000000",
        password="pw",
    )
    with pytest.raises(RebramaConnectionError):
        await client.async_get_places()


async def test_login_network_error_is_not_auth_failure() -> None:
    """A refresh rejection followed by a login timeout is still not an auth error."""

    def router(method, url, body, headers):
        if url.endswith("/api/places/user/devices"):
            return 400, {"error": {"code": 1100, "message": "Unauthorized"}}
        if url.endswith("/api/auth/refresh"):
            return 400, {"error": {"code": 1100, "message": "Unauthorized"}}
        if url.endswith("/api/auth/login"):
            raise TimeoutError
        raise AssertionError(f"unexpected call {url}")

    client = RebramaClient(
        FakeSession(router),
        fingerprint="fp",
        access="acc",
        refresh="ref",
        phone="380990000000",
        password="pw",
    )
    with pytest.raises(RebramaConnectionError):
        await client.async_get_places()


async def test_login_rejected_is_auth_failure() -> None:
    """A rejected password (1203) during re-login is a genuine auth error."""

    def router(method, url, body, headers):
        if url.endswith("/api/places/user/devices"):
            return 400, {"error": {"code": 1100, "message": "Unauthorized"}}
        if url.endswith("/api/auth/refresh"):
            return 400, {"error": {"code": 9999, "message": "malformed"}}
        if url.endswith("/api/auth/login"):
            return 400, {"error": {"code": 1203, "message": "Wrong user credentials"}}
        raise AssertionError(f"unexpected call {url}")

    client = RebramaClient(
        FakeSession(router),
        fingerprint="fp",
        access="acc",
        refresh="ref",
        phone="380990000000",
        password="pw",
    )
    with pytest.raises(RebramaAuthError):
        await client.async_get_places()


def test_token_expiry_parsing_is_defensive() -> None:
    """Unparseable or odd JWT payloads are treated as still valid."""
    payload = base64.urlsafe_b64encode(b"[1, 2]").rstrip(b"=").decode()
    assert RebramaClient._is_token_expired(f"h.{payload}.s") is False
    assert RebramaClient._is_token_expired("not-a-jwt") is False
    assert RebramaClient._is_token_expired(make_jwt(time.time() + 3600)) is False
    assert RebramaClient._is_token_expired(make_jwt(time.time() + 10)) is True


async def test_login_caches_tokens() -> None:
    """async_login stores the returned pair for subsequent calls."""

    def router(method, url, body, headers):
        if url.endswith("/api/auth/login"):
            assert "Authorization" not in headers
            return 201, {"data": {"access": "a1", "refresh": "r1"}}
        if url.endswith("/api/users/me"):
            assert headers["Authorization"] == "Bearer a1"
            return 200, {"data": {"id": "u1", "phone": "380990000000"}}
        raise AssertionError(f"unexpected call {url}")

    client = RebramaClient(
        FakeSession(router), fingerprint="fp", phone="380990000000", password="pw"
    )
    tokens = await client.async_login()
    assert (tokens.access, tokens.refresh) == ("a1", "r1")
    assert client.access_token == "a1"
    assert (await client.async_get_profile()).user_id == "u1"


async def test_auth_responses_without_tokens() -> None:
    """A refresh without tokens falls back to login; a login without tokens fails."""
    calls: list[str] = []

    def router(method, url, body, headers):
        calls.append(url.rsplit("/", 1)[-1])
        if url.endswith("/api/places/user/devices"):
            return 400, {"error": {"code": 1100, "message": "Unauthorized"}}
        if url.endswith("/api/auth/refresh"):
            return 200, {"data": {}}
        if url.endswith("/api/auth/login"):
            return 200, {"data": {"access": "only"}}
        raise AssertionError(f"unexpected call {url}")

    client = RebramaClient(
        FakeSession(router),
        fingerprint="fp",
        access="acc",
        refresh="ref",
        phone="380990000000",
        password="pw",
    )
    with pytest.raises(RebramaAuthError):
        await client.async_get_places()
    assert calls == ["devices", "refresh", "login"]


async def test_concurrent_requests_share_one_refresh() -> None:
    """Two requests hitting 1100 at once trigger a single token refresh."""
    refreshes = 0

    def router(method, url, body, headers):
        nonlocal refreshes
        if url.endswith("/api/places/user/devices"):
            if headers.get("Authorization") == "Bearer acc":
                return 400, {"error": {"code": 1100, "message": "Unauthorized"}}
            return 200, {"data": []}
        if url.endswith("/api/auth/refresh"):
            refreshes += 1
            return 200, {"data": {"access": "newacc", "refresh": "newref"}}
        raise AssertionError(f"unexpected call {url}")

    client = RebramaClient(
        FakeSession(router), fingerprint="fp", access="acc", refresh="ref"
    )
    await asyncio.gather(client.async_get_places(), client.async_get_places())
    assert refreshes == 1


async def test_unexpected_http_status_is_connection_error() -> None:
    """A non-envelope failure (e.g. a proxy 404 page) is a retryable error."""

    def router(method, url, body, headers):
        return 404, None

    client = RebramaClient(FakeSession(router), fingerprint="fp", access="acc")
    with pytest.raises(RebramaConnectionError):
        await client.async_get_settings()


def test_token_expiry_ignores_non_numeric_exp() -> None:
    """A JWT whose exp claim is not a number is treated as valid."""
    payload = base64.urlsafe_b64encode(b'{"exp": "soon"}').rstrip(b"=").decode()
    assert RebramaClient._is_token_expired(f"h.{payload}.s") is False


async def test_debug_log_shows_responses_but_never_tokens(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Debug logging captures API payloads for support, minus the auth calls."""
    caplog.set_level(logging.DEBUG, logger="custom_components.rebrama.api")

    def router(method, url, body, headers):
        if url.endswith("/api/auth/login"):
            return 201, {"data": {"access": "SECRET-A", "refresh": "SECRET-R"}}
        return 200, {"data": {"id": "u1", "validUntil": "1780000000"}}

    client = RebramaClient(
        FakeSession(router), fingerprint="fp", phone="380", password="pw"
    )
    await client.async_login()
    await client.async_get_profile()
    assert "SECRET" not in caplog.text
    assert "validUntil" in caplog.text
