"""Button platform for Rebrama: one 'open' button per access point."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import RebramaConfigEntry, RebramaCoordinator
from .entity import RebramaAccessPointEntity, async_setup_dynamic_entities
from .models import AccessPoint

# Opening a door is a write action; serialise commands to stay gentle on the API.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RebramaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Rebrama buttons, adding new access points as they appear."""
    async_setup_dynamic_entities(
        entry, async_add_entities, lambda data: data.access_points, RebramaOpenButton
    )


class RebramaOpenButton(RebramaAccessPointEntity, ButtonEntity):
    """A button that opens (buzzes) an access point."""

    _attr_translation_key = "open"

    def __init__(
        self, coordinator: RebramaCoordinator, access_point: AccessPoint
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, access_point)
        self._attr_unique_id = f"{access_point.id}_open"

    @property
    def available(self) -> bool:
        """Only available while the access point is online."""
        access_point = self.access_point
        return super().available and access_point is not None and access_point.is_online

    async def async_press(self) -> None:
        """Open the access point."""
        await self.coordinator.async_open(self._ap_id)
