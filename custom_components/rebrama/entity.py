"""Base entities and shared platform helpers for the Rebrama integration."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
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
from .coordinator import RebramaConfigEntry, RebramaCoordinator, RebramaData
from .models import AccessPoint, Place


@callback
def async_setup_dynamic_entities[T](
    entry: RebramaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    select: Callable[[RebramaData], Mapping[str, T]],
    factory: Callable[[RebramaCoordinator, T], Entity],
) -> None:
    """Add one entity per item of ``select(data)`` and keep tracking the items.

    New items get entities on the first coordinator update that reports them.
    Items that disappear are forgotten, so if they come back later (a door
    whose access was revoked and re-granted) their entities are created again
    instead of staying gone until the next reload.
    """
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _sync() -> None:
        nonlocal known
        current = select(coordinator.data)
        new_ids = current.keys() - known
        known = set(current)
        if new_ids:
            async_add_entities(
                factory(coordinator, current[item_id]) for item_id in new_ids
            )

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


def _linked_device_info(
    coordinator: RebramaCoordinator,
    *,
    identifier: str,
    name: str,
    model: str,
    parent: str,
    suggested_area: str | None = None,
) -> DeviceInfo:
    """Build the device info of a device that hangs off a parent device."""
    info = DeviceInfo(
        identifiers={(DOMAIN, identifier)},
        name=name,
        manufacturer=MANUFACTURER,
        model=model,
        configuration_url=CONFIGURATION_URL,
    )
    if suggested_area:
        info["suggested_area"] = suggested_area
    # The parent devices are registered by the integration before any
    # platform loads, so the lookup only fails on a corrupt registry.
    if (parent_id := coordinator.device_id(parent)) is not None:
        info["via_device_id"] = parent_id
    return info


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
        self._attr_device_info = _linked_device_info(
            coordinator,
            identifier=access_point_device_id(access_point.id),
            name=access_point.name,
            model=MODEL_ACCESS_POINT,
            parent=place_device_id(access_point.place_id),
            suggested_area=access_point.place_name or None,
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
        self._attr_device_info = _linked_device_info(
            coordinator,
            identifier=place_device_id(place.id),
            name=place.name,
            model=MODEL_PLACE,
            parent=account_device_id(coordinator.user_id),
        )

    @property
    def place(self) -> Place | None:
        """Return the live place record, or ``None`` if it vanished."""
        return self.coordinator.data.places.get(self._place_id)

    @property
    def available(self) -> bool:
        """Available while the coordinator succeeds and the place still exists."""
        return super().available and self.place is not None
