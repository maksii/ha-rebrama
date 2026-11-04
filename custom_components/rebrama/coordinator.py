"""DataUpdateCoordinator for the Rebrama integration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    RebramaApiError,
    RebramaAuthError,
    RebramaClient,
    RebramaError,
    RebramaRateLimitError,
)
from .const import (
    CONF_USER_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ERROR_TEMP_ACCESS_NOT_FOUND,
)
from .models import (
    AccessPoint,
    OpenLog,
    Place,
    Profile,
    TempAccess,
    temporary_access_slug,
)

_LOGGER = logging.getLogger(__name__)

type RebramaConfigEntry = ConfigEntry[RebramaCoordinator]


@dataclass
class RebramaData:
    """Snapshot of the data shown by the entities, rebuilt on every poll."""

    places: dict[str, Place]
    logs: dict[str, OpenLog | None]
    temp_accesses: list[TempAccess]
    profile: Profile
    failed_endpoints: frozenset[str] = frozenset()

    @property
    def access_points(self) -> dict[str, AccessPoint]:
        """Return every access point across all places, keyed by id."""
        return {
            access_point.id: access_point
            for place in self.places.values()
            for access_point in place.access_points.values()
        }


class RebramaCoordinator(DataUpdateCoordinator[RebramaData]):
    """Polls the account and runs the write actions (open, share links)."""

    config_entry: RebramaConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: RebramaConfigEntry,
        client: RebramaClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=DEFAULT_SCAN_INTERVAL,
            always_update=False,
        )
        self.client = client
        self.user_id: str = entry.data[CONF_USER_ID]
        # Server-side limits; kept for diagnostics/support only.
        self.settings: dict[str, Any] = {}
        self._data_lock = asyncio.Lock()
        self._failed_endpoints: set[str] = set()
        self.last_successful_update: datetime | None = None
        self.successful_updates = 0

    async def _async_setup(self) -> None:
        """Fetch the server-side settings once."""
        try:
            self.settings = await self.client.async_get_settings()
        except RebramaAuthError as err:
            raise ConfigEntryAuthFailed("Authentication with Rebrama failed") from err
        except RebramaError as err:
            _LOGGER.debug("Could not fetch settings (%s); continuing without", err)

    async def _async_update_data(self) -> RebramaData:
        """Keep snapshots serialized with coordinator-driven updates."""
        async with self._data_lock:
            return await self._async_poll()

    async def _async_poll(self) -> RebramaData:
        """Fetch places, the profile, share links and the latest opening logs.

        Only the place list is mandatory. Everything else is best-effort and
        keeps its previous value when its fetch fails, so one flaky endpoint
        does not make every entity unavailable.
        """
        previous = self.data
        try:
            places = await self.client.async_get_places()
            profile = await self._async_fetch(
                self.client.async_get_profile(),
                previous.profile
                if previous
                else Profile(user_id=self.user_id, phone=""),
                "profile",
            )
            temp_accesses = await self._async_fetch(
                self.client.async_list_temporary_accesses(),
                previous.temp_accesses if previous else [],
                "temporary accesses",
            )
            active_logs = {
                f"opening logs for place {p.id}" for p in places if p.can_manage
            }
            self._failed_endpoints.intersection_update(
                {"profile", "temporary accesses"} | active_logs
            )
            logs: dict[str, OpenLog | None] = {}
            for place in places:
                # Opening logs need "manage" permission on the place (the API
                # returns 1304 otherwise), so skip the doomed call for the rest.
                if not place.can_manage:
                    logs[place.id] = None
                    continue
                logs[place.id] = await self._async_fetch(
                    self.client.async_get_latest_open_log(place.id),
                    previous.logs.get(place.id) if previous else None,
                    f"opening logs for place {place.id}",
                )
        except RebramaAuthError as err:
            raise ConfigEntryAuthFailed("Authentication with Rebrama failed") from err
        except RebramaRateLimitError as err:
            raise UpdateFailed(str(err), retry_after=err.retry_after) from err
        except RebramaError as err:
            raise UpdateFailed(f"Error fetching data from Rebrama: {err}") from err

        self.last_successful_update = dt_util.utcnow()
        self.successful_updates += 1
        return RebramaData(
            places={place.id: place for place in places},
            logs=logs,
            temp_accesses=temp_accesses,
            profile=profile,
            failed_endpoints=frozenset(self._failed_endpoints),
        )

    async def _async_fetch[T](self, request: Awaitable[T], fallback: T, what: str) -> T:
        """Await a secondary fetch, keeping the last value on non-auth failures."""
        try:
            result = await request
        except RebramaAuthError, RebramaRateLimitError:
            raise
        except RebramaError:
            if what not in self._failed_endpoints:
                _LOGGER.warning(
                    "Could not fetch %s; affected entities are unavailable", what
                )
            self._failed_endpoints.add(what)
            return fallback
        if what in self._failed_endpoints:
            _LOGGER.info("Fetching %s recovered", what)
            self._failed_endpoints.remove(what)
        return result

    # ------------------------------------------------------------------ #
    # Devices
    # ------------------------------------------------------------------ #
    @callback
    def device_id(self, identifier: str) -> str | None:
        """Return the registry id of one of our devices (for ``via_device_id``)."""
        device = dr.async_get(self.hass).async_get_device_by_identifier(
            (DOMAIN, identifier), self.config_entry.entry_id
        )
        return device.id if device else None

    # ------------------------------------------------------------------ #
    # Actions. The button, the calendar and the service actions all route
    # through here so validation and error mapping exist exactly once.
    # ------------------------------------------------------------------ #
    async def async_open(self, access_point_id: str) -> None:
        """Open an access point, then pull a fresh 'last opened' log."""
        try:
            delivered = await self.client.async_open(access_point_id)
        except RebramaAuthError as err:
            raise self._auth_error() from err
        except RebramaError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="open_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        if not delivered:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="open_not_delivered"
            )
        await self.async_request_refresh()

    async def async_create_temporary_access(
        self,
        access_point_ids: Iterable[str],
        *,
        start: datetime,
        end: datetime,
        description: str,
        uses: int | None = None,
    ) -> str:
        """Create a share link for the given access points and return its URL.

        A start in the past is moved up to now: the API rejects it (code 1504),
        yet the calendar dialog and automations using ``now()`` produce one
        routinely. Naive datetimes are interpreted in the HA time zone.
        """
        now = dt_util.utcnow()
        start, end = dt_util.as_utc(start), dt_util.as_utc(end)
        if end <= now:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="end_in_past"
            )
        if end <= start:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_time_range"
            )
        start = max(start, now)
        if int(end.timestamp()) <= int(start.timestamp()):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_time_range"
            )
        self._validate_share_limits(start, end, uses)

        ids = sorted(set(access_point_ids))
        if not ids:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_shareable_access_points"
            )
        access_points = self.data.access_points
        for ap_id in ids:
            access_point = access_points.get(ap_id)
            if access_point is None or not access_point.can_share_access:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="access_point_not_shareable",
                    translation_placeholders={
                        "name": access_point.name if access_point else ap_id
                    },
                )

        try:
            result = await self.client.async_create_temporary_access(
                access_point_ids=ids,
                date_start=int(start.timestamp()),
                date_end=int(end.timestamp()),
                description=description,
                uses_number=uses,
            )
        except RebramaError as err:
            raise self._action_error(err) from err

        url = str(result.get("tempAccessLink") or result.get("url") or "")
        # A mutation is sent once. A failed refresh must never invite a retry of
        # the mutation, which could grant access twice.
        await self.async_request_refresh()
        if not temporary_access_slug(url):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="missing_created_link"
            )
        return url

    def _validate_share_limits(
        self, start: datetime, end: datetime, uses: int | None
    ) -> None:
        """Validate the server's published duration and usage limits."""
        if uses is not None and (
            isinstance(uses, bool) or not isinstance(uses, int) or uses < 1
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_uses"
            )
        maximum_uses = self.settings.get("maxTemporaryAccessUsesNumber")
        if uses is not None and isinstance(maximum_uses, int) and uses > maximum_uses:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_uses"
            )
        duration = int(end.timestamp()) - int(start.timestamp())
        minimum = self.settings.get("minTemporaryAccessTimeInterval")
        maximum = self.settings.get("maxTemporaryAccessTimeInterval")
        if (isinstance(minimum, int) and duration < minimum) or (
            isinstance(maximum, int) and duration > maximum
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_duration"
            )

    async def async_delete_temporary_access(self, slug: str) -> None:
        """Revoke a share link. A link that is already gone counts as revoked."""
        try:
            await self.client.async_delete_temporary_access(slug)
        except RebramaApiError as err:
            if err.code != ERROR_TEMP_ACCESS_NOT_FOUND:
                raise self._action_error(err) from err
            _LOGGER.debug("Temporary access %s no longer exists", slug)
        except RebramaError as err:
            raise self._action_error(err) from err
        await self.async_request_refresh()

    @callback
    def _auth_error(self) -> HomeAssistantError:
        """Start the reauth flow and return the error to show the user."""
        self.config_entry.async_start_reauth(self.hass)
        return HomeAssistantError(
            translation_domain=DOMAIN, translation_key="auth_failed"
        )

    @callback
    def _action_error(self, err: RebramaError) -> HomeAssistantError:
        """Map a client error from a share-link action to the HA exception."""
        if isinstance(err, RebramaAuthError):
            return self._auth_error()
        if isinstance(err, RebramaApiError):
            # The server rejected the request itself (dates, uses, permissions).
            return ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="rejected",
                translation_placeholders={"error": err.message},
            )
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="service_failed",
            translation_placeholders={"error": str(err)},
        )
