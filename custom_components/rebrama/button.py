"""Button platform for Rebrama — one 'open' button per access point."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import RebramaAuthError, RebramaError
from .const import DOMAIN
from .coordinator import RebramaConfigEntry, RebramaCoordinator
from .entity import RebramaAccessPointEntity
from .models import AccessPoint

# Opening a door is a write action; serialise commands to stay gentle on the API.
PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RebramaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Rebrama buttons, adding new access points as they appear."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_entities() -> None:
        new: list[RebramaOpenButton] = []
        for place in coordinator.data.places.values():
            for access_point in place.access_points.values():
                if access_point.id not in known:
                    known.add(access_point.id)
                    new.append(RebramaOpenButton(coordinator, access_point))
        if new:
            async_add_entities(new)

    _add_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_entities))


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
        try:
            delivered = await self.coordinator.client.async_open(self._ap_id)
        except RebramaAuthError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except RebramaError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="open_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        if not delivered:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="open_not_delivered"
            )

        await self.coordinator.async_request_refresh()
