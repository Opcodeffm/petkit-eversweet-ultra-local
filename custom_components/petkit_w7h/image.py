"""Vorschaubild des letzten Trinkvorgangs (für Bild-Karten im Dashboard)."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import W7HConfigEntry
from .device import W7HDevice
from .entity import W7HEntity
from .media import media_root


async def async_setup_entry(hass: HomeAssistant, entry: W7HConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([W7HLastDrinkImage(hass, entry.runtime_data.device)])


class W7HLastDrinkImage(W7HEntity, ImageEntity):
    _attr_content_type = "image/jpeg"

    def __init__(self, hass: HomeAssistant, device: W7HDevice) -> None:
        W7HEntity.__init__(self, device, "last_drink_image")
        ImageEntity.__init__(self, hass)
        self._root = media_root(hass)
        self._shown: str | None = None

    @property
    def available(self) -> bool:
        return True

    def _current(self) -> str | None:
        """Bild des jüngsten Trinkvorgangs, zu dem es eins gibt."""
        for drink in reversed(self.device.visible_drinks()[-20:]):
            thumb = self.device.media_for_drink(drink).get("thumb")
            if thumb:
                return thumb
        return None

    @property
    def image_last_updated(self) -> datetime | None:
        current = self._current()
        if current != self._shown:
            self._shown = current
            self._attr_image_last_updated = dt_util.utcnow() if current else None
        return self._attr_image_last_updated

    async def async_image(self) -> bytes | None:
        current = self._current()
        if not current:
            return None
        path = self._root / current
        try:
            return await self.hass.async_add_executor_job(path.read_bytes)
        except OSError:
            return None
