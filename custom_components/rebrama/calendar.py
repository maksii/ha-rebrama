"""Calendar platform for Rebrama — temporary-access share links.

Each active or upcoming temporary access shows up as a calendar event spanning
its validity window. Adding an event creates a new share link (for every access
point the account can share) and deleting an event revokes it, so links can be
managed entirely from the Home Assistant calendar panel. For per-door control or
a usage limit, use the ``rebrama.create_temporary_access`` action instead.
"""

from __future__ import annotations

from datetime import date, datetime
import logging
from typing import Any

from homeassistant.components.calendar import (
    EVENT_END,
    EVENT_START,
    EVENT_SUMMARY,
    CalendarEntity,
    CalendarEntityFeature,
    CalendarEvent,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import RebramaAuthError, RebramaError
from .const import DOMAIN
from .coordinator import RebramaConfigEntry, RebramaCoordinator
from .entity import RebramaAccountEntity
from .models import TempAccess

# Creating/deleting share links are write actions; serialise them.
PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


def _to_epoch(value: datetime | date) -> int:
    """Convert a calendar date/datetime to epoch seconds in the local zone."""
    if isinstance(value, datetime):
        moment = (
            value
            if value.tzinfo is not None
            else value.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        )
    else:  # all-day event: take local midnight
        moment = datetime(
            value.year, value.month, value.day, tzinfo=dt_util.DEFAULT_TIME_ZONE
        )
    return int(moment.timestamp())


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RebramaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the temporary-access calendar for the account."""
    async_add_entities([RebramaTemporaryAccessCalendar(entry.runtime_data)])


class RebramaTemporaryAccessCalendar(RebramaAccountEntity, CalendarEntity):
    """A calendar of active and upcoming temporary-access share links."""

    _attr_translation_key = "temporary_access"
    _attr_supported_features = (
        CalendarEntityFeature.CREATE_EVENT | CalendarEntityFeature.DELETE_EVENT
    )

    def __init__(self, coordinator: RebramaCoordinator) -> None:
        """Initialize the calendar."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.user_id}_temporary_access"

    @staticmethod
    def _to_event(access: TempAccess) -> CalendarEvent | None:
        """Map a temporary access to a calendar event, or None if unusable."""
        start, end = access.date_start, access.date_end
        if start is None or end is None or end <= start:
            return None
        return CalendarEvent(
            start=start,
            end=end,
            summary=access.description or "Temporary access",
            description=access.url or None,
            uid=access.slug,
        )

    def _events(self) -> list[CalendarEvent]:
        events = (self._to_event(a) for a in self.coordinator.data.temp_accesses)
        return [event for event in events if event is not None]

    @property
    def event(self) -> CalendarEvent | None:
        """Return the active event, else the next upcoming one."""
        now = dt_util.utcnow()
        events = self._events()
        ongoing = [event for event in events if event.start <= now <= event.end]
        if ongoing:
            return min(ongoing, key=lambda event: event.end)
        upcoming = sorted(
            (event for event in events if event.start > now),
            key=lambda event: event.start,
        )
        return upcoming[0] if upcoming else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return every temporary access overlapping the requested window."""
        return [
            event
            for event in self._events()
            if event.start < end_date and event.end > start_date
        ]

    async def async_create_event(self, **kwargs: Any) -> None:
        """Create a temporary access spanning the event for all shareable doors."""
        start = _to_epoch(kwargs[EVENT_START])
        end = _to_epoch(kwargs[EVENT_END])
        if end <= start:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="invalid_time_range"
            )

        access_point_ids = sorted(
            access_point.id
            for place in self.coordinator.data.places.values()
            for access_point in place.access_points.values()
            if access_point.can_share_access
        )
        if not access_point_ids:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="no_shareable_access_points",
            )

        try:
            await self.coordinator.client.async_create_temporary_access(
                access_point_ids=access_point_ids,
                date_start=start,
                date_end=end,
                description=kwargs.get(EVENT_SUMMARY) or "Home Assistant",
                uses_number=None,
            )
        except RebramaAuthError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except RebramaError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="service_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        await self.coordinator.async_refresh_temp_accesses()

    async def async_delete_event(
        self,
        uid: str,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """Delete the temporary access identified by ``uid`` (its slug)."""
        try:
            await self.coordinator.client.async_delete_temporary_access(uid)
        except RebramaAuthError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except RebramaError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="service_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        await self.coordinator.async_refresh_temp_accesses()
