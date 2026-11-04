"""Asynchronous client for the Rebrama cloud REST API.

The client owns the access/refresh token pair and keeps it valid transparently:

* **Proactively** — before every authenticated request it inspects the access
  token's ``exp`` claim and refreshes if it is about to expire.
* **Reactively** — if a request is still rejected as unauthorized (error code
  1100) it refreshes once and retries the original call.
* **Resiliently** — if the refresh token itself is rejected it falls back to a
  full ``phone + password`` re-login (when those are stored), so the user never
  has to re-authenticate manually for routine token expiry.

Only a *rejection by the server* counts as an auth failure. Network problems
during a refresh or re-login surface as ``RebramaConnectionError`` so a flaky
connection never triggers a spurious re-authentication prompt.

A refresh persists the rotated pair back to the config entry through the
``token_updater`` callback. All refreshes are serialised by a lock so concurrent
requests never race two ``/api/auth/refresh`` calls against the same (single
use) refresh token.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import Awaitable, Callable
from dataclasses import replace
from email.utils import parsedate_to_datetime
import json
import logging
import math
import time
from typing import Any

import aiohttp
from homeassistant.util import dt as dt_util

from .const import (
    APP_BUILD_NUMBER,
    BASE_URL,
    ERROR_UNAUTHORIZED,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from .models import (
    AuthTokens,
    OpenLog,
    Place,
    Profile,
    TempAccess,
    _to_datetime,
    temporary_access_slug,
)

_LOGGER = logging.getLogger(__name__)

# Refresh this many seconds before the access token's ``exp`` to avoid a
# guaranteed 401 on the very next call.
_TOKEN_EXPIRY_MARGIN = 60
_REQUEST_INTERVAL = 1.0

TokenUpdater = Callable[[str, str], Awaitable[None]]


class RebramaError(Exception):
    """Base error for all Rebrama client failures."""


class RebramaConnectionError(RebramaError):
    """A transient/connection problem (timeout, network, 429, 5xx)."""


class RebramaRateLimitError(RebramaConnectionError):
    """The account must wait before making another request."""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(f"Cloud requests paused for {math.ceil(retry_after)} seconds")


class RebramaAuthError(RebramaError):
    """Authentication failed and could not be recovered automatically."""


class RebramaApiError(RebramaError):
    """The API returned a structured domain error (HTTP 400 envelope)."""

    def __init__(self, code: int | None, message: str) -> None:
        """Store the API error code and message."""
        super().__init__(f"[{code}] {message}" if code is not None else message)
        self.code = code
        self.message = message


class RebramaClient:
    """A thin async wrapper around the Rebrama REST API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        fingerprint: str,
        access: str | None = None,
        refresh: str | None = None,
        phone: str | None = None,
        password: str | None = None,
        token_updater: TokenUpdater | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            session: Home Assistant's shared aiohttp session.
            fingerprint: A stable per-install identifier sent as
                ``User-Fingerprint`` (mirrors the mobile app).
            access: The current access JWT, if already known.
            refresh: The current refresh JWT, if already known.
            phone: Stored phone (digits only) used for re-login fallback.
            password: Stored password used for re-login fallback.
            token_updater: Async callback invoked with ``(access, refresh)``
                whenever the token pair rotates, so the caller can persist it.
        """
        self._session = session
        self._fingerprint = fingerprint
        self._access = access
        self._refresh = refresh
        self._phone = phone
        self._password = password
        self._token_updater = token_updater
        self._refresh_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._next_request_at = 0.0
        self._retry_at = 0.0
        self._failures = 0
        self._settings: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Token accessors
    # ------------------------------------------------------------------ #
    @property
    def access_token(self) -> str | None:
        """Return the current access token."""
        return self._access

    @property
    def refresh_token(self) -> str | None:
        """Return the current refresh token."""
        return self._refresh

    # ------------------------------------------------------------------ #
    # Public API methods
    # ------------------------------------------------------------------ #
    async def async_login(self) -> AuthTokens:
        """Log in with the stored phone/password and cache the tokens.

        Raises ``RebramaApiError`` (carrying the server's ``code``) on rejection
        so callers such as the config flow can distinguish wrong-password (1203)
        from unregistered/invalid phone (1201) and phone-format errors.
        """
        tokens = await self._post_login()
        self._access = tokens.access
        self._refresh = tokens.refresh
        return tokens

    @staticmethod
    def _object(body: Any) -> dict[str, Any]:
        data = RebramaClient._data(body)
        if not isinstance(data, dict):
            raise RebramaConnectionError("Expected an object from Rebrama")
        return data

    @staticmethod
    def _items(body: Any) -> list[dict[str, Any]]:
        data = RebramaClient._data(body)
        if not isinstance(data, list) or any(
            not isinstance(item, dict) for item in data
        ):
            raise RebramaConnectionError("Expected a list of objects from Rebrama")
        return data

    async def async_get_profile(self) -> Profile:
        """Return the authenticated user's profile."""
        body = await self._request("GET", "api/users/me")
        try:
            return Profile.from_api(self._object(body))
        except (KeyError, TypeError, ValueError) as err:
            raise RebramaConnectionError("Invalid profile from Rebrama") from err

    async def async_get_settings(self) -> dict[str, Any]:
        """Return server-side settings (limits, widget poll interval, ...)."""
        body = await self._request("GET", "api/settings")
        self._settings = self._object(body)
        return self._settings

    async def async_get_places(self) -> list[Place]:
        """Return all places (with access points) for the current user."""
        body = await self._request("GET", "api/places/user/devices")
        try:
            return [Place.from_api(item) for item in self._items(body)]
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            raise RebramaConnectionError("Invalid places from Rebrama") from err

    async def async_open(self, access_point_id: str) -> bool:
        """Open an access point. Returns ``isDelivered``."""
        body = await self._request(
            "POST",
            "api/devices/open",
            json_body={"accessPointId": access_point_id},
        )
        return self._object(body).get("isDelivered") is True

    async def async_get_latest_open_log(self, place_id: str) -> OpenLog | None:
        """Return the most recent opening-log entry for a place, if any."""
        body = await self._request(
            "GET",
            f"api/places/{place_id}/open-logs",
            params={"page": 1, "limit": 1},
        )
        items = self._object(body).get("items")
        if not isinstance(items, list) or any(
            not isinstance(item, dict) for item in items
        ):
            raise RebramaConnectionError("Invalid opening logs from Rebrama")
        return OpenLog.from_api(items[0]) if items else None

    async def async_list_temporary_accesses(self) -> list[TempAccess]:
        """Return all temporary-access share links for the current user."""
        body = await self._request("GET", "api/temp-accesses/user")
        try:
            accesses = [TempAccess.from_api(item) for item in self._items(body)]
        except (TypeError, ValueError) as err:
            raise RebramaConnectionError(
                "Invalid temporary accesses from Rebrama"
            ) from err
        # The list item's dates may only be provided by the detail endpoint. If
        # they only live on the detail endpoint, take them from there; this
        # never triggers once the list carries them.
        return [
            await self._async_with_dates(access)
            if access.slug and (access.date_start is None or access.date_end is None)
            else access
            for access in accesses
        ]

    async def _async_with_dates(self, access: TempAccess) -> TempAccess:
        """Fill validity dates; a confirmed missing link is already expired."""
        try:
            body = await self._request("GET", f"api/temp-accesses/{access.slug}/info")
        except RebramaApiError as err:
            if err.code == 1501:
                return replace(access, date_end=dt_util.utcnow())
            raise
        details = self._object(body)
        return replace(
            access,
            date_start=_to_datetime(details.get("dateStart")),
            date_end=_to_datetime(details.get("dateEnd")),
        )

    async def async_create_temporary_access(
        self,
        access_point_ids: list[str],
        date_start: int,
        date_end: int,
        description: str,
        uses_number: int | None = None,
    ) -> dict[str, Any]:
        """Create a time-bounded share link. Returns the API ``data`` object."""
        body = await self._request(
            "POST",
            "api/temp-accesses",
            json_body={
                "accessPointIds": access_point_ids,
                "dateStart": date_start,
                "dateEnd": date_end,
                "description": description,
                "usesNumber": uses_number,
            },
        )
        return self._object(body)

    async def async_delete_temporary_access(self, slug: str) -> None:
        """Delete a temporary access by its slug."""
        slug = temporary_access_slug(slug)
        if not slug:
            raise RebramaApiError(None, "Invalid temporary access link")
        await self._request("DELETE", f"api/temp-accesses/{slug}")

    # ------------------------------------------------------------------ #
    # Auth internals
    # ------------------------------------------------------------------ #
    async def _async_ensure_valid_token(self) -> None:
        """Refresh proactively if the access token is missing or near expiry."""
        if self._access and not self._is_token_expired(self._access):
            return
        await self._async_refresh(self._access)

    async def _async_refresh(self, seen_access: str | None) -> None:
        """Rotate the token pair, falling back to a full login if needed.

        ``seen_access`` is the access token the caller used; if another coroutine
        already rotated to a fresh, valid token while we waited for the lock we
        return without hitting the network again.
        """
        async with self._refresh_lock:
            if (
                self._access
                and self._access != seen_access
                and not self._is_token_expired(self._access)
            ):
                return

            tokens: AuthTokens | None = None
            if self._refresh:
                try:
                    tokens = await self._post_refresh()
                except RebramaAuthError, RebramaApiError:
                    # The server rejected the refresh token; connection errors
                    # propagate untouched so they stay retryable.
                    _LOGGER.debug("Token refresh rejected; trying password re-login")

            if tokens is None:
                # Last resort: full re-login. A rejection here (e.g. the password
                # was changed) is a genuine auth problem -> surface as an auth
                # error so the coordinator starts the reauth flow.
                try:
                    tokens = await self._post_login()
                except RebramaApiError as err:
                    if err.code not in (1100, 1201, 1203, 1209, 1210):
                        raise
                    raise RebramaAuthError("Stored credentials were rejected") from err

            self._access = tokens.access
            self._refresh = tokens.refresh
            if self._token_updater is not None:
                await self._token_updater(tokens.access, tokens.refresh)

    async def _post_refresh(self) -> AuthTokens:
        """Call ``/api/auth/refresh`` with the stored token pair."""
        body = await self._request(
            "POST",
            "api/auth/refresh",
            json_body={"refresh": self._refresh, "access": self._access},
            authed=False,
            manual_bearer=self._access,
            allow_auth_retry=False,
        )
        data = self._object(body)
        if not all(
            isinstance(data.get(key), str) and data[key]
            for key in ("access", "refresh")
        ):
            raise RebramaConnectionError("Refresh response did not contain tokens")
        return AuthTokens.from_api(data)

    async def _post_login(self) -> AuthTokens:
        """Call ``/api/auth/login`` with the stored phone/password."""
        if not (self._phone and self._password):
            raise RebramaAuthError("No stored credentials to re-authenticate")
        body = await self._request(
            "POST",
            "api/auth/login",
            json_body={"phone": self._phone, "password": self._password},
            authed=False,
        )
        data = self._object(body)
        if not all(
            isinstance(data.get(key), str) and data[key]
            for key in ("access", "refresh")
        ):
            raise RebramaConnectionError("Login response did not contain tokens")
        return AuthTokens.from_api(data)

    @staticmethod
    def _is_token_expired(token: str, margin: int = _TOKEN_EXPIRY_MARGIN) -> bool:
        """Return whether a JWT is expired (or expires within ``margin`` s).

        The signature is not verified — only the unauthenticated ``exp`` claim is
        read to decide whether to refresh early. If the token cannot be parsed we
        assume it is still valid and rely on the reactive 401 path.
        """
        try:
            payload_b64 = token.split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        except IndexError, ValueError, binascii.Error, json.JSONDecodeError:
            return False
        if not isinstance(payload, dict):
            return False
        exp = payload.get("exp")
        if not isinstance(exp, (int, float)):
            return False
        return time.time() >= (exp - margin)

    # ------------------------------------------------------------------ #
    # HTTP plumbing
    # ------------------------------------------------------------------ #
    def _base_headers(self) -> dict[str, str]:
        """Return the headers the official app sends on every request."""
        return {
            "Accept": "application/json",
            "App-Build-Number": APP_BUILD_NUMBER,
            "User-Fingerprint": self._fingerprint,
            "Is-Widget": "0",
            "User-Agent": USER_AGENT,
        }

    @staticmethod
    def _data(body: Any) -> Any:
        """Return the ``data`` field of a ``BaseResponse`` envelope."""
        return body.get("data") if isinstance(body, dict) else None

    @staticmethod
    async def _parse(resp: aiohttp.ClientResponse) -> Any:
        """Best-effort parse of a JSON response body."""
        try:
            return await resp.json(content_type=None)
        except aiohttp.ClientError, ValueError:
            return None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        authed: bool = True,
        allow_auth_retry: bool = True,
        manual_bearer: str | None = None,
    ) -> Any:
        """Perform a single HTTP request, handling auth and error envelopes."""
        if authed and manual_bearer is None:
            await self._async_ensure_valid_token()

        headers = self._base_headers()
        if manual_bearer is not None:
            headers["Authorization"] = f"Bearer {manual_bearer}"
        elif authed and self._access:
            headers["Authorization"] = f"Bearer {self._access}"
        seen_access = self._access
        status, body = await self._send(method, path, headers, json_body, params)

        error = self._response_error(status, body)
        if error is None:
            return body
        if (
            isinstance(error, RebramaAuthError)
            and authed
            and allow_auth_retry
            and manual_bearer is None
        ):
            _LOGGER.debug("Authentication rejected; refreshing and retrying once")
            await self._async_refresh(seen_access)
            return await self._request(
                method,
                path,
                json_body=json_body,
                params=params,
                authed=authed,
                allow_auth_retry=False,
            )
        raise error

    @staticmethod
    def _response_error(status: int, body: Any) -> RebramaError | None:
        """Classify HTTP and domain errors before accepting a success envelope."""
        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        if code == ERROR_UNAUTHORIZED or status in (401, 403):
            return RebramaAuthError("Authentication rejected by Rebrama")
        if isinstance(error, dict):
            return RebramaApiError(code, error.get("message") or "Request failed")
        if not 200 <= status < 300:
            return RebramaConnectionError(f"Unexpected HTTP {status}")
        if (
            not isinstance(body, dict)
            or "data" not in body
            or error not in (None, False)
        ):
            return RebramaConnectionError("Invalid response envelope from Rebrama")
        return None

    @staticmethod
    def _safe_path(path: str) -> str:
        """Share slugs are bearer credentials and must never appear in logs."""
        if path.startswith("api/temp-accesses/") and path != "api/temp-accesses/user":
            return "api/temp-accesses/[redacted]"
        return path

    def _backoff(self, retry_after: str) -> None:
        """Honor delta-seconds or an HTTP-date without retrying a mutation."""
        self._failures = min(self._failures + 1, 7)
        delay = min(3600.0, 60.0 * 2 ** (self._failures - 1))
        try:
            requested_delay = float(retry_after)
        except ValueError:
            try:
                requested_delay = (
                    parsedate_to_datetime(retry_after).timestamp() - time.time()
                )
            except ValueError, TypeError, OverflowError:
                requested_delay = 0.0
        if math.isfinite(requested_delay):
            delay = max(delay, min(86400.0, requested_delay))
        self._retry_at = time.monotonic() + delay
        raise RebramaRateLimitError(delay)

    def _prepare_share(self, body: dict[str, Any]) -> dict[str, Any]:
        """Normalize 'now' at dispatch, after pacing and authentication delays."""
        body = {**body, "dateStart": max(body["dateStart"], math.ceil(time.time()) + 1)}
        duration = body["dateEnd"] - body["dateStart"]
        if duration <= 0 or duration < self._settings.get(
            "minTemporaryAccessTimeInterval", 0
        ):
            raise RebramaApiError(1505, "The remaining access window is too short")
        return body

    async def _send(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        json_body: dict[str, Any] | None,
        params: dict[str, Any] | None,
    ) -> tuple[int, Any]:
        """Perform one paced HTTP exchange, never automatically replaying it."""
        safe_path = self._safe_path(path)
        try:
            async with self._request_lock:
                now = time.monotonic()
                if self._retry_at > now:
                    raise RebramaRateLimitError(self._retry_at - now)
                await asyncio.sleep(max(0, self._next_request_at - now))
                self._next_request_at = time.monotonic() + _REQUEST_INTERVAL
                if method == "POST" and path == "api/temp-accesses" and json_body:
                    json_body = self._prepare_share(json_body)
                async with self._session.request(
                    method,
                    f"{BASE_URL}/{path.lstrip('/')}",
                    headers=headers,
                    json=json_body,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                ) as resp:
                    status = resp.status
                    body = await self._parse(resp)
                    _LOGGER.debug("%s %s -> HTTP %s", method, safe_path, status)
                    if status == 429 or status >= 500:
                        self._backoff(resp.headers.get("Retry-After", ""))
                    self._failures = 0
                    return status, body
        except TimeoutError as err:
            raise RebramaConnectionError(f"Timeout calling {safe_path}") from err
        except aiohttp.ClientError as err:
            raise RebramaConnectionError(f"Network error calling {safe_path}") from err
