"""DataUpdateCoordinator for the Rebrama integration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RebramaAuthError, RebramaClient, RebramaError
from .const import (
    CONF_USER_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .models import OpenLog, Place, Profile, TempAccess

_LOGGER = logging.getLogger(__name__)

type RebramaConfigEntry = ConfigEntry[RebramaCoordinator]


@dataclass
class RebramaData:
    """Snapshot of the data shown by the entities.

    ``places``/``logs`` are refreshed every poll; ``temp_accesses`` is carried
    forward between polls and only re-fetched on demand (see the coordinator).
    """

    places: dict[str, Place]
    logs: dict[str, OpenLog | None]
    temp_accesses: list[TempAccess]


def _clamp(seconds: int) -> int:
    """Clamp a polling interval to the supported range."""
    return max(MIN_SCAN_INTERVAL, min(MAX_SCAN_INTERVAL, seconds))


class RebramaCoordinator(DataUpdateCoordinator[RebramaData]):
    """Coordinates polling of places, access points and opening logs."""

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
        self.settings: dict = {}
        # Subscription expiry rarely changes, so it is fetched once at setup
        # (and again on reload) rather than every poll.
        self.profile = Profile(user_id=self.user_id, phone="")

    async def _async_setup(self) -> None:
        """Fetch one-time data and resolve the polling interval."""
        # Settings are kept for diagnostics/support (server-side limits); the
        # poll interval is no longer derived from the widget cadence.
        try:
            self.settings = await self.client.async_get_settings()
        except RebramaError as err:
            _LOGGER.debug("Could not fetch settings (%s); using defaults", err)
            self.settings = {}
        try:
            self.profile = await self.client.async_get_profile()
        except RebramaError as err:
            _LOGGER.debug("Could not fetch profile (%s); subscription unknown", err)
        self.update_interval = self._resolve_interval()
        _LOGGER.debug("Rebrama polling interval set to %s", self.update_interval)

    def _resolve_interval(self) -> timedelta:
        """Pick the polling interval: the user's option, else the default."""
        override = self.config_entry.options.get(CONF_SCAN_INTERVAL)
        if override:
            return timedelta(seconds=_clamp(int(override)))
        return DEFAULT_SCAN_INTERVAL

    async def _async_update_data(self) -> RebramaData:
        """Fetch the current places, access points and latest opening logs."""
        try:
            places = await self.client.async_get_places()
        except RebramaAuthError as err:
            raise ConfigEntryAuthFailed("Authentication with Rebrama failed") from err
        except RebramaError as err:
            raise UpdateFailed(f"Error fetching data from Rebrama: {err}") from err

        # Temporary accesses are user-created and carry their own expiry, so
        # re-fetching them every poll adds load without value. Fetch the list
        # once (first poll) and carry it forward; it is refreshed on demand
        # after an HA-initiated create/delete via async_refresh_temp_accesses.
        if self.data is None:
            temp_accesses = await self._fetch_temp_accesses()
        else:
            temp_accesses = self.data.temp_accesses

        logs: dict[str, OpenLog | None] = {}
        for place in places:
            # Opening logs require "manage" permission on the place; the API
            # returns an error otherwise. Skip the doomed call for places the
            # user can only access. Log fetches are best-effort too.
            if not place.can_manage:
                logs[place.id] = None
                continue
            try:
                logs[place.id] = await self.client.async_get_latest_open_log(place.id)
            except RebramaError as err:
                _LOGGER.debug(
                    "Could not fetch opening logs for place %s: %s", place.id, err
                )
                logs[place.id] = self.data.logs.get(place.id) if self.data else None

        return RebramaData(
            places={place.id: place for place in places},
            logs=logs,
            temp_accesses=temp_accesses,
        )

    async def async_refresh_temp_accesses(self) -> None:
        """Re-fetch share links and push the update (after a create/delete)."""
        if self.data is None:
            return
        temp_accesses = await self._fetch_temp_accesses()
        self.async_set_updated_data(replace(self.data, temp_accesses=temp_accesses))

    async def _fetch_temp_accesses(self) -> list[TempAccess]:
        """Return temporary accesses, falling back to the last known value."""
        try:
            return await self.client.async_list_temporary_accesses()
        except RebramaError as err:
            _LOGGER.debug(
                "Could not fetch temporary accesses (%s); keeping last value", err
            )
            return self.data.temp_accesses if self.data is not None else []
