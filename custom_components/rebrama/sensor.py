"""Sensor platform for Rebrama — last opening per place."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import RebramaConfigEntry, RebramaCoordinator
from .entity import RebramaPlaceEntity
from .models import OpenLog, Place

# Read-only data fed by the coordinator — no per-entity update throttling needed.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RebramaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up last-opened sensors, adding new places as they appear."""
    coordinator = entry.runtime_data
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
