"""Sensor platform for Rebrama.

Account-level sensors (subscription expiry, access-point health, active share
links) live on the hub device; a *Last opened* sensor is created per managed
place.
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import RebramaConfigEntry, RebramaCoordinator
from .entity import RebramaAccountEntity, RebramaPlaceEntity
from .models import AccessPoint, OpenLog, Place, TempAccess

# Read-only data fed by the coordinator — no per-entity update throttling needed.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RebramaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the account sensors and the per-place last-opened sensors."""
    coordinator = entry.runtime_data

    async_add_entities(
        [
            RebramaSubscriptionSensor(coordinator),
            RebramaAccessPointsOnlineSensor(coordinator),
            RebramaTemporaryAccessSensor(coordinator),
        ]
    )

    known: set[str] = set()

    @callback
    def _add_entities() -> None:
        new: list[RebramaLastOpenedSensor] = []
        for place in coordinator.data.places.values():
            # Opening logs are only available for places the user manages.
            if not place.can_manage:
                continue
            if place.id not in known:
                known.add(place.id)
                new.append(RebramaLastOpenedSensor(coordinator, place))
        if new:
            async_add_entities(new)

    _add_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_entities))


class RebramaSubscriptionSensor(RebramaAccountEntity, SensorEntity):
    """Timestamp of when the Rebrama subscription expires."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "subscription_expires"

    def __init__(self, coordinator: RebramaCoordinator) -> None:
        """Initialize the subscription sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.user_id}_subscription_expires"

    @property
    def native_value(self) -> datetime | None:
        """Return the subscription expiry, if known."""
        return self.coordinator.profile.valid_until


class RebramaAccessPointsOnlineSensor(RebramaAccountEntity, SensorEntity):
    """How many access points across the account are online."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "access_points_online"

    def __init__(self, coordinator: RebramaCoordinator) -> None:
        """Initialize the access-points-online sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.user_id}_access_points_online"

    def _access_points(self) -> list[AccessPoint]:
        return [
            access_point
            for place in self.coordinator.data.places.values()
            for access_point in place.access_points.values()
        ]

    @property
    def native_value(self) -> int:
        """Return the number of online access points."""
        return sum(1 for ap in self._access_points() if ap.is_online)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return the total count and the names of any offline access points."""
        access_points = self._access_points()
        return {
            "total": len(access_points),
            "offline": sorted(ap.name for ap in access_points if not ap.is_online),
        }


class RebramaTemporaryAccessSensor(RebramaAccountEntity, SensorEntity):
    """How many temporary-access share links are currently active."""

    _attr_translation_key = "temporary_accesses"

    def __init__(self, coordinator: RebramaCoordinator) -> None:
        """Initialize the temporary-access sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.user_id}_temporary_accesses"

    def _active(self) -> list[TempAccess]:
        """Return links that have not yet expired (derived locally from dateEnd)."""
        now = dt_util.utcnow()
        return [a for a in self.coordinator.data.temp_accesses if a.is_active(now)]

    @property
    def native_value(self) -> int:
        """Return the number of active temporary accesses."""
        return len(self._active())

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return the details of each active temporary access."""
        return {
            "accesses": [
                {
                    "description": access.description,
                    "url": access.url,
                    "valid_from": (
                        access.date_start.isoformat() if access.date_start else None
                    ),
                    "valid_until": (
                        access.date_end.isoformat() if access.date_end else None
                    ),
                    "max_uses": access.uses_number,
                }
                for access in self._active()
            ]
        }


class RebramaLastOpenedSensor(RebramaPlaceEntity, SensorEntity):
    """Timestamp of the most recent opening at a place."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "last_opened"

    def __init__(self, coordinator: RebramaCoordinator, place: Place) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, place)
        self._attr_unique_id = f"{place.id}_last_opened"

    @property
    def _log(self) -> OpenLog | None:
        return self.coordinator.data.logs.get(self._place_id)

    @property
    def native_value(self) -> datetime | None:
        """Return the timestamp of the last opening."""
        log = self._log
        return log.created_at if log else None

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        """Return details about the last opening."""
        log = self._log
        if log is None:
            return None
        return {
            "opened_by_phone": log.user_phone,
            "opened_by": log.user_info,
            "access_point": log.access_point_name,
            "temporary_access": log.is_temp_access,
        }
