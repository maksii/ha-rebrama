"""Base entities for the Rebrama integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTRIBUTION,
    CONFIGURATION_URL,
    DOMAIN,
    MANUFACTURER,
    MODEL_ACCESS_POINT,
    MODEL_PLACE,
    access_point_device_id,
    account_device_id,
    place_device_id,
)
from .coordinator import RebramaCoordinator
from .models import AccessPoint, Place


class RebramaAccountEntity(CoordinatorEntity[RebramaCoordinator]):
    """Base entity for account-wide data (the hub device)."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: RebramaCoordinator) -> None:
        """Initialize the entity, linking it to the account hub device."""
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, account_device_id(coordinator.user_id))},
        )


class RebramaAccessPointEntity(CoordinatorEntity[RebramaCoordinator]):
    """Base entity for everything tied to a single access point (door/gate)."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(
        self, coordinator: RebramaCoordinator, access_point: AccessPoint
    ) -> None:
        """Initialize the entity and its device entry."""
        super().__init__(coordinator, context=access_point.id)
        self._ap_id = access_point.id
        self._place_id = access_point.place_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, access_point_device_id(access_point.id))},
            name=access_point.name,
            manufacturer=MANUFACTURER,
            model=MODEL_ACCESS_POINT,
            via_device=(DOMAIN, place_device_id(access_point.place_id)),
            suggested_area=access_point.place_name or None,
            configuration_url=CONFIGURATION_URL,
        )

    @property
    def access_point(self) -> AccessPoint | None:
        """Return the live access-point record, or ``None`` if it vanished."""
        place = self.coordinator.data.places.get(self._place_id)
        if place is None:
            return None
        return place.access_points.get(self._ap_id)

    @property
    def available(self) -> bool:
        """Available while the coordinator succeeds and the AP still exists."""
        return super().available and self.access_point is not None


class RebramaPlaceEntity(CoordinatorEntity[RebramaCoordinator]):
    """Base entity for everything tied to a place."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: RebramaCoordinator, place: Place) -> None:
        """Initialize the entity and its device entry."""
        super().__init__(coordinator, context=place.id)
        self._place_id = place.id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, place_device_id(place.id))},
            name=place.name,
            manufacturer=MANUFACTURER,
            model=MODEL_PLACE,
            via_device=(DOMAIN, account_device_id(coordinator.user_id)),
            configuration_url=CONFIGURATION_URL,
        )

    @property
    def place(self) -> Place | None:
        """Return the live place record, or ``None`` if it vanished."""
        return self.coordinator.data.places.get(self._place_id)

    @property
    def available(self) -> bool:
        """Available while the coordinator succeeds and the place still exists."""
        return super().available and self.place is not None
