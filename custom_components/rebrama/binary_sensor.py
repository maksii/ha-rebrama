"""Binary sensor platform for Rebrama: access-point connectivity."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import RebramaConfigEntry, RebramaCoordinator
from .entity import RebramaAccessPointEntity, async_setup_dynamic_entities
from .models import AccessPoint

# Read-only data fed by the coordinator: no per-entity update throttling needed.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RebramaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up connectivity sensors, adding new access points as they appear."""
    async_setup_dynamic_entities(
        entry,
        async_add_entities,
        lambda data: data.access_points,
        RebramaConnectivitySensor,
    )


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
