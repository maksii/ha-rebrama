"""Asynchronous client for the Rebrama cloud REST API.

The client owns the access/refresh token pair and keeps it valid transparently:

* **Proactively** — before every authenticated request it inspects the access
  token's ``exp`` claim and refreshes if it is about to expire.
* **Reactively** — if a request still returns ``401`` it refreshes once and
  retries the original call.
* **Resiliently** — if the refresh token itself is rejected it falls back to a
  full ``phone + password`` re-login (when those are stored), so the user never
  has to re-authenticate manually for routine token expiry.

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
import json
import logging
import time
from typing import Any

import aiohttp

from .const import (
    APP_BUILD_NUMBER,
    BASE_URL,
    ERROR_UNAUTHORIZED,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from .models import AuthTokens, OpenLog, Place, Profile, TempAccess

_LOGGER = logging.getLogger(__name__)

# Refresh this many seconds before the access token's ``exp`` to avoid a
# guaranteed 401 on the very next call.
_TOKEN_EXPIRY_MARGIN = 60

TokenUpdater = Callable[[str, str], Awaitable[None]]


class RebramaError(Exception):
    """Base error for all Rebrama client failures."""


class RebramaConnectionError(RebramaError):
    """A transient/connection problem (timeout, network, 429, 5xx)."""


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

    async def async_get_profile(self) -> Profile:
        """Return the authenticated user's profile."""
        body = await self._request("GET", "api/users/me")
        return Profile.from_api(self._data(body) or {})

    async def async_get_settings(self) -> dict[str, Any]:
        """Return server-side settings (limits, widget poll interval, ...)."""
        body = await self._request("GET", "api/settings")
        return self._data(body) or {}

    async def async_get_places(self) -> list[Place]:
        """Return all places (with access points) for the current user."""
        body = await self._request("GET", "api/places/user/devices")
        return [Place.from_api(item) for item in (self._data(body) or [])]

    async def async_open(self, access_point_id: str) -> bool:
        """Open an access point. Returns ``isDelivered``."""
        body = await self._request(
            "POST",
            "api/devices/open",
            json_body={"accessPointId": access_point_id},
        )
        return bool((self._data(body) or {}).get("isDelivered"))

    async def async_get_latest_open_log(self, place_id: str) -> OpenLog | None:
        """Return the most recent opening-log entry for a place, if any."""
        body = await self._request(
            "GET",
            f"api/places/{place_id}/open-logs",
            params={"page": 1, "limit": 1},
        )
        items = (self._data(body) or {}).get("items") or []
        return OpenLog.from_api(items[0]) if items else None

    async def async_list_temporary_accesses(self) -> list[TempAccess]:
        """Return all temporary-access share links for the current user."""
        body = await self._request("GET", "api/temp-accesses/user")
        return [TempAccess.from_api(item) for item in (self._data(body) or [])]

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
        return self._data(body) or {}

    async def async_delete_temporary_access(self, slug: str) -> None:
        """Delete a temporary access by its slug."""
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
                except RebramaError as err:
                    _LOGGER.debug(
                        "Token refresh failed (%s); trying password re-login", err
                    )

            if tokens is None:
                # Last resort: full re-login. Any failure here (e.g. the password
                # was changed) is a genuine auth problem -> surface as an auth
                # error so the coordinator starts the reauth flow.
                try:
                    tokens = await self._post_login()
                except RebramaError as err:
                    raise RebramaAuthError(f"Re-authentication failed: {err}") from err

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
        data = self._data(body) or {}
        if "access" not in data or "refresh" not in data:
            raise RebramaAuthError("Refresh response did not contain tokens")
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
        data = self._data(body) or {}
        if "access" not in data or "refresh" not in data:
            raise RebramaAuthError("Login response did not contain tokens")
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
        except (IndexError, ValueError, binascii.Error, json.JSONDecodeError):
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
        except (aiohttp.ClientError, ValueError):
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

        url = f"{BASE_URL}/{path.lstrip('/')}"
        headers = self._base_headers()
        if manual_bearer is not None:
            headers["Authorization"] = f"Bearer {manual_bearer}"
        elif authed and self._access:
            headers["Authorization"] = f"Bearer {self._access}"
        seen_access = self._access

        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                json=json_body,
                params=params,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                status = resp.status
                body = await self._parse(resp)
        except TimeoutError as err:
            raise RebramaConnectionError(f"Timeout calling {path}") from err
        except aiohttp.ClientError as err:
            raise RebramaConnectionError(f"Error calling {path}: {err}") from err

        if 200 <= status < 300:
            return body

        # Rebrama returns HTTP 400 for *every* failure (including auth, which is
        # error code 1100 — there is no 401/403). The error code is the real
        # signal; 401/403 are handled too in case the server ever changes.
        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        is_auth_failure = code == ERROR_UNAUTHORIZED or status in (401, 403)

        if is_auth_failure and authed and allow_auth_retry and manual_bearer is None:
            _LOGGER.debug(
                "Auth failure (code %s) on %s %s; refreshing and retrying",
                code,
                method,
                path,
            )
            await self._async_refresh(seen_access)
            return await self._request(
                method,
                path,
                json_body=json_body,
                params=params,
                authed=authed,
                allow_auth_retry=False,
            )

        if is_auth_failure:
            raise RebramaAuthError(message or f"Unauthorized (HTTP {status})")
        if isinstance(error, dict):
            raise RebramaApiError(code, message or "Request failed")
        if status == 429 or status >= 500:
            raise RebramaConnectionError(f"Server returned HTTP {status}")
        raise RebramaConnectionError(f"Unexpected HTTP {status}")
