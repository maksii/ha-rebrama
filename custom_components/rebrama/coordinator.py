"""DataUpdateCoordinator for the Rebrama integration."""

from __future__ import annotations

from dataclasses import dataclass
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
from .models import OpenLog, Place

_LOGGER = logging.getLogger(__name__)

type RebramaConfigEntry = ConfigEntry[RebramaCoordinator]


@dataclass
class RebramaData:
    """Snapshot of everything fetched each poll cycle."""

    places: dict[str, Place]
    logs: dict[str, OpenLog | None]


def _clamp(seconds: int) -> int:
    """Clamp a polling interval to the supported range."""
    return max(MIN_SCAN_INTERVAL, min(MAX_SCAN_INTERVAL, seconds))


def _normalize_period(period: object) -> int | None:
    """Normalize the server's ``widgetUpdatePeriod`` to whole seconds.

    The app reports the value in milliseconds; small values are treated as
    seconds defensively. Returns ``None`` when the value is unusable.
    """
    if not isinstance(period, (int, float)) or period <= 0:
        return None
    seconds = period / 1000 if period >= 1000 else period
    return int(seconds)


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

    async def _async_setup(self) -> None:
        """Fetch one-time data and resolve the polling interval."""
        try:
            self.settings = await self.client.async_get_settings()
        except RebramaError as err:
            _LOGGER.debug("Could not fetch settings (%s); using defaults", err)
            self.settings = {}
        self.update_interval = self._resolve_interval()
        _LOGGER.debug("Rebrama polling interval set to %s", self.update_interval)

    def _resolve_interval(self) -> timedelta:
        """Pick the polling interval: user option > server hint > default."""
        override = self.config_entry.options.get(CONF_SCAN_INTERVAL)
        if override:
            return timedelta(seconds=_clamp(int(override)))
        seconds = _normalize_period(self.settings.get("widgetUpdatePeriod"))
        if seconds is None:
            return DEFAULT_SCAN_INTERVAL
        return timedelta(seconds=_clamp(seconds))

    async def _async_update_data(self) -> RebramaData:
        """Fetch the current places, access points and latest opening logs."""
        try:
            places = await self.client.async_get_places()
        except RebramaAuthError as err:
            raise ConfigEntryAuthFailed("Authentication with Rebrama failed") from err
        except RebramaError as err:
            raise UpdateFailed(f"Error fetching data from Rebrama: {err}") from err

        logs: dict[str, OpenLog | None] = {}
        for place in places:
            # Opening logs require "manage" permission on the place; the API
            # returns an error otherwise. Skip the doomed call for places the
            # user can only access. Log fetches are best-effort: a failure must
            # never take the whole integration offline (auth is already verified
            # by the places call above).
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

        return RebramaData(places={place.id: place for place in places}, logs=logs)
