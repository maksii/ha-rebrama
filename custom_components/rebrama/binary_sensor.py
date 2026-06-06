"""Binary sensor platform for Rebrama — access-point connectivity."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import RebramaConfigEntry, RebramaCoordinator
from .entity import RebramaAccessPointEntity
from .models import AccessPoint

# Read-only data fed by the coordinator — no per-entity update throttling needed.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RebramaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up connectivity sensors, adding new access points as they appear."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_entities() -> None:
        new: list[RebramaConnectivitySensor] = []
        for place in coordinator.data.places.values():
            for access_point in place.access_points.values():
                if access_point.id not in known:
                    known.add(access_point.id)
                    new.append(RebramaConnectivitySensor(coordinator, access_point))
        if new:
            async_add_entities(new)

    _add_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_entities))


class RebramaConnectivitySensor(RebramaAccessPointEntity, BinarySensorEntity):
    """Reports whether an access point is online."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connectivity"

    def __init__(
        self, coordinator: RebramaCoordinator, access_point: AccessPoint
    ) -> None:
        """Initialize the connectivity sensor."""
        super().__init__(coordinator, access_point)
        self._attr_unique_id = f"{access_point.id}_connectivity"

    @property
    def is_on(self) -> bool | None:
        """Return True when the access point is online."""
        access_point = self.access_point
        return access_point.is_online if access_point else None
